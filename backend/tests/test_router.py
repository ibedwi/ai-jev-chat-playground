"""Conversation-flow tests with a scripted Jev. No API keys needed.

Run from backend/:  pytest -q
"""

import asyncio
import json

import pytest

from app import config
from app.events import EventBus
from app.jev import Answer, ChoiceQ, NoulQ
from app.llm import LLM
from app.router import Session


class ScriptedJev:
    """Returns pre-planned answers, one plan per Jev call, in order."""

    def __init__(self, plans):
        self.plans = list(plans)
        self.calls = []

    async def ask(self, state, questions):
        self.calls.append((state, questions))
        plan = self.plans.pop(0)
        out = {}
        for name, q in questions.items():
            if isinstance(q, NoulQ):
                out[name] = Answer(noul=plan.get(name, 0.0))
            else:
                default = "unspecified" if "unspecified" in q.criteria else next(iter(q.criteria))
                choice, conf = plan.get(name, (default, 0.9)) if isinstance(plan.get(name), tuple) else (plan.get(name, default), 0.9)
                assert choice in q.criteria, f"{name}={choice} not in options"
                out[name] = Answer(choice=choice, confidence=conf, probabilities={choice: conf})
        return out, ""

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(config, "USE_FAKE_LLM", True)


def run(session, msgs):
    replies = []
    for m in msgs:
        replies.append(asyncio.run(session.handle(m, EventBus())))
    return replies


def test_slot_filling_asks_back_then_books():
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95},
        {"turn": "answers"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
        {"turn": "answers"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "16:00"},
        {"turn": "confirm"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["set up a call with Jordan", "thursday at 3pm", "make it 4 instead", "yes"])

    assert r[0] == "Which day works? What time should it start?"
    assert "15:00" in r[1] and r[1].startswith("Book ")
    assert "16:00" in r[2]
    assert r[3].startswith("Booked:") and "Jordan" in r[3]
    assert s.pending is None and len(s.meetings) == 1
    assert s.meetings[0]["duration"] == 30  # default applied in code


def test_confirm_not_offered_while_slots_missing():
    jev = ScriptedJev([{"intent": "schedule_meeting"}, {}, {"turn": "answers"}, {}])
    s = Session(jev, LLM())
    run(s, ["book a meeting", "hmm"])
    follow_up_questions = jev.calls[2][1]
    assert "confirm" not in follow_up_questions["turn"].criteria


def test_low_confidence_slot_is_treated_as_missing():
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_sam": 0.9, "day": "friday", "time": ("15:00", 0.3)},
    ])
    s = Session(jev, LLM())
    (reply,) = run(s, ["meeting with sam friday around 3ish"])
    assert reply == "What time should it start?"


def test_new_request_drops_draft_and_is_answered():
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {},
        {"turn": "new_request"},
        {"intent": "list_tasks"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["schedule a meeting", "what are my tasks?"])
    assert s.pending is None
    assert r[1].startswith("(Dropped the meeting draft.)") and "Nothing yet." in r[1]


def test_cancel():
    jev = ScriptedJev([{"intent": "schedule_meeting"}, {}, {"turn": "cancel"}])
    s = Session(jev, LLM())
    r = run(s, ["schedule a meeting", "never mind"])
    assert r[1] == "OK, I've dropped that meeting." and s.pending is None


def test_low_confidence_intent_falls_back_to_llm():
    jev = ScriptedJev([{"intent": ("create_task", 0.3)}])
    s = Session(jev, LLM())
    (reply,) = run(s, ["hmm interesting"])
    assert reply.startswith("(fake LLM, chat)")
    assert s.tasks == []


def test_errors_become_events_not_crashes():
    class Boom:
        async def ask(self, *a):
            raise RuntimeError("jev down")

    s = Session(Boom(), LLM())
    bus = EventBus()
    reply = asyncio.run(s.handle("hi", bus))
    assert reply.startswith("Something went wrong")
    assert any(e.source == "ERROR" and "jev down" in e.detail for e in bus.history)


def test_api_streams_events_then_reply(monkeypatch):
    monkeypatch.setattr(config, "JEV_MODE", "mock")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"session_id": "t1", "message": "set up a call with Jordan"}) as r:
            lines = [json.loads(l) for l in r.iter_lines() if l]
        assert r.headers["content-type"].startswith("application/x-ndjson")
        kinds = [l["type"] for l in lines]
        assert kinds[-1] == "reply" and kinds.count("reply") == 1 and "event" in kinds
        sources = [l["event"]["source"] for l in lines if l["type"] == "event"]
        assert sources[0] == "USER" and "JEV" in sources
        assert lines[-1]["state"]["pending"]["intent"] == "schedule_meeting"

        client.post("/api/reset", json={"session_id": "t1"})
        with client.stream("POST", "/api/chat", json={"session_id": "t1", "message": "what are my tasks?"}) as r:
            last = [json.loads(l) for l in r.iter_lines() if l][-1]
        assert last["content"] == "Nothing yet." and last["state"]["pending"] is None
