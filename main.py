import os
import json
import secrets
import asyncio
import importlib.util
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Tuple, Optional
from multiprocessing import Process
from zoneinfo import ZoneInfo

# 符合 AstrBot 插件开发规范的导入
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from astrbot.api import logger

# ---------------- 常量 ----------------
_DEFAULT_TZ = "Asia/Shanghai"
_DEFAULT_TEMPLATE = "现在时间：{now} 当前行为：{action} 请在语气和内容上贴合该场景进行回复。"
_DEFAULT_WEBUI_PORT = 58101
WEEK_KEYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ---------------- 数据结构 ----------------
@dataclass
class RoutineItem:
    day: int                 # 0..6  (Mon..Sun)
    start: time
    end: time
    action: str
    raw_range: str           # "HH:MM-HH:MM"

# ---------------- 工具函数 ----------------
def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(hour=int(hh), minute=int(mm))

def _parse_range(range_str: str) -> Tuple[time, time]:
    s, e = range_str.split("-")
    return _parse_hhmm(s.strip()), _parse_hhmm(e.strip())

def _in_range(now_t: time, start: time, end: time) -> bool:
    # 不允许跨天块：直接比较
    return start < end and (start <= now_t < end)

def _normalize_schedule(sched_conf) -> List[RoutineItem]:
    """将配置中的 {Mon:{'07:00-08:00':'X'}, ...} 规范为 RoutineItem 列表"""
    items: List[RoutineItem] = []
    if isinstance(sched_conf, dict):
        for k in WEEK_KEYS:
            sub = sched_conf.get(k, {}) or {}
            if not isinstance(sub, dict):
                continue
            day_idx = WEEK_KEYS.index(k)
            for rng, act in sub.items():
                try:
                    s, e = _parse_range(str(rng))
                    if s >= e:
                        continue  # 暂不支持跨天
                    items.append(RoutineItem(
                        day=day_idx, start=s, end=e,
                        action=str(act).strip(), raw_range=str(rng)
                    ))
                except Exception:
                    continue
    return items

# =======================================================================

@register("routine_manager", "Huanghun", "每周作息表 - 动态注入当前行为到系统提示词", "0.8.1")
class RoutineManager(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 路径配置
        self._storage_dir = os.path.dirname(os.path.abspath(__file__))
        self._config_file = os.path.join(self._storage_dir, "routine_config.json")
        self._config_mtime: Optional[float] = None

        # 运行参数初始化
        self.timezone = _DEFAULT_TZ
        self.inject_scope = "all"
        self.prompt_template = _DEFAULT_TEMPLATE
        self.server_port = _DEFAULT_WEBUI_PORT
        self.schedule_items: List[RoutineItem] = []

        # WebUI 进程句柄
        self.webui_process: Optional[Process] = None

        # 初始化加载配置
        self._load_config_from_runtime()

    # ---------------- 配置加载与热更新 ----------------
    def _load_config_from_runtime(self):
        """从 JSON 文件加载配置（WebUI 修改的就是这个文件）"""
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                
                self.timezone = disk.get("timezone", _DEFAULT_TZ)
                self.inject_scope = disk.get("inject_scope", "all")
                
                # 解析提示词模板
                pf = disk.get("prompt") or {}
                self.prompt_template = pf.get("routine_prompt_template", _DEFAULT_TEMPLATE)
                
                # 解析端口
                self.server_port = int(disk.get("webui_port", _DEFAULT_WEBUI_PORT))
                
                # 解析作息表
                self.schedule_items = _normalize_schedule(disk.get("schedule", {}))
                
                # 更新文件修改时间戳
                self._config_mtime = os.path.getmtime(self._config_file)
            except Exception as e:
                logger.error(f"[RoutineManager] Failed to load config: {e}")

    def _maybe_reload_config(self):
        """检查文件是否变更，若变更则热重载"""
        try:
            if os.path.exists(self._config_file):
                mtime = os.path.getmtime(self._config_file)
                if self._config_mtime is None or mtime > self._config_mtime:
                    # logger.info("[RoutineManager] Detected config change, reloading...")
                    self._load_config_from_runtime()
        except Exception:
            pass

    # ---------------- 核心逻辑：时间与行为判定 ----------------
    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.timezone))
        except Exception:
            return datetime.now(ZoneInfo(_DEFAULT_TZ))

    def _current_action(self, when: Optional[datetime] = None) -> Tuple[str, str]:
        """计算当前时间对应的行为"""
        dt = when or self._now()
        now_t = time(dt.hour, dt.minute, dt.second)
        day = dt.weekday()  # 0..6 (Mon..Sun)
        
        for it in self.schedule_items:
            if it.day == day and _in_range(now_t, it.start, it.end):
                return it.action, it.raw_range
        return "（未定义，建议在 WebUI 中完善每周作息表）", "—"

    def _should_inject(self, event: AstrMessageEvent) -> bool:
        """判断当前场景是否需要注入"""
        if self.inject_scope == "off":
            return False
        
        try:
            is_private = event.is_private_chat()
        except Exception:
            is_private = True
            
        if self.inject_scope == "private":
            return is_private
        if self.inject_scope == "group":
            return not is_private
        return True

    # ---------------- 核心逻辑：Prompt 注入 (Hook) ----------------
    
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        # 1. 热重载检查
        self._maybe_reload_config()

        # 2. 范围判定
        if not self._should_inject(event):
            return

        # 3. 计算当前行为
        now = self._now()
        action, _ = self._current_action(now)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 4. 构建提示词
        try:
            injection_text = self.prompt_template.format(action=action, now=now_str)
        except Exception:
            injection_text = f"现在时间：{now_str} 当前行为：{action}"

        # 5. 注入到 System Prompt
        if req.system_prompt:
            req.system_prompt += f"\n\n{injection_text}"
        else:
            req.system_prompt = injection_text

    # ---------------- WebUI 管理与进程控制 ----------------
    async def _check_port_active(self) -> bool:
        """检查端口是否被占用"""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", int(self.server_port)), timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    def _generate_secret_key(self, n: int = 12) -> str:
        return secrets.token_urlsafe(n)

    def _export_runtime_config(self) -> dict:
        weekly = {k: {} for k in WEEK_KEYS}
        for it in self.schedule_items:
            key = f"{it.start.strftime('%H:%M')}-{it.end.strftime('%H:%M')}"
            weekly[WEEK_KEYS[it.day]][key] = it.action
            
        return {
            "timezone": self.timezone,
            "inject_scope": self.inject_scope,
            "webui_port": self.server_port,
            "schedule": weekly,
            "prompt": {"routine_prompt_template": self.prompt_template},
        }

    def _kill_webui_process(self):
        """【修复】独立的进程清理函数，不含 yield，可被 await 或直接调用"""
        if self.webui_process and self.webui_process.is_alive():
            try:
                self.webui_process.terminate()
                self.webui_process.join(timeout=2)
            except Exception:
                pass
        self.webui_process = None

    @filter.command_group("作息管理")
    def routine_manager(self):
        """命令组：作息管理"""
        pass

    @filter.permission_type(filter.PermissionType.ADMIN)
    @routine_manager.command("开启管理后台")
    async def start_webui(self, event: AstrMessageEvent):
        """启动作息管理 WebUI"""
        yield event.plain_result("🚀 正在启动管理后台，请稍等片刻～")

        self.server_port = int(self.config.get("webui_port", _DEFAULT_WEBUI_PORT))
        one_time_key = self._generate_secret_key(12)

        try:
            # 检查端口占用情况
            if await self._check_port_active():
                 # 端口被占，检查是否为本插件开启的进程
                 if self.webui_process and self.webui_process.is_alive():
                     # 是自己的进程 -> 重启（先杀掉）
                     self._kill_webui_process()
                     # 等待一小会儿让端口释放
                     await asyncio.sleep(1)
                 else:
                     # 端口被占，但不是我记录的进程（可能是僵尸进程或被其他软件占用）
                     yield event.plain_result(f"⚠️ 端口 {self.server_port} 已被占用，且无法自动释放。请检查后台进程或更换端口。")
                     return

            # 动态导入 WebUI
            try:
                from .webui import run_server
            except ImportError:
                spec = importlib.util.spec_from_file_location(
                    "routine_webui",
                    os.path.join(self._storage_dir, "webui.py")
                )
                if spec and spec.loader:
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)
                    run_server = m.run_server
                else:
                    raise ImportError("Cannot find webui.py")

            # 启动配置
            cfg = {
                "webui_port": self.server_port,
                "server_key": one_time_key,
                "storage_path": self._config_file,
                "plugin_config": self._export_runtime_config(),
                "host": "0.0.0.0",
                "one_time_key": True,
                "key_ttl_seconds": 600,
            }
            
            self.webui_process = Process(target=run_server, args=(cfg,), daemon=True)
            self.webui_process.start()

            # 轮询等待启动
            for _ in range(15):
                if await self._check_port_active():
                    break
                await asyncio.sleep(1)
            else:
                self._kill_webui_process()
                yield event.plain_result("⌛ 启动超时，请检查服务器防火墙或日志。")
                return

            safe_url = f"http://[您的公网ip]:{self.server_port}"
            yield event.plain_result(
                "✨ 管理后台已就绪！\n"
                "━━━━━━━━━━━━━━\n"
                f"🔑 临时密钥：{one_time_key}\n"
                "⚠️ 10分钟内有效，首次登录后即作废。\n"
                f"🔗 访问地址： {safe_url}"
            )

        except Exception as e:
            logger.error(f"[RoutineManager] Start WebUI failed: {e}")
            yield event.plain_result(f"⚠️ 后台启动失败：{e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @routine_manager.command("关闭管理后台")
    async def stop_webui(self, event: AstrMessageEvent):
        if self.webui_process and self.webui_process.is_alive():
            self._kill_webui_process()
            yield event.plain_result("🛑 管理后台已关闭")
        else:
            yield event.plain_result("ℹ️ 管理后台未在运行")

    async def terminate(self):
        """插件卸载时清理"""
        self._kill_webui_process()
        logger.info("[RoutineManager] Terminated.")