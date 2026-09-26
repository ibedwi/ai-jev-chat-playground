"""Keyword-based stand-in for Jev, for UI work without an API key.

It is deliberately dumb: it exists so the frontend and conversation flow can be exercised
offline. Every event it produces is labelled MOCK in the UI. Never tune thresholds on it.
"""

from __future__ import annotations

import asyncio
import re

from .jev import Answer, ChoiceQ, NoulQ

INTENT_KEYWORDS = {
    "schedule_meeting": ["meeting", "call with", "set up a call", "schedule", "book a", "sync with"],
    "create_task": ["remind", "todo", "to-do", "task to", "follow up", "add a task"],
    "show_agenda": ["my tasks", "list tasks", "what are my", "show tasks", "agenda"],
    "search_meeting_notes": ["notes", "what did", "recall", "last meeting", "remember when"],
}
YES = {"yes", "y", "yep", "yeah", "sure", "ok", "okay", "book it", "sounds good", "confirm"}
CANCEL = ["cancel", "never mind", "nevermind", "forget it", "stop"]


def _text(state) -> str:
    if isinstance(state, dict):
        preferred = [
            "user_reply",
            "message",
            "query",
            "request_and_replies",
        ]
        for key in preferred:
            if key in state:
                v = state[key]
                return (" ".join(v) if isinstance(v, list) else str(v)).lower()
        return " ".join(str(v) for v in state.values()).lower()
    return str(state).lower()


def _time_slot(text: str) -> str | None:
    found = None
    for m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text):
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not ampm and not m.group(2) and not re.search(r"\bat\s*$|\bmake it\s*$", text[: m.start()]):
            continue  # bare numbers ("40 seats") are not times
        if ampm == "pm" and hour < 12:
            hour += 12
        if not ampm and hour < 7:
            hour += 12  # "at 3" in a work context means 3 PM
        minute = 0 if minute < 15 else 30 if minute < 45 else 0
        if m.group(2) and int(m.group(2)) >= 45:
            hour += 1
        if 7 <= hour <= 20:
            found = f"{hour:02d}:{minute:02d}"  # last mention wins, so corrections apply
    return found


def _choice(name: str, q: ChoiceQ, text: str) -> str:
    opts = q.criteria
    if name == "intent":
        for intent, words in INTENT_KEYWORDS.items():
            if intent in opts and any(w in text for w in words):
                return intent
        return "chat"
    if name == "reply":
        words = set(re.findall(r"[a-z']+", text))
        if "confirm" in opts and (text.strip(" .!") in YES or words & {"yes", "yep", "sure"}):
            return "confirm"
        if any(c in text for c in CANCEL):
            return "cancel"
        if any(w in text for ws in INTENT_KEYWORDS.values() for w in ws if "meeting" not in w and "call" not in w):
            return "new_request"
        return "answers"
    if name == "time":
        return _time_slot(text) or "unspecified"
    if name == "priority":
        if "high" in text or "urgent" in text or "asap" in text:
            return "high"
        return "low" if "low priority" in text else "normal"
    # Generic: last option key mentioned in the text, else "unspecified"/"none"/first.
    hits = [(text.rfind(k), k) for k in opts if k not in ("unspecified", "none") and k in text]
    if hits:
        return max(hits)[1]
    for fallback in ("unspecified", "none"):
        if fallback in opts:
            return fallback
    return next(iter(opts))


def _note(q: ChoiceQ, text: str) -> str:
    words = {w for w in re.findall(r"[a-z]{4,}", text)}
    best, score = "none", 0
    for key, desc in q.criteria.items():
        if key == "none":
            continue
        s = len(words & set(re.findall(r"[a-z]{4,}", desc.lower())))
        if s > score:
            best, score = key, s
    return best


class MockJevBackend:
    async def ask(self, state, questions):
        await asyncio.sleep(0.05)  # pretend network
        text = _text(state)
        answers = {}
        for name, q in questions.items():
            if isinstance(q, NoulQ):
                if name.startswith("invite_"):
                    p = 0.95 if name.removeprefix("invite_") in text else 0.02
                elif name == "has_deadline":
                    p = 0.9 if re.search(r"\b(by|before|on|tomorrow|today|monday|tuesday|wednesday|thursday|friday)\b", text) else 0.05
                else:
                    p = 0.5
                answers[name] = Answer(noul=p)
            else:
                pick = _note(q, text) if name == "meeting_note" else _choice(name, q, text)
                answers[name] = Answer(choice=pick, confidence=0.9, probabilities={pick: 0.9})
        return answers, "MOCK (no API call)"

    async def aclose(self) -> None:
        pass
