"""Conversation-flow tests with a scripted Jev. No API keys needed.

Run from backend/:  pytest -q
"""

import asyncio
import json
from datetime import date

import pytest

from app import config, router
from app.events import EventBus
from app.jev import Answer, ChoiceQ, NoulQ
from app.llm import LLM
from app.router import SEED_MEETINGS, Agenda, Session, name_meeting, seeded_agenda, today


def pin_today(monkeypatch, iso):
    """Pin today() so weekday-relative dates are deterministic. Needed wherever a test names
    a bare weekday: whether it is 'today' depends on the real calendar otherwise."""
    monkeypatch.setattr(router, "today", lambda: date.fromisoformat(iso))


A_WEDNESDAY = "2026-09-30"  # "thursday" is tomorrow, never today
A_THURSDAY = "2026-10-01"  # "thursday" is today; next thursday is 2026-10-08


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


def test_slot_filling_asks_back_then_books(monkeypatch):
    pin_today(monkeypatch, A_WEDNESDAY)  # "thursday" resolves to tomorrow, unambiguously
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


def test_attendee_added_only_when_noul_confidence_meets_threshold(monkeypatch):
    monkeypatch.setattr(config, "NOUL_CONFIDENCE", 0.6)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        # sam is just above the threshold, jordan just below it.
        {"invite_sam": 0.65, "invite_jordan": 0.55, "day": "thursday", "time": "15:00"},
        {"turn": "confirm"},
    ])
    s = Session(jev, LLM())
    run(s, ["set up a call thursday at 3pm", "yes"])
    assert len(s.meetings) == 1
    assert s.meetings[0]["attendees"] == ["sam"]


def test_attendee_at_exactly_the_threshold_is_added(monkeypatch):
    monkeypatch.setattr(config, "NOUL_CONFIDENCE", 0.6)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_sam": 0.6, "day": "thursday", "time": "15:00"},
        {"turn": "confirm"},
    ])
    s = Session(jev, LLM())
    run(s, ["set up a call thursday at 3pm", "yes"])
    assert s.meetings[0]["attendees"] == ["sam"]


def test_low_confidence_slot_is_treated_as_missing():
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_sam": 0.9, "day": "friday", "time": ("15:00", 0.3)},
    ])
    s = Session(jev, LLM())
    (reply,) = run(s, ["meeting with sam friday around 3ish"])
    assert reply == "What time should it start?"


def test_bare_weekday_that_is_today_asks_which_one_naming_both_dates(monkeypatch):
    # "Thursday" said on a Thursday is ambiguous: it could mean today or next Thursday. The
    # Meeting Draft must ask which, naming both dates, instead of silently booking next week.
    pin_today(monkeypatch, A_THURSDAY)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
    ])
    s = Session(jev, LLM())
    (reply,) = run(s, ["set up a call with Jordan thursday at 3pm"])

    assert "today" in reply.lower() and "next thursday" in reply.lower()
    assert "01 Oct" in reply and "08 Oct" in reply  # both dates named
    assert s.draft is not None and s.draft.awaiting == "day"
    assert s.meetings == []  # nothing booked yet


def test_reply_today_books_today(monkeypatch):
    pin_today(monkeypatch, A_THURSDAY)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
        {"reply": "answers"},
        {"day": "today"},
        {"reply": "confirm"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["set up a call with Jordan thursday at 3pm", "today", "yes"])

    assert r[2].startswith("Booked:")
    assert len(s.meetings) == 1 and s.meetings[0]["date"] == date(2026, 10, 1)


def test_reply_next_week_books_seven_days_out(monkeypatch):
    # "next week" names no specific day; on a Thursday it means next Thursday, seven days out.
    pin_today(monkeypatch, A_THURSDAY)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
        {"reply": "answers"},
        {"day": "unspecified"},
        {"reply": "confirm"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["set up a call with Jordan thursday at 3pm", "next week", "yes"])

    assert r[2].startswith("Booked:")
    assert len(s.meetings) == 1 and s.meetings[0]["date"] == date(2026, 10, 8)


def test_reply_with_a_different_day_during_disambiguation_changes_the_slot(monkeypatch):
    # The Reply may change the day instead of picking today/next week; the Draft carries on to
    # confirm with the new day, resolved to its own next occurrence.
    pin_today(monkeypatch, A_THURSDAY)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "thursday", "time": "15:00"},
        {"reply": "answers"},
        {"day": "monday"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["set up a call with Jordan thursday at 3pm", "actually make it Monday"])

    assert r[1].startswith("Book ") and "05 Oct" in r[1]  # next Monday, not next Thursday
    assert s.draft.awaiting == "confirm"


def test_other_weekday_on_that_weekday_resolves_to_its_next_occurrence_without_asking(monkeypatch):
    # "Friday" on a Thursday is unambiguous: it is tomorrow. No question, straight to confirm.
    pin_today(monkeypatch, A_THURSDAY)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "friday", "time": "15:00"},
    ])
    s = Session(jev, LLM())
    (reply,) = run(s, ["set up a call with Jordan friday at 3pm"])

    assert reply.startswith("Book ") and "02 Oct" in reply  # tomorrow
    assert s.draft is not None and s.draft.awaiting == "confirm"


def test_explicit_today_is_not_treated_as_ambiguous(monkeypatch):
    # "today" and "tomorrow" are never ambiguous, even when today is the same weekday.
    pin_today(monkeypatch, A_THURSDAY)
    jev = ScriptedJev([
        {"intent": "schedule_meeting"},
        {"invite_jordan": 0.95, "day": "today", "time": "15:00"},
    ])
    s = Session(jev, LLM())
    (reply,) = run(s, ["set up a call with Jordan today at 3pm"])

    assert reply.startswith("Book ") and "01 Oct" in reply
    assert s.draft.awaiting == "confirm"


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


def test_low_confidence_intent_asks_to_clarify_not_llm():
    # An Unclear Message: Jev's likeliest Intent isn't chat, so the assistant suggests it
    # and asks — it must not hand the message to the LLM.
    jev = ScriptedJev([{"intent": ("schedule_meeting", 0.3)}])
    s = Session(jev, LLM())
    bus = EventBus()
    reply = asyncio.run(s.handle("something about jordan", bus))

    assert "schedule a meeting" in reply  # suggests the likeliest Intent
    assert not reply.startswith("(fake LLM")  # the LLM was not called
    assert "LLM" not in [e.source for e in bus.history]
    assert s.tasks == [] and s.meetings == []
    assert s.clarification is not None  # waiting to hear what the user meant


def test_unclear_message_event_carries_intent_and_confidence():
    jev = ScriptedJev([{"intent": ("schedule_meeting", 0.3)}])
    s = Session(jev, LLM())
    bus = EventBus()
    asyncio.run(s.handle("uhh", bus))

    unclear = next(e for e in bus.history if e.title == "Unclear Message")
    assert "schedule_meeting" in unclear.detail and "0.30" in unclear.detail
    assert "LLM" not in [e.source for e in bus.history]  # no LLM event for this turn


def test_clarify_yes_runs_suggested_intent_on_original_message():
    # "yes" carries the ORIGINAL message forward as the suggested Intent.
    jev = ScriptedJev([
        {"intent": ("create_task", 0.3)},
        {"confirmed": 0.95},
    ])
    s = Session(jev, LLM())
    r = run(s, ["the thing about emailing sam", "yes"])

    assert "schedule a meeting" not in r[0] and "create a task" in r[0]
    assert r[1].startswith("Task created:")
    assert "emailing sam" in r[1]  # the original message, not "yes", became the Task
    assert len(s.tasks) == 1 and s.clarification is None


def test_clarify_different_request_routes_normally():
    jev = ScriptedJev([
        {"intent": ("create_task", 0.3)},
        {"confirmed": 0.05},
        {"intent": "show_agenda"},
    ])
    s = Session(jev, LLM())
    r = run(s, ["mumble", "actually, what's on my agenda?"])

    assert r[1] == "Nothing yet."  # routed as show_agenda, not as a Task
    assert len(s.tasks) == 0 and s.clarification is None


def test_low_confidence_chat_asks_open_question_without_suggesting():
    # When the likeliest Intent is chat there's no action to suggest: ask an open question,
    # still without calling the LLM.
    jev = ScriptedJev([{"intent": ("chat", 0.3)}])
    s = Session(jev, LLM())
    bus = EventBus()
    reply = asyncio.run(s.handle("asdf", bus))

    assert "did you want to" not in reply.lower()  # no specific Intent suggested
    assert "LLM" not in [e.source for e in bus.history]
    assert s.clarification is None  # nothing to confirm; next message just routes normally


def test_confident_chat_still_goes_to_the_llm():
    jev = ScriptedJev([{"intent": ("chat", 0.9)}])
    s = Session(jev, LLM())
    (reply,) = run(s, ["how are you?"])
    assert reply.startswith("(fake LLM, chat)")


def test_errors_become_events_not_crashes():
    class Boom:
        async def ask(self, *a):
            raise RuntimeError("jev down")

    s = Session(Boom(), LLM())
    bus = EventBus()
    reply = asyncio.run(s.handle("hi", bus))
    assert reply.startswith("Something went wrong")
    assert any(e.source == "ERROR" and "jev down" in e.detail for e in bus.history)


def test_agenda_is_shared_across_sessions_but_draft_is_not(monkeypatch):
    pin_today(monkeypatch, A_WEDNESDAY)  # "thursday" resolves to tomorrow, unambiguously
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
    pin_today(monkeypatch, A_WEDNESDAY)  # "thursday at 3pm" resolves to tomorrow, unambiguously
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
