"""LLM wrapper: real Claude via the async Anthropic SDK, or a fake echo for cheap iteration."""

from __future__ import annotations

from . import config
from .events import EventBus, timer

SYSTEM_PROMPT = (
    "You are the assistant inside an AI notetaking app. Be concise. "
    "The user can also run commands (create tasks, list tasks, schedule meetings, search notes), "
    "which the app handles itself; you handle general conversation and questions."
)


class LLM:
    def __init__(self) -> None:
        self.fake = config.USE_FAKE_LLM
        if not self.fake:
            from anthropic import AsyncAnthropic

            self.client = AsyncAnthropic()

    async def complete(
        self,
        bus: EventBus,
        messages: list[dict],
        system: str = SYSTEM_PROMPT,
        max_tokens: int = 400,
        purpose: str = "chat",
    ) -> str:
        with timer() as t:
            if self.fake:
                text = f"(fake LLM, {purpose}) you said: {messages[-1]['content']!r}"
                usage = "fake"
            else:
                r = await self.client.messages.create(
                    model=config.LLM_MODEL,
                    max_tokens=max_tokens,
                    system=system,
                    messages=messages,
                )
                text = "".join(b.text for b in r.content if b.type == "text")
                usage = f"in={r.usage.input_tokens} out={r.usage.output_tokens}"
        model = "fake" if self.fake else config.LLM_MODEL
        bus.emit("LLM", f"{purpose}: {model}", usage, t["ms"])
        return text
