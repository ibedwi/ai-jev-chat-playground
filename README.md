# Jev chat: React + FastAPI

The notebook experiment as a small web app, with the same layout:

```
[ chat messages ] [ system events: Jev, LLM, tools ]
[ chat input                                       ]
```

Jev routes every message and extracts details. Code decides what to do. The LLM only writes free text. Events stream to the right-hand pane live while each request runs.

```
jev-chat/
├── backend/                 FastAPI (Python 3.10+, uv)
│   ├── app/
│   │   ├── main.py          HTTP API; streams events as NDJSON
│   │   ├── router.py        Routing + slot filling (ported from the notebook)
│   │   ├── jev.py           TypeSafe SDK adapter (async)
│   │   ├── mock_jev.py      Keyword stand-in for Jev (no key needed)
│   │   ├── llm.py           Claude via the async Anthropic SDK, or a fake echo
│   │   ├── events.py        Event model + per-request event bus
│   │   └── config.py        Settings from env / .env
│   ├── tests/test_router.py
│   ├── pyproject.toml       Dependencies (uv)
│   ├── uv.lock              Pinned versions; commit this
│   └── .env.example
└── frontend/                Vite + React + TypeScript
    ├── src/App.tsx          Two panes + composer
    ├── src/api.ts           NDJSON streaming client
    ├── src/index.css        Styles (light + dark)
    └── vite.config.ts       Proxies /api to FastAPI in dev
```

---

## 1. Run the backend

The backend uses [uv](https://docs.astral.sh/uv/). Install it once with `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `brew install uv`).

```bash
cd backend
uv sync                              # creates .venv and installs from uv.lock
cp .env.example .env                 # then fill in your keys
uv run uvicorn app.main:app --reload --port 8000
```

`uv run` uses the project's `.venv` automatically, so there's nothing to activate.

Check it's up: `curl localhost:8000/api/health` should return `{"jev_mode": "real", "llm": "claude-sonnet-5"}`.

**Keys and modes**

| `.env` | Result |
|---|---|
| `TYPESAFE_API_KEY` set | Real Jev calls |
| `TYPESAFE_API_KEY` blank | `JEV_MODE=mock`: a keyword stand-in, labelled "MOCK" in the event pane |
| `ANTHROPIC_API_KEY` set | Real Claude calls |
| `ANTHROPIC_API_KEY` blank | Fake LLM that echoes, so only Jev costs money while you tune |

The mock exists so you can work on the UI offline. Never tune thresholds on it.

## 2. Run the frontend

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api/*` to `127.0.0.1:8000`, so there's no CORS setup in development.

The pills in the top-right show whether Jev and the LLM are real, mock, or fake.

## 3. Try the flows

Click a suggestion chip, or type:

1. `set up a call with Jordan`: the app asks for the day and time
2. `thursday at 3pm`: it proposes the booking and asks you to confirm
3. `make it 4 instead`: the draft updates
4. `yes`: booked
5. `remind me to send Jordan the pricing, high priority`: Jev routes, the LLM writes the title
6. `what are my tasks?`: pure code, no model call
7. `what did Acme care about?`: two Jev calls, no LLM

While a meeting draft is pending, the event pane header shows `draft: schedule_meeting · filling slots` or `awaiting confirm`.

## 4. Run the tests

```bash
cd backend
uv run pytest -q
```

`pytest` and `httpx` are in the `dev` dependency group, which `uv sync` installs by default.

The tests use a scripted Jev, so they need no keys. They cover asking back, confirm/change/cancel, dropping a draft for a new request, low-confidence fallbacks, errors becoming events, and the streaming API.

---

## How it works

### Request flow

```
React ──POST /api/chat──► FastAPI ──► Session.handle()
  ▲                          │           ├─ Jev: route command
  │   NDJSON lines           │           ├─ Jev: extract slots / classify follow-up
  └──────────────────────────┘           ├─ LLM: task title or chat
     {"type":"event",...}  (as they happen)
     {"type":"reply",...}  (last line)
```

`POST /api/chat` returns `application/x-ndjson`: one JSON object per line. Every `emit()` in the router goes onto an `asyncio.Queue`, and the streaming response forwards it right away. That's why the right-hand pane fills in while Jev and the LLM are still working. The last line is the assistant's reply plus a `state` snapshot (tasks, meetings, pending draft).

NDJSON over `fetch` was chosen over SSE (`EventSource`) because `EventSource` only supports GET. With `fetch`, the message goes in a normal POST body. `src/api.ts` reads the body stream and splits on newlines.

### Backend design choices

- **Questions are app-level types.** The router builds `ChoiceQ` / `NoulQ` and gets back normalized `Answer`s. `jev.py` is the only file that touches the TypeSafe SDK. That's what makes the mock and the scripted test double trivial to swap in.
- **Async all the way.** It uses `AsyncTypeSafeClient` and `AsyncAnthropic`, so one slow LLM call doesn't block other users.
- **One message at a time per session.** A per-session `asyncio.Lock` stops double-submits from interleaving slot-filling turns.
- **Errors become events.** An exception in a handler shows up as a red `ERROR` event with a polite reply, not a 500.
- **"Today" uses `APP_TIMEZONE`** (default `Asia/Jakarta`), so "thursday" resolves to the right date even when the server runs in UTC.

### Frontend design choices

- **No state library.** Four `useState`s cover it: messages, events, app state, and busy.
- **The input is disabled while a request runs.** This matches the per-session lock on the server.
- **Clear starts a new session ID** and tells the server to drop the old one.

---

## Where to take it next

- **Persistence.** Sessions live in memory and vanish on restart. Move `Session`'s lists to Postgres (Supabase) and keep only the pending draft in memory or Redis. Store every event too, so you can analyze Jev's confidence and latency across real conversations and tune thresholds on data.
- **Stream LLM tokens.** Use `client.messages.stream(...)` and emit `{"type":"delta"}` lines so chat replies type out.
- **Real calendar.** Replace `book_meeting()` with a Google Calendar call, and turn `CONTACTS` into the user's real contacts. Keep Jev's Noul-per-contact pattern, but only for a shortlist (for example, recent collaborators), since each contact is one question.
- **Auth.** Session IDs are random UUIDs from the browser, which is fine locally. Put real auth in front before exposing it.
- **Deploy.** Build the frontend (`npm run build`), serve `frontend/dist` from FastAPI or a CDN, and set `CORS_ORIGINS` if they're on different origins. If a reverse proxy sits in front, turn off response buffering for `/api/chat`. The response already sends `X-Accel-Buffering: no` for nginx.
