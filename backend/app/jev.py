"""Thin layer over TypeSafe Jev.

The router builds questions with the small `ChoiceQ` / `NoulQ` types below and gets back
normalized `Answer`s. That keeps the routing logic independent of SDK details and lets tests
and the mock mode swap in a fake client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .events import EventBus, timer


@dataclass
class ChoiceQ:
    instructions: str
    criteria: dict[str, str]


@dataclass
class NoulQ:
    instructions: str


Question = ChoiceQ | NoulQ


@dataclass
class Answer:
    choice: str | None = None
    confidence: float = 0.0
    probabilities: dict[str, float] = field(default_factory=dict)
    noul: float | None = None


class JevBackend(Protocol):
    async def ask(self, state: Any, questions: dict[str, Question]) -> tuple[dict[str, Answer], str]:
        """Return (answers, usage string)."""
        ...


class TypeSafeBackend:
    """Calls the real API through the official async SDK."""

    def __init__(self) -> None:
        from typesafe_sdk import AsyncTypeSafeClient  # imported lazily so mock mode needs no SDK

        self.client = AsyncTypeSafeClient()

    async def ask(self, state, questions):
        from typesafe_sdk import Choice, Noul

        sdk_questions = {
            name: Choice(instructions=q.instructions, criteria=q.criteria)
            if isinstance(q, ChoiceQ)
            else Noul(instructions=q.instructions)
            for name, q in questions.items()
        }
        r = await self.client.system_one(state=state, questions=sdk_questions)

        answers: dict[str, Answer] = {}
        for name, a in r.answers.items():
            if getattr(a, "noul", None) is not None:
                answers[name] = Answer(noul=float(a.noul))
            else:
                answers[name] = Answer(
                    choice=a.choice,
                    confidence=float(a.confidence),
                    probabilities={str(k): float(v) for k, v in a.probabilities.items()},
                )
        usage = getattr(r, "usage", None)
        usage_str = f"in={usage.input_tokens} out={usage.output_tokens}" if usage else ""
        return answers, usage_str

    async def aclose(self) -> None:
        await self.client.aclose()


def describe(answers: dict[str, Answer]) -> str:
    lines = []
    for name, a in answers.items():
        if a.noul is not None:
            lines.append(f"{name}: p={a.noul:.2f}")
        else:
            top = sorted(a.probabilities.items(), key=lambda kv: -kv[1])[:3]
            probs = ", ".join(f"{k}={v:.2f}" for k, v in top)
            lines.append(f"{name}: {a.choice} (conf {a.confidence:.2f}) [{probs}]")
    return "\n".join(lines)


async def jev_call(
    backend: JevBackend, bus: EventBus, label: str, state: Any, questions: dict[str, Question]
) -> dict[str, Answer]:
    with timer() as t:
        answers, usage = await backend.ask(state, questions)
    detail = describe(answers) + (f"\n{usage}" if usage else "")
    bus.emit("JEV", label, detail, t["ms"])
    return answers
