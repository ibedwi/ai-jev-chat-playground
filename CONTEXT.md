# Jev Chat

A chat assistant where Jev routes each message and extracts details, code decides what to do, and an LLM writes only free text. It exists to test Jev as the decision layer of a conversational assistant.

## Language

### Conversation

**Session**:
One continuous conversation between the user and the assistant. Its messages and its open Draft belong to it; ending a Session discards both.
_Avoid_: Chat, conversation, thread

**Intent**:
The one thing the user wants done with a message, such as creating a Task, scheduling a Meeting or just chatting.
_Avoid_: Command, route, action

**Draft**:
An unfinished request the assistant is completing with the user over several turns, before anything is created.
_Avoid_: Pending, pending command, slot state

**Meeting Draft**:
A Draft of a Meeting: its details are gathered turn by turn until the user confirms, changes or cancels it.
_Avoid_: Pending meeting, meeting request

**Unclear Message**:
A message whose Intent Jev cannot tell with enough Confidence. It is not a chat: the user may well want something done.
_Avoid_: Fallback, low-confidence message, chat

**Slot**:
One detail a Draft needs before it can be confirmed, such as a Meeting's Attendees, day, start time or duration.
_Avoid_: Field, parameter, entity

**Required Slot**:
A Slot the user must supply; any other Slot falls back to a default when not given.
_Avoid_: Mandatory field

**Reply**:
The user's message while a Draft is open. It either answers or changes the Draft, confirms it, cancels it, or is a new request that abandons it.
_Avoid_: Follow-up, follow-up turn

### Agenda

**Agenda**:
All of the user's Tasks and Meetings together, past and upcoming. It belongs to the user and outlives any Session.
_Avoid_: Task list, schedule

**Meeting**:
A gathering between the user and one or more other people at a set day, time and duration, whether the assistant booked it or it came from elsewhere.
_Avoid_: Event, calendar event, appointment, call

**Attendee**:
Any person taking part in a Meeting, including the user. An Attendee need not be a Contact.
_Avoid_: Invitee, participant, guest

**Meeting Note**:
A written record of what was said or decided in a Meeting; it always belongs to exactly one Meeting.
_Avoid_: Minutes, memo, note (on its own)

**Task**:
Something the user wants to remember to do, with a title and a Priority.
_Avoid_: To-do, reminder, follow-up

**Priority**:
How urgent the user says a Task is: Low, Normal or High, where Normal means the user did not say otherwise. Only Tasks have a Priority.
_Avoid_: Urgency, importance, severity

### People

**Contact**:
A person the assistant already knows about, inside or outside the user's team.
_Avoid_: Person, user, customer (a Contact may be a customer, but the term is Contact)

### Understanding

**Jev**:
The service that reads each message and answers structured questions about it: which Intent it has and which Slots it fills. Jev decides; it never writes text for the user.
_Avoid_: Classifier, model, router

**Choice Question**:
A question to Jev that picks exactly one option from a fixed set, such as the Intent or a Meeting's day.
_Avoid_: Classification, enum question

**Noul Question**:
A yes/no question to Jev, answered with the Confidence that the answer is yes, such as whether a given person should attend.
_Avoid_: Boolean question, flag

**Confidence**:
How sure Jev is of an answer: of the chosen option for a Choice Question, or that the answer is yes for a Noul Question. A low Confidence means the assistant treats the answer as unknown.
_Avoid_: Score, probability, certainty

**LLM**:
The language model that writes free text for the user, such as a Task's title or a chat reply; it never decides what the assistant does.
_Avoid_: AI, Claude, model

### Observability

**Event**:
A record of one thing that happened inside the app while handling a message (the user's message, a Jev call, an LLM call, a tool action, a system decision or an error), shown in the event pane.
_Avoid_: Log, system message; never use for a Meeting
