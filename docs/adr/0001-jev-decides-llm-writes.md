---
status: accepted
---

# Jev decides, the LLM only writes free text

Every decision about what the assistant does is made by code, based on Jev's answers to Choice and Noul Questions: which Intent a message has, which Slots it fills, and how a Reply relates to an open Draft. The LLM is used only to write text the user reads, such as a Task's title or a chat reply, and never chooses an action.

We chose this because the project exists to test Jev as the decision layer of a conversational assistant, and because every Jev answer carries a Confidence. That lets code treat a doubtful answer as unknown and ask the user again, instead of acting on a guess.

## Considered Options

- **An LLM with tool calling that decides and writes.** This is the usual design. It was rejected because the LLM's choices come with no Confidence, so the assistant can't tell when to ask again rather than act, and because it would take Jev out of the decision role this project exists to test.

## Consequences

- Adding a feature means adding an Intent and its Questions to Jev, plus code to act on the answers. It does not mean adding a prompt or a tool for the LLM.
- Confidence thresholds are what tune the behavior. Tune them on real Jev answers, never on the mock.
