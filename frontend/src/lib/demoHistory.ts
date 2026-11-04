import type { AnswerResponse } from '../types'
import type { HistorySession, HistoryStore } from './api'

// Demo mode stores no history on the server, so this tab's sessionStorage keeps it.
const DEMO_HISTORY_KEY = 'demo_history'
const MAX_SESSIONS = 20

function read(): HistorySession[] {
  try {
    return JSON.parse(window.sessionStorage.getItem(DEMO_HISTORY_KEY) || '[]')
  } catch {
    return []
  }
}

function write(sessions: HistorySession[]): void {
  window.sessionStorage.setItem(DEMO_HISTORY_KEY, JSON.stringify(sessions))
}

export const demoHistory: HistoryStore = {
  list: async (limit) => read().slice(0, limit),
  remove: async (sessionId) => write(read().filter((s) => s.id !== sessionId)),
  clear: async () => {
    const count = read().length
    write([])
    return { count }
  },
}

/** Add an answer to this tab's demo history. */
export function saveDemoAnswer(answer: AnswerResponse): void {
  if (!answer.run_id) return
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
  write([session, ...read()].slice(0, MAX_SESSIONS))
}
