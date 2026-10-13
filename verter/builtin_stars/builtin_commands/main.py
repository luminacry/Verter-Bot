from verter.api.event import MessageEvent, filter
from verter.api.star import Star, register


@register("builtin_commands", "verter", "Help, session id, and plugin administration", "1.0.0")
class BuiltinCommands(Star):
    @filter.command("help")
    async def help_command(self, event: MessageEvent):
        names = self.context.command_names()
        body = "\n".join(f"/{name}" for name in names) if names else "（无）"
        yield event.plain_result(f"指令：\n{body}")

    @filter.command("sid")
    async def sid(self, event: MessageEvent):
        yield event.plain_result(event.unified_msg_origin)

    @filter.command("plugin")
    async def plugin_command(self, event: MessageEvent):
        if not event.is_admin():
            yield event.plain_result("只有管理员可以管理插件。").stop_event()
            return
        parts = event.message_str.split()
        sub = parts[1] if len(parts) > 1 else "ls"
        if sub == "ls":
            rows: list[str] = []
            for info in self.context.describe_plugins():
                state = "failed" if info.error else ("on" if info.enabled else "off")
                detail = info.error or info.desc
                rows.append(f"{info.name}  {state}  {info.version}  {detail}")
            yield event.plain_result("\n".join(rows) if rows else "没有插件")
            return
        if sub not in {"on", "off", "reload"} or len(parts) < 3:
            yield event.plain_result("用法：/plugin ls | on <名称> | off <名称> | reload <名称>")
            return
        name = parts[2]
        if sub == "reload":
            yield event.plain_result(await self.context.reload_plugin(name))
            return
        yield event.plain_result(await self.context.set_plugin_enabled(name, sub == "on"))
