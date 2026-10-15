# 插件开发

插件是一个目录，运行时从 `data/plugins/<名称>/` 加载。内置指令在 `verter/builtin_stars/`，不需要安装。首次启动会把 `examples/plugins/helloworld/` 复制到 `data/plugins/helloworld/`（目录已存在时不会覆盖）。

本接口的命名对齐 AstrBot 的公开插件 API，方便移植时改 import。这里没有 `astrbot` 这个包，也不要复制 AstrBot 的实现。

## 目录

```text
data/plugins/helloworld/
  metadata.yaml       # 名称、作者、说明、版本。有这个文件时以它为准
  main.py             # Star 子类和 handler
  requirements.txt    # 可选。缺依赖时插件标记为失败，不会自动安装
  _conf_schema.json   # 可选。P1 只保存，不提供表单编辑
```

`metadata.yaml` 里的 `name` 必须和目录名一致。没有 `metadata.yaml` 时使用类上的 `@register`。

## 最小插件

```python
from verter.api import logger
from verter.api.event import MessageEvent, filter
from verter.api.star import Context, Star, register


@register("helloworld", "you", "Reply to /helloworld", "1.0.0")
class HelloWorld(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.command("helloworld")
    async def helloworld(self, event: MessageEvent):
        logger.info("hello from %s", event.get_sender_id())
        yield event.plain_result("Hello, World!")

    async def terminate(self) -> None:
        """Disable、reload 和进程退出时调用。在这里停止后台任务。"""
```

`AstrMessageEvent` 是 `MessageEvent` 的别名，写在 `verter.api.event` 里。

## 分发

指令、正则、监听器在插件加载时进入一个不可变索引，加载、卸载、启停时整体替换。

- 未唤醒的消息不扫描指令 handler。群聊里没有唤醒前缀、也没有 @ 或引用机器人时，开销与指令数量无关。
- `@filter.command("ping")` 匹配唤醒后的第一个词。唤醒前缀（默认 `/`）会先被去掉。
- `@filter.command_group("tool")` 再 `@tool.command("list")` 匹配 `/tool list`。
- `@filter.regex(r"pattern")` 按正文匹配，命中也会唤醒。
- `@filter.event_message_type(...)` 单独使用时是监听器；和指令一起用时只是额外条件。
- `@filter.permission_type`、`@filter.platform_adapter_type`、`@filter.custom_filter` 只对已经入选的 handler 执行。
- 数字越大的 `priority` 越先执行。handler 可以 `yield` 多次，每次都会立刻修饰并发送。
- `@filter.on_decorating_result` 和 `@filter.after_message_sent` 是钩子。

## 通知、请求与平台接口

OneBot 的 notice（撤回、戳一戳、群成员变动、禁言等）和 request（好友 / 加群请求）会作为 `MessageType.NOTICE` / `MessageType.REQUEST` 事件进入流水线。它们不受唤醒规则限制，也不会匹配指令，只投递给订阅了这两种类型的 handler。`event.message_str` 是 `notice_type.sub_type`（或 `request_type.sub_type`），原始报文在 `event.message_obj.raw_message`。

`context.call_action(platform_id, action, params)` 调用平台接口并返回 `data`。OneBot 平台可以调用 NapCat 的任意接口；接口失败时抛出 `OneBotActionError`，平台未运行或不支持接口时抛出 `RuntimeError`。

```python
from verter.api.event import MessageEvent, MessageType, filter


class Moderator(Star):
    @filter.event_message_type(MessageType.REQUEST)
    async def on_request(self, event: MessageEvent):
        raw = event.message_obj.raw_message
        if event.message_str == "friend":
            await self.context.call_action(
                event.get_platform_id(),
                "set_friend_add_request",
                {"flag": raw["flag"], "approve": True},
            )

    @filter.command("mute")
    async def mute(self, event: MessageEvent):
        # message_str 已去掉唤醒前缀，形如 "mute 123456"。handler 只接收 event。
        _, _, user_id = event.message_str.partition(" ")
        if not user_id.strip():
            yield event.plain_result("用法：/mute <QQ号>")
            return
        await self.context.call_action(
            event.get_platform_id(),
            "set_group_ban",
            {"group_id": event.get_group_id(), "user_id": user_id.strip(), "duration": 600},
        )
        yield event.plain_result("已禁言 10 分钟")
```

## 多轮对话

`await event.wait_next(timeout=60)` 接收本会话的下一条消息，这条消息不会再进流水线。超时返回 `None`。

## 生命周期

`Context.create_task` 创建的任务会在 `terminate` 之后被取消。`/plugin off` 会调用 `terminate` 并把 handler 移出索引；`/plugin on` 会再次调用 `initialize`。`/plugin reload` 会卸掉旧模块并重新导入，进程不重启。

`/plugin` 只有管理员能用。内置指令插件不能关闭。

插件里可以 `await context.get_kv(key)` / `await context.put_kv(key, value)`。值按 JSON 存在 SQLite 里，按插件隔离。

## 和 AstrBot 的 import 对照

| AstrBot | Verter-Bot |
|---------|------------|
| `astrbot.api.event.filter` | `verter.api.event.filter` |
| `astrbot.api.event.AstrMessageEvent` | `verter.api.event.AstrMessageEvent`（即 `MessageEvent`） |
| `astrbot.api.star.Star` / `Context` / `register` | `verter.api.star` 下的同名对象 |
| `astrbot.api.logger` | `verter.api.logger` |

移植时改这几处 import，并确认装饰器名字。不要把 AstrBot 的源码复制进来。
