import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  getHealth,
  resetSession,
  streamChat,
  type AppState,
  type ChatMessage,
  type Health,
  type SystemEvent,
} from './api'

const SUGGESTIONS = [
  'set up a call with Jordan',
  'thursday at 3pm',
  'make it 4 instead',
  'yes',
  'remind me to send Jordan the pricing, high priority',
  'what are my tasks?',
  'what did Acme care about?',
]

function newSessionId() {
  return crypto.randomUUID()
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [events, setEvents] = useState<SystemEvent[]>([])
  const [appState, setAppState] = useState<AppState | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)

  const chatEnd = useRef<HTMLDivElement>(null)
  const eventsEnd = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null))
  }, [])
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ block: 'end' })
  }, [messages, busy])
  useEffect(() => {
    eventsEnd.current?.scrollIntoView({ block: 'end' })
  }, [events])

  async function send(text: string) {
    const msg = text.trim()
    if (!msg || busy) return
    setInput('')
    setBusy(true)
    setMessages((m) => [...m, { role: 'user', content: msg }])

    try {
      for await (const line of streamChat(sessionId, msg)) {
        if (line.type === 'event') {
          setEvents((e) => [...e, line.event])
        } else {
          setMessages((m) => [...m, { role: 'assistant', content: line.content }])
          setAppState(line.state)
        }
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err)
      setEvents((e) => [
        ...e,
        { source: 'ERROR', title: 'request failed', detail, ms: null, ts: new Date().toLocaleTimeString() },
      ])
      setMessages((m) => [...m, { role: 'assistant', content: 'Could not reach the server; see the event log.' }])
    } finally {
      setBusy(false)
      inputRef.current?.focus()
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    send(input)
  }

  async function clearAll() {
    await resetSession(sessionId).catch(() => {})
    setSessionId(newSessionId())
    setMessages([])
    setEvents([])
    setAppState(null)
    inputRef.current?.focus()
  }

  const pending = appState?.pending

  return (
    <div className="app">
      <header className="topbar">
        <h1>Jev chat</h1>
        {health && (
          <div className="modes">
            <span className={`pill ${health.jev_mode === 'mock' ? 'warn' : ''}`}>Jev: {health.jev_mode}</span>
            <span className={`pill ${health.llm === 'fake' ? 'warn' : ''}`}>LLM: {health.llm}</span>
          </div>
        )}
      </header>

      <main className="panes">
        <section className="pane chat" aria-label="Chat messages">
          {messages.length === 0 && (
            <div className="empty">
              <p>Try one of these:</p>
              <div className="chips">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" className="chip" onClick={() => send(s)} disabled={busy}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              {m.content}
            </div>
          ))}
          {busy && <div className="bubble assistant typing">…</div>}
          <div ref={chatEnd} />
        </section>

        <section className="pane events" aria-label="System events">
          <div className="events-head">
            <span>System events</span>
            {pending && (
              <span className="draft" title={JSON.stringify(pending.slots)}>
                draft: {pending.intent} · {pending.awaiting === 'confirm' ? 'awaiting confirm' : 'filling slots'}
              </span>
            )}
          </div>
          {events.length === 0 && <p className="muted">Jev, LLM and tool calls appear here as they happen.</p>}
          {events.map((e, i) => (
            <div key={i} className={`event src-${e.source}`}>
              <div className="event-meta">
                <span className="src">{e.source}</span>
                <span className="muted">
                  {e.ts}
                  {e.ms != null && ` · ${Math.round(e.ms)} ms`}
                </span>
              </div>
              <div className="event-title">{e.title}</div>
              {e.detail && <pre className="event-detail">{e.detail}</pre>}
            </div>
          ))}
          <div ref={eventsEnd} />
        </section>
      </main>

      <form className="composer" onSubmit={onSubmit}>
        <input
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={pending ? 'Answer, change a detail, or cancel…' : 'Type a command…'}
          disabled={busy}
          autoFocus
          aria-label="Chat input"
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
        <button type="button" className="secondary" onClick={clearAll} disabled={busy}>
          Clear
        </button>
      </form>
    </div>
  )
}
