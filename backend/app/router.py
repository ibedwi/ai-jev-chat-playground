"""Conversation logic: Jev routes, code decides, LLM fills in free text.

Ported from the notebook. One `Session` holds one user's chat, tasks, meetings and any
open Draft (slot filling).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config
from .events import EventBus
from .jev import Answer, ChoiceQ, JevBackend, NoulQ, jev_call
from .llm import LLM

INTENTS = {
    "create_task": "Create a Task: something the user wants to remember to do",
    "show_agenda": "Show the Agenda: the user's Tasks and Meetings",
    "schedule_meeting": "Book, schedule, or set up a meeting or call with people",
    "search_meeting_notes": "Find or recall something from past Meeting Notes",
    "chat": "General conversation, questions, or anything else",
}

PRIORITIES = {"low": "Low priority", "normal": "Normal or unspecified", "high": "High or urgent"}

# Seeded past Meetings, one per user Agenda. Each carries exactly one Meeting Note, so
# "what did we say about X?" resolves back to the Meeting the note belongs to (its title and
# date). Dates are relative to today() so they always read as plausibly past. A Meeting booked
# in the app has no note; adding notes to Meetings is out of scope.
SEED_MEETINGS = [
    {
        "id": "acme",
        "title": "Acme sales call with Jordan",
        "attendees": ["jordan"],
        "days_ago": 8,
        "time": "10:00",
        "duration": 45,
        "note": "Jordan cares most about room double-booking; asked for pricing on 40 seats.",
    },
    {
        "id": "retro",
        "title": "Sprint 42 retro",
        "attendees": ["sam", "maya", "priya"],
        "days_ago": 5,
        "time": "16:00",
        "duration": 30,
        "note": "Releases were smooth; a Supabase outage on Tuesday slowed QA.",
    },
    {
        "id": "q4",
        "title": "Q4 planning",
        "attendees": ["maya", "priya"],
        "days_ago": 14,
        "time": "13:00",
        "duration": 60,
        "note": "Choose between live action items and CRM sync for the meeting bot.",
    },
    {
        "id": "maya-1on1",
        "title": "1:1 with Maya",
        "attendees": ["maya"],
        "days_ago": 12,
        "time": "14:00",
        "duration": 30,
        "note": "Discussed the promo cycle and goals for next quarter.",
    },
]

CONTACTS = {
    "sam": "Sam, engineer on the team",
    "jordan": "Jordan from Acme (customer)",
    "maya": "Maya, engineering manager",
    "priya": "Priya, product manager",
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAYS = {
    "today": "Today",
    "tomorrow": "Tomorrow",
    **{d: d.capitalize() for d in WEEKDAYS},
    "unspecified": "No day mentioned",
}
TIMES = {
    f"{h:02d}:{m:02d}": f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    for h in range(7, 21)
    for m in (0, 30)
}
TIMES["unspecified"] = "No start time mentioned"
DURATIONS = {
    "15": "15 minutes",
    "30": "Half an hour",
    "45": "45 minutes",
    "60": "One hour",
    "unspecified": "No duration mentioned",
}

REQUIRED = ["attendees", "day", "time"]  # duration defaults to 30 in code
ASK = {
    "attendees": "Who should I invite? (Sam, Jordan, Maya, Priya)",
    "day": "Which day works?",
    "time": "What time should it start?",
}


@dataclass
class Draft:
    intent: str
    transcript: list[str]
    slots: dict = field(default_factory=dict)
    awaiting: str = "slots"  # "slots" | "confirm"
    last_question: str = ""


@dataclass
class Agenda:
    """All of a user's Tasks and Meetings. Belongs to the user and outlives any Session."""

    tasks: list[dict] = field(default_factory=list)
    meetings: list[dict] = field(default_factory=list)


def today() -> date:
    return datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()


def resolve_day(day: str) -> date:
    t = today()
    if day == "today":
        return t
    if day == "tomorrow":
        return t + timedelta(days=1)
    delta = (WEEKDAYS.index(day) - t.weekday()) % 7 or 7
    return t + timedelta(days=delta)


def describe_meeting(m: dict) -> str:
    who = ", ".join(n.capitalize() for n in m["attendees"])
    when = f"{m['date']:%a %d %b} at {m['time']} for {m['duration']} min with {who}"
    return f"{m['title']} — {when}" if m.get("title") else when


def name_meeting(m: dict) -> str:
    """How a Meeting is referred to in a search result, e.g. '1:1 with Maya on Mon 14 Sep'."""
    label = m.get("title") or "meeting"
    return f"{label} on {m['date']:%a %d %b}"


def seeded_agenda() -> Agenda:
    """A fresh Agenda pre-populated with the seeded past Meetings and their Meeting Notes."""
    t = today()
    meetings = [
        {
            "id": s["id"],
            "title": s["title"],
            "attendees": list(s["attendees"]),
            "date": t - timedelta(days=s["days_ago"]),
            "time": s["time"],
            "duration": s["duration"],
            "note": s["note"],
        }
        for s in SEED_MEETINGS
    ]
    return Agenda(meetings=meetings)


class Session:
    def __init__(self, jev: JevBackend, llm: LLM, agenda: Agenda | None = None) -> None:
        self.jev = jev
        self.llm = llm
        self.agenda = agenda if agenda is not None else Agenda()
        self.chat: list[dict] = []
        self.draft: Draft | None = None
        self.bus = EventBus()  # replaced per request

    @property
    def tasks(self) -> list[dict]:
        return self.agenda.tasks

    @property
    def meetings(self) -> list[dict]:
        return self.agenda.meetings

    # ---- entry point -------------------------------------------------------

    async def handle(self, msg: str, bus: EventBus) -> str:
        self.bus = bus
        self.chat.append({"role": "user", "content": msg})
        bus.emit("USER", msg)
        try:
            reply = await self.respond(msg)
        except Exception as ex:  # surface failures in the event pane instead of a 500
            bus.emit("ERROR", type(ex).__name__, str(ex))
            reply = "Something went wrong; see the event log."
        self.chat.append({"role": "assistant", "content": reply})
        return reply

    async def ask(self, label, state, questions) -> dict[str, Answer]:
        return await jev_call(self.jev, self.bus, label, state, questions)

    def recent_context(self, n: int = 6) -> str:
        return "\n".join(f"{m['role']}: {m['content']}" for m in self.chat[-n - 1 : -1])

    async def respond(self, msg: str) -> str:
        if self.draft is not None:
            return await self.continue_draft(msg)

        a = await self.ask(
            "classify intent",
            {"message": msg, "recent_conversation": self.recent_context()},
            {
                "intent": ChoiceQ("What does the user want to do?", INTENTS),
                "priority": ChoiceQ("Priority the user asked for, if any", PRIORITIES),
                "has_deadline": NoulQ("The message mentions a due date or time"),
            },
        )
        intent = a["intent"]
        if intent.confidence < config.CONFIDENCE_THRESHOLD:
            self.bus.emit("SYSTEM", "low confidence → LLM", f"{intent.choice} @ {intent.confidence:.2f}")
            return await self.handle_chat(msg, a)
        self.bus.emit("SYSTEM", f"intent → {intent.choice}")
        handler = {
            "create_task": self.handle_create_task,
            "show_agenda": self.handle_show_agenda,
            "schedule_meeting": self.handle_schedule_meeting,
            "search_meeting_notes": self.handle_search_meeting_notes,
            "chat": self.handle_chat,
        }[intent.choice]
        return await handler(msg, a)

    # ---- simple handlers ---------------------------------------------------

    async def handle_create_task(self, msg, a):
        title = (
            await self.llm.complete(
                self.bus,
                [{"role": "user", "content": msg}],
                system="Rewrite the user's request as a short task title (max 10 words). Reply with the title only.",
                max_tokens=40,
                purpose="task title",
            )
        ).strip()
        task = {
            "title": title,
            "priority": a["priority"].choice,
            "has_deadline": (a["has_deadline"].noul or 0) > 0.5,
        }
        self.tasks.append(task)
        self.bus.emit("TOOL", "create_task", str(task))
        due = " (has a deadline; parse it in code)" if task["has_deadline"] else ""
        return f"Task created: {title} · priority {task['priority']}{due}"

    async def handle_show_agenda(self, msg, a):
        self.bus.emit("TOOL", "show_agenda", f"{len(self.tasks)} task(s), {len(self.meetings)} meeting(s)")
        lines = [f"Task {i + 1}: {t['title']} ({t['priority']})" for i, t in enumerate(self.tasks)]
        lines += [f"Meeting: {describe_meeting(m)}" for m in self.meetings]
        return "\n".join(lines) or "Nothing yet."

    async def handle_search_meeting_notes(self, msg, a):
        # Meeting Notes live on the Meetings they belong to; search only over Meetings that have one.
        noted = [m for m in self.meetings if m.get("note")]
        criteria = {m["id"]: f"{m['title']}: {m['note']}" for m in noted}
        ans = await self.ask(
            "pick best Meeting Note",
            {"query": msg},
            {"meeting_note": ChoiceQ("Which Meeting Note best answers the query?", {**criteria, "none": "No Meeting Note is relevant"})},
        )
        pick = ans["meeting_note"]
        if pick.choice == "none" or pick.confidence < config.CONFIDENCE_THRESHOLD:
            self.bus.emit("TOOL", "search_meeting_notes", "no confident match")
            return "I couldn't find a Meeting Note that clearly matches."
        m = next(m for m in noted if m["id"] == pick.choice)
        self.bus.emit("TOOL", "search_meeting_notes", f"matched {pick.choice}")
        return f"From your {name_meeting(m)}: {m['note']}"

    async def handle_chat(self, msg, a):
        return await self.llm.complete(self.bus, self.chat[-10:])

    # ---- slot filling: schedule_meeting ------------------------------------

    async def extract_meeting_slots(self, texts: list[str]) -> dict:
        questions: dict = {
            f"invite_{k}": NoulQ(f"The user wants {desc} to attend the meeting") for k, desc in CONTACTS.items()
        }
        questions["day"] = ChoiceQ("Which day should the meeting be on?", DAYS)
        questions["time"] = ChoiceQ(
            "What start time did the user ask for, rounded to the nearest half hour?", TIMES
        )
        questions["duration"] = ChoiceQ("How long should the meeting be?", DURATIONS)

        a = await self.ask("extract meeting slots", {"request_and_replies": texts}, questions)

        slots: dict = {}
        invited = [k for k in CONTACTS if (a[f"invite_{k}"].noul or 0) >= 0.5]
        if invited:
            slots["attendees"] = invited
        for s in ("day", "time", "duration"):
            ans = a[s]
            # Low confidence counts as missing: better to ask than to book the wrong slot.
            if ans.choice != "unspecified" and ans.confidence >= config.SLOT_CONFIDENCE:
                slots[s] = ans.choice
        return slots

    def proposed_meeting(self) -> dict:
        s = self.draft.slots
        return {
            "attendees": s["attendees"],
            "date": resolve_day(s["day"]),
            "time": s["time"],
            "duration": int(s.get("duration", 30)),
        }

    async def fill_meeting(self) -> str:
        d = self.draft
        d.slots = {**d.slots, **await self.extract_meeting_slots(d.transcript)}
        missing = [s for s in REQUIRED if s not in d.slots]
        self.bus.emit("SYSTEM", "Meeting Draft", f"filled={d.slots}\nmissing={missing}")

        if missing:
            d.awaiting = "slots"
            d.last_question = " ".join(ASK[m] for m in missing)
            return d.last_question

        d.awaiting = "confirm"
        d.last_question = f"Book {describe_meeting(self.proposed_meeting())}?"
        return d.last_question + " (yes, change something, or cancel)"

    def book_meeting(self) -> str:
        m = self.proposed_meeting()
        self.meetings.append(m)
        self.bus.emit("TOOL", "create_meeting", str({**m, "date": m["date"].isoformat()}))
        self.draft = None
        return f"Booked: {describe_meeting(m)}"

    async def handle_schedule_meeting(self, msg, a):
        self.draft = Draft(intent="schedule_meeting", transcript=[msg])
        return await self.fill_meeting()

    async def continue_draft(self, msg: str) -> str:
        options = {
            "answers": "Answers the assistant's question or adds or changes meeting details",
            "confirm": "Says yes or agrees to book the proposed meeting",
            "cancel": "Wants to stop or cancel scheduling this meeting",
            "new_request": "Asks for something unrelated to this meeting",
        }
        if self.draft.awaiting == "slots":
            options.pop("confirm")

        a = await self.ask(
            "classify reply",
            {"assistant_asked": self.draft.last_question, "user_reply": msg},
            {"reply": ChoiceQ("How does the user's Reply relate to the Meeting Draft?", options)},
        )
        reply = a["reply"]

        if reply.confidence < config.CONFIDENCE_THRESHOLD:
            self.bus.emit("SYSTEM", "unclear reply", f"{reply.choice} @ {reply.confidence:.2f}")
            return f"Sorry, I didn't catch that. {self.draft.last_question}"
        if reply.choice == "cancel":
            self.bus.emit("SYSTEM", "Meeting Draft cancelled")
            self.draft = None
            return "OK, I've dropped that meeting."
        if reply.choice == "new_request":
            self.bus.emit("SYSTEM", "Meeting Draft dropped for new request")
            self.draft = None
            return "(Dropped the Meeting Draft.)\n" + await self.respond(msg)
        if reply.choice == "confirm":
            return self.book_meeting()

        self.draft.transcript.append(msg)  # "answers": fill or change slots
        return await self.fill_meeting()

    # ---- state for the UI --------------------------------------------------

    def snapshot(self) -> dict:
        return {
            "tasks": self.tasks,
            "meetings": [{**m, "date": m["date"].isoformat()} for m in self.meetings],
            "draft": None
            if self.draft is None
            else {
                "intent": self.draft.intent,
                "slots": self.draft.slots,
                "awaiting": self.draft.awaiting,
            },
        }
