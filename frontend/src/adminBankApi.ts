import { api, Job } from './api'

export type BankPoint = { id: string; name: string }
export type BankQuestionSummary = {
  id: string
  number: string
  title: string
  status: 'eligible' | 'blocked' | 'disabled' | string
  block_reasons: string[]
  point_ids: string[]
  prerequisite_ids: string[]
  difficulty_score: number | null
  adjusted: boolean
  disabled: boolean
}
export type BankSummary = {
  revision: number
  configured: boolean
  question_count: number
  eligible_count: number
  blocked_count: number
  disabled_count: number
  source_count: number
  points: BankPoint[]
  active_version: string | number | null
  history: Record<string, unknown>[]
  imports: Record<string, unknown>[]
}
export type BankQuestionDetail = BankQuestionSummary & {
  revision: number
  question_html: string
  answer_html: string
  analysis_html: string
  ai_assessment: Record<string, unknown> | null
  adjustment: Record<string, unknown> | null
  history: Record<string, unknown>[]
}
export type BankQuestionPage = { revision: number; total: number; items: BankQuestionSummary[] }
export type BankImportJob = Job & { kind: 'bank_import'; resource_kind?: 'bank_import' }

export function getBankSummary() {
  return api<BankSummary>('/admin/bank')
}

export function getBankQuestions(input: {
  q: string; status: 'all' | 'eligible' | 'blocked' | 'disabled'; point_id: string; offset: number; limit: number
}) {
  const params = new URLSearchParams({ q: input.q, status: input.status, offset: String(input.offset), limit: String(input.limit) })
  if (input.point_id) params.set('point_id', input.point_id)
  return api<BankQuestionPage>(`/admin/bank/questions?${params.toString()}`)
}

export function getBankQuestion(questionId: string) {
  return api<BankQuestionDetail>(`/admin/bank/questions/${encodeURIComponent(questionId)}`)
}

export function patchBankQuestion(questionId: string, input: {
  expected_revision: number; enabled: boolean; point_ids: string[]; prerequisite_ids: string[];
  difficulty_score: number; reason: string; note: string
}) {
  return api<BankQuestionDetail>(`/admin/bank/questions/${encodeURIComponent(questionId)}`, {
    method: 'PATCH', body: JSON.stringify(input),
  })
}

export function checkBank(input: { expected_revision: number }) {
  return api<BankSummary & { checks?: Record<string, unknown>[] }>('/admin/bank/check', {
    method: 'POST', body: JSON.stringify(input),
  })
}

export function importBank(input: { expected_revision: number; reason: string; files: File[] }) {
  const body = new FormData()
  body.set('expected_revision', String(input.expected_revision))
  body.set('reason', input.reason)
  for (const file of input.files) body.append('files', file, file.name)
  return api<BankImportJob>('/admin/bank/imports', { method: 'POST', body })
}

export function restoreBank(input: { expected_revision: number; version: string; reason: string }) {
  return api<BankSummary>('/admin/bank/restore', { method: 'POST', body: JSON.stringify(input) })
}
