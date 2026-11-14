import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AdminModelBilling from './AdminModelBilling'
import AdminBank from './AdminBank'
import { SanitizedPreview } from './SafePreview'
import UsageGuide from './UsageGuide'
import { MathBlock, MathText } from './MathText'
import { setNavigationBlocker, useBackToClose, useHashRoute, type Route, type View } from './router'
import { prepareUpload } from './imageUpload'
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BookOpenCheck,
  Check,
  CheckCircle2,
  Camera,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Images,
  CircleHelp,
  ClipboardList,
  Download,
  FileCheck2,
  FileSearch2,
  FilePlus2,
  FileText,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  LogOut,
  Plus,
  RotateCcw,
  Save,
  Search,
  Settings2,
  ShieldCheck,
  UserRound,
  Users,
  X,
} from 'lucide-react'
import {
  ApiError,
  api,
  apiBlob,
  createJobForExam,
  getCurrentSession,
  Job,
  pollJob,
  newClientId,
  readableJobError,
  safeApiHref,
  setCsrfToken,
  Student,
  unwrap,
  todayIso,
  User,
  Curriculum,
  Exam,
  ExamAsset,
  InventoryPoint,
  Difficulty,
  Practice,
} from './api'

type Notice = { kind: 'success' | 'error' | 'info'; text: string }
type ExamAnalysisResponse = {
  analysis: Record<string, any>
  teacher_report: Record<string, any> | null
  pages?: { page_id: string; role: string; parent_page_id?: string | null; parent_bbox?: number[] | null; url?: string }[]
}
type Prompt = {
  key: string
  label: string
  description: string
  active_revision: number | string
  body: string
}
type PromptRevision = { revision: number | string; body: string; created_at: string; created_by: string }

const DIFFICULTIES: { key: Difficulty; label: string; color: string }[] = [
  { key: 'basic', label: '基础', color: 'mint' },
  { key: 'intermediate', label: '提高', color: 'amber' },
  { key: 'advanced', label: '挑战', color: 'rose' },
]

// Same entries, names and order on desktop (sidebar) and phone (bottom tab bar); see docs/ui-guidelines.md §8.
const NAV_ITEMS: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'home', label: '工作台', icon: LayoutDashboard },
  { id: 'exam', label: '考试分析', icon: ClipboardList },
  { id: 'students', label: '学生档案', icon: Users },
  { id: 'practice', label: '组卷', icon: BookOpenCheck },
]
const TAB_ITEMS: { id: View; label: string; icon: typeof LayoutDashboard }[] = [...NAV_ITEMS, { id: 'me', label: '我的', icon: UserRound }]

function arrayOf<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : []
}

function objectOf(value: unknown): Record<string, any> {
  return value && typeof value === 'object' ? value as Record<string, any> : {}
}

function messageFor(error: unknown) {
  if (error instanceof ApiError) return error.message
  return error instanceof Error ? error.message : '操作未完成，请稍后重试'
}

function statusText(status?: string) {
  return ({
    queued: '排队中', running: '处理中', completed: '已完成', partial: '部分完成', failed: '失败',
    processing: '处理中', ready: '可用', draft: '草稿', analyzed: '已分析',
  } as Record<string, string>)[status ?? ''] ?? status ?? '待处理'
}

function dateLabel(value?: string | null) {
  if (!value) return '未填写日期'
  const parsed = new Date(`${value.slice(0, 10)}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'short', day: 'numeric' }).format(parsed)
}

function gradeLabel(grade: number) {
  return ({ 7: '初一', 8: '初二', 9: '初三' } as Record<number, string>)[grade] ?? `${grade} 年级`
}

function normalizeStudent(value: unknown): Student {
  const row = unwrap<Record<string, any>>(value)
  return {
    ...row,
    id: String(row.id ?? ''),
    name: String(row.name ?? ''),
    school: String(row.school ?? ''),
    grade: Number(row.grade ?? 7) as Student['grade'],
    learned_point_ids: arrayOf<string>(row.learned_point_ids),
    progress_revision: Number(row.progress_revision ?? row.revision ?? 0),
  }
}

function normalizeExam(value: unknown): Exam {
  const row = unwrap<Record<string, any>>(value)
  return {
    ...row,
    id: String(row.id ?? ''),
    student_id: String(row.student_id ?? ''),
    title: String(row.title ?? ''),
    exam_date: row.exam_date ?? null,
    total_score: row.total_score ?? null,
    max_score: row.max_score ?? null,
    class_rank: row.class_rank ?? null,
    grade_rank: row.grade_rank ?? null,
    assets: arrayOf<ExamAsset>(row.assets).sort((a, b) => a.ordinal - b.ordinal),
  }
}

function Button({
  children,
  variant = 'primary',
  size = 'md',
  icon,
  disabled,
  type = 'button',
  onClick,
  className = '',
}: {
  children: React.ReactNode
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger' | 'white'
  size?: 'sm' | 'md'
  icon?: React.ReactNode
  disabled?: boolean
  type?: 'button' | 'submit'
  onClick?: () => void
  className?: string
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`button button-${variant} button-${size} ${className}`}
    >
      {icon}{children}
    </button>
  )
}

function Loading({ label = '正在读取工作台…' }: { label?: string }) {
  return <div className="loading-state"><LoaderCircle className="spin" size={22} /><span>{label}</span></div>
}

function EmptyState({ icon, title, detail, action }: {
  icon: React.ReactNode; title: string; detail: string; action?: React.ReactNode
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3><p>{detail}</p>{action}
    </div>
  )
}

function ModalFrame({ className, label, labelledBy, onClose, children }: {
  className: string; label?: string; labelledBy?: string; onClose: () => void; children: React.ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useBackToClose(() => closeRef.current())
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const dialog = ref.current
    dialog?.focus()
    const focusables = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href]') ?? [])
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return }
      if (event.key !== 'Tab') return
      const items = focusables()
      if (!items.length) { event.preventDefault(); return }
      const first = items[0]; const last = items[items.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); previous?.focus?.() }
  }, [])
  return <div className="modal-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeRef.current() }}>
    <div ref={ref} tabIndex={-1} className={className} role="dialog" aria-modal="true" aria-label={label} aria-labelledby={labelledBy}>{children}</div>
  </div>
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return <label className="field"><span className="field-label">{label}</span>{children}{hint && <small>{hint}</small>}</label>
}

function LoginView({ onLogin, busy, error }: { onLogin: (username: string, password: string) => void; busy: boolean; error: string }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  return (
    <main className="login-page">
      <section className="login-panel">
        <div className="login-brand brand"><span className="brand-mark">题</span><span>Teassistant<small>初中数学考试分析与组卷</small></span></div>
        <div className="login-card">
          <h2>登录</h2>
          <p className="muted">使用管理员分配的教师账号</p>
          <form onSubmit={(event) => { event.preventDefault(); onLogin(username.trim(), password) }}>
            <Field label="账号">
              <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="请输入账号" required />
            </Field>
            <Field label="密码">
              <input autoComplete="current-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="请输入密码" required />
            </Field>
            {error && <div className="inline-error">{error}</div>}
            <Button type="submit" disabled={busy || !username || !password} className="full-width" icon={busy ? <LoaderCircle className="spin" size={17} /> : undefined}>
              {busy ? '登录中…' : '登录'}
            </Button>
          </form>
          <p className="login-note">没有账号请联系管理员开通。</p>
        </div>
        <p className="login-legal">学生资料仅本人账号可见。在共享电脑上用完请退出登录。</p>
      </section>
    </main>
  )
}

function App() {
  const [session, setSession] = useState<{ user: User; csrf_token: string } | null>(null)
  const [authLoading, setAuthLoading] = useState(true)
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginError, setLoginError] = useState('')
  const { route, go, back } = useHashRoute()
  const isPhone = useIsPhone()
  const view = route.view
  const [usageGuideOpen, setUsageGuideOpen] = useState(false)
  const usageGuideOpener = useRef<HTMLElement | null>(null)
  const [students, setStudents] = useState<Student[]>([])
  const [curriculum, setCurriculum] = useState<Curriculum | null>(null)
  const [selectedStudentId, setSelectedStudentId] = useState('')
  const [exams, setExams] = useState<Exam[]>([])
  const [selectedExamId, setSelectedExamId] = useState('')
  const [practice, setPractice] = useState<Practice | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [bootLoading, setBootLoading] = useState(false)
  const [studentsError, setStudentsError] = useState('')
  const [bootSlow, setBootSlow] = useState(false)
  const [examsLoading, setExamsLoading] = useState(false)
  // Student whose exam list is currently loaded; drafts and checks wait until the list belongs to the selected student.
  const [examsFor, setExamsFor] = useState('')
  const [examsError, setExamsError] = useState('')
  const [examReload, setExamReload] = useState(0)
  // Upload progress lives here so it survives switching tabs while photos are still uploading.
  const [uploadQueue, setUploadQueue] = useState<UploadItem[]>([])
  // A view with unsaved input registers a warning; navigation asks before discarding it.
  const leaveWarning = useRef<string | null>(null)
  const setLeaveWarning = useCallback((message: string | null) => { leaveWarning.current = message }, [])
  // The phone/browser back button asks too; choosing 取消 keeps the teacher on the form with input intact.
  useEffect(() => {
    setNavigationBlocker(() => {
      const message = leaveWarning.current
      if (!message) return true
      if (!window.confirm(message)) return false
      leaveWarning.current = null
      return true
    })
    return () => setNavigationBlocker(null)
  }, [])

  const notify = useCallback((kind: Notice['kind'], text: string) => {
    setNotice({ kind, text })
    window.setTimeout(() => setNotice((current) => current?.text === text ? null : current), 4800)
  }, [])
  const notifyError = useCallback((text: string) => notify('error', text), [notify])

  const loadWorkspace = useCallback(async () => {
    setBootLoading(true)
    setBootSlow(false)
    setStudentsError('')
    const slowTimer = window.setTimeout(() => setBootSlow(true), 15000)
    const [studentResult, curriculumResult] = await Promise.allSettled([
      api<unknown[]>('/students'),
      api<Curriculum>('/curriculum'),
    ])
    if (studentResult.status === 'fulfilled') {
      const normalized = arrayOf<unknown>(studentResult.value).map(normalizeStudent)
      setStudents(normalized)
      setSelectedStudentId((current) => current && normalized.some((row) => row.id === current)
        ? current : normalized[0]?.id ?? '')
    } else {
      setStudentsError(messageFor(studentResult.reason))
    }
    if (curriculumResult.status === 'fulfilled') setCurriculum(curriculumResult.value)
    window.clearTimeout(slowTimer)
    setBootLoading(false)
  }, [])

  useEffect(() => {
    let active = true
    getCurrentSession().then((value) => {
      if (!active) return
      setCsrfToken(value.csrf_token)
      setSession(value)
    }).catch((error: unknown) => {
      if (error instanceof ApiError && error.status === 401) return
      if (active) setLoginError(`连接工作台失败：${messageFor(error)}`)
    }).finally(() => { if (active) setAuthLoading(false) })
    return () => { active = false }
  }, [])

  useEffect(() => { if (session) void loadWorkspace() }, [session, loadWorkspace])

  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (leaveWarning.current || uploadQueue.some((item) => item.status === 'uploading' || item.status === 'waiting')) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [uploadQueue])

  // The URL is the source of truth for where the teacher is; mirror it into the current selection.
  useEffect(() => {
    if (route.s) setSelectedStudentId(route.s)
    if (route.view === 'students' && route.item && route.item !== 'new') setSelectedStudentId(route.item)
  }, [route])

  useEffect(() => {
    if (!session || !selectedStudentId) { setExams([]); setSelectedExamId(''); return }
    let active = true
    setExamsLoading(true)
    setExamsError('')
    setExams([])
    api<unknown[]>(`/students/${encodeURIComponent(selectedStudentId)}/exams`)
      .then((rows) => {
        if (!active) return
        const normalized = arrayOf<unknown>(rows).map(normalizeExam)
        setExams(normalized)
        setExamsFor(selectedStudentId)
        setSelectedExamId((current) => current && normalized.some((exam) => exam.id === current)
          ? current : normalized[0]?.id ?? '')
      })
      .catch((error: unknown) => { if (active) setExamsError(messageFor(error)) })
      .finally(() => { if (active) setExamsLoading(false) })
    return () => { active = false }
  }, [session, selectedStudentId, examReload])

  const currentStudentRef = useRef('')
  currentStudentRef.current = selectedStudentId
  // Background uploads may finish after the teacher switched student; only refresh the list that is on screen.
  const refreshExams = useCallback(async (studentId = currentStudentRef.current) => {
    if (!studentId) return [] as Exam[]
    const rows = await api<unknown[]>(`/students/${encodeURIComponent(studentId)}/exams`)
    const normalized = arrayOf<unknown>(rows).map(normalizeExam)
    if (studentId === currentStudentRef.current) setExams(normalized)
    return normalized
  }, [])

  const login = async (username: string, password: string) => {
    setLoginBusy(true)
    setLoginError('')
    try {
      const value = await api<{ user: User; csrf_token: string }>('/auth/login', {
        method: 'POST', body: JSON.stringify({ username, password }),
      })
      setCsrfToken(value.csrf_token)
      setSession(value)
      go({ view: 'home' }, { replace: true })
      setUsageGuideOpen(false)
    } catch (error) { setLoginError(messageFor(error)) }
    finally { setLoginBusy(false) }
  }

  const logout = async () => {
    try { await api<void>('/auth/logout', { method: 'POST' }) }
    catch (error) { notify('error', `退出失败：${messageFor(error)}`); return }
    setCsrfToken('')
    setSession(null)
    setUsageGuideOpen(false)
    setStudents([])
    setExams([])
    setSelectedStudentId('')
    setSelectedExamId('')
    setPractice(null)
    go({ view: 'home' }, { replace: true })
  }

  if (authLoading) return <div className="boot-screen"><Loading /></div>
  if (!session) return <LoginView onLogin={login} busy={loginBusy} error={loginError} />

  const selectedStudent = students.find((student) => student.id === selectedStudentId) ?? null
  const studentParam = selectedStudentId || undefined
  // Detail screens read the id straight from the URL. Never fall back to another record: an unknown id is “找不到”.
  const routeId = route.item && route.item !== 'new' && route.item !== 'result' ? route.item : ''
  const routeExam = view === 'exam' && routeId ? exams.find((exam) => exam.id === routeId) ?? null : null
  const examNotFound = view === 'exam' && Boolean(routeId) && !routeExam && !examsLoading && !bootLoading
  const selectedExam = view === 'exam' && routeId ? routeExam : isPhone ? null : exams.find((exam) => exam.id === selectedExamId) ?? null
  const routeStudent = view === 'students' && routeId ? students.find((student) => student.id === routeId) ?? null : null
  const studentNotFound = view === 'students' && Boolean(routeId) && !routeStudent && !bootLoading
  const editorStudent = view === 'students' && routeId ? routeStudent : isPhone ? null : selectedStudent

  // Uploads keep running in the background (queue lives in App), so only unsaved form input needs a confirmation.
  const confirmLeave = () => {
    const message = leaveWarning.current
    if (!message) return true
    if (!window.confirm(message)) return false
    leaveWarning.current = null
    return true
  }
  // Tabs behave like an app: switching tabs does not stack history; back from any tab root returns to 工作台.
  const navigate = (next: View) => {
    if (!confirmLeave()) return
    const target = { view: next, s: next === 'me' || next === 'admin' ? undefined : studentParam }
    go(target, { replace: view !== 'home' && !route.item })
  }
  const goGuarded: typeof go = (next, options) => { if (confirmLeave()) go(next, options) }
  const backGuarded = (fallback: Route) => { if (confirmLeave()) back(fallback) }
  // Switching student from a selector stays on the same screen and does not add a history step.
  const switchStudent = (id: string) => {
    setSelectedStudentId(id)
    go({ view, s: id || undefined }, { replace: true })
  }
  const openUsageGuide = () => { usageGuideOpener.current = document.activeElement as HTMLElement | null; setUsageGuideOpen(true) }
  const closeUsageGuide = () => {
    setUsageGuideOpen(false)
    window.requestAnimationFrame(() => usageGuideOpener.current?.focus?.())
  }

  const detail = Boolean(route.item)
  const mobileTitle = view === 'exam'
    ? route.item === 'new' ? '新建考试' : route.item ? (selectedExam?.title ?? '考试详情') : '考试分析'
    : view === 'students'
      ? route.item === 'new' ? '新增学生' : route.item ? (routeStudent?.name ?? '学生详情') : '学生档案'
      : view === 'practice' ? (route.item === 'result' ? '练习卷' : '组卷')
        : view === 'me' ? '我的' : view === 'admin' ? '管理设置' : '工作台'
  const showBack = detail || view === 'admin'
  const backTarget: Route = view === 'admin' ? { view: 'me' } : { view, s: route.s ?? studentParam }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><div className="brand-mark">题</div><div><b>Teassistant</b><small>教师工作台</small></div></div>
        <p className="workspace-tag">初中数学（湖南）</p>
        <nav className="main-nav" aria-label="主导航">
          {TAB_ITEMS.map(({ id, label, icon: Icon }) => (
            <button className={`nav-item ${view === id ? 'active' : ''}`} key={id} onClick={() => navigate(id)} aria-current={view === id ? 'page' : undefined}>
              <Icon size={18} strokeWidth={1.8} /><span>{label}</span>
            </button>
          ))}
          {session.user.role === 'admin' && <>
            <span className="nav-divider" />
            <button className={`nav-item ${view === 'admin' ? 'active' : ''}`} onClick={() => navigate('admin')}>
              <Settings2 size={18} strokeWidth={1.8} /><span>管理设置</span>
            </button>
          </>}
        </nav>
        <div className="sidebar-bottom">
          <button type="button" className="help-link" aria-haspopup="dialog" aria-controls="usage-guide" aria-expanded={usageGuideOpen} onClick={openUsageGuide}><CircleHelp size={16} />了解使用方式</button>
          <button className="account-row" onClick={() => navigate('me')} title="我的：账号、使用方式、退出登录">
            <span className="avatar avatar-sm">{session.user.display_name.slice(0, 1) || '师'}</span>
            <span className="account-copy"><b>{session.user.display_name}</b><small>{session.user.role === 'admin' ? '管理员' : '任课老师'}</small></span>
            <ChevronRight size={16} />
          </button>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div className="topbar-right">
            {selectedStudent && <span className="current-student">当前学生：<b>{selectedStudent.name}</b></span>}
            <span className="today-label">{new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date())}</span>
          </div>
        </header>
        <header className="mobile-header">
          {showBack ? <button type="button" className="mobile-back" onClick={() => backGuarded(backTarget)}><ChevronLeft size={22} />返回</button> : <span className="mobile-header-spacer" />}
          <h1>{mobileTitle}</h1>
          <span className="mobile-header-spacer" />
        </header>
        <div className={`page-wrap view-${view} ${detail ? 'is-detail' : 'is-list'}`}>
          {bootLoading && <div className="workspace-banner"><LoaderCircle className="spin" size={16} /> 正在加载学生和考点目录…{bootSlow && <span>加载较慢，可刷新页面重试。</span>}</div>}
          {!bootLoading && studentsError && <div className="workspace-banner banner-error"><AlertCircle size={16} /><span>学生列表加载失败：{studentsError}</span><button type="button" className="banner-retry" onClick={() => void loadWorkspace()}>重试</button></div>}
          {!bootLoading && !curriculum && <div className="workspace-banner banner-error"><AlertCircle size={16} /><span>考点目录加载失败，学生进度和组卷暂不可用。</span><button type="button" className="banner-retry" onClick={() => void loadWorkspace()}>重试</button></div>}
          {!bootLoading && examsError && <div className="workspace-banner banner-error"><AlertCircle size={16} /><span>考试记录加载失败：{examsError}</span><button type="button" className="banner-retry" onClick={() => setExamReload((value) => value + 1)}>重试</button></div>}
          {view === 'home' && !bootLoading && !studentsError && <HomeView
            user={session.user} students={students} exams={exams} examsLoading={examsLoading} examsError={examsError} curriculum={curriculum} selectedStudentId={selectedStudentId}
            onNewExam={() => students.length ? goGuarded({ view: 'exam', item: 'new', s: studentParam }) : goGuarded({ view: 'students', item: 'new' })}
            onNavigate={navigate} onSelectStudent={switchStudent}
            onOpenExam={(exam) => goGuarded({ view: 'exam', item: exam.id, s: exam.student_id })}
            onPracticeFor={(exam) => goGuarded({ view: 'practice', s: exam.student_id, e: exam.id })}
            onCreateStudent={() => goGuarded({ view: 'students', item: 'new' })}
          />}
          {view === 'students' && <StudentsView
            students={students} curriculum={curriculum} selectedStudent={editorStudent} notFound={studentNotFound} detailKey={route.item ?? ''}
            creating={route.item === 'new'} onLeaveWarning={setLeaveWarning}
            onOpenStudent={(id) => goGuarded({ view: 'students', item: id })}
            onStartCreate={() => goGuarded({ view: 'students', item: 'new' })}
            onCancelCreate={() => backGuarded({ view: 'students' })}
            onSaved={async (student, created) => {
              setStudents((rows) => rows.some((row) => row.id === student.id)
                ? rows.map((row) => row.id === student.id ? student : row)
                : [student, ...rows])
              setSelectedStudentId(student.id)
              if (created) setLeaveWarning(null); go({ view: 'students', item: student.id }, { replace: true })
              notify('success', created ? '学生已添加，请勾选已学考点' : '学生资料与已学考点已保存')
            }} onError={(text) => notify('error', text)}
          />}
          {view === 'exam' && <ExamView
            students={students} exams={exams} examsLoading={examsLoading} creating={route.item === 'new'} selectedStudentId={selectedStudentId}
            selectedExam={selectedExam} notFound={examNotFound} uploadQueue={uploadQueue} setUploadQueue={setUploadQueue} onLeaveWarning={setLeaveWarning}
            onCreateStudent={() => goGuarded({ view: 'students', item: 'new' })}
            selectedStudent={selectedStudent} curriculum={curriculum}
            onSelectStudent={switchStudent}
            onOpenExam={(id) => goGuarded({ view: 'exam', item: id, s: studentParam })}
            onStartCreate={() => goGuarded({ view: 'exam', item: 'new', s: studentParam })}
            onCancelCreate={() => backGuarded({ view: 'exam', s: studentParam })}
            onPracticeFor={(exam) => goGuarded({ view: 'practice', s: exam.student_id, e: exam.id })}
            onCreated={async (exam) => {
              setSelectedExamId(exam.id)
              await refreshExams(exam.student_id)
              setLeaveWarning(null); go({ view: 'exam', item: exam.id, s: exam.student_id }, { replace: true })
              notify('success', '考试已创建，请上传试卷和学生作答')
            }} onRefresh={refreshExams} notify={notify}
          />}
          {view === 'practice' && <PracticeView
            students={students} selectedStudentId={selectedStudentId} exams={exams} examsLoading={examsLoading || examsFor !== selectedStudentId} curriculum={curriculum}
            practice={practice} onSelectStudent={switchStudent} onPracticeChange={setPractice}
            presetExamId={route.e} showResult={route.item === 'result'}
            onShowResult={() => goGuarded({ view: 'practice', item: 'result', s: studentParam })}
            onShowSettings={() => back({ view: 'practice', s: studentParam })}
            onError={notifyError} onNotice={notify}
          />}
          {view === 'me' && <MeView user={session.user} onGuide={openUsageGuide} onAdmin={() => goGuarded({ view: 'admin' })} onLogout={() => void logout()} />}
          {view === 'admin' && session.user.role === 'admin' && <AdminView onNotice={notify} />}
          {view === 'admin' && session.user.role !== 'admin' && <EmptyState icon={<ShieldCheck />} title="此区域仅管理员可见" detail="当前账号没有管理设置权限。" />}
        </div>
      </main>
      <nav className="tabbar" aria-label="主导航">
        {TAB_ITEMS.map(({ id, label, icon: Icon }) => {
          const active = view === id || (id === 'me' && view === 'admin')
          return <button type="button" key={id} className={`tab-item ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined} onClick={() => navigate(id)}>
            <Icon size={24} strokeWidth={active ? 2.2 : 1.8} /><span>{label}</span>
          </button>
        })}
      </nav>
      {usageGuideOpen && <UsageGuide onClose={closeUsageGuide} onNavigate={(next) => { closeUsageGuide(); navigate(next) }} />}
      {notice && <div className={`toast toast-${notice.kind}`} role="status"><span>{notice.kind === 'success' ? <CheckCircle2 size={18} /> : notice.kind === 'error' ? <AlertCircle size={18} /> : <Activity size={18} />}</span>{notice.text}<button onClick={() => setNotice(null)} aria-label="关闭提醒"><X size={16} /></button></div>}
    </div>
  )
}

function useIsPhone() {
  const query = '(max-width: 920px)'
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const media = window.matchMedia(query)
    const onChange = () => setMatches(media.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])
  return matches
}

function MeView({ user, onGuide, onAdmin, onLogout }: { user: User; onGuide: () => void; onAdmin: () => void; onLogout: () => void }) {
  return <>
    <div className="page-heading"><div><h1>我的</h1></div></div>
    <section className="panel me-card">
      <span className="avatar">{user.display_name.slice(0, 1) || '师'}</span>
      <span className="me-card-copy"><b>{user.display_name}</b><small>{user.role === 'admin' ? '管理员' : '任课老师'} · 初中数学（湖南）</small></span>
    </section>
    <section className="panel me-list">
      <button type="button" className="me-row" onClick={onGuide} aria-haspopup="dialog"><CircleHelp size={22} /><span>使用方式</span><ChevronRight size={20} /></button>
      {user.role === 'admin' && <button type="button" className="me-row" onClick={onAdmin}><Settings2 size={22} /><span>管理设置</span><ChevronRight size={20} /></button>}
      <button type="button" className="me-row me-row-danger" onClick={onLogout}><LogOut size={22} /><span>退出登录</span></button>
    </section>
  </>
}

export default App

type NextAction = { label: string; kind: 'open' | 'practice'; tone: 'primary' | 'secondary' }

/** One next step per exam, derived only from uploaded assets and the analysis job status. */
function nextActionFor(exam: Exam): NextAction {
  const hasPaper = exam.assets.some((asset) => asset.role === 'paper')
  const hasResponse = exam.assets.some((asset) => asset.role === 'response')
  const status = exam.analysis_job?.status
  if (status === 'queued' || status === 'running') return { label: '查看进度', kind: 'open', tone: 'secondary' }
  if (status === 'failed') return { label: '查看原因', kind: 'open', tone: 'primary' }
  if (status === 'partial') return { label: '去核对', kind: 'open', tone: 'primary' }
  if (status === 'completed') return { label: '组卷', kind: 'practice', tone: 'primary' }
  if (!hasPaper || !hasResponse) return { label: '继续上传', kind: 'open', tone: 'primary' }
  return { label: '开始分析', kind: 'open', tone: 'primary' }
}

function HomeView({ user, students, exams, examsLoading, examsError, curriculum, selectedStudentId, onNavigate, onNewExam, onSelectStudent, onOpenExam, onPracticeFor, onCreateStudent }: {
  user: User; students: Student[]; exams: Exam[]; examsLoading: boolean; examsError: string; curriculum: Curriculum | null; selectedStudentId: string
  onNavigate: (view: View) => void; onNewExam: () => void; onSelectStudent: (id: string) => void; onOpenExam: (exam: Exam) => void
  onPracticeFor: (exam: Exam) => void; onCreateStudent: () => void
}) {
  const hour = new Date().getHours()
  const greeting = hour < 11 ? '早上好' : hour < 18 ? '下午好' : '晚上好'
  const selectedStudent = students.find((student) => student.id === selectedStudentId) ?? null
  const needsReview = exams.filter((exam) => ['partial', 'failed'].includes(exam.analysis_job?.status ?? '')).length
  const runAction = (exam: Exam) => nextActionFor(exam).kind === 'practice' ? onPracticeFor(exam) : onOpenExam(exam)
  return (
    <>
      <div className="page-heading">
        <div><h1>工作台</h1><p>{greeting}，{user.display_name}</p></div>
      </div>
      <div className="home-entry">
        <button type="button" className="entry-card entry-primary" onClick={onNewExam}><Camera size={28} /><span><b>上传考试</b><small>拍照或选择试卷、学生作答</small></span></button>
        <button type="button" className="entry-card" onClick={() => onNavigate('practice')}><BookOpenCheck size={28} /><span><b>组卷</b><small>按考点出练习卷，下载 PDF</small></span></button>
      </div>
      {students.length === 0 ? <section className="panel"><EmptyState icon={<Users />} title="还没有学生" detail="先添加学生并勾选已学考点，再上传考试。" action={<Button onClick={onCreateStudent} icon={<Plus size={16} />}>新增学生</Button>} /></section> : <div className="home-grid">
        <section className="panel home-exams">
          <div className="section-heading">
            <h2>考试记录</h2>
            <label className="inline-select"><span>学生</span><select value={selectedStudentId} onChange={(e) => onSelectStudent(e.target.value)}>{students.map((student) => <option key={student.id} value={student.id}>{student.name}（{gradeLabel(student.grade)}）</option>)}</select></label>
          </div>
          {needsReview > 0 && <p className="home-review-note"><AlertCircle size={15} />{needsReview} 场考试的分析含待核对项或失败，需要查看。</p>}
          {examsLoading ? <Loading label="正在加载考试记录…" /> : examsError ? <p className="inline-error">考试记录加载失败，请点击页面顶部的“重试”。</p> : exams.length ? <ul className="exam-cards">{exams.map((exam) => {
            const action = nextActionFor(exam)
            return <li key={exam.id} className="exam-card">
              <button type="button" className="exam-card-main" onClick={() => onOpenExam(exam)}>
                <b>{exam.title}</b>
                <small>{dateLabel(exam.exam_date)} · 材料 {exam.assets.length} 份</small>
                <span className={`status-pill status-${exam.analysis_job?.status ?? 'queued'}`}>{exam.analysis_job ? homeStatusText(exam.analysis_job.status) : '未分析'}</span>
              </button>
              <button type="button" className={`button ${action.tone === 'primary' ? 'button-outline' : 'button-secondary'} button-md exam-card-action`} onClick={() => runAction(exam)}>{action.label}</button>
            </li>
          })}</ul> : <div className="empty-inline"><span>{selectedStudent ? `${selectedStudent.name}暂无考试记录` : '暂无考试记录'}</span><Button onClick={onNewExam} icon={<Plus size={16} />}>新建考试</Button></div>}
        </section>
        <section className="panel home-students">
          <div className="section-heading"><h2>学生</h2><button className="text-action" onClick={() => onNavigate('students')}>管理<ArrowRight size={14} /></button></div>
          <ul className="plain-list">{students.map((student) => <li key={student.id}><button type="button" className={student.id === selectedStudentId ? 'active' : ''} onClick={() => onSelectStudent(student.id)}>
            <span className="plain-list-main"><b>{student.name}</b><small>{gradeLabel(student.grade)}{student.school ? ` · ${student.school}` : ''}</small></span>
            {student.learned_point_ids.length ? <span className="plain-list-meta">已学 {student.learned_point_ids.length} 个考点</span> : <span className="plain-list-meta warn">未设置已学考点</span>}
          </button></li>)}</ul>
        </section>
      </div>}
      {curriculum && <p className="footer-note">考点目录共 {curriculum.points.length} 个考点</p>}
    </>
  )
}

function homeStatusText(status?: string) {
  return status === 'partial' ? '含待核对项' : statusText(status)
}

function StudentsView({ students, curriculum, selectedStudent, notFound, detailKey, creating, onLeaveWarning, onOpenStudent, onStartCreate, onCancelCreate, onSaved, onError }: {
  students: Student[]; curriculum: Curriculum | null; selectedStudent: Student | null; notFound: boolean; detailKey: string; creating: boolean
  onLeaveWarning: (message: string | null) => void
  onOpenStudent: (id: string) => void; onStartCreate: () => void; onCancelCreate: () => void
  onSaved: (student: Student, created: boolean) => void; onError: (text: string) => void
}) {
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [form, setForm] = useState({ name: '', school: '', grade: 7 as 7 | 8 | 9 })
  const [learned, setLearned] = useState<string[]>([])
  const pointMap = useMemo(() => new Map((curriculum?.points ?? []).map((point) => [point.id, point])), [curriculum])

  useEffect(() => { if (creating) setForm({ name: '', school: '', grade: 7 }) }, [creating])

  // Re-entering a student (detailKey) always shows the saved data, never an earlier unsaved edit.
  useEffect(() => {
    if (!selectedStudent || creating) return
    setForm({ name: selectedStudent.name, school: selectedStudent.school ?? '', grade: selectedStudent.grade })
    setLearned(selectedStudent.learned_point_ids)
  }, [selectedStudent, creating, detailKey])

  const dirty = creating
    ? Boolean(form.name.trim() || form.school.trim())
    : Boolean(selectedStudent) && (form.name !== selectedStudent!.name || form.school !== (selectedStudent!.school ?? '') || form.grade !== selectedStudent!.grade
      || learned.length !== selectedStudent!.learned_point_ids.length || learned.some((id) => !selectedStudent!.learned_point_ids.includes(id)))
  useEffect(() => { onLeaveWarning(dirty && !saving ? '学生资料有修改还没有保存，离开后会丢失。确定离开吗？' : null) }, [dirty, saving, onLeaveWarning])
  useEffect(() => () => onLeaveWarning(null), [onLeaveWarning])

  const visibleStudents = students.filter((student) => `${student.name} ${student.school}`.toLowerCase().includes(query.toLowerCase()))
  const togglePoint = (id: string) => setLearned((values) => values.includes(id) ? values.filter((item) => item !== id) : [...values, id])
  const createStudent = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      const raw = await api<unknown>('/students', { method: 'POST', body: JSON.stringify({ ...form, learned_point_ids: [] }) })
      onSaved(normalizeStudent(raw), true)
    } catch (error) { onError(`创建学生失败：${messageFor(error)}`) }
    finally { setSaving(false) }
  }
  const saveStudent = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedStudent) return
    setSaving(true)
    try {
      const raw = await api<unknown>(`/students/${encodeURIComponent(selectedStudent.id)}`, {
        method: 'PATCH', body: JSON.stringify({
          name: form.name, school: form.school, grade: form.grade,
          learned_point_ids: learned, expected_revision: selectedStudent.progress_revision,
        }),
      })
      onSaved(normalizeStudent(raw), false)
    } catch (error) { onError(`保存失败：${messageFor(error)}`) }
    finally { setSaving(false) }
  }

  return (
    <>
      <div className="page-heading"><div><h1>学生档案</h1><p>勾选的已学考点决定组卷范围。</p></div><Button onClick={onStartCreate} icon={<Plus size={16} />}>新增学生</Button></div>
      <div className="students-layout">
        <section className="panel students-list-panel">
          <div className="list-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索姓名或学校" /></div>
          {students.length === 0 ? <EmptyState icon={<Users />} title="还没有学生" detail="点击“新增学生”添加第一位学生。" /> : <div className="student-list">
            {visibleStudents.map((student) => <button key={student.id} className={`student-list-item ${!creating && selectedStudent?.id === student.id ? 'selected' : ''}`} onClick={() => onOpenStudent(student.id)}>
              <span className="avatar">{student.name.slice(0, 1) || '学'}</span><span className="student-mini"><b>{student.name}</b><small>{student.school || '未填写学校'} · {gradeLabel(student.grade)}</small></span><ChevronDown className="student-list-chevron" size={16} />
            </button>)}
            {visibleStudents.length === 0 && <p className="list-empty">没有找到匹配的学生</p>}
          </div>}
          <div className="list-footer"><span>共 {students.length} 位学生</span></div>
        </section>
        <section className="student-editor panel">
          {notFound ? <EmptyState icon={<UserRound />} title="找不到这位学生" detail="链接可能已失效，或学生不属于当前账号。" action={<Button onClick={onCancelCreate}>返回学生列表</Button>} /> : creating ? <>
            <div className="section-heading"><h2>新增学生</h2><button className="icon-button" onClick={onCancelCreate} aria-label="取消新增"><X size={17} /></button></div>
            <p className="section-intro">已学考点可在创建后勾选；未勾选前不能组卷。</p>
            <form className="student-form" onSubmit={createStudent}>
              <div className="two-fields"><Field label="姓名 / 称呼"><input required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：小林" /></Field><Field label="年级"><select value={form.grade} onChange={(e) => setForm({ ...form, grade: Number(e.target.value) as 7 | 8 | 9 })}><option value={7}>初一</option><option value={8}>初二</option><option value={9}>初三</option></select></Field></div>
              <Field label="学校" hint="选填"><input maxLength={160} value={form.school} onChange={(e) => setForm({ ...form, school: e.target.value })} placeholder="学校名称" /></Field>
              <div className="form-foot sticky-actions"><Button variant="quiet" onClick={onCancelCreate}>取消</Button><Button type="submit" disabled={saving} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}>{saving ? '创建中…' : '创建'}</Button></div>
            </form>
          </> : selectedStudent ? <>
            <div className="section-heading"><h2>{selectedStudent.name}</h2></div>
            <form onSubmit={saveStudent}>
              <div className="two-fields"><Field label="姓名 / 称呼"><input required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field><Field label="年级"><select value={form.grade} onChange={(e) => setForm({ ...form, grade: Number(e.target.value) as 7 | 8 | 9 })}><option value={7}>初一</option><option value={8}>初二</option><option value={9}>初三</option></select></Field></div>
              <Field label="学校"><input maxLength={160} value={form.school} onChange={(e) => setForm({ ...form, school: e.target.value })} placeholder="可留空" /></Field>
              <div className="progress-heading"><div><h3>已学考点 <span>已选 {learned.length} / {curriculum?.points.length ?? 0}</span></h3></div><div className="progress-shortcuts"><button type="button" onClick={() => setLearned(curriculum?.points.map((p) => p.id) ?? [])}>全选</button><button type="button" onClick={() => setLearned([])}>清空</button></div></div>
              {!curriculum ? <div className="inline-error">考点目录不可用，进度暂时无法编辑。</div> : <div className="point-picker">
                {curriculum.points.map((point) => <label key={point.id} className={`point-option ${learned.includes(point.id) ? 'point-selected' : ''}`}>
                  <input type="checkbox" checked={learned.includes(point.id)} onChange={() => togglePoint(point.id)} />
                  <span className="check-box">{learned.includes(point.id) && <Check size={12} />}</span>
                  <span className="point-name">{point.name}</span>
                  {point.prerequisite_ids.length > 0 && <span className="prereq-count" title={`前置考点：${point.prerequisite_ids.map((id) => pointMap.get(id)?.name ?? id).join('、')}`}>前置 {point.prerequisite_ids.length}</span>}
                </label>)}
              </div>}
              <p className="progress-note">组卷只选用考点及其前置考点都已勾选的题目。</p>
              <div className="form-foot sticky-actions"><Button type="submit" disabled={saving} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}>{saving ? '保存中…' : '保存'}</Button></div>
            </form>
          </> : <EmptyState icon={<UserRound />} title="未选择学生" detail="在左侧选择学生，或点击“新增学生”。" />}
        </section>
      </div>
    </>
  )
}

function ExamView({ students, exams, examsLoading, creating, selectedStudentId, selectedExam, notFound, uploadQueue: allUploads, setUploadQueue, onLeaveWarning, onCreateStudent, selectedStudent, curriculum, onSelectStudent, onOpenExam, onStartCreate, onCancelCreate, onPracticeFor, onCreated, onRefresh, notify }: {
  students: Student[]; exams: Exam[]; examsLoading: boolean; creating: boolean; selectedStudentId: string; selectedExam: Exam | null; notFound: boolean
  uploadQueue: UploadItem[]; setUploadQueue: React.Dispatch<React.SetStateAction<UploadItem[]>>; onLeaveWarning: (message: string | null) => void; onCreateStudent: () => void
  selectedStudent: Student | null; curriculum: Curriculum | null
  onSelectStudent: (id: string) => void; onOpenExam: (id: string) => void; onStartCreate: () => void; onCancelCreate: () => void
  onPracticeFor: (exam: Exam) => void
  onCreated: (exam: Exam) => void; onRefresh: (studentId?: string) => Promise<Exam[]>
  notify: (kind: Notice['kind'], text: string) => void
}) {
  const [createBusy, setCreateBusy] = useState(false)
  const [examForm, setExamForm] = useState({ title: '', exam_date: todayIso(), question_count: '', total_score: '', max_score: '', class_rank: '', grade_rank: '' })
  const uploadQueue = allUploads.filter((item) => item.examId === selectedExam?.id)
  const [previewAsset, setPreviewAsset] = useState<ExamAsset | null>(null)
  const [orderBusy, setOrderBusy] = useState(false)
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [provider, setProvider] = useState<'deepseek' | 'codex'>('deepseek')
  const [allowCodex, setAllowCodex] = useState(false)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [analysisData, setAnalysisData] = useState<ExamAnalysisResponse | null>(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [jobReadError, setJobReadError] = useState('')
  const [examStatusLoading, setExamStatusLoading] = useState(false)
  const [editingResponse, setEditingResponse] = useState<Record<string, any> | null>(null)
  const [correctionText, setCorrectionText] = useState('')
  const [correctionNumber, setCorrectionNumber] = useState('')
  const [correctionPart, setCorrectionPart] = useState('')
  const [correctionBusy, setCorrectionBusy] = useState(false)
  const selectedExamIdRef = useRef(selectedExam?.id ?? '')
  const selectedStudentIdRef = useRef(selectedStudentId)
  const examMountedRef = useRef(false)
  const examEpochRef = useRef(0)
  const analysisReadRef = useRef(0)
  const jobControllerRef = useRef<AbortController | null>(null)
  selectedExamIdRef.current = selectedExam?.id ?? ''
  selectedStudentIdRef.current = selectedStudentId

  const isCurrentExam = useCallback((examId: string, studentId: string) => (
    examMountedRef.current && selectedExamIdRef.current === examId && selectedStudentIdRef.current === studentId
  ), [])

  const loadAnalysis = useCallback(async (examId: string, studentId: string) => {
    const request = ++analysisReadRef.current
    setAnalysisLoading(true)
    try {
      const value = await api<ExamAnalysisResponse>(`/exams/${encodeURIComponent(examId)}/analysis`)
      if (isCurrentExam(examId, studentId) && request === analysisReadRef.current) setAnalysisData(value)
    } catch (error) {
      if (isCurrentExam(examId, studentId) && request === analysisReadRef.current && !(error instanceof ApiError && error.status === 409)) {
        notify('error', `读取考试分析失败：${messageFor(error)}`)
      }
    } finally {
      if (isCurrentExam(examId, studentId) && request === analysisReadRef.current) setAnalysisLoading(false)
    }
  }, [isCurrentExam, notify])

  const updateJob = useCallback(async (jobId: string) => {
    const examId = selectedExam?.id
    const studentId = selectedStudentId
    if (!examId || !isCurrentExam(examId, studentId)) return
    jobControllerRef.current?.abort()
    const controller = new AbortController()
    jobControllerRef.current = controller
    setJobReadError('')
    const current = () => !controller.signal.aborted && isCurrentExam(examId, studentId)
    try {
      const finalJob = await pollJob(jobId, (job) => { if (current()) setActiveJob(job) }, controller.signal)
      if (!current()) return
      if (finalJob.status === 'completed' || finalJob.status === 'partial') {
        await onRefresh(studentId)
        if (!current()) return
        await loadAnalysis(examId, studentId)
        if (current()) notify(finalJob.status === 'completed' ? 'success' : 'info', finalJob.status === 'completed' ? '考试分析已完成' : '分析已生成，仍有未确定项目')
      } else if (finalJob.status === 'failed') {
        notify('error', `分析失败：${readableJobError(finalJob.error)}`)
      }
    } catch (error) {
      if (current() && !(error instanceof DOMException && error.name === 'AbortError')) {
        setJobReadError(messageFor(error))
        notify('error', messageFor(error))
      }
    }
  }, [selectedExam?.id, selectedStudentId, isCurrentExam, onRefresh, loadAnalysis, notify])

  useEffect(() => {
    api<{ allow_codex: boolean }>('/capabilities').then((value) => setAllowCodex(Boolean(value.allow_codex))).catch(() => setAllowCodex(false))
  }, [])

  useEffect(() => {
    ++examEpochRef.current
    examMountedRef.current = true
    setAnalysisData(null)
    setJobReadError('')
    setAnalysisBusy(false)
    setCorrectionBusy(false)
    setEditingResponse(null)
    setActiveJob(selectedExam?.analysis_job ?? null)
    const epoch = examEpochRef.current
    const examId = selectedExam?.id
    if (examId) {
      const current = () => epoch === examEpochRef.current && isCurrentExam(examId, selectedStudentId)
      setExamStatusLoading(true)
      void loadAnalysis(examId, selectedStudentId)
      // The cached list may predate a job submitted just before leaving this view.
      void onRefresh(selectedStudentId).then((rows) => {
        if (!current()) return
        const fresh = rows.find((exam) => exam.id === examId && exam.student_id === selectedStudentId)
        const job = fresh?.analysis_job ?? null
        setActiveJob(job)
        if (job && ['queued', 'running'].includes(job.status)) void updateJob(job.id)
      }).catch((error) => {
        if (current()) notify('error', `读取考试状态失败：${messageFor(error)}`)
      }).finally(() => { if (current()) setExamStatusLoading(false) })
    } else setExamStatusLoading(false)
    return () => {
      examMountedRef.current = false
      ++analysisReadRef.current
      jobControllerRef.current?.abort()
    }
  }, [selectedExam?.id, selectedStudentId, loadAnalysis, updateJob, isCurrentExam, onRefresh, notify])

  const createDirty = creating && Boolean(examForm.title.trim() || examForm.question_count || examForm.total_score || examForm.max_score)
  useEffect(() => { onLeaveWarning(createDirty && !createBusy ? '新建考试的内容还没有保存，离开后会丢失。确定离开吗？' : null) }, [createDirty, createBusy, onLeaveWarning])
  useEffect(() => () => onLeaveWarning(null), [onLeaveWarning])
  useEffect(() => { if (!creating) setExamForm({ title: '', exam_date: todayIso(), question_count: '', total_score: '', max_score: '', class_rank: '', grade_rank: '' }) }, [creating])

  const createExam = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedStudentId) { notify('error', '请先选择学生'); return }
    setCreateBusy(true)
    try {
      const raw = await api<unknown>('/exams', {
        method: 'POST',
        body: JSON.stringify({
          student_id: selectedStudentId,
          title: examForm.title.trim(),
          exam_date: examForm.exam_date || null,
          question_count: Number(examForm.question_count),
          total_score: examForm.total_score === '' ? null : Number(examForm.total_score),
          max_score: examForm.max_score === '' ? null : Number(examForm.max_score),
          class_rank: examForm.class_rank === '' ? null : Number(examForm.class_rank),
          grade_rank: examForm.grade_rank === '' ? null : Number(examForm.grade_rank),
        }),
      })
      const created = normalizeExam(raw)
      onCreated(created)
      setExamForm({ title: '', exam_date: todayIso(), question_count: '', total_score: '', max_score: '', class_rank: '', grade_rank: '' })
    } catch (error) { notify('error', `创建考试失败：${messageFor(error)}`) }
    finally { setCreateBusy(false) }
  }

  // Upload one file at a time; keep going after a failure so the teacher can retry just the failed pages.
  const uploadOne = async (item: UploadItem) => {
    setUploadQueue((queue) => queue.map((row) => row.key === item.key ? { ...row, status: 'uploading', error: '' } : row))
    try {
      const prepared = await prepareUpload(item.file)
      const form = new FormData()
      form.set('role', item.role)
      form.set('file', prepared.file)
      await api(`/exams/${encodeURIComponent(item.examId)}/assets`, { method: 'POST', body: form })
      setUploadQueue((queue) => queue.filter((row) => row.key !== item.key))
      await onRefresh().catch(() => undefined)
      return true
    } catch (error) {
      setUploadQueue((queue) => queue.map((row) => row.key === item.key ? { ...row, status: 'failed', error: messageFor(error) } : row))
      return false
    }
  }

  const uploadFiles = async (files: FileList | null, role: AssetRole) => {
    if (!selectedExam || !files?.length) return
    const examId = selectedExam.id
    const items: UploadItem[] = Array.from(files).map((file) => ({ key: newClientId(), examId, file, role, status: 'waiting', error: '' }))
    setUploadQueue((queue) => [...queue, ...items])
    let done = 0
    for (const item of items) if (await uploadOne(item)) done += 1
    const failed = items.length - done
    if (failed === 0) notify('success', `已上传 ${done} 页${roleName(role)}`)
    else notify('error', `${roleName(role)}有 ${failed} 页没有传上去，请在列表中点“重试”`)
  }

  const moveAsset = async (assetId: string, delta: number) => {
    if (!selectedExam || orderBusy) return
    const ordered = [...selectedExam.assets].sort((a, b) => a.ordinal - b.ordinal)
    const index = ordered.findIndex((asset) => asset.id === assetId)
    if (index < 0) return
    // Pages are shown per material type, so “上移/下移” swaps with the neighbouring page of the same type.
    const sameRole = ordered.map((asset, position) => ({ asset, position })).filter((row) => row.asset.role === ordered[index].role)
    const roleIndex = sameRole.findIndex((row) => row.asset.id === assetId)
    const neighbour = sameRole[roleIndex + delta]
    if (!neighbour) return
    const target = neighbour.position
    ;[ordered[index], ordered[target]] = [ordered[target], ordered[index]]
    setOrderBusy(true)
    try {
      await api(`/exams/${encodeURIComponent(selectedExam.id)}/assets/order`, {
        method: 'POST', body: JSON.stringify({ asset_ids: ordered.map((asset) => asset.id) }),
      })
      await onRefresh(selectedStudentId)
      notify('success', '页面顺序已保存')
    } catch (error) { notify('error', `调整顺序失败：${messageFor(error)}`) }
    finally { setOrderBusy(false) }
  }

  const taskRunning = examStatusLoading || analysisBusy || correctionBusy || ['queued', 'running'].includes(activeJob?.status ?? '')

  const analyze = async () => {
    if (!selectedExam || taskRunning) return
    const epoch = examEpochRef.current
    const current = () => epoch === examEpochRef.current && isCurrentExam(selectedExam.id, selectedStudentId)
    if (!curriculum) { notify('error', '考点目录当前不可用，无法开始分析'); return }
    if (!selectedExam.assets.some((asset) => asset.role === 'paper') || !selectedExam.assets.some((asset) => asset.role === 'response')) {
      notify('error', '分析需要试卷和已批改的学生作答')
      return
    }
    setAnalysisBusy(true)
    try {
      const job = unwrap<Job>(await createJobForExam(selectedExam.id, provider))
      if (!current()) return
      setActiveJob(job)
      notify('info', '考试分析任务已提交')
      await updateJob(job.id)
    } catch (error) { if (current()) notify('error', `提交分析失败：${messageFor(error)}`) }
    finally { if (current()) setAnalysisBusy(false) }
  }

  const retry = async () => {
    if (!activeJob || !selectedExam || taskRunning) return
    const epoch = examEpochRef.current
    const current = () => epoch === examEpochRef.current && isCurrentExam(selectedExam.id, selectedStudentId)
    setAnalysisBusy(true)
    try {
      const job = unwrap<Job>(await api<Job>(`/jobs/${encodeURIComponent(activeJob.id)}/retry`, { method: 'POST' }))
      if (!current()) return
      setActiveJob(job)
      await updateJob(job.id)
    } catch (error) { if (current()) notify('error', `重试失败：${messageFor(error)}`) }
    finally { if (current()) setAnalysisBusy(false) }
  }

  const openCorrection = (response: Record<string, any>) => {
    setEditingResponse(response)
    setCorrectionText(String(response.transcript ?? ''))
    setCorrectionNumber(String(response.number ?? ''))
    setCorrectionPart(String(response.part_path ?? ''))
  }

  const submitCorrection = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedExam || !analysisData || !editingResponse || taskRunning) return
    const epoch = examEpochRef.current
    const current = () => epoch === examEpochRef.current && isCurrentExam(selectedExam.id, selectedStudentId)
    const responseSources = arrayOf<Record<string, any>>(editingResponse.sources)
    if (!responseSources.length) { notify('error', '这条转录没有可用的作答区域证据，暂时不能提交修正'); return }
    const destination = Number(correctionNumber)
    if (!Number.isInteger(destination) || destination < 1) { notify('error', '请输入有效的目标题号'); return }
    const originalNumber = Number(editingResponse.number)
    const originalPart = String(editingResponse.part_path ?? '')
    const numberChanged = destination !== originalNumber
    const partChanged = correctionPart !== originalPart
    const paperQuestions = arrayOf<Record<string, any>>(analysisData.analysis.paper_questions)
    const destinationQuestions = numberChanged || partChanged
      ? paperQuestions.filter((question) => Number(question.number) === destination)
      : []
    if ((numberChanged || partChanged) && destinationQuestions.length !== 1) {
      notify('error', '目标题号没有唯一对应的原题来源，暂时不能更改题号或小问')
      return
    }
    const destinationSources = destinationQuestions.length === 1
      ? arrayOf<Record<string, any>>(destinationQuestions[0].sources)
      : []
    if ((numberChanged || partChanged) && destinationSources.length === 0) {
      notify('error', '目标原题缺少来源区域，暂时不能更改题号或小问')
      return
    }
    if (numberChanged && !destinationQuestions[0].part_paths?.includes(correctionPart) && correctionPart) {
      notify('error', '该小问路径不属于目标题号，请先修正小问路径')
      return
    }
    setCorrectionBusy(true)
    try {
      const correction = {
        id: newClientId(),
        base_revision: String(analysisData.analysis.revision),
        number: destination,
        part_path: correctionPart,
        response_id: String(editingResponse.id),
        corrected_transcript: correctionText,
        ...(numberChanged ? { corrected_response_number: destination } : {}),
        ...(partChanged ? { corrected_part_path: correctionPart } : {}),
        evidence: [responseSources[0], ...destinationSources.slice(0, 1)],
      }
      const job = unwrap<Job>(await api<Job>(`/exams/${encodeURIComponent(selectedExam.id)}/corrections`, {
        method: 'POST', body: JSON.stringify({ corrections: [correction] }),
      }))
      if (!current()) return
      setEditingResponse(null)
      setActiveJob(job)
      notify('info', '修正任务已提交，原分析版本会保留')
      await updateJob(job.id)
    } catch (error) { if (current()) notify('error', `提交修正失败：${messageFor(error)}`) }
    finally { if (current()) setCorrectionBusy(false) }
  }

  const sortedAssets = [...(selectedExam?.assets ?? [])].sort((a, b) => a.ordinal - b.ordinal)
  const assetCount = (role: 'paper' | 'response' | 'reference') => selectedExam?.assets.filter((asset) => asset.role === role).length ?? 0
  const missingRequired = (['paper', 'response'] as const).filter((role) => assetCount(role) === 0).map(roleName)

  return (
    <>
      <div className="page-heading"><div><h1>考试分析</h1></div><Button onClick={onStartCreate} icon={<Plus size={16} />}>新建考试</Button></div>
      <div className="exam-layout">
        <aside className="panel exam-index">
          <div className="exam-index-head"><h2>考试列表</h2></div>
          <Field label="学生"><select value={selectedStudentId} onChange={(e) => onSelectStudent(e.target.value)}><option value="">请选择</option>{students.map((student) => <option key={student.id} value={student.id}>{student.name} · {gradeLabel(student.grade)}</option>)}</select></Field>
          {students.length === 0 ? <div className="small-empty">还没有学生，先新增学生再上传考试。<Button onClick={onCreateStudent} icon={<Plus size={16} />}>新增学生</Button></div> : !selectedStudentId ? <div className="small-empty">请先选择学生。</div> : examsLoading ? <Loading label="正在加载…" /> : exams.length === 0 ? <div className="small-empty">暂无考试记录 <Button onClick={onStartCreate} icon={<Plus size={16} />}>新建考试</Button></div> : <div className="exam-list">{exams.map((exam) => <button key={exam.id} onClick={() => onOpenExam(exam.id)} className={`exam-list-item ${selectedExam?.id === exam.id ? 'active' : ''}`}>
            <span className="exam-list-copy"><b>{exam.title}</b><small>{dateLabel(exam.exam_date)} · {exam.analysis_job ? homeStatusText(exam.analysis_job.status) : '未分析'}</small></span><i className={`status-dot status-dot-${exam.analysis_job?.status ?? exam.status ?? 'draft'}`} aria-hidden="true" />
          </button>)}</div>}
        </aside>
        <div className="exam-main">
          {creating && <div className="panel create-exam-panel">
            <div className="section-heading"><h2>新建考试{selectedStudent ? `（${selectedStudent.name}）` : ''}</h2><button className="icon-button" onClick={onCancelCreate} aria-label="取消新建"><X size={17} /></button></div>
            <form onSubmit={createExam} className="exam-create-form">
              <Field label="考试名称"><input required maxLength={200} value={examForm.title} onChange={(e) => setExamForm({ ...examForm, title: e.target.value })} placeholder="例如：九月阶段检测" /></Field>
              <div className="three-fields"><Field label="考试日期"><input type="date" value={examForm.exam_date} onChange={(e) => setExamForm({ ...examForm, exam_date: e.target.value })} /></Field><Field label="题目数" hint="按原卷大题题号数填写"><input type="number" min="1" max="200" step="1" required value={examForm.question_count} onChange={(e) => setExamForm({ ...examForm, question_count: e.target.value })} /></Field><Field label="满分（选填）"><input type="number" min="0.5" step="0.5" value={examForm.max_score} onChange={(e) => setExamForm({ ...examForm, max_score: e.target.value })} /></Field></div>
              <div className="two-fields"><Field label="得分（选填）"><input type="number" min="0" step="0.5" value={examForm.total_score} onChange={(e) => setExamForm({ ...examForm, total_score: e.target.value })} /></Field><Field label="班级排名（选填）"><input type="number" min="1" value={examForm.class_rank} onChange={(e) => setExamForm({ ...examForm, class_rank: e.target.value })} /></Field></div>
              <div className="two-fields"><Field label="年级排名（选填）"><input type="number" min="1" value={examForm.grade_rank} onChange={(e) => setExamForm({ ...examForm, grade_rank: e.target.value })} /></Field></div>
              {!selectedStudentId && (students.length ? <div className="inline-error">请先选择学生（在考试列表上方）</div> : <div className="inline-error">还没有学生，请先新增学生。<Button size="sm" onClick={onCreateStudent} icon={<Plus size={14} />}>新增学生</Button></div>)}
              <div className="form-foot sticky-actions"><Button variant="quiet" onClick={onCancelCreate}>取消</Button><Button type="submit" disabled={createBusy || !selectedStudentId} icon={createBusy ? <LoaderCircle className="spin" size={15} /> : <FilePlus2 size={15} />}>{createBusy ? '创建中…' : '创建'}</Button></div>
            </form>
          </div>}
          {notFound ? <div className="panel"><EmptyState icon={<ClipboardList />} title="找不到这场考试" detail="链接可能已失效，或这场考试属于其他学生或账号。" action={<Button onClick={onCancelCreate}>返回考试列表</Button>} /></div> : !selectedExam ? creating || examsLoading ? null : <div className="panel"><EmptyState icon={<ClipboardList />} title={!selectedStudentId ? '未选择学生' : exams.length ? '未选择考试' : '新建考试'} detail={!selectedStudentId ? '先在左侧选择学生。' : exams.length ? '在左侧选择一场考试。' : '点击右上角“新建考试”，填写名称和题目数，再上传试卷和学生作答。'} /></div> : <>
            <section className="panel exam-summary-card">
              <div className="exam-title-row"><div><h2>{selectedExam.title}</h2><p>{selectedStudent?.name ?? '学生'} · {dateLabel(selectedExam.exam_date)}</p></div><span className={`status-pill status-${activeJob?.status ?? selectedExam.analysis_job?.status ?? 'queued'}`}>{(activeJob ?? selectedExam.analysis_job) ? homeStatusText(activeJob?.status ?? selectedExam.analysis_job?.status) : '未分析'}</span></div>
              <div className="exam-summary-meta"><span>试卷 <b>{assetCount('paper')}</b> 份</span><span>学生作答 <b>{assetCount('response')}</b> 份</span><span>参考答案 <b>{assetCount('reference')}</b> 份</span></div>
            </section>
            <section className="panel upload-panel">
              <div className="section-heading"><h2><span className="step-no">1</span>上传材料</h2></div>
              <p className="section-intro">试卷和学生作答必传，参考答案选传。{missingRequired.length ? <strong className="missing-note">还缺：{missingRequired.join('、')}</strong> : <strong className="ready-note">必需材料已齐</strong>}</p>
              <div className="upload-roles">
                {(['paper', 'response', 'reference'] as const).map((role) => <UploadRoleCard key={role} role={role} required={role !== 'reference'} assets={sortedAssets.filter((asset) => asset.role === role)} queue={uploadQueue.filter((item) => item.role === role)}
                  orderBusy={orderBusy} onFiles={(files) => void uploadFiles(files, role)} onRetry={(item) => void uploadOne(item)} onDiscard={(item) => setUploadQueue((queue) => queue.filter((row) => row.key !== item.key))}
                  onMove={(id, delta) => void moveAsset(id, delta)} onPreview={setPreviewAsset} />)}
              </div>
              {previewAsset && <ModalFrame className="modal-card image-modal" label="页面预览" onClose={() => setPreviewAsset(null)}><div className="section-heading"><h2>{roleName(previewAsset.role)}：{previewAsset.filename}</h2><button className="icon-button" onClick={() => setPreviewAsset(null)} aria-label="关闭"><X size={17} /></button></div><div className="source-image-stage"><img className="asset-preview-image" src={`/api/assets/${encodeURIComponent(previewAsset.id)}/content`} alt={`${roleName(previewAsset.role)}页面`} /></div></ModalFrame>}
            </section>
            <section className="panel analysis-action-panel">
              <div className="section-heading"><h2><span className="step-no">2</span>分析</h2></div>
              {missingRequired.length > 0 && <p className="section-intro">上传{missingRequired.join('和')}后才能开始分析。</p>}
              <div className="analysis-controls">
                {allowCodex && <Field label="分析通道（开发环境）">
                  <select value={provider} onChange={(e) => setProvider(e.target.value as 'deepseek' | 'codex')}>
                    <option value="deepseek">DeepSeek</option>
                    <option value="codex">Codex CLI（开发验证）</option>
                  </select>
                </Field>}
                <Button onClick={() => void analyze()} disabled={taskRunning || !curriculum || missingRequired.length > 0} icon={analysisBusy ? <LoaderCircle className="spin" size={16} /> : undefined}>{taskRunning ? '分析中…' : '开始分析'}</Button>
                {activeJob?.status === 'failed' && <Button variant="secondary" onClick={() => void retry()} disabled={analysisBusy} icon={<RotateCcw size={15} />}>重试</Button>}
              </div>
              {provider === 'codex' && <p className="dev-provider-note">当前使用 Codex CLI 开发验证通道，结果不代表正式模型效果。</p>}
              {activeJob && <JobProgress job={activeJob} />}
              {jobReadError && activeJob && <div className="inline-error">读取进度失败：{jobReadError}<Button variant="secondary" onClick={() => void updateJob(activeJob.id)}>重新读取进度</Button></div>}
              {analysisLoading && <Loading label="正在加载分析结果…" />}
            </section>
            <div className="mobile-action-bar">{(() => {
              const status = activeJob?.status ?? selectedExam.analysis_job?.status
              if (assetCount('paper') === 0) return <FilePickButton capture accept="image/*" className="button button-primary button-lg" onFiles={(files) => void uploadFiles(files, 'paper')}><Camera size={22} />拍试卷</FilePickButton>
              if (assetCount('response') === 0) return <FilePickButton capture accept="image/*" className="button button-primary button-lg" onFiles={(files) => void uploadFiles(files, 'response')}><Camera size={22} />拍学生作答</FilePickButton>
              if (status === 'queued' || status === 'running') return <Button className="button-lg" disabled>分析中，可以先离开，稍后回来查看</Button>
              if (status === 'partial') return <Button className="button-lg" onClick={() => document.querySelector('.analysis-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>去核对分析结果</Button>
              if (status === 'completed') return <Button className="button-lg" onClick={() => onPracticeFor(selectedExam)} icon={<BookOpenCheck size={20} />}>用这次考试组卷</Button>
              if (status === 'failed') return <Button className="button-lg" onClick={() => void retry()} disabled={analysisBusy} icon={<RotateCcw size={20} />}>重试分析</Button>
              return <Button className="button-lg" onClick={() => void analyze()} disabled={taskRunning || !curriculum}>{taskRunning ? '分析中…' : '材料已齐，开始分析'}</Button>
            })()}</div>
            {analysisData && <AnalysisPanel
              onPractice={['completed', 'partial'].includes(activeJob?.status ?? selectedExam.analysis_job?.status ?? '') ? () => onPracticeFor(selectedExam) : undefined}
              data={analysisData} examId={selectedExam.id} curriculum={curriculum} onEdit={openCorrection}
              onSubmitCorrection={submitCorrection} editingResponse={editingResponse}
              setEditingResponse={setEditingResponse} correctionText={correctionText} setCorrectionText={setCorrectionText}
              correctionNumber={correctionNumber} setCorrectionNumber={setCorrectionNumber}
              correctionPart={correctionPart} setCorrectionPart={setCorrectionPart}
              correctionBusy={correctionBusy}
            />}
          </>}
        </div>
      </div>
    </>
  )
}

type AssetRole = 'paper' | 'response' | 'reference'
type UploadItem = { key: string; examId: string; file: File; role: AssetRole; status: 'waiting' | 'uploading' | 'failed'; error: string }

const CAPTURE_LABEL: Record<AssetRole, string> = { paper: '拍试卷', response: '拍学生作答', reference: '拍参考答案' }
// HEIC/WebP are converted to JPEG in the browser when it can decode them (imageUpload.ts); the server accepts JPEG/PNG/PDF.
const UPLOAD_ACCEPT = 'image/jpeg,image/png,image/heic,image/heif,image/webp,application/pdf'

function FilePickButton({ capture, accept, multiple, className, onFiles, children }: {
  capture?: boolean; accept: string; multiple?: boolean; className: string; onFiles: (files: FileList | null) => void; children: React.ReactNode
}) {
  return <label className={className}>
    <input type="file" accept={accept} multiple={multiple} capture={capture ? 'environment' : undefined} onChange={(event) => { onFiles(event.target.files); event.currentTarget.value = '' }} />
    {children}
  </label>
}

function UploadRoleCard({ role, required, assets, queue, orderBusy, onFiles, onRetry, onDiscard, onMove, onPreview }: {
  role: AssetRole; required: boolean; assets: ExamAsset[]; queue: UploadItem[]; orderBusy: boolean
  onFiles: (files: FileList | null) => void; onRetry: (item: UploadItem) => void; onDiscard: (item: UploadItem) => void
  onMove: (id: string, delta: number) => void; onPreview: (asset: ExamAsset) => void
}) {
  const status = assets.length ? `已传 ${assets.length} 份` : required ? '必传，还没有上传' : '选传'
  return <div className={`upload-role-card ${required ? '' : 'optional'}`}>
    <div className="upload-role-head"><b>{roleName(role)}</b><span className={`role-tag ${assets.length ? 'ok' : required ? 'need' : ''}`}>{status}</span></div>
    <div className="upload-role-actions">
      <FilePickButton capture accept="image/*" className={`button ${required ? 'button-primary' : 'button-secondary'} button-lg capture-only`} onFiles={onFiles}><Camera size={22} />{CAPTURE_LABEL[role]}</FilePickButton>
      <FilePickButton multiple accept={UPLOAD_ACCEPT} className="button button-secondary button-lg" onFiles={onFiles}><Images size={20} /><span className="phone-only">从相册或文件选择</span><span className="desktop-only">选择{roleName(role)}文件（可多选）</span></FilePickButton>
    </div>
    {(assets.length > 0 || queue.length > 0) && <ol className="page-thumbs">
      {assets.map((asset, index) => {
        const isPdf = asset.filename.toLowerCase().endsWith('.pdf')
        const label = isPdf ? `第 ${index + 1} 份（PDF）` : `第 ${index + 1} 页`
        const href = `/api/assets/${encodeURIComponent(asset.id)}/content`
        return <li key={asset.id} className="page-thumb">
          {isPdf
            ? <a className="thumb-image thumb-pdf" href={href} target="_blank" rel="noreferrer" aria-label={`打开${label}`}><FileText size={30} /><span>PDF</span></a>
            : <button type="button" className="thumb-image" onClick={() => onPreview(asset)} aria-label={`查看${label}`}><img src={href} loading="lazy" alt="" /></button>}
          <span className="thumb-label">{label}</span>
          <span className="thumb-move">
            <button type="button" disabled={index === 0 || orderBusy} onClick={() => onMove(asset.id, -1)} aria-label={`${label}上移`}><ArrowUp size={18} /></button>
            <button type="button" disabled={index === assets.length - 1 || orderBusy} onClick={() => onMove(asset.id, 1)} aria-label={`${label}下移`}><ArrowDown size={18} /></button>
          </span>
        </li>
      })}
      {queue.map((item) => <li key={item.key} className={`page-thumb upload-${item.status}`}>
        <span className="thumb-image thumb-pending">{item.status === 'failed' ? <AlertCircle size={28} /> : <LoaderCircle className="spin" size={28} />}</span>
        <span className="thumb-label">{item.status === 'failed' ? '上传失败' : item.status === 'uploading' ? '上传中…' : '等待上传'}</span>
        {item.status === 'failed' && <>
          <small className="thumb-error">{item.error}</small>
          <span className="thumb-move thumb-retry"><button type="button" onClick={() => onRetry(item)}>重试</button><button type="button" onClick={() => onDiscard(item)}>移除</button></span>
        </>}
      </li>)}
    </ol>}
  </div>
}

function roleName(role: 'paper' | 'response' | 'reference') {
  return ({ paper: '试卷', response: '学生作答', reference: '参考答案' })[role]
}


function JobProgress({ job }: { job: Job }) {
  const completed = job.status === 'completed' || job.status === 'partial'
  const settled = completed || job.status === 'failed'
  const progress = settled ? 100 : 38
  const label = job.status === 'partial' ? '已完成，含待核对项' : statusText(job.status)
  return <div className={`job-progress job-${job.status}`}><div className="job-icon">{completed ? <CheckCircle2 size={17} /> : job.status === 'failed' ? <CircleHelp size={17} /> : <LoaderCircle className="spin" size={17} />}</div><div className="job-main"><div className="job-title-row"><b>{label}</b></div><div className={`progress-track ${settled ? '' : 'progress-indeterminate'}`}><i style={{ width: `${progress}%` }} /></div>{job.error && <p>{readableJobError(job.error)}</p>}</div></div>
}

function AnalysisPanel({ onPractice, data, examId, curriculum, onEdit, onSubmitCorrection, editingResponse, setEditingResponse, correctionText, setCorrectionText, correctionNumber, setCorrectionNumber, correctionPart, setCorrectionPart, correctionBusy }: {
  onPractice?: () => void; data: ExamAnalysisResponse; examId: string; curriculum: Curriculum | null; onEdit: (response: Record<string, any>) => void
  onSubmitCorrection: (event: React.FormEvent) => void; editingResponse: Record<string, any> | null
  setEditingResponse: (value: Record<string, any> | null) => void
  correctionText: string; setCorrectionText: (value: string) => void
  correctionNumber: string; setCorrectionNumber: (value: string) => void
  correctionPart: string; setCorrectionPart: (value: string) => void; correctionBusy: boolean
}) {
  const [focusEvidence, setFocusEvidence] = useState<Record<string, any> | null>(null)
  const analysis = objectOf(data.analysis)
  const questions = arrayOf<Record<string, any>>(analysis.questions)
  const paperQuestions = arrayOf<Record<string, any>>(analysis.paper_questions)
  const responses = arrayOf<Record<string, any>>(analysis.student_responses)
  const pointNames = new Map((curriculum?.points ?? []).map((point) => [point.id, point.name]))
  const paperMap = new Map<number, Record<string, any>>()
  for (const paper of paperQuestions) if (!paperMap.has(Number(paper.number))) paperMap.set(Number(paper.number), paper)
  const responseMap = new Map(responses.map((response) => [String(response.id), response]))
  const report = objectOf(data.teacher_report)
  const pageNumbers = new Map<string, number>()
  const roleCounts: Record<string, number> = {}
  for (const page of data.pages ?? []) {
    if (page.parent_page_id) continue
    roleCounts[page.role] = (roleCounts[page.role] ?? 0) + 1
    pageNumbers.set(page.page_id, roleCounts[page.role])
  }
  for (const page of data.pages ?? []) if (page.parent_page_id && pageNumbers.has(page.parent_page_id)) pageNumbers.set(page.page_id, pageNumbers.get(page.parent_page_id)!)
  const recognized = new Set(questions.map((question) => Number(question.number)))
  const missingNumbers = arrayOf<number>(analysis.expected_question_numbers).filter((number) => !recognized.has(Number(number)))
  const warnings = arrayOf<string>(analysis.warnings)

  return <section className="analysis-results">
    <div className="section-heading results-heading"><h2><span className="step-no">3</span>分析结果</h2>{onPractice && <Button onClick={onPractice} icon={<BookOpenCheck size={16} />}>用这次考试组卷</Button>}</div>
    <p className="analysis-caution">“错误”只代表本次考试的作答结果，不等于没有掌握。无法确定的内容标为“未确定”或“待核对”，请对照原图。</p>
    {report && Object.keys(report).length > 0 && <TeacherReport report={report} pointNames={pointNames} />}
    <div className="analysis-totals"><span><small>已识别题目</small><b>{questions.length} / {arrayOf<number>(analysis.expected_question_numbers).length || questions.length}</b></span><span><small>逐题得分合计</small><b>{analysis.total_awarded ?? '未确定'} / {analysis.total_maximum ?? '未确定'}</b></span><span><small>考试日期</small><b>{analysis.exam_date ? dateLabel(String(analysis.exam_date)) : '未提供'}</b></span></div>
    {arrayOf<Record<string, any>>(analysis.observed_totals).length > 0 && <div className="observed-totals"><b>卷面上写的总分（不用于推算每题得分）</b>{arrayOf<Record<string, any>>(analysis.observed_totals).map((row, index) => <span key={index}>{String(row.literal)}：{row.value ?? row.mark}</span>)}</div>}
    {(missingNumbers.length > 0 || warnings.length > 0) && <div className="recognition-notes"><b><AlertCircle size={15} />识别提示</b><ul>{missingNumbers.length > 0 && <li>未识别到第 {missingNumbers.join('、')} 题，下方没有这些题的结果，请对照原卷。</li>}{warnings.map((warning, index) => <li key={index}><MathText text={String(warning)} /></li>)}</ul></div>}
    <div className="question-results">{questions.map((question) => {
      const number = Number(question.number)
      const paper = paperMap.get(number)
      const evidenceIds = arrayOf<string>(objectOf(question.association).response_ids)
      const qResponses = evidenceIds.map((id) => responseMap.get(id)).filter((item): item is Record<string, any> => Boolean(item))
      const points = arrayOf<string>(question.point_ids).map((id) => pointNames.get(id) ?? id)
      const evidenceRows = arrayOf<Record<string, any>>(question.evidence)
      const evidenceKeySet = new Set(evidenceRows.map((row) => JSON.stringify(row)))
      for (const response of qResponses) {
        for (const evidence of arrayOf<Record<string, any>>(response.sources)) {
          const key = JSON.stringify(evidence)
          if (!evidenceKeySet.has(key)) { evidenceRows.push(evidence); evidenceKeySet.add(key) }
        }
      }
      return <article className="question-result" key={number}>
        <div className="question-result-header"><div className="question-head-copy"><b>第 {number} 题</b><small>{associationLabel(String(objectOf(question.association).status ?? 'unmatched'))}</small></div><span className={`outcome-pill outcome-${question.outcome ?? 'unknown'}`}>{outcomeLabel(String(question.outcome ?? 'unknown'))}</span></div>
        <div className="question-result-body">
          <p className="question-stem-preview"><MathText text={String(paper?.text ?? '未能从试卷中识别题干')} /></p>
          <div className="question-meta-row"><span><small>得分</small><b>{question.awarded ?? '—'} / {question.maximum ?? '—'}</b></span><span><small>失分</small><b>{question.deducted ?? '—'}</b></span><span className="question-points"><small>相关考点</small><b>{points.length ? points.join('、') : '未确定'}</b></span></div>
          {question.reason && <p className="question-reason"><b>分析</b><MathText text={String(question.reason)} /> <i>{reasonStatus(String(question.reason_status ?? 'unknown'))}</i></p>}
          {evidenceRows.length > 0 && <div className="evidence-links"><b>查看原图</b>{evidenceRows.map((evidence, index) => <button key={`${evidence.page_id}-${index}`} onClick={() => setFocusEvidence(evidence)}><FileSearch2 size={13} />{evidence.kind === 'printed_question' ? '试卷' : evidence.kind === 'handwriting' ? '作答' : evidence.kind === 'grading' ? '批改' : evidence.kind === 'reference' ? '参考答案' : '页眉'} · {pageNumbers.has(String(evidence.page_id)) ? `第 ${pageNumbers.get(String(evidence.page_id))} 页` : '页码未知'}{evidence.excerpt ? <> · <MathText className="evidence-excerpt" text={String(evidence.excerpt)} /></> : null}</button>)}</div>}
          {qResponses.length > 0 ? <div className="response-snippets">{qResponses.map((response) => <div className="response-snippet" key={String(response.id)}><span className="response-label">作答 {String(response.part_path || '整题')} · {legibilityLabel(String(response.legibility ?? 'partial'))}</span><p><MathText text={String(response.transcript || '未能识别作答文字')} /></p><button className="text-action" onClick={() => onEdit(response)}>修正识别</button></div>)}</div> : <p className="no-response-note">未找到本题对应的作答，不因此按零分处理。</p>}
          {arrayOf<Record<string, any>>(question.part_diagnoses).length > 0 && <details className="subquestion-details"><summary>小问分析 <ChevronDown size={14} /></summary><div>{arrayOf<Record<string, any>>(question.part_diagnoses).map((part, index) => <div className="part-row" key={`${part.part_path}-${index}`}><b>{String(part.part_path || '整题')}</b><span>{outcomeLabel(String(part.outcome ?? 'unknown'))} · {reasonStatus(String(part.reason_status ?? 'unknown'))}</span><p><MathText text={String(part.reason || '原因未确定')} /></p></div>)}</div></details>}
        </div>
      </article>
    })}</div>
    {focusEvidence && <SourceImageModal examId={examId} evidence={focusEvidence} pages={data.pages ?? []} onClose={() => setFocusEvidence(null)} />}
    {editingResponse && <ModalFrame className="modal-card correction-modal" labelledBy="correction-title" onClose={() => setEditingResponse(null)}><div className="section-heading"><h2 id="correction-title">修正作答识别</h2><button className="icon-button" onClick={() => setEditingResponse(null)} aria-label="关闭"><X size={17} /></button></div><p className="section-intro">只修改这一条作答的文字、题号或小问。提交后生成新的分析，原分析保留。</p><form onSubmit={onSubmitCorrection}><div className="two-fields"><Field label="修正后的题号"><input type="number" min="1" max="300" required value={correctionNumber} onChange={(e) => setCorrectionNumber(e.target.value)} /></Field><Field label="小问（可留空）"><input value={correctionPart} onChange={(e) => setCorrectionPart(e.target.value)} placeholder="例如 1/2" /></Field></div><Field label="作答文字" hint="公式用 $...$ 包围，例如 $x^2-1$"><textarea rows={5} value={correctionText} onChange={(e) => setCorrectionText(e.target.value)} /></Field>{correctionText.trim() && <div className="correction-preview"><span>预览</span><p><MathText text={correctionText} /></p></div>}<p className="correction-evidence-note">改题号或小问后，这条作答会关联到对应的原题。</p><div className="form-foot"><Button variant="quiet" onClick={() => setEditingResponse(null)}>取消</Button><Button type="submit" disabled={correctionBusy} icon={correctionBusy ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}>{correctionBusy ? '提交中…' : '提交修正'}</Button></div></form></ModalFrame>}
  </section>
}

function associationLabel(value: string) { return ({ linked: '已找到作答', ambiguous: '作答对应待核对', unmatched: '未找到作答' } as Record<string, string>)[value] ?? '关联待确认' }
function outcomeLabel(value: string) { return ({ correct: '正确', incorrect: '错误', partial: '部分得分', unanswered: '未作答', unknown: '未确定' } as Record<string, string>)[value] ?? '未确定' }
function reasonStatus(value: string) { return ({ supported: '有作答依据', hypothesis: '推测，待核对', unknown: '原因未确定' } as Record<string, string>)[value] ?? '原因未确定' }
function legibilityLabel(value: string) { return ({ readable: '字迹清楚', partial: '部分看不清，请核对原图', illegible: '无法辨认', blank: '空白' } as Record<string, string>)[value] ?? '可辨度未确认' }

function SourceImageModal({ examId, evidence, pages, onClose }: {
  examId: string; evidence: Record<string, any>; pages: ExamAnalysisResponse['pages']; onClose: () => void
}) {
  const [src, setSrc] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let objectUrl = ''
    const pageId = String(evidence.page_id ?? '')
    apiBlob(`/exams/${encodeURIComponent(examId)}/analysis/pages/${encodeURIComponent(pageId)}/content`)
      .then((blob) => {
        if (!blob.type.startsWith('image/')) throw new Error('原图格式无法显示')
        objectUrl = URL.createObjectURL(blob)
        if (active) setSrc(objectUrl)
      })
      .catch((reason) => { if (active) setError(messageFor(reason)) })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [examId, evidence.page_id])
  const page = pages?.find((item) => item.page_id === evidence.page_id)
  const bbox = arrayOf<number>(evidence.bbox)
  return <ModalFrame className="modal-card image-modal" label="原卷证据区域" onClose={onClose}><div className="section-heading"><div><h2>{page?.role === 'paper' ? '试卷' : page?.role === 'response' ? '学生作答' : '参考答案'}原图{evidence.excerpt ? <>：<MathText text={String(evidence.excerpt)} /></> : null}</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={17} /></button></div><div className="source-image-stage">{src ? <div className="source-image-wrap"><img src={src} alt="考试材料原始页面" /><span className="source-evidence-box" style={{ left: `${(bbox[0] ?? 0) * 100}%`, top: `${(bbox[1] ?? 0) * 100}%`, width: `${((bbox[2] ?? 0) - (bbox[0] ?? 0)) * 100}%`, height: `${((bbox[3] ?? 0) - (bbox[1] ?? 0)) * 100}%` }} /></div> : error ? <div className="source-image-error"><CircleHelp size={22} /><b>原图暂不可用</b><span>{error}</span></div> : <Loading label="正在加载原图…" />}</div>{src && <p className="source-image-caption">{bbox.length === 4 ? '红框为识别到的区域。' : '未提供区域位置，请查看整页。'}</p>}</ModalFrame>
}

function TeacherReport({ report, pointNames }: { report: Record<string, any>; pointNames: Map<string, string> }) {
  const strengths = arrayOf<Record<string, any>>(report.strengths)
  const priorities = arrayOf<Record<string, any>>(report.priorities)
  return <div className="teacher-report"><div className="report-overview"><div><h3 className="report-title">总结</h3><p><MathText text={String(report.overview ?? '暂无总结，请查看下方逐题结果。')} /></p></div></div>
    {(strengths.length > 0 || priorities.length > 0) && <div className="report-columns">
      {strengths.length > 0 && <div className="report-column report-strengths"><h3><span />掌握较好</h3>{strengths.map((item, index) => <ReportPoint key={index} item={item} pointNames={pointNames} />)}</div>}
      {priorities.length > 0 && <div className="report-column report-priorities"><h3><span />需要加强</h3>{priorities.map((item, index) => <ReportPoint key={index} item={item} pointNames={pointNames} />)}</div>}
    </div>}
    {arrayOf<string>(report.next_steps).length > 0 && <div className="next-steps"><b>建议</b><ul>{arrayOf<string>(report.next_steps).map((step, index) => <li key={index}><MathText text={String(step)} /></li>)}</ul></div>}
    {arrayOf<string>(report.unresolved).length > 0 && <div className="report-unresolved"><b>尚待核对</b><ul>{arrayOf<string>(report.unresolved).map((item, index) => <li key={index}><MathText text={String(item)} /></li>)}</ul></div>}
  </div>
}

function ReportPoint({ item, pointNames }: { item: Record<string, any>; pointNames: Map<string, string> }) {
  const pointIds = arrayOf<string>(item.point_ids)
  const labels = pointIds.map((id) => pointNames.get(id) ?? id)
  return <div className="report-point"><b>{String(item.title ?? '观察')}</b>{arrayOf<number>(item.question_numbers).length > 0 && <small>第 {arrayOf<number>(item.question_numbers).join('、')} 题</small>}{labels.length > 0 && <div className="report-tags">{labels.map((label) => <span key={label}>{label}</span>)}</div>}{item.observation && <p><MathText text={String(item.observation)} /></p>}{item.interpretation && <p className="report-interpretation"><MathText text={String(item.interpretation)} /></p>}{item.verification && <small className="report-verification">核对：<MathText text={String(item.verification)} /></small>}<i className={`report-status report-status-${String(item.status ?? 'unknown')}`}>{String(item.status ?? 'unknown') === 'confirmed' ? '有作答依据' : String(item.status ?? 'unknown') === 'hypothesis' ? '推测，待核对' : '未确定'}</i></div>
}

type FeedbackDraft = { expectation: string; quality: number | null; saved_minutes: number | null; reasons: string[]; comment: string }
// No pre-filled answers: default values would be submitted as if the teacher chose them.
const EMPTY_FEEDBACK: FeedbackDraft = { expectation: '', quality: null, saved_minutes: null, reasons: [], comment: '' }
function feedbackFrom(row: Record<string, any>): FeedbackDraft {
  return {
    expectation: ['yes', 'partial', 'no'].includes(String(row.expectation)) ? String(row.expectation) : '',
    quality: Number.isInteger(row.quality) ? Number(row.quality) : null,
    saved_minutes: Number.isInteger(row.saved_minutes) ? Number(row.saved_minutes) : null,
    reasons: arrayOf<string>(row.reasons),
    comment: String(row.comment ?? ''),
  }
}
function feedbackComplete(value: FeedbackDraft) {
  return Boolean(value.expectation) && value.quality !== null && value.saved_minutes !== null && Number.isInteger(value.saved_minutes)
}

type PracticeDraft = { mode: 'default' | 'advanced'; layout: 'answer_space' | 'compact'; pdfEngine: 'latex' | 'word'; examIds: string[]; quotas: Record<string, QuotaConfig> }
const draftKey = (studentId: string) => `teassistant:practice-draft:${studentId}`
function readPracticeDraft(studentId: string): PracticeDraft | null {
  if (!studentId) return null
  try { return JSON.parse(window.sessionStorage.getItem(draftKey(studentId)) ?? 'null') as PracticeDraft | null } catch { return null }
}
function writePracticeDraft(studentId: string, draft: PracticeDraft) {
  try { window.sessionStorage.setItem(draftKey(studentId), JSON.stringify(draft)) } catch { /* storage full or disabled: drafts are a convenience only */ }
}

type QuotaConfig = { selected: boolean; basic: number; intermediate: number; advanced: number }

function PracticeView({ students, selectedStudentId, exams, examsLoading, curriculum, practice, onSelectStudent, onPracticeChange, presetExamId, showResult, onShowResult, onShowSettings, onError, onNotice }: {
  students: Student[]; selectedStudentId: string; exams: Exam[]; examsLoading: boolean; curriculum: Curriculum | null
  practice: Practice | null; onSelectStudent: (id: string) => void
  onPracticeChange: (practice: Practice | null) => void
  presetExamId?: string; showResult: boolean; onShowResult: () => void; onShowSettings: () => void
  onError: (text: string) => void; onNotice: (kind: Notice['kind'], text: string) => void
}) {
  const [inventory, setInventory] = useState<InventoryPoint[]>([])
  const [inventoryFor, setInventoryFor] = useState('')
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<'default' | 'advanced'>('default')
  const [layout, setLayout] = useState<'answer_space' | 'compact'>('answer_space')
  const [pdfEngine, setPdfEngine] = useState<'latex' | 'word'>('latex')
  const [quotas, setQuotas] = useState<Record<string, QuotaConfig>>({})
  const [examIds, setExamIds] = useState<string[]>([])
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [job, setJob] = useState<Job | null>(null)
  const [practiceHistory, setPracticeHistory] = useState<Practice[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [feedback, setFeedback] = useState<FeedbackDraft>(EMPTY_FEEDBACK)
  const [practiceError, setPracticeError] = useState('')
  const [feedbackBusy, setFeedbackBusy] = useState(false)
  const practiceStudentRef = useRef(selectedStudentId)
  const practiceEpochRef = useRef(0)
  const practiceMountedRef = useRef(false)
  const practiceControllerRef = useRef<AbortController | null>(null)
  practiceStudentRef.current = selectedStudentId

  useEffect(() => {
    ++practiceEpochRef.current
    practiceMountedRef.current = true
    setCreating(false)
    setJob(null)
    setPracticeHistory([])
    setFeedback(EMPTY_FEEDBACK)
    setFeedbackBusy(false)
    setPracticeError('')
    return () => {
      practiceMountedRef.current = false
      practiceControllerRef.current?.abort()
    }
  }, [selectedStudentId])

  const followPractice = useCallback(async (started: { id: string; job_id: string }, studentId: string, epoch: number, controller: AbortController) => {
    const current = () => practiceMountedRef.current && practiceStudentRef.current === studentId
      && epoch === practiceEpochRef.current && !controller.signal.aborted
    if (!current()) return
    setCreating(true)
    onPracticeChange(null)
    try {
      const finished = await pollJob(started.job_id, (value) => { if (current()) setJob(value) }, controller.signal)
      if (!current()) return
      if (finished.status === 'failed') throw new Error(readableJobError(finished.error))
      const result = unwrap<Practice>(await api<Practice>(`/practices/${encodeURIComponent(started.id)}`, { signal: controller.signal }))
      if (!current()) return
      if (result.student_id !== studentId) throw new Error('练习卷所属学生不一致，请重新打开组卷记录')
      onPracticeChange(result)
      setFeedback(result.feedback ? feedbackFrom(objectOf(result.feedback)) : EMPTY_FEEDBACK)
      setPracticeHistory((rows) => [result, ...rows.filter((item) => item.id !== result.id)])
      onNotice(result.status === 'completed' ? 'success' : 'info', result.status === 'completed' ? '练习卷已生成' : '练习卷已生成，部分考点题量不足')
    } catch (error) {
      if (current()) { setPracticeError(messageFor(error)); onError(`组卷未完成：${messageFor(error)}`) }
    } finally { if (current()) setCreating(false) }
  }, [onPracticeChange, onNotice, onError])

  const selectedStudent = students.find((student) => student.id === selectedStudentId)
  const analyzedExams = exams.filter((exam) => ['completed', 'partial'].includes(exam.analysis_job?.status ?? ''))

  useEffect(() => {
    setInventory([])
    setInventoryFor('')
    if (!selectedStudentId) return
    let active = true
    setLoading(true)
    api<{ points: InventoryPoint[] }>(`/inventory?student_id=${encodeURIComponent(selectedStudentId)}`)
      .then((result) => {
        if (!active) return
        const points = arrayOf<InventoryPoint>(result.points)
        setInventory(points)
        const saved = readPracticeDraft(selectedStudentId)?.quotas ?? {}
        setInventoryFor(selectedStudentId)
        setQuotas(Object.fromEntries(points.map((point) => [point.point_id, saved[point.point_id] ?? { selected: false, basic: 4, intermediate: 4, advanced: 2 }])))
      })
      .catch((error) => { if (active) onError(`可用题数加载失败：${messageFor(error)}`) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [selectedStudentId, onError])

  useEffect(() => {
    if (!selectedStudentId) {
      setPracticeHistory([])
      onPracticeChange(null)
      return
    }
    let active = true
    setHistoryLoading(true)
    onPracticeChange(null)
    setPracticeError('')
    api<unknown[]>(`/students/${encodeURIComponent(selectedStudentId)}/practices`)
      .then((rows) => {
        if (!active || (practiceControllerRef.current && !practiceControllerRef.current.signal.aborted)) return
        const saved = arrayOf<unknown>(rows).map((row) => unwrap<Practice>(row))
        setPracticeHistory(saved)
        const latestPublished = [...saved].reverse().find((item) => ['completed', 'partial'].includes(item.status))
        const pending = [...saved].reverse().find((item) => ['queued', 'running'].includes(item.status) && item.job_id)
        const recent = pending ?? latestPublished ?? saved.at(-1) ?? null
        if (pending?.job_id) {
          const controller = new AbortController()
          practiceControllerRef.current = controller
          void followPractice({ id: pending.id, job_id: pending.job_id }, selectedStudentId, practiceEpochRef.current, controller)
          return
        }
        onPracticeChange(recent)
        if (recent?.feedback) {
          const row = objectOf(recent.feedback)
          setFeedback(feedbackFrom(row))
        } else {
          setFeedback(EMPTY_FEEDBACK)
        }
      })
      .catch((error) => { if (active) onError(`历史练习加载失败：${messageFor(error)}`) })
      .finally(() => { if (active) setHistoryLoading(false) })
    return () => { active = false }
  }, [selectedStudentId, onPracticeChange, onError, followPractice])

  useEffect(() => { if (!examsLoading) setExamIds((current) => current.filter((id) => analyzedExams.some((exam) => exam.id === id))) }, [examsLoading, exams])
  // Settings survive switching tabs: restore this student's draft once their exams are loaded, then keep it updated.
  const draftRestoredFor = useRef('')
  useEffect(() => {
    draftRestoredFor.current = ''
    const draft = readPracticeDraft(selectedStudentId)
    // Each student has their own settings; without a saved draft, start from the defaults.
    setMode(draft?.mode ?? 'default'); setLayout(draft?.layout ?? 'answer_space'); setPdfEngine(draft?.pdfEngine ?? 'latex')
    if (!draft) setExamIds([])
  }, [selectedStudentId])
  useEffect(() => {
    if (!selectedStudentId || draftRestoredFor.current === selectedStudentId || examsLoading) return
    if (exams.some((exam) => exam.student_id !== selectedStudentId)) return
    const draft = readPracticeDraft(selectedStudentId)
    if (draft) setExamIds(draft.examIds.filter((id) => analyzedExams.some((exam) => exam.id === id)))
    draftRestoredFor.current = selectedStudentId
  }, [exams, selectedStudentId, examsLoading])
  useEffect(() => {
    if (!selectedStudentId || draftRestoredFor.current !== selectedStudentId) return
    // Until this student's available points are loaded, keep the saved point choices instead of overwriting them.
    const selected = inventoryFor === selectedStudentId
      ? Object.fromEntries(Object.entries(quotas).filter(([, quota]) => quota.selected))
      : readPracticeDraft(selectedStudentId)?.quotas ?? {}
    writePracticeDraft(selectedStudentId, { mode, layout, pdfEngine, examIds, quotas: selected })
  }, [selectedStudentId, mode, layout, pdfEngine, examIds, quotas, inventoryFor])
  // Coming from an exam (“用这次考试组卷”) preselects that exam once it is loaded and analysed.
  useEffect(() => {
    if (presetExamId && analyzedExams.some((exam) => exam.id === presetExamId)) setExamIds((current) => current.includes(presetExamId) ? current : [...current, presetExamId])
  }, [presetExamId, exams])

  const selectedPoints = inventory.filter((point) => quotas[point.point_id]?.selected && selectedStudent?.learned_point_ids.includes(point.point_id))
  const requestedTotal = selectedPoints.reduce((sum, point) => {
    const quota = quotas[point.point_id]
    return sum + (mode === 'default' ? 10 : quota.basic + quota.intermediate + quota.advanced)
  }, 0)
  const visiblePoints = inventory.filter((point) => `${point.name} ${point.point_id}`.toLowerCase().includes(query.toLowerCase()))
  const studentQuery = selectedStudentId ? `?s=${encodeURIComponent(selectedStudentId)}` : ''
  // Each unmet condition for “生成练习卷”, with a direct link where the teacher can fix it.
  const missingConditions: { text: string; href?: string }[] = [
    ...(historyLoading ? [{ text: '正在读取历史练习，请稍候' }] : []),
    ...(!selectedStudent ? [{ text: '选择学生' }] : []),
    ...(!curriculum ? [{ text: '考点目录未加载，请刷新页面' }] : []),
    ...(selectedStudent && selectedStudent.learned_point_ids.length === 0 ? [{ text: '先在学生档案勾选已学考点', href: `#/students/${encodeURIComponent(selectedStudent.id)}` }] : []),
    ...(selectedStudent && analyzedExams.length === 0 ? [{ text: '先完成一场考试分析', href: `#/exam${studentQuery}` }] : []),
    ...(selectedStudent && analyzedExams.length > 0 && examIds.length === 0 ? [{ text: '在“1 参考考试”中选一场考试' }] : []),
    ...(selectedStudent && selectedStudent.learned_point_ids.length > 0 && selectedPoints.length === 0 ? [{ text: '在“2 考点”中至少选一个考点' }] : []),
  ]
  const canCreate = Boolean(selectedStudent && curriculum && selectedStudent.learned_point_ids.length && examIds.length && selectedPoints.length && !creating && !historyLoading)

  const setQuota = (pointId: string, patchValue: Partial<QuotaConfig>) => setQuotas((current) => ({ ...current, [pointId]: { ...current[pointId], ...patchValue } }))
  const createPractice = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canCreate) return
    if (mode === 'advanced' && selectedPoints.some((point) => {
      const quota = quotas[point.point_id]
      return quota.basic + quota.intermediate + quota.advanced === 0
    })) { onError('每个已选考点的进阶配额合计至少为 1'); return }
    const studentId = selectedStudentId
    const epoch = practiceEpochRef.current
    practiceControllerRef.current?.abort()
    const controller = new AbortController()
    practiceControllerRef.current = controller
    const current = () => practiceMountedRef.current && practiceStudentRef.current === studentId
      && epoch === practiceEpochRef.current && !controller.signal.aborted
    setCreating(true)
    setPracticeError('')
    onPracticeChange(null)
    try {
      const requestQuotas = selectedPoints.map((point) => {
        const quota = quotas[point.point_id]
        return mode === 'default'
          ? { point_id: point.point_id, count: 10, reference_score: 50 }
          : { point_id: point.point_id, count: quota.basic + quota.intermediate + quota.advanced, reference_score: 50, by_difficulty: { basic: quota.basic, intermediate: quota.intermediate, advanced: quota.advanced } }
      })
      const started = await api<{ id: string; job_id: string; status: string }>('/practices', {
        method: 'POST', body: JSON.stringify({ student_id: selectedStudentId, exam_ids: examIds, quotas: requestQuotas, seed: 0, layout, pdf_engine: pdfEngine }),
      })
      if (!current()) return
      onNotice('info', '已提交组卷')
      onShowResult()
      await followPractice(started, studentId, epoch, controller)
    } catch (error) { if (current()) { setPracticeError(messageFor(error)); onError(`组卷未完成：${messageFor(error)}`) } }
    finally { if (current()) setCreating(false) }
  }

  const sendFeedback = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!practice || practice.student_id !== selectedStudentId) return
    const studentId = selectedStudentId
    const epoch = practiceEpochRef.current
    const current = () => practiceMountedRef.current && practiceStudentRef.current === studentId && epoch === practiceEpochRef.current
    setFeedbackBusy(true)
    try {
      if (!feedbackComplete(feedback)) { onError('请先选择是否符合预期、质量评分并填写节省时间'); return }
      const result = await api<Record<string, any>>(`/practices/${encodeURIComponent(practice.id)}/feedback`, { method: 'POST', body: JSON.stringify(feedback) })
      if (!current()) return
      onPracticeChange({ ...practice, feedback: result })
      onNotice('success', '反馈已提交')
    } catch (error) { if (current()) onError(`反馈未提交：${messageFor(error)}`) }
    finally { if (current()) setFeedbackBusy(false) }
  }

  return <>
    <div className="page-heading"><div><h1>组卷</h1><p>按考点从题库选题，生成学生卷和教师卷。</p></div></div>
    <div className={`builder-layout ${showResult ? 'show-result' : 'show-settings'}`}>
      <form id="practice-form" className="builder-panel panel" onSubmit={createPractice}>
        <div className="section-heading"><h2>组卷设置</h2></div>
        <Field label="学生"><select value={selectedStudentId} onChange={(e) => { onSelectStudent(e.target.value); onPracticeChange(null) }}><option value="">请选择学生</option>{students.map((student) => <option key={student.id} value={student.id}>{student.name} · {gradeLabel(student.grade)}</option>)}</select></Field>
        {!selectedStudent ? students.length === 0 ? <div className="inline-warning"><CircleHelp size={16} /><span>还没有学生。先新增学生并勾选已学考点，再组卷。<a className="button button-primary button-md inline-cta" href="#/students/new">新增学生</a></span></div> : <div className="inline-warning"><CircleHelp size={16} /> 请先选择学生。</div> : <>
          <div className="builder-subhead"><div><h3><span className="step-no">1</span>参考考试</h3><p>至少选一场已完成分析的考试，随练习卷一起记录</p></div><span>已选 {examIds.length} 场</span></div>
          <div className="exam-signal-list">{exams.map((exam) => {
            const done = ['completed', 'partial'].includes(exam.analysis_job?.status ?? '')
            const checked = examIds.includes(exam.id)
            return <label key={exam.id} className={`exam-signal ${checked ? 'checked' : ''} ${!done ? 'disabled-row' : ''}`}><input type="checkbox" disabled={!done} checked={checked} onChange={() => setExamIds((current) => checked ? current.filter((id) => id !== exam.id) : [...current, exam.id])} /><span className="check-box">{checked && <Check size={12} />}</span><span className="signal-copy"><b>{exam.title}</b><small>{dateLabel(exam.exam_date)}{exam.analysis_job ? ` · ${homeStatusText(exam.analysis_job.status)}` : ''}</small></span><span className={`mini-status ${done ? 'mini-status-done' : ''}`}>{done ? '可选' : exam.analysis_job?.status === 'failed' ? '分析失败，不可选' : exam.analysis_job ? '分析未完成，不可选' : '未分析，不可选'}</span></label>
          })}{exams.length === 0 && <p className="small-empty">暂无考试记录。请先在“考试分析”上传并分析考试。</p>}</div>
          <div className="builder-divider" />
          <div className="builder-subhead"><div><h3><span className="step-no">2</span>考点</h3><p>只能选已学考点</p></div><div className="progress-shortcuts"><button type="button" onClick={() => setQuotas((current) => Object.fromEntries(Object.entries(current).map(([id, quota]) => [id, { ...quota, selected: selectedStudent.learned_point_ids.includes(id) }]))) }>全选已学</button><button type="button" onClick={() => setQuotas((current) => Object.fromEntries(Object.entries(current).map(([id, quota]) => [id, { ...quota, selected: false }]))) }>清空</button></div></div>
          <div className="inventory-toolbar"><div className="list-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索考点" /></div><span>已选 {selectedPoints.length} 个</span></div>
          {loading ? <Loading label="正在加载可用题数…" /> : inventory.length === 0 ? <div className="inline-warning"><CircleHelp size={16} /> 题库中没有可用于该学生的题目。</div> : <div className="inventory-list">{visiblePoints.map((point) => {
            const quota = quotas[point.point_id] ?? { selected: false, basic: 4, intermediate: 4, advanced: 2 }
            const learned = selectedStudent.learned_point_ids.includes(point.point_id)
            return <div key={point.point_id} className={`inventory-row ${quota.selected ? 'selected' : ''} ${!learned ? 'inventory-unlearned' : ''}`}>
              <label className="inventory-choice"><input type="checkbox" disabled={!learned} checked={quota.selected && learned} onChange={(e) => setQuota(point.point_id, { selected: e.target.checked })} /><span className="check-box">{quota.selected && <Check size={12} />}</span><span className="inventory-name"><b>{point.name}</b><small>{learned ? `可用 ${point.candidate_count} 题` : '未勾选为已学，不能选'}</small></span></label>
              {quota.selected && mode === 'advanced' && <div className="difficulty-quota-grid">{DIFFICULTIES.map((difficulty) => <label key={difficulty.key} className="quota-input"><span>{difficulty.label}<small>可用 {point.by_difficulty[difficulty.key] ?? 0}</small></span><input type="number" min="0" max="100" value={quota[difficulty.key]} onChange={(e) => setQuota(point.point_id, { [difficulty.key]: Math.max(0, Number(e.target.value) || 0) })} /></label>)}</div>}
            </div>
          })}</div>}
          <div className="builder-divider" />
          <div className="builder-subhead"><div><h3><span className="step-no">3</span>题量与版式</h3></div></div>
          <div className="mode-cards"><label className={`mode-card ${mode === 'default' ? 'active' : ''}`}><input type="radio" checked={mode === 'default'} onChange={() => setMode('default')} /><span className="mode-radio" /><b>默认</b><small>每个考点 10 题，优先选中等难度</small></label><label className={`mode-card ${mode === 'advanced' ? 'active' : ''}`}><input type="radio" checked={mode === 'advanced'} onChange={() => setMode('advanced')} /><span className="mode-radio" /><b>按难度设置</b><small>每个考点分别设基础、提高、挑战题数</small></label></div>
          <div className="layout-choice"><span>学生卷版式</span><label><input type="radio" checked={layout === 'answer_space'} onChange={() => setLayout('answer_space')} /> 作答版</label><label><input type="radio" checked={layout === 'compact'} onChange={() => setLayout('compact')} /> 紧凑版</label></div>
          <details className="more-settings"><summary>更多设置（PDF 排版方式）</summary><Field label="PDF 排版方式" hint="默认即可。排版不理想时可换另一种方式重新生成。"><select value={pdfEngine} onChange={(e) => setPdfEngine(e.target.value as 'latex' | 'word')}><option value="latex">公式统一排版（LaTeX）</option><option value="word">按 Word 模板转换</option></select></Field></details>
          {selectedPoints.length > 0 && <div className="quota-summary"><span>计划题量</span><b>{requestedTotal}<small> 题</small></b><span>{selectedPoints.length} 个考点</span></div>}
          <p className="progress-note">只选用考点及前置考点都已学的题。某考点题数不够时会列出缺口，不用超纲题补足。</p>
          <div className="sticky-actions practice-submit">
            {!creating && missingConditions.length > 0 && <p className="missing-conditions"><AlertCircle size={16} /><span>还差：{missingConditions.map((item, index) => <React.Fragment key={item.text}>{index > 0 && '；'}{item.href ? <a href={item.href}>{item.text}</a> : item.text}</React.Fragment>)}</span></p>}
            <Button type="submit" disabled={!canCreate} className="full-width builder-submit button-lg" icon={creating ? <LoaderCircle className="spin" size={18} /> : undefined}>{creating ? '组卷中…' : '生成练习卷'}</Button>
          </div>
        </>}
      </form>
      <section className="panel preview-panel"><div className="section-heading"><h2>练习卷</h2>{showResult && <button type="button" className="text-action phone-only" onClick={onShowSettings}>修改设置</button>}</div>
        {practice && practiceHistory.length > 1 && <label className="practice-history-select"><span>历史练习</span><select value={practice.id} onChange={(event) => {
          const selected = practiceHistory.find((item) => item.id === event.target.value)
          if (!selected) return
          onPracticeChange(selected)
          const row = objectOf(selected.feedback)
          setFeedback(selected.feedback ? feedbackFrom(row) : EMPTY_FEEDBACK)
        }}>{practiceHistory.map((item, index) => <option key={item.id} value={item.id}>第 {index + 1} 份（{statusText(item.status)}）</option>)}</select></label>}
        {!practice ? historyLoading ? <Loading label="正在加载最近的练习…" /> : creating && job ? <><JobProgress job={job} /><div className="preview-processing"><div className="processing-orbit"><LoaderCircle className="spin" size={34} /></div><b>正在组卷</b><p>完成后在这里显示题目、题量缺口和下载文件。</p></div></> : practiceError ? <div className="practice-failed"><AlertCircle size={18} /><div><b>组卷失败</b><p>{practiceError}</p><button type="submit" form="practice-form" className="button button-secondary button-sm" disabled={!canCreate}>按当前设置重新生成</button></div></div> : <EmptyState icon={<BookOpenCheck />} title="还没有练习卷" detail="完成组卷设置后点击“生成练习卷”。" action={showResult ? <Button onClick={onShowSettings}>去组卷设置</Button> : undefined} /> : <PracticeResult practice={practice} pointNames={new Map((curriculum?.points ?? []).map((point) => [point.id, point.name]))} feedback={feedback} onFeedbackChange={(patchValue) => setFeedback((current) => ({ ...current, ...patchValue }))} onSubmit={sendFeedback} feedbackBusy={feedbackBusy} />}
      </section>
    </div>
  </>
}

function PracticeResult({ practice, pointNames, feedback, onFeedbackChange, onSubmit, feedbackBusy }: {
  practice: Practice; pointNames: Map<string, string>; feedback: FeedbackDraft
  onFeedbackChange: (value: Partial<typeof feedback>) => void; onSubmit: (event: React.FormEvent) => void; feedbackBusy: boolean
}) {
  const preview = objectOf(practice.preview)
  const items = arrayOf<Record<string, any>>(preview.items)
  const snapshotItems = arrayOf<Record<string, any>>(objectOf(practice.snapshot).items)
  const shortfalls = arrayOf<Record<string, any>>(objectOf(practice.snapshot).shortfalls)
  const warnings = arrayOf<string>(practice.warnings)
  const files = arrayOf<Record<string, any>>(practice.files)
  const reasons = ['题目难度不合适', '考点不匹配', '题量不足', '答案需要核对', '导出格式问题']
  const setReason = (reason: string) => onFeedbackChange({ reasons: feedback.reasons.includes(reason) ? feedback.reasons.filter((item) => item !== reason) : [...feedback.reasons, reason] })
  return <div className="practice-result">
    <div className="result-state-row"><span className={`status-pill status-${practice.status}`}>{statusText(practice.status)}</span></div>
    {files.length > 0 && <div className="download-hero">{[...files].sort((a, b) => Number(isPdfFile(b)) - Number(isPdfFile(a))).map((file, index) => {
      const href = safeApiHref(String(file.url ?? ''))
      const pdf = isPdfFile(file)
      const label = String(file.label ?? '导出文件')
      return href
        ? <a key={String(file.id ?? index)} href={href} className={`button ${pdf ? 'button-primary' : 'button-secondary'} ${pdf ? 'button-lg' : 'button-md'} download-button`}><Download size={pdf ? 22 : 18} /><span>下载{label}</span></a>
        : <div key={String(file.id ?? index)} className="download-missing"><AlertCircle size={16} /> {label}暂时无法下载，请重新生成</div>
    })}</div>}
    {(warnings.length > 0 || shortfalls.length > 0) && <div className="shortfall-box"><b><AlertCircle size={15} /> 需要注意</b>{warnings.map((warning, index) => <p key={`w-${index}`}>{warning}</p>)}{shortfalls.map((item, index) => <p key={`s-${index}`}>{pointNames.get(String(item.point_id)) ?? String(item.point_id)} · {item.difficulty ? `${difficultyLabel(String(item.difficulty))} ` : ''}需要 {String(item.requested)} 题，只选到 {String(item.selected)} 题</p>)}</div>}
    {items.length > 0 ? <div className="preview-items"><div className="preview-items-title"><b>题目</b><span>{items.length} 道</span></div>{items.map((item, index) => <div className="preview-item" key={`${item.question_id}-${index}`}><div className="preview-item-head"><span>{String(item.order ?? index + 1).padStart(2, '0')}</span><b>{String(item.point_name ?? item.target_point_id ?? '考点')}</b><small>{difficultyLabel(String(item.difficulty ?? ''))}</small></div><div className="preview-content-box"><SanitizedPreview html={String(item.question_html ?? '')} /><details><summary>答案与解析</summary><SanitizedPreview html={String(item.answer_html ?? '')} /></details></div></div>)}</div> : snapshotItems.length > 0 ? <div className="preview-items"><div className="preview-items-title"><b>题目</b><span>{snapshotItems.length} 道</span></div>{snapshotItems.map((item, index) => <QuestionContent key={String(item.order ?? index)} item={item} index={index} />)}</div> : <EmptyState icon={<FileCheck2 />} title="没有选到题目" detail="请查看上方的题量缺口和提示。" />}
    <form className="feedback-form" onSubmit={onSubmit}><div className="feedback-heading"><div><b>这份练习卷符合预期吗？</b></div></div><div className="feedback-choice">{[['yes', '符合'], ['partial', '部分符合'], ['no', '不符合']].map(([value, label]) => <label key={value} className={feedback.expectation === value ? 'selected' : ''}><input type="radio" name="feedback-expectation" checked={feedback.expectation === value} onChange={() => onFeedbackChange({ expectation: value })} />{label}</label>)}</div><div className="field"><span className="field-label">质量评分（1 最低，5 最高）</span><div className="feedback-choice">{[1, 2, 3, 4, 5].map((value) => <label key={value} className={feedback.quality === value ? 'selected' : ''}><input type="radio" name="feedback-quality" checked={feedback.quality === value} onChange={() => onFeedbackChange({ quality: value })} />{value}</label>)}</div></div><Field label="节省备课时间（分钟）" hint="与自己备同样一份练习相比；多花了时间填负数"><input type="number" min="-600" max="600" required value={feedback.saved_minutes ?? ''} onChange={(e) => onFeedbackChange({ saved_minutes: e.target.value === '' ? null : Number(e.target.value) })} /></Field><div className="reason-options">{reasons.map((reason) => <label key={reason} className={feedback.reasons.includes(reason) ? 'selected' : ''}><input type="checkbox" checked={feedback.reasons.includes(reason)} onChange={() => setReason(reason)} />{reason}</label>)}</div><Field label="补充说明（选填）"><textarea rows={3} maxLength={2000} value={feedback.comment} onChange={(e) => onFeedbackChange({ comment: e.target.value })} placeholder="例如：第 3 题超纲、答案有误" /></Field><Button type="submit" size="sm" disabled={feedbackBusy || !feedbackComplete(feedback)} icon={feedbackBusy ? <LoaderCircle className="spin" size={14} /> : undefined}>{feedbackBusy ? '提交中…' : '提交反馈'}</Button></form>
  </div>
}

function isPdfFile(file: Record<string, any>) {
  return /pdf/i.test(String(file.label ?? '')) || /\.pdf$|_pdf$/i.test(String(file.url ?? ''))
}

function difficultyLabel(value: string) { return ({ basic: '基础', intermediate: '提高', advanced: '挑战' } as Record<string, string>)[value] ?? '难度未确认' }

function QuestionContent({ item, index }: { item: Record<string, any>; index: number }) {
  const question = objectOf(item.question)
  const renderNodes = (nodes: unknown) => arrayOf<Record<string, any>>(nodes).map((node, n) => node.kind === 'text' ? <p key={n}>{String(node.text)}</p> : node.kind === 'formula' ? <div className="formula-token" key={n}><MathBlock tex={String(node.latex)} /></div> : <div className="image-token" key={n}>[配图：此处无法预览，请查看导出文件]</div>)
  return <div className="preview-item"><div className="preview-item-head"><span>{String(item.order ?? index + 1).padStart(2, '0')}</span><b>{String(item.target_point_name ?? item.target_point_id ?? '考点')}</b><small>{question.difficulty_score === undefined ? '难度未确认' : `难度 ${question.difficulty_score}`}</small></div><div className="plain-content">{renderNodes(question.stem)}</div><details><summary>答案与解析</summary><div className="plain-content">{renderNodes(question.answer)}{renderNodes(question.solution)}</div></details></div>
}

function AdminView({ onNotice }: { onNotice: (kind: Notice['kind'], text: string) => void }) {
  const [tab, setTab] = useState<'users' | 'prompts' | 'billing' | 'bank'>('users')
  const [users, setUsers] = useState<User[]>([])
  const [prompts, setPrompts] = useState<Prompt[]>([])
  const [selectedPrompt, setSelectedPrompt] = useState<Prompt | null>(null)
  const [history, setHistory] = useState<PromptRevision[]>([])
  const [showHistory, setShowHistory] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [userForm, setUserForm] = useState({ username: '', display_name: '', password: '' })
  const [promptBody, setPromptBody] = useState('')

  const loadUsers = useCallback(async () => {
    const result = await api<unknown[]>('/admin/users')
    setUsers(arrayOf<unknown>(result).map((value) => unwrap<User>(value)))
  }, [])
  const loadPrompts = useCallback(async () => {
    const result = await api<unknown[]>('/admin/prompts')
    const rows = arrayOf<unknown>(result).map((value) => unwrap<Prompt>(value))
    setPrompts(rows)
    setSelectedPrompt((current) => current ? rows.find((row) => row.key === current.key) ?? null : rows[0] ?? null)
  }, [])

  useEffect(() => {
    let active = true
    setLoading(true)
    if (tab === 'billing' || tab === 'bank') { setLoading(false); return }
    const loader = tab === 'users' ? loadUsers : loadPrompts
    loader().catch((error) => { if (active) onNotice('error', `加载失败：${messageFor(error)}`) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [tab, loadUsers, loadPrompts, onNotice])

  useEffect(() => { setPromptBody(selectedPrompt?.body ?? '') }, [selectedPrompt?.key, selectedPrompt?.active_revision])

  const createUser = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      await api('/admin/users', { method: 'POST', body: JSON.stringify({ ...userForm, role: 'teacher' }) })
      setUserForm({ username: '', display_name: '', password: '' })
      await loadUsers()
      onNotice('success', '教师账号已创建')
    } catch (error) { onNotice('error', `账号未创建：${messageFor(error)}`) }
    finally { setSaving(false) }
  }

  const openHistory = async () => {
    if (!selectedPrompt) return
    try {
      const rows = await api<unknown[]>(`/admin/prompts/${encodeURIComponent(selectedPrompt.key)}/history`)
      setHistory(arrayOf<unknown>(rows).map((value) => unwrap<PromptRevision>(value)))
      setShowHistory(true)
    } catch (error) { onNotice('error', `历史版本加载失败：${messageFor(error)}`) }
  }

  const savePrompt = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedPrompt) return
    setSaving(true)
    try {
      const updated = unwrap<Prompt>(await api<Prompt>(`/admin/prompts/${encodeURIComponent(selectedPrompt.key)}`, {
        method: 'POST', body: JSON.stringify({ body: promptBody, expected_revision: selectedPrompt.active_revision }),
      }))
      setSelectedPrompt(updated)
      await loadPrompts()
      onNotice('success', '已保存为新版本，之后提交的任务使用新内容')
    } catch (error) { onNotice('error', `提示词未保存：${messageFor(error)}`) }
    finally { setSaving(false) }
  }

  const activateRevision = async (revision: PromptRevision) => {
    if (!selectedPrompt) return
    setSaving(true)
    try {
      const updated = unwrap<Prompt>(await api<Prompt>(`/admin/prompts/${encodeURIComponent(selectedPrompt.key)}/activate`, {
        method: 'POST', body: JSON.stringify({ revision: revision.revision, expected_revision: selectedPrompt.active_revision }),
      }))
      setSelectedPrompt(updated)
      setPromptBody(updated.body)
      await loadPrompts()
      setShowHistory(false)
      onNotice('success', '已启用该版本内容（保存为新版本）')
    } catch (error) { onNotice('error', `版本切换未完成：${messageFor(error)}`) }
    finally { setSaving(false) }
  }

  return <>
    <div className="page-heading"><div><h1>管理设置</h1><p>仅管理员可见。</p></div></div>
    <div className="admin-tabs"><button className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}>教师账号</button><button className={tab === 'prompts' ? 'active' : ''} onClick={() => setTab('prompts')}>提示词</button><button className={tab === 'billing' ? 'active' : ''} onClick={() => setTab('billing')}>模型与额度</button><button className={tab === 'bank' ? 'active' : ''} onClick={() => setTab('bank')}>题库</button></div>
    {tab === 'bank' ? <AdminBank onNotice={onNotice} /> : tab === 'billing' ? <AdminModelBilling onNotice={onNotice} /> : loading ? <div className="panel"><Loading label="正在加载…" /></div> : tab === 'users' ? <div className="admin-grid"><section className="panel admin-form-panel"><div className="section-heading"><h2>创建教师账号</h2></div><p className="section-intro">没有公开注册入口。教师只能看到自己名下的学生和记录。</p><form onSubmit={createUser}><Field label="登录账号"><input required maxLength={100} autoComplete="off" value={userForm.username} onChange={(e) => setUserForm({ ...userForm, username: e.target.value })} /></Field><Field label="教师显示名称"><input required maxLength={100} value={userForm.display_name} onChange={(e) => setUserForm({ ...userForm, display_name: e.target.value })} /></Field><Field label="初始密码"><input required autoComplete="new-password" type="password" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} /><small>创建后请自行告知教师；系统不保存明文密码。</small></Field><Button type="submit" disabled={saving} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}>{saving ? '创建中…' : '创建账号'}</Button></form></section><section className="panel admin-users-panel"><div className="section-heading"><h2>账号（{users.length}）</h2></div>{users.length ? users.map((user) => <div className="admin-user-row" key={user.id}><span className="avatar avatar-sm">{user.display_name.slice(0, 1) || '师'}</span><span><b>{user.display_name}</b><small>{user.username}</small></span><span className="role-badge">{user.role === 'admin' ? '管理员' : '教师'}</span></div>) : <EmptyState icon={<Users />} title="暂无账号" detail="在左侧创建教师账号。" />}</section></div> : <PromptAdmin prompts={prompts} selected={selectedPrompt} onSelect={setSelectedPrompt} body={promptBody} onBody={setPromptBody} onSave={savePrompt} onHistory={openHistory} saving={saving} />}
    {showHistory && <ModalFrame className="modal-card history-modal" labelledBy="history-title" onClose={() => setShowHistory(false)}><div className="section-heading"><div><h2 id="history-title">{selectedPrompt?.label}：历史版本</h2></div><button className="icon-button" onClick={() => setShowHistory(false)} aria-label="关闭"><X size={17} /></button></div><p className="section-intro">启用旧版本会复制其内容生成一个新版本，历史记录不会被覆盖。</p><div className="history-list">{history.map((item) => <div className={`history-row ${item.revision === selectedPrompt?.active_revision ? 'active' : ''}`} key={String(item.revision)}><div><b>版本 {String(item.revision)} {item.revision === selectedPrompt?.active_revision && <span className="active-label">使用中</span>}</b><small>{item.created_at ? dateLabel(item.created_at) : '时间未记录'} · {item.created_by || '系统'}</small><pre>{item.body}</pre></div>{item.revision !== selectedPrompt?.active_revision && <Button size="sm" variant="secondary" onClick={() => void activateRevision(item)} disabled={saving} icon={<RotateCcw size={14} />}>启用此版本</Button>}</div>)}</div></ModalFrame>}
  </>
}

function PromptAdmin({ prompts, selected, onSelect, body, onBody, onSave, onHistory, saving }: {
  prompts: Prompt[]; selected: Prompt | null; onSelect: (prompt: Prompt) => void; body: string; onBody: (text: string) => void
  onSave: (event: React.FormEvent) => void; onHistory: () => void; saving: boolean
}) {
  const [query, setQuery] = useState('')
  const visible = prompts.filter((prompt) => `${prompt.label} ${prompt.key}`.toLowerCase().includes(query.toLowerCase()))
  return <div className="prompt-layout"><section className="panel prompt-list-panel"><div className="section-heading"><h2>提示词</h2></div><div className="list-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索任务" /></div><div className="prompt-list">{visible.map((prompt) => <button key={prompt.key} onClick={() => onSelect(prompt)} className={`prompt-list-row ${selected?.key === prompt.key ? 'active' : ''}`}><span><b>{prompt.label}</b><small>{prompt.description}</small></span><span className="revision-mini">v{String(prompt.active_revision)}</span></button>)}</div></section><section className="panel prompt-editor-panel">{selected ? <><div className="section-heading"><div><h2>{selected.label}</h2><p className="muted">使用中：版本 {String(selected.active_revision)}</p></div><Button variant="secondary" size="sm" onClick={onHistory} icon={<ListChecks size={15} />}>查看历史</Button></div><p className="section-intro">{selected.description}</p><form onSubmit={onSave}><Field label="提示词正文"><textarea className="prompt-textarea" maxLength={20000} value={body} onChange={(e) => onBody(e.target.value)} rows={18} required /></Field><div className="prompt-char-count">{body.length.toLocaleString()} / 20,000 字符</div><div className="prompt-guardrails"><div><b>以下规则由系统强制执行，修改提示词不会改变：</b><p>输出格式校验、考点必须在目录内、学生已学范围、题量配额、同题去重、已明确的原题排除、账号权限、导出完整性。</p></div></div><div className="form-foot"><span className="revision-note">每次保存生成新版本</span><Button type="submit" disabled={saving || body.length > 20000} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}>{saving ? '保存中…' : '保存为新版本'}</Button></div></form></> : <EmptyState icon={<Settings2 />} title="未选择提示词" detail="在左侧选择要编辑的提示词。" />}</section></div>
}

