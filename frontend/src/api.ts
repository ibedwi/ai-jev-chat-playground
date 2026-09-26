// Types and client for the FastAPI backend.

export type Source = 'USER' | 'JEV' | 'LLM' | 'TOOL' | 'SYSTEM' | 'ERROR'

export interface SystemEvent {
  source: Source
  title: string
  detail: string
  ms: number | null
  ts: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AppState {
  tasks: { title: string; priority: string; has_deadline: boolean }[]
  // Seeded past Meetings also carry id, title and a Meeting Note; Meetings booked in the app do not.
  meetings: {
    attendees: string[]
    date: string
    time: string
    duration: number
    id?: string
    title?: string
    note?: string
  }[]
  draft: { intent: string; slots: Record<string, unknown>; awaiting: string } | null
}

export type StreamLine =
  | { type: 'event'; event: SystemEvent }
  | { type: 'reply'; content: string; state: AppState }

export interface Health {
  jev_mode: string
  llm: string
}

/**
 * POST a message and yield each NDJSON line as it arrives.
 * Events stream in while Jev and the LLM run; the final line is the reply.
 */
export async function* streamChat(
  userId: string,
  sessionId: string,
  message: string,
): AsyncGenerator<StreamLine> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, session_id: sessionId, message }),
  })
  if (!res.ok || !res.body) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let newline: number
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline).trim()
      buffer = buffer.slice(newline + 1)
      if (line) yield JSON.parse(line) as StreamLine
    }
  }
  if (buffer.trim()) yield JSON.parse(buffer) as StreamLine
}

export async function resetSession(userId: string, sessionId: string): Promise<void> {
  await fetch('/api/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId, session_id: sessionId }),
  })
}

export async function getHealth(): Promise<Health> {
  const res = await fetch('/api/health')
  return res.json()
}
