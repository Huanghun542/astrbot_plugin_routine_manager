# astrbot_plugin_routine_manager

- WebUI 字段：`schedule_text`、`prompt_head`、`inject_enabled`、`timezone`、`fallback_action`
- 开启注入后，会将“当前时间对应的行为”以 **system prompt 风格**前缀注入到请求中（通过 outline/text 兼容方式）。
- 指令：/作息 现在 | 导出 | 导入 | 开关 | 测试