import type { AnswerResponse, ProgressUpdate } from '../types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

const POLL_INTERVAL_MS = 1500

const CLIENT_ID_KEY = 'client_id'

/**
 * Stable anonymous identifier for this browser, used to scope Q&A history to
 * the current user (the app has no auth). Generated once and persisted in
 * localStorage; resets if the user clears storage or switches devices.
 */
export function getClientId(): string {
  // SSR / non-browser: no stable storage, so return a throwaway id.
  if (typeof window === 'undefined') return 'ssr'

  let clientId = window.localStorage.getItem(CLIENT_ID_KEY)
  if (!clientId) {
    clientId = crypto.randomUUID()
    window.localStorage.setItem(CLIENT_ID_KEY, clientId)
  }
  return clientId
}

/** Headers that identify this browser to the history routes. */
function clientHeaders(): Record<string, string> {
  return { 'X-Client-Id': getClientId() }
}

// --- Demo mode -------------------------------------------------------------

export interface DemoStatus {
  demo: boolean
  remaining?: number | null
}

/** Thrown when the API returns 429 because a demo limit is used up. */
export class DemoLimitError extends Error {
  deployUrl: string

  constructor(message: string, deployUrl: string) {
    super(message)
    this.name = 'DemoLimitError'
    this.deployUrl = deployUrl
  }
}

// When true, history lives in sessionStorage. The server stores none in demo mode.
let demoMode = false
const DEMO_HISTORY_KEY = 'demo_history'

export async function getDemoStatus(): Promise<DemoStatus> {
  const response = await fetch(`${API_BASE_URL}/demo/status`)
  if (!response.ok) {
    throw new Error(`Failed to fetch demo status: ${response.statusText}`)
  }
  const status = (await response.json()) as DemoStatus
  demoMode = status.demo
  return status
}

function readDemoHistory(): HistorySession[] {
  try {
    return JSON.parse(window.sessionStorage.getItem(DEMO_HISTORY_KEY) || '[]')
  } catch {
    return []
  }
}

function writeDemoHistory(sessions: HistorySession[]): void {
  window.sessionStorage.setItem(DEMO_HISTORY_KEY, JSON.stringify(sessions))
}

/** In demo mode, keep an answer in this tab's history. Does nothing otherwise. */
export function saveDemoAnswer(answer: AnswerResponse): void {
  if (!demoMode || !answer.run_id) return
  const session: HistorySession = {
    id: answer.run_id,
    question: answer.question,
    answer: answer.answer,
    sources: answer.sources,
    claims: answer.claims,
    evaluations: answer.evaluations,
    quality_score: answer.quality_score,
    total_cost: answer.total_cost,
    total_duration_ms: answer.total_duration_ms,
    created_at: new Date().toISOString(),
    stages: answer.stages,
    run_id: answer.run_id,
    progress_token: answer.progress_token,
  }
  writeDemoHistory([session, ...readDemoHistory()].slice(0, 20))
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true }
    )
  })
}

/**
 * Ask a question via the Workflows-backed pipeline.
 *
 * The gateway triggers a Render Workflow run and we poll it for the result.
 * The pipeline runs out-of-band in the Workflow service, so there is no
 * token-by-token answer streaming. The orchestrator records real per-stage
 * progress under a token, which we poll alongside the run status and replay
 * through `onProgress` as each new stage lands.
 */
export async function askQuestion(
  question: string,
  onProgress?: (update: ProgressUpdate) => void,
  signal?: AbortSignal,
  onStart?: (remaining: number | null | undefined) => void
): Promise<AnswerResponse> {
  // 1. Trigger the workflow run.
  const startResponse = await fetch(`${API_BASE_URL}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, client_id: getClientId() }),
    signal,
  })

  if (startResponse.status === 429) {
    const body = await startResponse.json().catch(() => null)
    throw new DemoLimitError(
      body?.detail?.message || 'Demo limit reached. Deploy your own copy to keep going.',
      body?.detail?.deploy_url || 'https://render.com/deploy?repo=https://github.com/render-examples/pydantic-agents-workflows'
    )
  }

  if (!startResponse.ok) {
    throw new Error(`API error: ${startResponse.statusText}`)
  }

  const { run_id: runId, progress_token: progressToken, remaining } = (await startResponse.json()) as {
    run_id: string
    progress_token: string
    remaining?: number | null
  }
  onStart?.(remaining)

  const progressQuery = `?progress_token=${encodeURIComponent(progressToken)}`

  // 2. Poll for completion. The server returns the cumulative list of stage
  //    updates each time; replay only the ones we haven't surfaced yet.
  let seen = 0
  const replayNew = (updates?: ProgressUpdate[]) => {
    if (!updates || !onProgress) return
    for (; seen < updates.length; seen++) {
      onProgress(updates[seen])
    }
  }

  while (true) {
    await delay(POLL_INTERVAL_MS, signal)

    const pollResponse = await fetch(`${API_BASE_URL}/ask/${runId}${progressQuery}`, { signal })
    if (!pollResponse.ok) {
      throw new Error(`API error: ${pollResponse.statusText}`)
    }

    const data = (await pollResponse.json()) as {
      status: 'running' | 'done' | 'failed'
      result?: AnswerResponse
      error?: string
      updates?: ProgressUpdate[]
    }

    replayNew(data.updates)

    if (data.status === 'done' && data.result) {
      return { ...data.result, run_id: runId, progress_token: progressToken }
    }

    if (data.status === 'failed') {
      throw new Error(data.error || 'Pipeline run failed')
    }
  }
}

export async function checkHealth(): Promise<{ status: string; database_connected: boolean }> {
  const response = await fetch(`${API_BASE_URL}/health`)
  
  if (!response.ok) {
    throw new Error(`Health check failed: ${response.statusText}`)
  }

  return response.json()
}

export interface HistorySession {
  id: string
  question: string
  answer: string
  sources: any[]
  claims: any[]
  evaluations: any[]
  quality_score: number
  total_cost: number
  total_duration_ms: number
  created_at: string
  stages?: any[]  // Pipeline stages (optional for backwards compatibility)
  trace_id?: string  // Logfire trace ID (optional)
  run_id?: string  // Demo mode: run that produced the answer
  progress_token?: string  // Demo mode: token that opens the run's logs
}

export async function getHistory(limit: number = 20): Promise<HistorySession[]> {
  if (demoMode) return readDemoHistory().slice(0, limit)

  const response = await fetch(`${API_BASE_URL}/history?limit=${limit}`, {
    headers: clientHeaders(),
  })

  if (!response.ok) {
    throw new Error(`Failed to fetch history: ${response.statusText}`)
  }

  const data = await response.json()
  return data.sessions
}

export async function getSession(sessionId: string): Promise<HistorySession> {
  const response = await fetch(`${API_BASE_URL}/history/${sessionId}`, {
    headers: clientHeaders(),
  })

  if (!response.ok) {
    throw new Error(`Failed to fetch session: ${response.statusText}`)
  }

  return response.json()
}

export async function deleteSession(sessionId: string): Promise<void> {
  if (demoMode) {
    writeDemoHistory(readDemoHistory().filter((s) => s.id !== sessionId))
    return
  }

  const response = await fetch(`${API_BASE_URL}/history/${sessionId}`, {
    method: 'DELETE',
    headers: clientHeaders(),
  })

  if (!response.ok) {
    throw new Error(`Failed to delete session: ${response.statusText}`)
  }
}

export async function clearAllHistory(): Promise<{ count: number }> {
  if (demoMode) {
    const count = readDemoHistory().length
    writeDemoHistory([])
    return { count }
  }

  const response = await fetch(`${API_BASE_URL}/history`, {
    method: 'DELETE',
    headers: clientHeaders(),
  })

  if (!response.ok) {
    throw new Error(`Failed to clear history: ${response.statusText}`)
  }

  return response.json()
}

// Matches the row shape returned by the backend Logfire query
// (see backend/api/logs.py). `level` is a numeric OTel severity, and
// `attributes` can arrive as a JSON string that the renderer parses.
export interface LogfireLog {
  start_timestamp: string
  message: string
  level: number
  span_name: string
  span_id: string
  parent_span_id: string | null
  attributes: Record<string, any> | string
  service_name: string
  trace_id: string
}

export interface SessionLogsResponse {
  trace_id: string
  logs: LogfireLog[]
  record_count: number
}

async function parseLogsResponse(response: Response): Promise<SessionLogsResponse> {
  if (!response.ok) {
    if (response.status === 404) {
      const error = await response.json().catch(() => null)
      throw new Error(error?.detail || 'Logs not found')
    }
    if (response.status === 501) {
      throw new Error('Logfire integration not configured')
    }
    throw new Error('Failed to fetch logs')
  }

  return response.json()
}

export async function getSessionLogs(sessionId: string): Promise<SessionLogsResponse> {
  const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/logs`, {
    headers: clientHeaders(),
  })
  return parseLogsResponse(response)
}

/** Fetch logs for a run this browser started. Works in demo mode. */
export async function getRunLogs(runId: string, progressToken: string): Promise<SessionLogsResponse> {
  const response = await fetch(
    `${API_BASE_URL}/ask/${runId}/logs?progress_token=${encodeURIComponent(progressToken)}`
  )
  return parseLogsResponse(response)
}
