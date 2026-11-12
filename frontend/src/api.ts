export type Role = 'teacher' | 'admin'

export type User = {
  id: string
  username: string
  display_name: string
  role: Role
}

export type Session = { user: User; csrf_token: string }

export type Student = {
  id: string
  name: string
  school: string
  grade: 7 | 8 | 9
  learned_point_ids: string[]
  progress_revision: number
  created_at?: string
  updated_at?: string
}

export type ExamAsset = {
  id: string
  role: 'paper' | 'response' | 'reference'
  filename: string
  ordinal: number
  size?: number
}

export type Exam = {
  id: string
  student_id: string
  title: string
  exam_date: string | null
  total_score: number | null
  max_score: number | null
  class_rank: number | null
  grade_rank: number | null
  assets: ExamAsset[]
  analysis_job?: Job | null
  analysis_summary?: Record<string, unknown> | null
  status?: string
}

export type Job = {
  id: string
  kind: string
  resource_id: string
  status: 'queued' | 'running' | 'completed' | 'partial' | 'failed'
  error: string | null
  result: Record<string, unknown> | null
  attempts: number
  created_at?: string
  updated_at?: string
}

export type CurriculumPoint = {
  id: string
  name: string
  prerequisite_ids: string[]
}

export type Curriculum = { version: string; points: CurriculumPoint[] }

export type Difficulty = 'basic' | 'intermediate' | 'advanced'

export type InventoryPoint = {
  point_id: string
  name: string
  candidate_count: number
  by_difficulty: Partial<Record<Difficulty, number>>
}

export type PracticeFile = { id: string; label: string; url: string }

export type Practice = {
  id: string
  student_id: string
  status: string
  snapshot: Record<string, unknown> | null
  files: PracticeFile[]
  warnings: string[]
  feedback: Record<string, unknown> | null
  preview: Record<string, unknown> | null
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

let csrfToken = ''

export function setCsrfToken(token: string) {
  csrfToken = token
}

export function unwrap<T>(value: unknown): T {
  if (value && typeof value === 'object' && 'data' in value) {
    const wrapper = value as { id?: string; revision?: string; data: unknown }
    if (wrapper.data && typeof wrapper.data === 'object') {
      return { id: wrapper.id, revision: wrapper.revision, ...wrapper.data as object } as T
    }
    return wrapper.data as T
  }
  return value as T
}

const KNOWN_ERRORS: [RegExp, string][] = [
  [/invalid username or password/i, '账号或密码不正确'],
  [/not authenticated|authentication required|session/i, '登录已过期，请重新登录'],
  [/csrf/i, '页面已过期，请刷新后重试'],
  [/page not found/i, '找不到这一页原图'],
  [/not found/i, '要访问的内容不存在或已删除'],
  [/student progress is not set/i, '该学生还没有勾选已学考点'],
  [/has not learned every requested point prerequisite/i, '所选考点或其前置考点有未勾选为已学的'],
  [/quota_shortfall/i, '所选考点的可用题目不足'],
  [/practice bank is not configured/i, '题库尚未配置，请联系管理员'],
  [/insufficient|credit|balance/i, '额度不足，请联系管理员'],
  [/revision|conflict/i, '内容已被更新，请刷新后重试'],
]

/** Turn backend detail into teacher-readable Chinese; keep the original after a separator for diagnosis. */
export function readableError(detail: unknown, status: number): string {
  const text = typeof detail === 'string' ? detail.trim() : Array.isArray(detail) ? '' : detail == null ? '' : String(detail)
  if (status >= 500 && (!text || /<html|<!doctype/i.test(text))) return `服务暂时不可用（${status}），请稍后重试`
  if (status === 422 && !text) return '提交的内容格式不正确，请检查后重试'
  if (!text) return `请求失败（${status}），请稍后重试`
  if (/[\u4e00-\u9fff]/.test(text)) return text
  const known = KNOWN_ERRORS.find(([pattern]) => pattern.test(text))
  return known ? `${known[1]}（${text}）` : `请求未完成（${status}）：${text}`
}

/** Job failure reasons come from the worker as plain text; map them the same way as HTTP errors. */
export function readableJobError(text: string | null | undefined): string {
  const value = (text ?? '').trim()
  if (!value) return '处理失败，服务未返回原因'
  if (/[\u4e00-\u9fff]/.test(value)) return value
  const known = KNOWN_ERRORS.find(([pattern]) => pattern.test(value))
  return known ? `${known[1]}（${value}）` : `处理失败：${value}`
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = new Headers(init.headers)
  if (!(init.body instanceof FormData) && init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  let response: Response
  try {
    response = await fetch(path.startsWith('/api') ? path : `/api${path}`, {
      ...init,
      method,
      headers,
      credentials: 'include',
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('无法连接服务，请检查网络后重试', 0)
  }
  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') ?? ''
  const payload = contentType.includes('application/json') ? await response.json() : await response.text()
  if (!response.ok) {
    const raw = payload && typeof payload === 'object' && 'detail' in payload
      ? (payload as { detail: unknown }).detail
      : null
    throw new ApiError(readableError(raw, response.status), response.status)
  }
  return payload as T
}

export async function apiBlob(path: string): Promise<Blob> {
  const response = await fetch(path.startsWith('/api') ? path : `/api${path}`, {
    credentials: 'include',
    headers: csrfToken ? { 'X-CSRF-Token': csrfToken } : undefined,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new ApiError(readableError(payload?.detail, response.status), response.status)
  }
  return response.blob()
}

export const getCurrentSession = () => api<Session>('/auth/me')

export async function createJobForExam(examId: string, provider: 'deepseek' | 'codex') {
  return api<Job>(`/exams/${encodeURIComponent(examId)}/analyze`, {
    method: 'POST',
    body: JSON.stringify({ provider }),
  })
}

export async function pollJob(
  jobId: string,
  onUpdate: (job: Job) => void,
  signal?: AbortSignal,
): Promise<Job> {
  while (!signal?.aborted) {
    const job = unwrap<Job>(await api<Job>(`/jobs/${encodeURIComponent(jobId)}`))
    onUpdate(job)
    if (['completed', 'partial', 'failed'].includes(job.status)) return job
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(resolve, 1300)
      signal?.addEventListener('abort', () => {
        window.clearTimeout(timeout)
        reject(new DOMException('Aborted', 'AbortError'))
      }, { once: true })
    })
  }
  throw new DOMException('Aborted', 'AbortError')
}

export function safeApiHref(value: string): string | null {
  if (!value.startsWith('/api/')) return null
  try {
    const url = new URL(value, window.location.origin)
    return url.origin === window.location.origin ? url.pathname + url.search : null
  } catch {
    return null
  }
}

export function formatBytes(size?: number) {
  if (!size || size < 0) return '—'
  if (size < 1024 * 1024) return `${Math.max(1, Math.round(size / 1024))} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

export function todayIso() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}
