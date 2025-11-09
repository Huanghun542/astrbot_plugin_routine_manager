import re
from datetime import datetime
from typing import List, Tuple
from astrbot.api.event import filter, AstrMessageEvent, EventMessageType
from astrbot.api.star import Context, Star, register
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

TIME_PATTERN = re.compile(r'^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*(.+?)\s*$')

def hm_to_min(h: int, m: int) -> int:
    return (h % 24) * 60 + (m % 60)

@register(
    "astrbot_plugin_routine_manager",
    "Huanghun",
    "根据作息表把当前行为写入 LLM 提示词的插件",
    "0.1.1",
    "https://example.com/astrbot_plugin_routine_manager"
)
class RoutineManager(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config or {}
        self._load_config_to_cache()

    def _load_config_to_cache(self):
        self.inject_enabled = bool(self.config.get("inject_enabled", True))
        self.prompt_head = str(self.config.get("prompt_head", ""))
        self.fallback_action = str(self.config.get("fallback_action", "自由安排/机动"))
        self.schedule_text = str(self.config.get("schedule_text", ""))
        self.tz = (self.config.get("timezone") or "").strip()
        self.parsed = self._parse_schedule(self.schedule_text)

    def on_config_update(self, new_config: dict):
        self.config = new_config or {}
        self._load_config_to_cache()

    def _parse_schedule(self, text: str) -> List[Tuple[int, int, str]]:
        items: List[Tuple[int,int,str]] = []
        if not text:
            return items
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = TIME_PATTERN.match(line)
            if not m:
                continue
            sh, sm, eh, em, action = m.groups()
            s = hm_to_min(int(sh), int(sm))
            e = hm_to_min(int(eh), int(em))
            action = action.strip()
            if s == e:
                items.append((0, 1440, action))
            elif s < e:
                items.append((s, e, action))
            else:
                items.append((s, 1440, action))
                items.append((0, e, action))
        items.sort(key=lambda x: (x[0], x[1]))
        return items

    def _now_minutes(self):
        if self.tz and ZoneInfo:
            try:
                now = datetime.now(ZoneInfo(self.tz))
            except Exception:
                now = datetime.now()
        else:
            now = datetime.now()
        hhmm = now.strftime("%H:%M")
        minutes = now.hour * 60 + now.minute
        return hhmm, minutes

    def resolve_action(self, minutes: int) -> str:
        for s, e, act in self.parsed:
            if s <= minutes < e or (e == 1440 and minutes >= s):
                return act
        return self.fallback_action

    # Commands
    @filter.command_group("作息")
    def routine_group(self):
        pass

    @routine_group.command("现在")
    async def show_now(self, event: AstrMessageEvent):
        hhmm, minutes = self._now_minutes()
        action = self.resolve_action(minutes)
        yield event.plain_result(f"⏰ 当前时间 {hhmm}\n📌 当前行为：{action}")

    @routine_group.command("导出")
    async def export_schedule(self, event: AstrMessageEvent):
        text = self.schedule_text or "(空)"
        yield event.plain_result("当前作息表：\n" + text)

    @routine_group.command("导入")
    async def import_schedule(self, event: AstrMessageEvent):
        raw = (event.message_str or "").split("导入", 1)[-1].strip()
        if not raw:
            yield event.plain_result("❌ 未检测到作息文本。用法：/作息 导入 \\nHH:MM-HH:MM 行为")
            return
        parsed = self._parse_schedule(raw)
        if not parsed:
            yield event.plain_result("❌ 解析失败。请检查格式：HH:MM-HH:MM 行为，例如 08:00-09:00 早餐")
            return
        self.schedule_text = raw
        self.parsed = parsed
        self.config["schedule_text"] = raw
        yield event.plain_result(f"✅ 导入成功，共 {len(parsed)} 条。")

    @routine_group.command("开关")
    async def switch_injection(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        if "开启" in text:
            self.inject_enabled = True
            self.config["inject_enabled"] = True
            yield event.plain_result("✅ 已开启提示词注入。")
        elif "关闭" in text:
            self.inject_enabled = False
            self.config["inject_enabled"] = False
            yield event.plain_result("✅ 已关闭提示词注入。")
        else:
            yield event.plain_result(f"当前状态：{'开启' if self.inject_enabled else '关闭'}；用法：/作息 开关 开启|关闭")

    @routine_group.command("测试")
    async def test_at(self, event: AstrMessageEvent):
        m = re.search(r"(\\d{1,2}):(\\d{2})", event.message_str or "")
        if not m:
            yield event.plain_result("用法：/作息 测试 HH:MM")
            return
        hh, mm = int(m.group(1)), int(m.group(2))
        minutes = (hh % 24) * 60 + (mm % 60)
        action = self.resolve_action(minutes)
        yield event.plain_result(f"⏱ 指定时间 {hh:02d}:{mm:02d}\n📌 对应行为：{action}")

    # Instead of relying on request_llm hooks (which may vary by AstrBot version),
    # we prepend a lightweight system-style prefix to the user's prompt by setting outline.
    @filter.event_message_type(EventMessageType.ALL)
    async def maybe_inject(self, event: AstrMessageEvent):
        msg = (event.get_message_str() or "").strip()
        if msg.startswith(("/作息", "作息")):
            return
        if not self.inject_enabled:
            return

        # Build the system-style head
        hhmm, minutes = self._now_minutes()
        action = self.resolve_action(minutes)
        head = (self.prompt_head or "").format(action=action, now=hhmm).strip()

        if not head:
            return

        # For compatibility, put the head into the message outline if available;
        # otherwise, prepend it to the message text.
        try:
            outline = event.get_message_outline()
            if outline:
                event.set_message_outline(head + "\\n\\n" + outline)
            else:
                event.set_message_str(head + "\\n\\n" + (event.get_message_str() or ""))
        except Exception:
            # Fallback: just prefix user's text
            event.set_message_str(head + "\\n\\n" + (event.get_message_str() or ""))