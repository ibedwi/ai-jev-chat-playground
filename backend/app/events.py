"""System events shown in the right-hand pane of the UI."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["USER", "JEV", "LLM", "TOOL", "SYSTEM", "ERROR"]


class Event(BaseModel):
    source: Source
    title: str
    detail: str = ""
    ms: float | None = None
    ts: str = Field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


class EventBus:
    """Collects events for one request and forwards them to the streaming response."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Event] = asyncio.Queue()
        self.history: list[Event] = []

    def emit(self, source: Source, title: str, detail: str = "", ms: float | None = None) -> None:
        event = Event(source=source, title=title, detail=detail, ms=ms)
        self.history.append(event)
        self.queue.put_nowait(event)


@contextmanager
def timer():
    t = {"ms": 0.0}
    start = time.perf_counter()
    try:
        yield t
    finally:
        t["ms"] = (time.perf_counter() - start) * 1000
