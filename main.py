# main.py  — astrbot_plugin_routine_manager
# 说明：
# 1) 默认时区 Asia/Shanghai
# 2) 仅管理员可执行 “作息管理 开启管理后台/关闭管理后台”
# 3) 开启后台时生成一次性临时密钥（默认 10 分钟有效，首次登录即失效）
# 4) WebUI 地址在聊天里以 http://[您的公网ip]:端口 的形式输出（不暴露真实 IP）
# 5) WebUI 进程通过 .webui.run_server 启动；若相对导入失败，自动从同目录 webui.py 加载

import os
import json
import copy
import asyncio
import importlib.util
import secrets
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Tuple, Optional
from multiprocessing import Process
from zoneinfo import ZoneInfo

# ========== 兼容 AstrBot SDK 的导入（无 SDK 时不报错，便于静态检查） ==========
try:
    from astrbot.api.event import filter as _ab_filter, AstrMessageEvent as _AstrMessageEvent
    from astrbot.api.star import Context as _Context, Star as _Star, register as _ab_register
    from astrbot.api.event.filter import EventMessageType as _EventMessageType
except Exception:  # 运行时一定会有 SDK，这里只是兜底
    _ab_filter = None
    class _Context: ...
    class _Star: ...
    class _AstrMessageEvent: ...
    class _EventMessageType:
        ALL = "ALL"
    def _ab_register(*_a, **_k):
        def deco(cls): return cls
        return deco

if _ab_filter is None:
    class _DummyFilter:
        class PermissionType:
            ADMIN = "ADMIN"
        def permission_type(self, *_a, **_k):
            def deco(fn): return fn
            return deco
        def command_group(self, *_a, **_k):
            def deco(fn): return fn
            return deco
        def event_message_type(self, *_a, **_k):
            def deco(fn): return fn
            return deco
    filter = _DummyFilter()
else:
    filter = _ab_filter

register = _ab_register
Star = _Star
Context = _Context
AstrMessageEvent = _AstrMessageEvent
EventMessageType = _EventMessageType
# =======================================================================

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
    """将 {Mon:{'07:00-08:00':'X'}, ...} 规范为 RoutineItem 列表"""
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
                        continue  # 不接受跨天
                    items.append(RoutineItem(
                        day=day_idx, start=s, end=e,
                        action=str(act).strip(), raw_range=str(rng)
                    ))
                except Exception:
                    continue
    return items

def _apply_prompt_only_prompt_field(personas, backup, sys_add: str):
    """只在 persona['prompt'] 末尾追加，不动昵称/名称。"""
    try:
        if isinstance(personas, list) and isinstance(backup, list):
            for i in range(len(personas)):
                p = personas[i]; b = backup[i] if i < len(backup) else {}
                if isinstance(p, dict) and isinstance(b, dict):
                    base = b.get("prompt", p.get("prompt", ""))
                    if isinstance(base, str):
                        p["prompt"] = (base + "\n\n" + sys_add).strip()
        elif isinstance(personas, dict) and isinstance(backup, dict):
            for k, p in personas.items():
                b = backup.get(k, {})
                if isinstance(p, dict) and isinstance(b, dict):
                    base = b.get("prompt", p.get("prompt", ""))
                    if isinstance(base, str):
                        p["prompt"] = (base + "\n\n" + sys_add).strip()
    except Exception:
        pass

def _restore_prompt_only_prompt_field(personas, backup):
    try:
        if isinstance(personas, list) and isinstance(backup, list):
            for i in range(len(personas)):
                p = personas[i]; b = backup[i] if i < len(backup) else {}
                if isinstance(p, dict) and isinstance(b, dict) and isinstance(b.get("prompt"), str):
                    p["prompt"] = b["prompt"]
        elif isinstance(personas, dict) and isinstance(backup, dict):
            for k, p in personas.items():
                b = backup.get(k, {})
                if isinstance(p, dict) and isinstance(b, dict) and isinstance(b.get("prompt"), str):
                    p["prompt"] = b["prompt"]
    except Exception:
        pass

# =======================================================================

@register("routine_manager", "Huanghun", "每周作息表 - 动态注入当前行为到系统提示词", "0.7.0")
class RoutineManager(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 路径
        self._storage_dir = os.path.dirname(os.path.abspath(__file__))
        self._config_file = os.path.join(self._storage_dir, "routine_config.json")
        self._config_mtime: Optional[float] = None

        # 运行参数
        self.timezone = self.config.get("timezone", _DEFAULT_TZ)
        self.inject_scope = self.config.get("inject_scope", "all")     # off / private / group / all
        self.prompt_template = (self.config.get("prompt") or {}).get(
            "routine_prompt_template", _DEFAULT_TEMPLATE
        )
        self.server_port = int(self.config.get("webui_port", _DEFAULT_WEBUI_PORT))

        # 作息项（只从磁盘读取）
        self.schedule_items: List[RoutineItem] = []

        # 人格注入
        try:
            self._personas = self.context.provider_manager.personas
        except Exception:
            self._personas = []
        self.persona_backup = copy.deepcopy(self._personas)
        self._last_injected_key: Optional[str] = None

        # WebUI
        self.webui_process: Optional[Process] = None

        # 从磁盘配置合并
        self._load_config_from_runtime(config)

    # ---------------- 配置加载与热更新 ----------------
    def _load_config_from_runtime(self, base_conf: Optional[dict] = None):
        # 先读磁盘（包含 schedule / prompt / timezone 等）
        if os.path.exists(self._config_file):
            try:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                self.timezone = disk.get("timezone", self.timezone)
                self.inject_scope = disk.get("inject_scope", self.inject_scope)
                pf = disk.get("prompt") or {}
                self.prompt_template = pf.get("routine_prompt_template", self.prompt_template)
                self.server_port = int(disk.get("webui_port", self.server_port))
                self.schedule_items = _normalize_schedule(disk.get("schedule", {}))
                self._config_mtime = os.path.getmtime(self._config_file)
            except Exception:
                pass

    def _maybe_reload_config(self):
        try:
            if os.path.exists(self._config_file):
                mtime = os.path.getmtime(self._config_file)
                if self._config_mtime is None or mtime > self._config_mtime:
                    self._config_mtime = mtime
                    self._load_config_from_runtime(self.config)
                    self._last_injected_key = None
        except Exception:
            pass

    # ---------------- 注入相关 ----------------
    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.timezone))
        except Exception:
            return datetime.now(ZoneInfo(_DEFAULT_TZ))

    def _current_action(self, when: Optional[datetime] = None) -> Tuple[str, str]:
        dt = when or self._now()
        now_t = time(dt.hour, dt.minute, dt.second)
        day = dt.weekday()  # 0..6
        for it in self.schedule_items:
            if it.day == day and _in_range(now_t, it.start, it.end):
                return it.action, it.raw_range
        return "（未定义，建议在 WebUI 中完善每周作息表）", "—"

    def _build_sys_prompt(self, action: str, now_str: str) -> str:
        try:
            return self.prompt_template.format(action=action, now=now_str)
        except Exception:
            return f"现在时间：{now_str} 当前行为：{action} 请在语气和内容上贴合该场景进行回复。"

    def _apply_injection(self, action: str, now_str: str):
        try:
            self._personas = self.context.provider_manager.personas
        except Exception:
            pass
        _apply_prompt_only_prompt_field(
            self._personas, self.persona_backup, self._build_sys_prompt(action, now_str)
        )

    def _clear_injection(self):
        try:
            self._personas = self.context.provider_manager.personas
        except Exception:
            pass
        _restore_prompt_only_prompt_field(self._personas, self.persona_backup)

    def _should_inject_for_event(self, event: AstrMessageEvent) -> bool:
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

    # ---------------- WebUI 帮助函数 ----------------
    async def _check_port_active(self) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", int(self.server_port)), timeout=1.0
            )
            writer.close()
            return True
        except Exception:
            return False

    def _generate_secret_key(self, n: int = 12) -> str:
        return secrets.token_urlsafe(n)

    def _export_runtime_config(self) -> dict:
        """给 WebUI 的初始配置（包含现有 schedule）"""
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

    # ---------------- 事件与命令 ----------------
    @filter.event_message_type(EventMessageType.ALL)
    async def _inject_on_every_message(self, event: AstrMessageEvent):
        # 热更新（webui 保存后会触发）
        self._maybe_reload_config()

        if not self._should_inject_for_event(event):
            self._clear_injection()
            self._last_injected_key = None
            return

        now = self._now()
        action, rng = self._current_action(now)
        key = f"{now.weekday()}|{rng}|{action}"
        if key != self._last_injected_key:
            self._clear_injection()
            self._apply_injection(action, now.strftime("%Y-%m-%d %H:%M:%S"))
            self._last_injected_key = key

    @filter.command_group("作息管理")
    def routine_manager(self):
        """命令组：作息管理"""
        ...

    @filter.permission_type(filter.PermissionType.ADMIN)
    @routine_manager.command("开启管理后台")
    async def start_webui(self, event: AstrMessageEvent):
        """启动作息管理 WebUI（一次性临时密钥 & 安全地址占位）"""
        yield event.plain_result("🚀 正在启动管理后台，请稍等片刻～")

        # 一次性密钥（首次登录即作废；10 分钟有效）
        self.server_port = int(self.config.get("webui_port", _DEFAULT_WEBUI_PORT))
        one_time_key = self._generate_secret_key(12)

        try:
            # 如果端口已被占用，复用现有进程
            already = await self._check_port_active()
            if not already:
                # —— 动态导入 run_server（相对导入失败则从文件加载）——
                try:
                    from .webui import run_server  # type: ignore
                except Exception:
                    spec = importlib.util.spec_from_file_location(
                        "routine_webui",
                        os.path.join(self._storage_dir, "webui.py")
                    )
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)  # type: ignore
                    run_server = m.run_server  # type: ignore

                cfg = {
                    "webui_port": self.server_port,
                    "server_key": one_time_key,
                    "storage_path": self._config_file,
                    "plugin_config": self._export_runtime_config(),
                    "host": "0.0.0.0",
                    "one_time_key": True,
                    "key_ttl_seconds": 600,  # 10 分钟
                }
                self.webui_process = Process(target=run_server, args=(cfg,), daemon=True)
                self.webui_process.start()

            # 等待端口就绪
            for _ in range(12):
                if await self._check_port_active():
                    break
                await asyncio.sleep(1)
            else:
                yield event.plain_result("⌛ 启动超时，请检查服务器防火墙或端口映射")
                return

            # 安全输出（不暴露真实 IP）
            safe_url = f"http://[您的公网ip]:{self.server_port}"
            yield event.plain_result(
                "✨ 管理后台已就绪！\n"
                "━━━━━━━━━━━━━━\n"
                f"🔑 临时密钥（一次性，10 分钟内有效）：{one_time_key}\n"
                "⚠️ 首次成功登录后该密钥立即失效；超时也会失效\n"
                "⚠️ 请勿分享给未授权用户"
            )
            yield event.plain_result(f"🔗 访问地址： {safe_url}")

        except Exception as e:
            yield event.plain_result(f"⚠️ 后台启动失败：{e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @routine_manager.command("关闭管理后台")
    async def stop_webui(self, event: AstrMessageEvent):
        if self.webui_process and self.webui_process.is_alive():
            self.webui_process.terminate()
            self.webui_process.join(timeout=2)
            self.webui_process = None
            yield event.plain_result("🛑 管理后台已关闭")
        else:
            yield event.plain_result("ℹ️ 管理后台未在运行")

    async def terminate(self):
        self._clear_injection()
        if self.webui_process and self.webui_process.is_alive():
            self.webui_process.terminate()
            self.webui_process.join(timeout=2)
        self.webui_process = None
