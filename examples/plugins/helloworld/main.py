from verter.api import logger
from verter.api.event import MessageEvent, filter
from verter.api.star import Context, Star, register


@register("helloworld", "verter", "Reply to /helloworld", "1.0.0")
class HelloWorld(Star):
    def __init__(self, context: Context) -> None:
        super().__init__(context)

    @filter.command("helloworld")
    async def helloworld(self, event: MessageEvent):
        logger.info("helloworld from %s", event.get_sender_id())
        yield event.plain_result("Hello, World!")

    async def terminate(self) -> None:
        logger.info("helloworld stopped")
