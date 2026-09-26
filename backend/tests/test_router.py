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
from app.router import SEED_MEETINGS, Agenda, Session, name_meeting, seeded_agenda, today


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
        {"reply": "answers"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
        {"reply": "answers"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "16:00"},
        {"reply": "confirm"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["set up a call with Jordan", "thursday at 3pm", "make it 4 instead", "yes"])

    assert r[0] == "Which day works? What time should it start?"
    assert "15:00" in r[1] and r[1].startswith("Book ")
    assert "16:00" in r[2]
    assert r[3].startswith("Booked:") and "Jordan" in r[3]
    assert s.draft is None and len(s.meetings) == 1
    assert s.meetings[0]["duration"] == 30  # default applied in code


def test_confirm_not_offered_while_slots_missing():
    jev = ScriptedJev([{"intent": "schedule_meeting"}, {}, {"reply": "answers"}, {}])
    s = Session(jev, LLM())
    run(s, ["book a meeting", "hmm"])
    reply_questions = jev.calls[2][1]
    assert "confirm" not in reply_questions["reply"].criteria


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
        {"reply": "new_request"},
        {"intent": "show_agenda"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["schedule a meeting", "what are my tasks?"])
    assert s.draft is None
    assert r[1].startswith("(Dropped the Meeting Draft.)") and "Nothing yet." in r[1]


def test_cancel():
    jev = ScriptedJev([{"intent": "schedule_meeting"}, {}, {"reply": "cancel"}])
    s = Session(jev, LLM())
    r = run(s, ["schedule a meeting", "never mind"])
    assert r[1] == "OK, I've dropped that meeting." and s.draft is None


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


def test_agenda_is_shared_across_sessions_but_draft_is_not():
    agenda = Agenda()
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
        {"reply": "confirm"},
    ])
    first = Session(jev, LLM(), agenda)
    run(first, ["set up a call with Jordan thursday at 3pm", "yes"])
    assert len(agenda.meetings) == 1

    # A new Session for the same user reads the same Agenda, but starts with no Draft.
    second = Session(ScriptedJev([]), LLM(), agenda)
    assert second.draft is None
    assert len(second.snapshot()["meetings"]) == 1


def test_open_draft_does_not_carry_into_a_new_session():
    agenda = Agenda()
    jev = ScriptedJev([{"intent": "schedule_meeting"}, {}])  # asks a slot question; Draft stays open
    first = Session(jev, LLM(), agenda)
    run(first, ["schedule a meeting"])
    assert first.draft is not None  # Draft is open on this Session

    second = Session(ScriptedJev([]), LLM(), agenda)
    assert second.draft is None
    assert second.snapshot()["draft"] is None


def test_two_users_have_separate_agendas():
    alice = Agenda()
    bob = Agenda()
    jev = ScriptedJev([{"intent": "create_task"}])
    run(Session(jev, LLM(), alice), ["remind me to email Sam"])
    assert len(alice.tasks) == 1 and len(bob.tasks) == 0


def test_seeded_agenda_links_each_note_to_exactly_one_past_meeting():
    agenda = seeded_agenda()
    assert len(agenda.meetings) == len(SEED_MEETINGS)
    # Every seeded Meeting has exactly one Meeting Note and a plausible past date.
    assert all(m.get("note") for m in agenda.meetings)
    assert all(m["date"] < today() for m in agenda.meetings)
    # A note belongs to exactly one Meeting: ids are unique.
    ids = [m["id"] for m in agenda.meetings]
    assert len(set(ids)) == len(ids)


def test_seeded_meetings_appear_on_the_agenda():
    jev = ScriptedJev([{"intent": "show_agenda"}])
    s = Session(jev, LLM(), seeded_agenda())
    (reply,) = run(s, ["what's on my agenda?"])
    assert reply.count("Meeting:") == len(SEED_MEETINGS)
    assert "1:1 with Maya" in reply


def test_search_reply_names_the_meeting_and_its_date():
    agenda = seeded_agenda()
    jev = ScriptedJev([
        {"intent": "search_meeting_notes"},
        {"meeting_note": "maya-1on1"},
    ])
    s = Session(jev, LLM(), agenda)
    (reply,) = run(s, ["what did I discuss with Maya?"])

    maya = next(m for m in agenda.meetings if m["id"] == "maya-1on1")
    assert reply == f"From your {name_meeting(maya)}: {maya['note']}"
    assert "1:1 with Maya" in reply  # names the Meeting
    assert f"{maya['date']:%d %b}" in reply  # and its date


def test_search_no_confident_match_still_replies_nothing_matches():
    jev = ScriptedJev([
        {"intent": "search_meeting_notes"},
        {"meeting_note": ("none", 0.9)},
    ])
    s = Session(jev, LLM(), seeded_agenda())
    (reply,) = run(s, ["what did we decide about llamas?"])
    assert reply == "I couldn't find a Meeting Note that clearly matches."


def test_agenda_survives_clear_across_the_api(monkeypatch):
    monkeypatch.setattr(config, "JEV_MODE", "mock")
    from fastapi.testclient import TestClient

    from app.main import app

    def send(client, session_id, message):
        with client.stream(
            "POST", "/api/chat", json={"user_id": "u1", "session_id": session_id, "message": message}
        ) as r:
            return [json.loads(l) for l in r.iter_lines() if l][-1]

    seeded = len(SEED_MEETINGS)  # every Agenda starts with the seeded past Meetings
    with TestClient(app) as client:
        # Book a meeting and create a task in the first Session, then open a Draft.
        send(client, "s1", "set up a call with Jordan thursday at 3pm")
        booked = send(client, "s1", "yes")
        assert len(booked["state"]["meetings"]) == seeded + 1  # seeded + the one just booked
        tasked = send(client, "s1", "remind me to send Jordan the pricing")
        assert len(tasked["state"]["tasks"]) == 1
        drafting = send(client, "s1", "schedule another meeting")
        assert drafting["state"]["draft"] is not None

        # Clear ends the Session (and its Draft) but leaves the Agenda alone.
        client.post("/api/reset", json={"user_id": "u1", "session_id": "s1"})
        after = send(client, "s2", "what's on my agenda?")
        assert len(after["state"]["meetings"]) == seeded + 1  # booked Meeting survived Clear
        assert len(after["state"]["tasks"]) == 1  # Task survived Clear
        assert after["state"]["draft"] is None  # Draft did not

        # A different user has a separate Agenda: seeded, but without u1's booked Meeting.
        with client.stream(
            "POST", "/api/chat", json={"user_id": "u2", "session_id": "s3", "message": "what's on my agenda?"}
        ) as r:
            other = [json.loads(l) for l in r.iter_lines() if l][-1]
        assert len(other["state"]["meetings"]) == seeded


def test_api_streams_events_then_reply(monkeypatch):
    monkeypatch.setattr(config, "JEV_MODE", "mock")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"user_id": "tu1", "session_id": "t1", "message": "set up a call with Jordan"}) as r:
            lines = [json.loads(l) for l in r.iter_lines() if l]
        assert r.headers["content-type"].startswith("application/x-ndjson")
        kinds = [l["type"] for l in lines]
        assert kinds[-1] == "reply" and kinds.count("reply") == 1 and "event" in kinds
        sources = [l["event"]["source"] for l in lines if l["type"] == "event"]
        assert sources[0] == "USER" and "JEV" in sources
        assert lines[-1]["state"]["draft"]["intent"] == "schedule_meeting"

        client.post("/api/reset", json={"user_id": "tu1", "session_id": "t1"})
        with client.stream("POST", "/api/chat", json={"user_id": "tu1", "session_id": "t2", "message": "what are my tasks?"}) as r:
            last = [json.loads(l) for l in r.iter_lines() if l][-1]
        # No Tasks yet, but the Agenda still lists the seeded past Meetings.
        assert "Meeting:" in last["content"] and last["state"]["draft"] is None
