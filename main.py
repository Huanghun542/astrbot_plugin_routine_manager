# main.py
# name: astrbot_plugin_routine_manager
# desc: 生成并管理日常作息表；WebUI 可编辑“时间段-行为”映射，并将当前行为注入到 LLM 的 system prompt。
# author: Huanghun
# repo: https://github.com/Huanghun542/astrbot_plugin_routine_manager

import os
import copy
import json
import asyncio
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Tuple, Optional, Dict
from multiprocessing import Process
from zoneinfo import ZoneInfo

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import EventMessageType

# ========== 可调默认值 ==========
_DEFAULT_TZ = "Asia/Shanghai"  # 可在 WebUI/配置中改为 Asia/Tokyo 等
_DEFAULT_TEMPLATE = (
    "【Routine Manager 注入】\n"
    "现在时间：{now}\n"
    "当前行为：{action}\n"
    "请在语气和内容上贴合该场景进行回复。"
)
_DEFAULT_SCHEDULE: List[Dict[str, str]] = [
    {"range": "07:00-08:30", "action": "起床 / 洗漱 / 早餐"},
    {"range": "08:30-12:00", "action": "课程 / 学习"},
    {"range": "12:00-13:30", "action": "午餐 / 休息"},
    {"range": "13:30-18:00", "action": "实验 / 项目 / 自习"},
    {"range": "18:00-19:30", "action": "晚餐 / 散步"},
    {"range": "19:30-23:30", "action": "作业 / 复盘"},
    {"range": "23:30-07:00", "action": "睡觉"},
]

# 注入范围：all（所有对话）/ private（仅私聊）/ group（仅群聊）/ off（关闭）
_DEFAULT_INJECT_SCOPE = "all"

# WebUI 默认端口（后续在 webui.py 实现）
_DEFAULT_WEBUI_PORT = 58101


@dataclass
class RoutineItem:
    start: time
    end: time
    action: str
    raw_range: str


def _parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(hour=int(hh), minute=int(mm))


def _parse_range(range_str: str) -> Tuple[time, time]:
    s, e = range_str.split("-")
    return _parse_hhmm(s.strip()), _parse_hhmm(e.strip())


def _normalize_schedule(sched_conf) -> List[RoutineItem]:
    """
    支持两种配置格式：
    1) 列表：[{ "range": "HH:MM-HH:MM", "action": "..." }, ...]
    2) 映射：{ "HH:MM-HH:MM": "..." , ... }
    """
    items: List[RoutineItem] = []
    if isinstance(sched_conf, list):
        for row in sched_conf:
            r = row.get("range", "").strip()
            a = row.get("action", "").strip()
            if not r or not a:
                continue
            s, e = _parse_range(r)
            items.append(RoutineItem(start=s, end=e, action=a, raw_range=r))
    elif isinstance(sched_conf, dict):
        for r, a in sched_conf.items():
            r = str(r).strip()
            a = str(a).strip()
            if not r or not a:
                continue
            s, e = _parse_range(r)
            items.append(RoutineItem(start=s, end=e, action=a, raw_range=r))
    return items


def _in_range(now_t: time, start: time, end: time) -> bool:
    """支持跨午夜区间（如 23:00-06:00）。"""
    if start <= end:
        return start <= now_t < end
    # 跨天
    return now_t >= start or now_t < end


@register("routine_manager", "Huanghun", "日常作息表 - 动态注入当前行为到系统提示词", "0.1.0")
class RoutineManager(Star):
    """
    主要能力：
    - WebUI 可编辑 “时间段-行为” 映射与注入模板（本文件先打好入口，webui 后续补）
    - 在每次消息事件到来前，根据当前时间计算“当前行为”，把它附加到 provider 的 persona prompt
    - 提供基础指令：查看当前行为 / 开启/关闭管理后台 / 设置注入范围
    """

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 读取配置
        self.timezone = self.config.get("timezone", _DEFAULT_TZ)
        self.inject_scope = self.config.get("inject_scope", _DEFAULT_INJECT_SCOPE)

        # 提示词模板配置：与 meme_manager 类似，预留 prompt.* 字段供 WebUI 编辑
        prompt_cfg = self.config.get("prompt") or {}
        self.prompt_template: str = prompt_cfg.get("routine_prompt_template", _DEFAULT_TEMPLATE)

        # 作息表
        self.schedule_items: List[RoutineItem] = _normalize_schedule(
            self.config.get("schedule", _DEFAULT_SCHEDULE)
        )

        # 维护已注入状态，避免重复拼接
        personas = self.context.provider_manager.personas
        self.persona_backup = copy.deepcopy(personas)
        self._last_injected_action: Optional[str] = None

        # WebUI 管理
        self.webui_process: Optional[Process] = None
        self.server_port: int = int(self.config.get("webui_port", _DEFAULT_WEBUI_PORT))
        self.server_key: Optional[str] = None  # 登录密钥（一次性）
        self._webui_ready = False

    # ---------- 公共工具 ----------
    def _now(self) -> datetime:
        try:
            return datetime.now(ZoneInfo(self.timezone))
        except Exception:
            return datetime.now(ZoneInfo(_DEFAULT_TZ))

    def _current_action(self, when: Optional[datetime] = None) -> Tuple[str, str]:
        dt = when or self._now()
        now_t = time(dt.hour, dt.minute, dt.second)
        for it in self.schedule_items:
            if _in_range(now_t, it.start, it.end):
                return it.action, it.raw_range
        return "（未定义，建议在 WebUI 中完善作息表）", "00:00-24:00"

    def _build_sys_prompt_add(self, action: str, now_str: str) -> str:
        try:
            return self.prompt_template.format(action=action, now=now_str)
        except Exception:
            # 防止模板错误导致崩溃
            return f"【Routine Manager 注入】\n现在：{now_str}\n当前行为：{action}"

    def _apply_injection(self, action: str, now_str: str):
        """把动态注入段追加到所有 persona 的系统提示词里。"""
        personas = self.context.provider_manager.personas
        sys_add = self._build_sys_prompt_add(action, now_str)
        for persona, persona_backup in zip(personas, self.persona_backup):
            persona["prompt"] = persona_backup["prompt"] + "\n\n" + sys_add

    def _clear_injection(self):
        """恢复到原始 persona 提示词。"""
        personas = self.context.provider_manager.personas
        for persona, persona_backup in zip(personas, self.persona_backup):
            persona["prompt"] = persona_backup["prompt"]
        self._last_injected_action = None

    def _should_inject_for_event(self, event: AstrMessageEvent) -> bool:
        if self.inject_scope == "off":
            return False
        is_private = event.is_private_chat()
        if self.inject_scope == "private":
            return is_private
        if self.inject_scope == "group":
            return not is_private
        return True  # all

    # ---------- 关键：在 LLM 请求前完成注入 ----------
    @filter.event_message_type(EventMessageType.ALL)
    async def _inject_on_every_message(self, event: AstrMessageEvent):
        """
        该 Handler 在消息事件进入流水线的前段触发，
        先根据注入范围决定是否注入，再根据“当前行为”刷新 personas 的系统提示词。
        """
        if not self._should_inject_for_event(event):
            self._clear_injection()
            return

        now = self._now()
        action, _ = self._current_action(now)
        if action != self._last_injected_action:
            # 仅当行为变化时刷新，避免反复叠加
            self._clear_injection()
            self._apply_injection(action, now.strftime("%Y-%m-%d %H:%M:%S"))
            self._last_injected_action = action

    # ---------- 指令组 ----------
    @filter.command_group("作息管理")
    def routine_manager(self):
        """
        作息管理：
        - 查看当前行为
        - 开启管理后台
        - 关闭管理后台
        - 设置注入范围
        """
        pass

    @routine_manager.command("查看当前行为")
    async def show_current_action(self, event: AstrMessageEvent):
        now = self._now()
        action, rng = self._current_action(now)
        yield event.plain_result(
            f"⏰ 当前：{now.strftime('%Y-%m-%d %H:%M')}\n"
            f"🧭 命中区间：{rng}\n"
            f"🏷️ 当前行为：{action}"
        )

    @routine_manager.command("设置注入范围")
    async def set_inject_scope(self, event: AstrMessageEvent):
        """
        解析消息里的选项：all / private / group / off
        例：作息管理 设置注入范围 all
        """
        text = event.get_message_str().strip()
        if any(x in text for x in [" all", " all\n"]) or text.endswith(" all"):
            self.inject_scope = "all"
        elif " private" in text or text.endswith(" private"):
            self.inject_scope = "private"
        elif " group" in text or text.endswith(" group"):
            self.inject_scope = "group"
        elif " off" in text or text.endswith(" off"):
            self.inject_scope = "off"
            self._clear_injection()
        else:
            yield event.plain_result("用法：作息管理 设置注入范围 [all|private|group|off]")
            return
        yield event.plain_result(f"✅ 已设置注入范围：{self.inject_scope}")

    # ---------- WebUI 管理（占位，后续补 webui.py） ----------
    async def _check_port_active(self) -> bool:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            try:
                s.connect(("127.0.0.1", int(self.server_port)))
                return True
            except Exception:
                return False

    @routine_manager.command("开启管理后台")
    async def open_webui(self, event: AstrMessageEvent):
        """
        启动 WebUI（后续在 webui.py 内实现 run_server）：
        - 在线编辑作息表与模板
        - 立即重载注入
        """
        if self.webui_process and self.webui_process.is_alive():
            yield event.plain_result(f"🟢 管理后台已在 {self.server_port} 端口运行")
            return

        # 延后导入，避免未实现时报错
        try:
            from .webui import run_server, generate_login_key  # 待实现
        except Exception:
            yield event.plain_result("⚠️ WebUI 暂未实现，请稍后添加 webui.py。")
            return

        self.server_key = generate_login_key()
        cfg = {
            "server_key": self.server_key,
            "server_port": self.server_port,
            "plugin_config": {
                "timezone": self.timezone,
                "inject_scope": self.inject_scope,
                "schedule": [
                    {"range": it.raw_range, "action": it.action}
                    for it in self.schedule_items
                ],
                "prompt": {"routine_prompt_template": self.prompt_template},
            },
        }

        self.webui_process = Process(target=run_server, args=(cfg,), daemon=True)
        self.webui_process.start()

        # 等待就绪
        for _ in range(12):
            if await self._check_port_active():
                self._webui_ready = True
                break
            await asyncio.sleep(0.5)

        if not self._webui_ready:
            yield event.plain_result("❌ 管理后台启动失败，请检查端口占用或稍后重试。")
        else:
            yield event.plain_result(
                f"✨ 管理后台已就绪：\n"
                f"http://127.0.0.1:{self.server_port}\n"
                f"🔑 一次性登录密钥：{self.server_key}"
            )

    @routine_manager.command("关闭管理后台")
    async def close_webui(self, event: AstrMessageEvent):
        if self.webui_process and self.webui_process.is_alive():
            self.webui_process.terminate()
            self.webui_process.join(timeout=3)
            self.webui_process = None
            self._webui_ready = False
            yield event.plain_result("🛑 管理后台已关闭")
        else:
            yield event.plain_result("ℹ️ 管理后台未在运行")

    # ---------- 生命周期 ----------
    async def terminate(self):
        """插件禁用/重载/关闭时恢复系统提示词并清理资源。"""
        self._clear_injection()
        if self.webui_process and self.webui_process.is_alive():
            self.webui_process.terminate()
            self.webui_process.join(timeout=3)
        self.webui_process = None
