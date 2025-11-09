# routine_manager

An AstrBot plugin for injecting the "current routine action" into the LLM system prompt.
- Edit `schedule_text` in the plugin WebUI. Each line: `HH:MM-HH:MM Action`.
- Turn `inject_enabled` on to inject `{action}` and `{now}` into the LLM's system prompt via `prompt_head`.
- Use `/作息` command group to manage.