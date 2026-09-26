"""FastAPI app: streams system events and the final reply as NDJSON.

POST /api/chat   {"user_id": "...", "session_id": "...", "message": "..."}
  -> application/x-ndjson, one JSON object per line:
     {"type": "event", "event": {...}}   zero or more, as they happen
     {"type": "reply", "content": "...", "state": {...}}
POST /api/reset  {"user_id": "...", "session_id": "..."}
GET  /api/health
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import config
from .events import EventBus
from .llm import LLM
from .router import Agenda, Session

# The Agenda belongs to the user and outlives any Session; Sessions come and go with Clear.
agendas: dict[str, Agenda] = {}
sessions: dict[str, Session] = {}
locks: dict[str, asyncio.Lock] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.JEV_MODE == "mock":
        from .mock_jev import MockJevBackend

        app.state.jev = MockJevBackend()
    else:
        from .jev import TypeSafeBackend

        app.state.jev = TypeSafeBackend()
    app.state.llm = LLM()
    yield
    await app.state.jev.aclose()


app = FastAPI(title="Jev chat", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)


class ResetIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=100)


def get_agenda(user_id: str) -> Agenda:
    if user_id not in agendas:
        agendas[user_id] = Agenda()
    return agendas[user_id]


def get_session(user_id: str, session_id: str) -> Session:
    if session_id not in sessions:
        sessions[session_id] = Session(app.state.jev, app.state.llm, get_agenda(user_id))
        locks[session_id] = asyncio.Lock()
    return sessions[session_id]


def line(obj: dict) -> bytes:
    return (json.dumps(obj, default=str) + "\n").encode()


@app.get("/api/health")
async def health():
    return {
        "jev_mode": config.JEV_MODE,
        "llm": "fake" if config.USE_FAKE_LLM else config.LLM_MODEL,
    }


@app.post("/api/chat")
async def chat(body: ChatIn):
    session = get_session(body.user_id, body.session_id)
    lock = locks[body.session_id]
    bus = EventBus()

    async def stream():
        async with lock:  # one message at a time per session
            task = asyncio.create_task(session.handle(body.message.strip(), bus))
            # Forward events as they are emitted, until the handler finishes.
            while True:
                getter = asyncio.create_task(bus.queue.get())
                done, _ = await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield line({"type": "event", "event": getter.result().model_dump()})
                    continue
                getter.cancel()
                break
            while not bus.queue.empty():
                yield line({"type": "event", "event": bus.queue.get_nowait().model_dump()})
            yield line({"type": "reply", "content": task.result(), "state": session.snapshot()})

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/reset")
async def reset(body: ResetIn):
    # Clear ends the Session (dropping its messages and open Draft) but leaves the user's Agenda alone.
    sessions.pop(body.session_id, None)
    locks.pop(body.session_id, None)
    return {"ok": True}
