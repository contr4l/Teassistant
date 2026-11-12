import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AdminModelBilling from './AdminModelBilling'
import UsageGuide from './UsageGuide'
import { MathBlock, MathText } from './MathText'
import {
  Activity,
  AlertCircle,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BookOpenCheck,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  ClipboardList,
  CloudUpload,
  Download,
  FileCheck2,
  FileSearch2,
  FilePlus2,
  FileText,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  LogOut,
  Menu,
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
  formatBytes,
  getCurrentSession,
  Job,
  pollJob,
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

type View = 'home' | 'students' | 'exam' | 'practice' | 'admin'
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

const NAV_ITEMS: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'home', label: '工作台', icon: LayoutDashboard },
  { id: 'students', label: '学生档案', icon: Users },
  { id: 'exam', label: '考试分析', icon: ClipboardList },
  { id: 'practice', label: '组卷', icon: BookOpenCheck },
]

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
  const [view, setView] = useState<View>('home')
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const [usageGuideOpen, setUsageGuideOpen] = useState(false)
  const usageGuideTrigger = useRef<HTMLButtonElement>(null)
  const mobileMenuTrigger = useRef<HTMLButtonElement>(null)
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
  const [examsError, setExamsError] = useState('')
  const [examCreateRequest, setExamCreateRequest] = useState(0)
  const [examReload, setExamReload] = useState(0)

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
        setSelectedExamId((current) => current && normalized.some((exam) => exam.id === current)
          ? current : normalized[0]?.id ?? '')
      })
      .catch((error: unknown) => { if (active) setExamsError(messageFor(error)) })
      .finally(() => { if (active) setExamsLoading(false) })
    return () => { active = false }
  }, [session, selectedStudentId, examReload])

  const refreshExams = useCallback(async (studentId = selectedStudentId) => {
    if (!studentId) return [] as Exam[]
    const rows = await api<unknown[]>(`/students/${encodeURIComponent(studentId)}/exams`)
    const normalized = arrayOf<unknown>(rows).map(normalizeExam)
    setExams(normalized)
    return normalized
  }, [selectedStudentId])

  const login = async (username: string, password: string) => {
    setLoginBusy(true)
    setLoginError('')
    try {
      const value = await api<{ user: User; csrf_token: string }>('/auth/login', {
        method: 'POST', body: JSON.stringify({ username, password }),
      })
      setCsrfToken(value.csrf_token)
      setSession(value)
      setView('home')
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
  }

  if (authLoading) return <div className="boot-screen"><Loading /></div>
  if (!session) return <LoginView onLogin={login} busy={loginBusy} error={loginError} />

  const selectedStudent = students.find((student) => student.id === selectedStudentId) ?? null
  const selectedExam = exams.find((exam) => exam.id === selectedExamId) ?? null

  const navigate = (next: View) => { setView(next); setMobileNavOpen(false) }
  const closeUsageGuide = () => {
    setUsageGuideOpen(false)
    window.requestAnimationFrame(() => {
      const target = window.matchMedia('(max-width: 920px)').matches
        ? mobileMenuTrigger.current : usageGuideTrigger.current
      target?.focus()
    })
  }

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNavOpen ? 'sidebar-open' : ''}`}>
        <div className="sidebar-brand"><div className="brand-mark">题</div><div><b>Teassistant</b><small>教师工作台</small></div></div>
        <p className="workspace-tag">初中数学（湖南）</p>
        <nav className="main-nav" aria-label="主导航">
          {NAV_ITEMS.map(({ id, label, icon: Icon }) => (
            <button className={`nav-item ${view === id ? 'active' : ''}`} key={id} onClick={() => navigate(id)}>
              <Icon size={18} strokeWidth={1.8} /><span>{label}</span>{id === 'exam' && exams.length > 0 && <small>{exams.length}</small>}
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
          <button type="button" className="help-link" ref={usageGuideTrigger} aria-haspopup="dialog" aria-controls="usage-guide" aria-expanded={usageGuideOpen} onClick={() => { setMobileNavOpen(false); setUsageGuideOpen(true) }}><CircleHelp size={16} />了解使用方式</button>
          <button className="account-row" onClick={logout} title="退出登录">
            <span className="avatar avatar-sm">{session.user.display_name.slice(0, 1) || '师'}</span>
            <span className="account-copy"><b>{session.user.display_name}</b><small>{session.user.role === 'admin' ? '管理员' : '任课老师'}</small></span>
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      {mobileNavOpen && <button className="mobile-scrim" onClick={() => setMobileNavOpen(false)} aria-label="关闭导航" />}
      <main className="main-area">
        <header className="topbar">
          <button className="mobile-menu" ref={mobileMenuTrigger} onClick={() => setMobileNavOpen(true)} aria-label="打开导航"><Menu size={21} /></button>
          <div className="topbar-right">
            {selectedStudent && <span className="current-student">当前学生：<b>{selectedStudent.name}</b></span>}
            <span className="today-label">{new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date())}</span>
          </div>
        </header>
        <div className="page-wrap">
          {bootLoading && <div className="workspace-banner"><LoaderCircle className="spin" size={16} /> 正在加载学生和考点目录…{bootSlow && <span>加载较慢，可刷新页面重试。</span>}</div>}
          {!bootLoading && studentsError && <div className="workspace-banner banner-error"><AlertCircle size={16} /><span>学生列表加载失败：{studentsError}</span><button type="button" className="banner-retry" onClick={() => void loadWorkspace()}>重试</button></div>}
          {!bootLoading && !curriculum && <div className="workspace-banner banner-error"><AlertCircle size={16} /><span>考点目录加载失败，学生进度和组卷暂不可用。</span><button type="button" className="banner-retry" onClick={() => void loadWorkspace()}>重试</button></div>}
          {!bootLoading && examsError && <div className="workspace-banner banner-error"><AlertCircle size={16} /><span>考试记录加载失败：{examsError}</span><button type="button" className="banner-retry" onClick={() => setExamReload((value) => value + 1)}>重试</button></div>}
          {view === 'home' && !bootLoading && !studentsError && <HomeView
            user={session.user} students={students} exams={exams} examsLoading={examsLoading} examsError={examsError} curriculum={curriculum} selectedStudentId={selectedStudentId}
            onNewExam={() => { setExamCreateRequest((value) => value + 1); navigate('exam') }}
            onNavigate={navigate} onSelectStudent={setSelectedStudentId}
            onOpenExam={(exam) => { setSelectedStudentId(exam.student_id); setSelectedExamId(exam.id); navigate('exam') }}
            onCreateStudent={() => navigate('students')}
          />}
          {view === 'students' && <StudentsView
            students={students} curriculum={curriculum} selectedStudent={selectedStudent}
            onSelectStudent={setSelectedStudentId} onSaved={async (student) => {
              setStudents((rows) => rows.some((row) => row.id === student.id)
                ? rows.map((row) => row.id === student.id ? student : row)
                : [student, ...rows])
              setSelectedStudentId(student.id)
              notify('success', '学生资料与教学进度已保存')
            }} onError={(text) => notify('error', text)}
          />}
          {view === 'exam' && <ExamView
            students={students} exams={exams} examsLoading={examsLoading} createRequest={examCreateRequest} selectedStudentId={selectedStudentId} selectedExam={selectedExam}
            selectedStudent={selectedStudent} curriculum={curriculum}
            onSelectStudent={setSelectedStudentId} onSelectExam={setSelectedExamId}
            onCreated={async (exam) => {
              setSelectedExamId(exam.id)
              await refreshExams(exam.student_id)
              notify('success', '考试记录已创建，可以开始上传材料')
            }} onRefresh={refreshExams} notify={notify}
          />}
          {view === 'practice' && <PracticeView
            students={students} selectedStudentId={selectedStudentId} exams={exams} curriculum={curriculum}
            practice={practice} onSelectStudent={setSelectedStudentId} onPracticeChange={setPractice}
            onError={notifyError} onNotice={notify}
          />}
          {view === 'admin' && session.user.role === 'admin' && <AdminView onNotice={notify} />}
          {view === 'admin' && session.user.role !== 'admin' && <EmptyState icon={<ShieldCheck />} title="此区域仅管理员可见" detail="当前账号没有管理设置权限。" />}
        </div>
      </main>
      {usageGuideOpen && <UsageGuide onClose={closeUsageGuide} onNavigate={(next) => { closeUsageGuide(); navigate(next) }} />}
      {notice && <div className={`toast toast-${notice.kind}`} role="status"><span>{notice.kind === 'success' ? <CheckCircle2 size={18} /> : notice.kind === 'error' ? <AlertCircle size={18} /> : <Activity size={18} />}</span>{notice.text}<button onClick={() => setNotice(null)} aria-label="关闭提醒"><X size={16} /></button></div>}
    </div>
  )
}

export default App

function HomeView({ user, students, exams, examsLoading, examsError, curriculum, selectedStudentId, onNavigate, onNewExam, onSelectStudent, onOpenExam, onCreateStudent }: {
  user: User; students: Student[]; exams: Exam[]; examsLoading: boolean; examsError: string; curriculum: Curriculum | null; selectedStudentId: string
  onNavigate: (view: View) => void; onNewExam: () => void; onSelectStudent: (id: string) => void; onOpenExam: (exam: Exam) => void; onCreateStudent: () => void
}) {
  const hour = new Date().getHours()
  const greeting = hour < 11 ? '早上好' : hour < 18 ? '下午好' : '晚上好'
  const selectedStudent = students.find((student) => student.id === selectedStudentId) ?? null
  const needsReview = exams.filter((exam) => ['partial', 'failed'].includes(exam.analysis_job?.status ?? '')).length
  return (
    <>
      <div className="page-heading">
        <div><h1>工作台</h1><p>{greeting}，{user.display_name}</p></div>
        <div className="heading-actions">
          <Button variant="secondary" onClick={() => onNavigate('practice')} icon={<BookOpenCheck size={16} />}>组卷</Button>
          <Button onClick={onNewExam} icon={<Plus size={16} />}>新建考试</Button>
        </div>
      </div>
      {students.length === 0 ? <section className="panel"><EmptyState icon={<Users />} title="还没有学生" detail="先添加学生并勾选已学考点，再上传考试。" action={<Button size="sm" onClick={onCreateStudent} icon={<Plus size={14} />}>新增学生</Button>} /></section> : <div className="home-grid">
        <section className="panel home-exams">
          <div className="section-heading">
            <h2>考试记录</h2>
            <label className="inline-select"><span>学生</span><select value={selectedStudentId} onChange={(e) => onSelectStudent(e.target.value)}>{students.map((student) => <option key={student.id} value={student.id}>{student.name}（{gradeLabel(student.grade)}）</option>)}</select></label>
          </div>
          {needsReview > 0 && <p className="home-review-note"><AlertCircle size={15} />{needsReview} 场考试的分析含待核对项或失败，需要查看。</p>}
          {examsLoading ? <Loading label="正在加载考试记录…" /> : examsError ? <p className="inline-error">考试记录加载失败，请点击页面顶部的“重试”。</p> : exams.length ? <table className="record-table">
            <thead><tr><th>考试</th><th>日期</th><th className="num">材料</th><th>分析</th><th aria-label="操作" /></tr></thead>
            <tbody>{exams.map((exam) => <tr key={exam.id} onClick={() => onOpenExam(exam)}>
              <td className="record-title">{exam.title}</td>
              <td className="nowrap">{dateLabel(exam.exam_date)}</td>
              <td className="num">{exam.assets.length} 份</td>
              <td><span className={`status-pill status-${exam.analysis_job?.status ?? 'queued'}`}>{exam.analysis_job ? homeStatusText(exam.analysis_job.status) : '未分析'}</span></td>
              <td className="row-action"><button type="button" className="text-action" onClick={(event) => { event.stopPropagation(); onOpenExam(exam) }}>查看<ArrowRight size={14} /></button></td>
            </tr>)}</tbody>
          </table> : <div className="empty-inline"><span>{selectedStudent ? `${selectedStudent.name}暂无考试记录` : '暂无考试记录'}</span><Button size="sm" onClick={onNewExam} icon={<Plus size={14} />}>新建考试</Button></div>}
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

function StudentsView({ students, curriculum, selectedStudent, onSelectStudent, onSaved, onError }: {
  students: Student[]; curriculum: Curriculum | null; selectedStudent: Student | null
  onSelectStudent: (id: string) => void; onSaved: (student: Student) => void; onError: (text: string) => void
}) {
  const [creating, setCreating] = useState(false)
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState('')
  const [form, setForm] = useState({ name: '', school: '', grade: 7 as 7 | 8 | 9 })
  const [learned, setLearned] = useState<string[]>([])
  const pointMap = useMemo(() => new Map((curriculum?.points ?? []).map((point) => [point.id, point])), [curriculum])

  useEffect(() => {
    if (!selectedStudent || creating) return
    setForm({ name: selectedStudent.name, school: selectedStudent.school ?? '', grade: selectedStudent.grade })
    setLearned(selectedStudent.learned_point_ids)
  }, [selectedStudent, creating])

  const visibleStudents = students.filter((student) => `${student.name} ${student.school}`.toLowerCase().includes(query.toLowerCase()))
  const togglePoint = (id: string) => setLearned((values) => values.includes(id) ? values.filter((item) => item !== id) : [...values, id])
  const createStudent = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    try {
      const raw = await api<unknown>('/students', { method: 'POST', body: JSON.stringify({ ...form, learned_point_ids: [] }) })
      onSaved(normalizeStudent(raw))
      setCreating(false)
      setForm({ name: '', school: '', grade: 7 })
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
      onSaved(normalizeStudent(raw))
    } catch (error) { onError(`保存失败：${messageFor(error)}`) }
    finally { setSaving(false) }
  }

  return (
    <>
      <div className="page-heading"><div><h1>学生档案</h1><p>勾选的已学考点决定组卷范围。</p></div><Button onClick={() => { setCreating(true); onSelectStudent('') }} icon={<Plus size={16} />}>新增学生</Button></div>
      <div className="students-layout">
        <section className="panel students-list-panel">
          <div className="list-search"><Search size={16} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索姓名或学校" /></div>
          {students.length === 0 ? <EmptyState icon={<Users />} title="还没有学生" detail="添加学生后可记录已学考点和考试。" action={<Button size="sm" onClick={() => setCreating(true)} icon={<Plus size={14} />}>新增学生</Button>} /> : <div className="student-list">
            {visibleStudents.map((student) => <button key={student.id} className={`student-list-item ${!creating && selectedStudent?.id === student.id ? 'selected' : ''}`} onClick={() => { setCreating(false); onSelectStudent(student.id) }}>
              <span className="avatar">{student.name.slice(0, 1) || '学'}</span><span className="student-mini"><b>{student.name}</b><small>{student.school || '未填写学校'} · {gradeLabel(student.grade)}</small></span><ChevronDown className="student-list-chevron" size={16} />
            </button>)}
            {visibleStudents.length === 0 && <p className="list-empty">没有找到匹配的学生</p>}
          </div>}
          <div className="list-footer"><span>共 {students.length} 位学生</span></div>
        </section>
        <section className="student-editor panel">
          {creating ? <>
            <div className="section-heading"><h2>新增学生</h2><button className="icon-button" onClick={() => setCreating(false)} aria-label="取消新增"><X size={17} /></button></div>
            <p className="section-intro">已学考点可在创建后勾选；未勾选前不能组卷。</p>
            <form className="student-form" onSubmit={createStudent}>
              <div className="two-fields"><Field label="姓名 / 称呼"><input required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：小林" /></Field><Field label="年级"><select value={form.grade} onChange={(e) => setForm({ ...form, grade: Number(e.target.value) as 7 | 8 | 9 })}><option value={7}>初一</option><option value={8}>初二</option><option value={9}>初三</option></select></Field></div>
              <Field label="学校" hint="选填"><input maxLength={160} value={form.school} onChange={(e) => setForm({ ...form, school: e.target.value })} placeholder="学校名称" /></Field>
              <div className="form-foot"><Button variant="quiet" onClick={() => setCreating(false)}>取消</Button><Button type="submit" disabled={saving} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}>{saving ? '创建中…' : '创建'}</Button></div>
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
              <div className="form-foot"><Button type="submit" disabled={saving} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}>{saving ? '保存中…' : '保存'}</Button></div>
            </form>
          </> : <EmptyState icon={<UserRound />} title="未选择学生" detail="在左侧选择学生，或点击“新增学生”。" />}
        </section>
      </div>
    </>
  )
}

function ExamView({ students, exams, examsLoading, createRequest, selectedStudentId, selectedExam, selectedStudent, curriculum, onSelectStudent, onSelectExam, onCreated, onRefresh, notify }: {
  students: Student[]; exams: Exam[]; examsLoading: boolean; createRequest: number; selectedStudentId: string; selectedExam: Exam | null
  selectedStudent: Student | null; curriculum: Curriculum | null
  onSelectStudent: (id: string) => void; onSelectExam: (id: string) => void
  onCreated: (exam: Exam) => void; onRefresh: (studentId?: string) => Promise<Exam[]>
  notify: (kind: Notice['kind'], text: string) => void
}) {
  const [creating, setCreating] = useState(false)
  const [createBusy, setCreateBusy] = useState(false)
  const [examForm, setExamForm] = useState({ title: '', exam_date: todayIso(), question_count: '', total_score: '', max_score: '', class_rank: '', grade_rank: '' })
  const [uploadRole, setUploadRole] = useState<'paper' | 'response' | 'reference'>('paper')
  const [uploadBusy, setUploadBusy] = useState(false)
  const [orderBusy, setOrderBusy] = useState(false)
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [provider, setProvider] = useState<'deepseek' | 'codex'>('deepseek')
  const [allowCodex, setAllowCodex] = useState(false)
  const [activeJob, setActiveJob] = useState<Job | null>(null)
  const [analysisData, setAnalysisData] = useState<ExamAnalysisResponse | null>(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)
  const [editingResponse, setEditingResponse] = useState<Record<string, any> | null>(null)
  const [correctionText, setCorrectionText] = useState('')
  const [correctionNumber, setCorrectionNumber] = useState('')
  const [correctionPart, setCorrectionPart] = useState('')
  const [correctionBusy, setCorrectionBusy] = useState(false)
  const selectedExamIdRef = useRef(selectedExam?.id ?? '')
  const jobControllerRef = useRef<AbortController | null>(null)
  selectedExamIdRef.current = selectedExam?.id ?? ''

  useEffect(() => { if (createRequest > 0) setCreating(true) }, [createRequest])

  useEffect(() => {
    api<{ allow_codex: boolean }>('/capabilities').then((value) => setAllowCodex(Boolean(value.allow_codex))).catch(() => setAllowCodex(false))
  }, [])

  useEffect(() => {
    jobControllerRef.current?.abort()
    setAnalysisData(null)
    setActiveJob(selectedExam?.analysis_job ?? null)
    if (!selectedExam) return
    let active = true
    setAnalysisLoading(true)
    api<ExamAnalysisResponse>(`/exams/${encodeURIComponent(selectedExam.id)}/analysis`)
      .then((value) => { if (active) setAnalysisData(value) })
      .catch((error: unknown) => {
        if (active && !(error instanceof ApiError && error.status === 409)) {
          notify('error', `读取考试分析失败：${messageFor(error)}`)
        }
      })
      .finally(() => { if (active) setAnalysisLoading(false) })
    return () => { active = false }
    return () => { jobControllerRef.current?.abort() }
  }, [selectedExam?.id, notify])

  const updateJob = async (jobId: string) => {
    const examId = selectedExam?.id
    if (!examId) return
    const controller = new AbortController()
    jobControllerRef.current = controller
    try {
      const finalJob = await pollJob(jobId, (job) => {
        if (selectedExamIdRef.current === examId) setActiveJob(job)
      }, controller.signal)
      if (selectedExamIdRef.current !== examId) return
      if (finalJob.status === 'completed' || finalJob.status === 'partial') {
        await onRefresh(selectedStudentId)
        const updated = await api<ExamAnalysisResponse>(`/exams/${encodeURIComponent(examId)}/analysis`)
        if (selectedExamIdRef.current === examId) setAnalysisData(updated)
        notify(finalJob.status === 'completed' ? 'success' : 'info', finalJob.status === 'completed' ? '考试分析已完成' : '分析已生成，仍有未确定项目')
      } else if (finalJob.status === 'failed') {
        notify('error', `分析失败：${readableJobError(finalJob.error)}`)
      }
    } catch (error) { if (!(error instanceof DOMException && error.name === 'AbortError')) notify('error', messageFor(error)) }
  }

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
      setCreating(false)
      setExamForm({ title: '', exam_date: todayIso(), question_count: '', total_score: '', max_score: '', class_rank: '', grade_rank: '' })
    } catch (error) { notify('error', `创建考试失败：${messageFor(error)}`) }
    finally { setCreateBusy(false) }
  }

  const uploadFiles = async (files: FileList | null) => {
    if (!selectedExam || !files?.length) return
    setUploadBusy(true)
    const entries = Array.from(files)
    let uploaded = 0
    try {
      for (const file of entries) {
        const form = new FormData()
        form.set('role', uploadRole)
        form.set('file', file)
        await api(`/exams/${encodeURIComponent(selectedExam.id)}/assets`, { method: 'POST', body: form })
        uploaded += 1
      }
      await onRefresh(selectedStudentId)
      notify('success', `已上传 ${uploaded} 份${roleName(uploadRole)}材料`)
    } catch (error) {
      await onRefresh(selectedStudentId).catch(() => undefined)
      notify('error', `上传在第 ${uploaded + 1} 份材料处停止：${messageFor(error)}。已上传的文件仍保留。`)
    } finally { setUploadBusy(false) }
  }

  const moveAsset = async (assetId: string, delta: number) => {
    if (!selectedExam || orderBusy) return
    const ordered = [...selectedExam.assets].sort((a, b) => a.ordinal - b.ordinal)
    const index = ordered.findIndex((asset) => asset.id === assetId)
    const target = index + delta
    if (index < 0 || target < 0 || target >= ordered.length) return
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

  const analyze = async () => {
    if (!selectedExam) return
    if (!curriculum) { notify('error', '考点目录当前不可用，无法开始分析'); return }
    if (!selectedExam.assets.some((asset) => asset.role === 'paper') || !selectedExam.assets.some((asset) => asset.role === 'response')) {
      notify('error', '分析需要试卷和已批改的学生作答')
      return
    }
    setAnalysisBusy(true)
    try {
      const job = unwrap<Job>(await createJobForExam(selectedExam.id, provider))
      setActiveJob(job)
      notify('info', '考试分析任务已提交')
      await updateJob(job.id)
    } catch (error) { notify('error', `提交分析失败：${messageFor(error)}`) }
    finally { setAnalysisBusy(false) }
  }

  const retry = async () => {
    if (!activeJob) return
    setAnalysisBusy(true)
    try {
      const job = unwrap<Job>(await api<Job>(`/jobs/${encodeURIComponent(activeJob.id)}/retry`, { method: 'POST' }))
      setActiveJob(job)
      await updateJob(job.id)
    } catch (error) { notify('error', `重试失败：${messageFor(error)}`) }
    finally { setAnalysisBusy(false) }
  }

  const openCorrection = (response: Record<string, any>) => {
    setEditingResponse(response)
    setCorrectionText(String(response.transcript ?? ''))
    setCorrectionNumber(String(response.number ?? ''))
    setCorrectionPart(String(response.part_path ?? ''))
  }

  const submitCorrection = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedExam || !analysisData || !editingResponse) return
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
        id: crypto.randomUUID(),
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
      setEditingResponse(null)
      setActiveJob(job)
      notify('info', '修正任务已提交，原分析版本会保留')
      await updateJob(job.id)
    } catch (error) { notify('error', `提交修正失败：${messageFor(error)}`) }
    finally { setCorrectionBusy(false) }
  }

  const sortedAssets = [...(selectedExam?.assets ?? [])].sort((a, b) => a.ordinal - b.ordinal)
  const assetCount = (role: 'paper' | 'response' | 'reference') => selectedExam?.assets.filter((asset) => asset.role === role).length ?? 0
  const missingRequired = (['paper', 'response'] as const).filter((role) => assetCount(role) === 0).map(roleName)

  return (
    <>
      <div className="page-heading"><div><h1>考试分析</h1></div><Button onClick={() => setCreating(true)} icon={<Plus size={16} />}>新建考试</Button></div>
      <div className="exam-layout">
        <aside className="panel exam-index">
          <div className="exam-index-head"><h2>考试列表</h2></div>
          <Field label="学生"><select value={selectedStudentId} onChange={(e) => { onSelectStudent(e.target.value); onSelectExam('') }}><option value="">请选择</option>{students.map((student) => <option key={student.id} value={student.id}>{student.name} · {gradeLabel(student.grade)}</option>)}</select></Field>
          {!selectedStudentId ? <div className="small-empty">请先选择学生。</div> : examsLoading ? <Loading label="正在加载…" /> : exams.length === 0 ? <div className="small-empty">暂无考试记录</div> : <div className="exam-list">{exams.map((exam) => <button key={exam.id} onClick={() => onSelectExam(exam.id)} className={`exam-list-item ${selectedExam?.id === exam.id ? 'active' : ''}`}>
            <span className="exam-list-copy"><b>{exam.title}</b><small>{dateLabel(exam.exam_date)} · {exam.analysis_job ? homeStatusText(exam.analysis_job.status) : '未分析'}</small></span><i className={`status-dot status-dot-${exam.analysis_job?.status ?? exam.status ?? 'draft'}`} aria-hidden="true" />
          </button>)}</div>}
        </aside>
        <div className="exam-main">
          {creating && <div className="panel create-exam-panel">
            <div className="section-heading"><h2>新建考试{selectedStudent ? `（${selectedStudent.name}）` : ''}</h2><button className="icon-button" onClick={() => setCreating(false)} aria-label="取消新建"><X size={17} /></button></div>
            <form onSubmit={createExam} className="exam-create-form">
              <Field label="考试名称"><input required maxLength={200} value={examForm.title} onChange={(e) => setExamForm({ ...examForm, title: e.target.value })} placeholder="例如：九月阶段检测" /></Field>
              <div className="three-fields"><Field label="考试日期"><input type="date" value={examForm.exam_date} onChange={(e) => setExamForm({ ...examForm, exam_date: e.target.value })} /></Field><Field label="题目数" hint="按原卷大题题号数填写"><input type="number" min="1" max="200" step="1" required value={examForm.question_count} onChange={(e) => setExamForm({ ...examForm, question_count: e.target.value })} /></Field><Field label="满分（选填）"><input type="number" min="0.5" step="0.5" value={examForm.max_score} onChange={(e) => setExamForm({ ...examForm, max_score: e.target.value })} /></Field></div>
              <div className="two-fields"><Field label="得分（选填）"><input type="number" min="0" step="0.5" value={examForm.total_score} onChange={(e) => setExamForm({ ...examForm, total_score: e.target.value })} /></Field><Field label="班级排名（选填）"><input type="number" min="1" value={examForm.class_rank} onChange={(e) => setExamForm({ ...examForm, class_rank: e.target.value })} /></Field></div>
              <div className="two-fields"><Field label="年级排名（选填）"><input type="number" min="1" value={examForm.grade_rank} onChange={(e) => setExamForm({ ...examForm, grade_rank: e.target.value })} /></Field></div>
              {!selectedStudentId && <div className="inline-error">请先在左侧选择学生</div>}
              <div className="form-foot"><Button variant="quiet" onClick={() => setCreating(false)}>取消</Button><Button type="submit" disabled={createBusy || !selectedStudentId} icon={createBusy ? <LoaderCircle className="spin" size={15} /> : <FilePlus2 size={15} />}>{createBusy ? '创建中…' : '创建'}</Button></div>
            </form>
          </div>}
          {!selectedExam ? creating || examsLoading ? null : <div className="panel"><EmptyState icon={<ClipboardList />} title={!selectedStudentId ? '未选择学生' : exams.length ? '未选择考试' : '新建考试'} detail={!selectedStudentId ? '先在左侧选择学生。' : exams.length ? '在左侧选择一场考试。' : '点击右上角“新建考试”，填写名称和题目数，再上传试卷和学生作答。'} /></div> : <>
            <section className="panel exam-summary-card">
              <div className="exam-title-row"><div><h2>{selectedExam.title}</h2><p>{selectedStudent?.name ?? '学生'} · {dateLabel(selectedExam.exam_date)}</p></div><span className={`status-pill status-${activeJob?.status ?? selectedExam.analysis_job?.status ?? 'queued'}`}>{(activeJob ?? selectedExam.analysis_job) ? homeStatusText(activeJob?.status ?? selectedExam.analysis_job?.status) : '未分析'}</span></div>
              <div className="exam-summary-meta"><span>试卷 <b>{assetCount('paper')}</b> 份</span><span>学生作答 <b>{assetCount('response')}</b> 份</span><span>参考答案 <b>{assetCount('reference')}</b> 份</span></div>
            </section>
            <section className="panel upload-panel">
              <div className="section-heading"><h2><span className="step-no">1</span>上传材料</h2></div>
              <p className="section-intro">试卷和学生作答必传，参考答案选传。{missingRequired.length ? <strong className="missing-note">还缺：{missingRequired.join('、')}</strong> : <strong className="ready-note">必需材料已齐</strong>}</p>
              <div className="upload-role-tabs">{(['paper', 'response', 'reference'] as const).map((role) => <button key={role} className={uploadRole === role ? 'active' : ''} onClick={() => setUploadRole(role)}>{roleIcon(role)}<span>{roleName(role)}</span><small>{selectedExam.assets.filter((asset) => asset.role === role).length}</small></button>)}</div>
              <div className="upload-dropzone">
                <input type="file" multiple accept="image/jpeg,image/png,application/pdf" onChange={(e) => { void uploadFiles(e.target.files); e.currentTarget.value = '' }} disabled={uploadBusy} aria-label={`上传${roleName(uploadRole)}`} />
                <div className="upload-drop-icon">{uploadBusy ? <LoaderCircle className="spin" size={23} /> : <CloudUpload size={24} />}</div>
                <b>{uploadBusy ? '上传中…' : `上传${roleName(uploadRole)}`}</b>
                <span>点击选择文件，可多选；按选择顺序排列</span>
                <small>支持 PDF、JPG、PNG</small>
              </div>
              <div className="mobile-capture-row"><span>手机可逐页拍照</span><label className="capture-button"><input type="file" accept="image/*" capture="environment" onChange={(e) => { void uploadFiles(e.target.files); e.currentTarget.value = '' }} disabled={uploadBusy} /><CloudUpload size={16} />拍照上传</label></div>
              {sortedAssets.length > 0 && <div className="asset-list"><div className="asset-list-title"><b>已上传 {sortedAssets.length} 份</b><small>用上下箭头调整顺序</small></div>{sortedAssets.map((asset, index) => <div className="asset-row" key={asset.id}><span className="asset-ordinal">{String(index + 1).padStart(2, '0')}</span><span className="asset-file-icon">{asset.filename.toLowerCase().endsWith('.pdf') ? <FileText size={17} /> : <FileCheck2 size={17} />}</span><span className="asset-name"><b>{asset.filename}</b><small>{roleName(asset.role)} · {formatBytes(asset.size)}</small></span><div className="asset-controls"><button disabled={index === 0 || orderBusy} onClick={() => void moveAsset(asset.id, -1)} title="上移"><ArrowUp size={15} /></button><button disabled={index === sortedAssets.length - 1 || orderBusy} onClick={() => void moveAsset(asset.id, 1)} title="下移"><ArrowDown size={15} /></button></div></div>)}</div>}
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
                <Button onClick={() => void analyze()} disabled={analysisBusy || !curriculum || missingRequired.length > 0} icon={analysisBusy ? <LoaderCircle className="spin" size={16} /> : undefined}>{analysisBusy ? '分析中…' : '开始分析'}</Button>
                {activeJob?.status === 'failed' && <Button variant="secondary" onClick={() => void retry()} disabled={analysisBusy} icon={<RotateCcw size={15} />}>重试</Button>}
              </div>
              {provider === 'codex' && <p className="dev-provider-note">当前使用 Codex CLI 开发验证通道，结果不代表正式模型效果。</p>}
              {activeJob && <JobProgress job={activeJob} />}
              {analysisLoading && <Loading label="正在加载分析结果…" />}
            </section>
            {analysisData && <AnalysisPanel
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

function roleName(role: 'paper' | 'response' | 'reference') {
  return ({ paper: '试卷', response: '学生作答', reference: '参考答案' })[role]
}

function roleIcon(role: 'paper' | 'response' | 'reference') {
  return role === 'paper' ? <FileText size={16} /> : role === 'response' ? <FileCheck2 size={16} /> : <BookOpenCheck size={16} />
}

function JobProgress({ job }: { job: Job }) {
  const completed = job.status === 'completed' || job.status === 'partial'
  const settled = completed || job.status === 'failed'
  const progress = settled ? 100 : 38
  const label = job.status === 'partial' ? '已完成，含待核对项' : statusText(job.status)
  return <div className={`job-progress job-${job.status}`}><div className="job-icon">{completed ? <CheckCircle2 size={17} /> : job.status === 'failed' ? <CircleHelp size={17} /> : <LoaderCircle className="spin" size={17} />}</div><div className="job-main"><div className="job-title-row"><b>{label}</b></div><div className={`progress-track ${settled ? '' : 'progress-indeterminate'}`}><i style={{ width: `${progress}%` }} /></div>{job.error && <p>{readableJobError(job.error)}</p>}</div></div>
}

function AnalysisPanel({ data, examId, curriculum, onEdit, onSubmitCorrection, editingResponse, setEditingResponse, correctionText, setCorrectionText, correctionNumber, setCorrectionNumber, correctionPart, setCorrectionPart, correctionBusy }: {
  data: ExamAnalysisResponse; examId: string; curriculum: Curriculum | null; onEdit: (response: Record<string, any>) => void
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
    <div className="section-heading results-heading"><h2><span className="step-no">3</span>分析结果</h2></div>
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

type QuotaConfig = { selected: boolean; basic: number; intermediate: number; advanced: number }

function PracticeView({ students, selectedStudentId, exams, curriculum, practice, onSelectStudent, onPracticeChange, onError, onNotice }: {
  students: Student[]; selectedStudentId: string; exams: Exam[]; curriculum: Curriculum | null
  practice: Practice | null; onSelectStudent: (id: string) => void
  onPracticeChange: (practice: Practice | null) => void
  onError: (text: string) => void; onNotice: (kind: Notice['kind'], text: string) => void
}) {
  const [inventory, setInventory] = useState<InventoryPoint[]>([])
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
  const selectedStudent = students.find((student) => student.id === selectedStudentId)
  const analyzedExams = exams.filter((exam) => ['completed', 'partial'].includes(exam.analysis_job?.status ?? ''))

  useEffect(() => {
    setInventory([])
    if (!selectedStudentId) return
    let active = true
    setLoading(true)
    api<{ points: InventoryPoint[] }>(`/inventory?student_id=${encodeURIComponent(selectedStudentId)}`)
      .then((result) => {
        if (!active) return
        const points = arrayOf<InventoryPoint>(result.points)
        setInventory(points)
        setQuotas(Object.fromEntries(points.map((point) => [point.point_id, { selected: false, basic: 4, intermediate: 4, advanced: 2 }])))
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
        if (!active) return
        const saved = arrayOf<unknown>(rows).map((row) => unwrap<Practice>(row))
        setPracticeHistory(saved)
        const latestPublished = [...saved].reverse().find((item) => ['completed', 'partial'].includes(item.status))
        const recent = latestPublished ?? saved.at(-1) ?? null
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
  }, [selectedStudentId, onPracticeChange, onError])

  useEffect(() => setExamIds((current) => current.filter((id) => analyzedExams.some((exam) => exam.id === id))), [exams])

  const selectedPoints = inventory.filter((point) => quotas[point.point_id]?.selected && selectedStudent?.learned_point_ids.includes(point.point_id))
  const requestedTotal = selectedPoints.reduce((sum, point) => {
    const quota = quotas[point.point_id]
    return sum + (mode === 'default' ? 10 : quota.basic + quota.intermediate + quota.advanced)
  }, 0)
  const visiblePoints = inventory.filter((point) => `${point.name} ${point.point_id}`.toLowerCase().includes(query.toLowerCase()))
  const canCreate = Boolean(selectedStudent && curriculum && selectedStudent.learned_point_ids.length && examIds.length && selectedPoints.length && !creating)

  const setQuota = (pointId: string, patchValue: Partial<QuotaConfig>) => setQuotas((current) => ({ ...current, [pointId]: { ...current[pointId], ...patchValue } }))
  const createPractice = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canCreate) return
    if (mode === 'advanced' && selectedPoints.some((point) => {
      const quota = quotas[point.point_id]
      return quota.basic + quota.intermediate + quota.advanced === 0
    })) { onError('每个已选考点的进阶配额合计至少为 1'); return }
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
      onNotice('info', '已提交组卷')
      const finished = await pollJob(started.job_id, setJob)
      if (finished.status === 'failed') throw new Error(readableJobError(finished.error))
      const result = unwrap<Practice>(await api<Practice>(`/practices/${encodeURIComponent(started.id)}`))
      onPracticeChange(result)
      setPracticeHistory((rows) => [result, ...rows.filter((item) => item.id !== result.id)])
      if (result.status === 'completed') onNotice('success', '练习卷已生成')
      else onNotice('info', '练习卷已生成，部分考点题量不足')
    } catch (error) { setPracticeError(messageFor(error)); onError(`组卷未完成：${messageFor(error)}`) }
    finally { setCreating(false) }
  }

  const sendFeedback = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!practice) return
    setFeedbackBusy(true)
    try {
      if (!feedbackComplete(feedback)) { onError('请先选择是否符合预期、质量评分并填写节省时间'); return }
      const result = await api<Record<string, any>>(`/practices/${encodeURIComponent(practice.id)}/feedback`, { method: 'POST', body: JSON.stringify(feedback) })
      onPracticeChange({ ...practice, feedback: result })
      onNotice('success', '反馈已提交')
    } catch (error) { onError(`反馈未提交：${messageFor(error)}`) }
    finally { setFeedbackBusy(false) }
  }

  return <>
    <div className="page-heading"><div><h1>组卷</h1><p>按考点从题库选题，生成学生卷和教师卷。</p></div></div>
    <div className="builder-layout">
      <form id="practice-form" className="builder-panel panel" onSubmit={createPractice}>
        <div className="section-heading"><h2>组卷设置</h2></div>
        <Field label="学生"><select value={selectedStudentId} onChange={(e) => { onSelectStudent(e.target.value); onPracticeChange(null) }}><option value="">请选择学生</option>{students.map((student) => <option key={student.id} value={student.id}>{student.name} · {gradeLabel(student.grade)}</option>)}</select></Field>
        {!selectedStudent ? <div className="inline-warning"><CircleHelp size={16} /> 请先选择学生。</div> : <>
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
          <Field label="PDF 排版方式" hint="排版不理想时可换一种方式重新生成。"><select value={pdfEngine} onChange={(e) => setPdfEngine(e.target.value as 'latex' | 'word')}><option value="latex">LaTeX 排版</option><option value="word">Word 模板转换</option></select></Field>
          {selectedPoints.length > 0 && <div className="quota-summary"><span>计划题量</span><b>{requestedTotal}<small> 题</small></b><span>{selectedPoints.length} 个考点</span></div>}
          <p className="progress-note">只选用考点及前置考点都已学的题。某考点题数不够时会列出缺口，不用超纲题补足。</p>
          <Button type="submit" disabled={!canCreate} className="full-width builder-submit" icon={creating ? <LoaderCircle className="spin" size={16} /> : undefined}>{creating ? '组卷中…' : '生成练习卷'}</Button>
          {selectedStudent.learned_point_ids.length === 0 && <p className="inline-error">该学生还没有勾选已学考点，请先到“学生档案”勾选。</p>}
        </>}
      </form>
      <section className="panel preview-panel"><div className="section-heading"><h2>练习卷</h2></div>
        {practice && practiceHistory.length > 1 && <label className="practice-history-select"><span>历史练习</span><select value={practice.id} onChange={(event) => {
          const selected = practiceHistory.find((item) => item.id === event.target.value)
          if (!selected) return
          onPracticeChange(selected)
          const row = objectOf(selected.feedback)
          setFeedback(selected.feedback ? feedbackFrom(row) : EMPTY_FEEDBACK)
        }}>{practiceHistory.map((item, index) => <option key={item.id} value={item.id}>第 {index + 1} 份（{statusText(item.status)}）</option>)}</select></label>}
        {!practice ? historyLoading ? <Loading label="正在加载最近的练习…" /> : creating && job ? <><JobProgress job={job} /><div className="preview-processing"><div className="processing-orbit"><LoaderCircle className="spin" size={34} /></div><b>正在组卷</b><p>完成后在这里显示题目、题量缺口和下载文件。</p></div></> : practiceError ? <div className="practice-failed"><AlertCircle size={18} /><div><b>组卷失败</b><p>{practiceError}</p><button type="submit" form="practice-form" className="button button-secondary button-sm" disabled={!canCreate}>按当前设置重新生成</button></div></div> : <EmptyState icon={<BookOpenCheck />} title="还没有练习卷" detail="完成左侧设置后点击“生成练习卷”。" /> : <PracticeResult practice={practice} pointNames={new Map((curriculum?.points ?? []).map((point) => [point.id, point.name]))} feedback={feedback} onFeedbackChange={(patchValue) => setFeedback((current) => ({ ...current, ...patchValue }))} onSubmit={sendFeedback} feedbackBusy={feedbackBusy} />}
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
    {(warnings.length > 0 || shortfalls.length > 0) && <div className="shortfall-box"><b><AlertCircle size={15} /> 需要注意</b>{warnings.map((warning, index) => <p key={`w-${index}`}>{warning}</p>)}{shortfalls.map((item, index) => <p key={`s-${index}`}>{pointNames.get(String(item.point_id)) ?? String(item.point_id)} · {item.difficulty ? `${difficultyLabel(String(item.difficulty))} ` : ''}需要 {String(item.requested)} 题，只选到 {String(item.selected)} 题</p>)}</div>}
    {files.length > 0 && <div className="export-downloads"><b>下载文件</b>{files.map((file, index) => { const href = safeApiHref(String(file.url ?? '')); return href ? <a key={String(file.id ?? index)} href={href} className="download-link"><Download size={16} /><span>{String(file.label ?? '导出文件')}</span></a> : <div key={String(file.id ?? index)} className="download-missing"><AlertCircle size={15} /> {String(file.label ?? '文件')}暂时无法下载，请重新生成</div> })}</div>}
    {items.length > 0 ? <div className="preview-items"><div className="preview-items-title"><b>题目</b><span>{items.length} 道</span></div>{items.map((item, index) => <div className="preview-item" key={`${item.question_id}-${index}`}><div className="preview-item-head"><span>{String(item.order ?? index + 1).padStart(2, '0')}</span><b>{String(item.point_name ?? item.target_point_id ?? '考点')}</b><small>{difficultyLabel(String(item.difficulty ?? ''))}</small></div><div className="preview-content-box"><SanitizedPreview html={String(item.question_html ?? '')} /><details><summary>答案与解析</summary><SanitizedPreview html={String(item.answer_html ?? '')} /></details></div></div>)}</div> : snapshotItems.length > 0 ? <div className="preview-items"><div className="preview-items-title"><b>题目</b><span>{snapshotItems.length} 道</span></div>{snapshotItems.map((item, index) => <QuestionContent key={String(item.order ?? index)} item={item} index={index} />)}</div> : <EmptyState icon={<FileCheck2 />} title="没有选到题目" detail="请查看上方的题量缺口和提示。" />}
    <form className="feedback-form" onSubmit={onSubmit}><div className="feedback-heading"><div><b>这份练习卷符合预期吗？</b></div></div><div className="feedback-choice">{[['yes', '符合'], ['partial', '部分符合'], ['no', '不符合']].map(([value, label]) => <label key={value} className={feedback.expectation === value ? 'selected' : ''}><input type="radio" name="feedback-expectation" checked={feedback.expectation === value} onChange={() => onFeedbackChange({ expectation: value })} />{label}</label>)}</div><div className="field"><span className="field-label">质量评分（1 最低，5 最高）</span><div className="feedback-choice">{[1, 2, 3, 4, 5].map((value) => <label key={value} className={feedback.quality === value ? 'selected' : ''}><input type="radio" name="feedback-quality" checked={feedback.quality === value} onChange={() => onFeedbackChange({ quality: value })} />{value}</label>)}</div></div><Field label="节省备课时间（分钟）" hint="与自己备同样一份练习相比；多花了时间填负数"><input type="number" min="-600" max="600" required value={feedback.saved_minutes ?? ''} onChange={(e) => onFeedbackChange({ saved_minutes: e.target.value === '' ? null : Number(e.target.value) })} /></Field><div className="reason-options">{reasons.map((reason) => <label key={reason} className={feedback.reasons.includes(reason) ? 'selected' : ''}><input type="checkbox" checked={feedback.reasons.includes(reason)} onChange={() => setReason(reason)} />{reason}</label>)}</div><Field label="补充说明（选填）"><textarea rows={3} maxLength={2000} value={feedback.comment} onChange={(e) => onFeedbackChange({ comment: e.target.value })} placeholder="例如：第 3 题超纲、答案有误" /></Field><Button type="submit" size="sm" disabled={feedbackBusy || !feedbackComplete(feedback)} icon={feedbackBusy ? <LoaderCircle className="spin" size={14} /> : undefined}>{feedbackBusy ? '提交中…' : '提交反馈'}</Button></form>
  </div>
}

function difficultyLabel(value: string) { return ({ basic: '基础', intermediate: '提高', advanced: '挑战' } as Record<string, string>)[value] ?? '难度未确认' }

function QuestionContent({ item, index }: { item: Record<string, any>; index: number }) {
  const question = objectOf(item.question)
  const renderNodes = (nodes: unknown) => arrayOf<Record<string, any>>(nodes).map((node, n) => node.kind === 'text' ? <p key={n}>{String(node.text)}</p> : node.kind === 'formula' ? <div className="formula-token" key={n}><MathBlock tex={String(node.latex)} /></div> : <div className="image-token" key={n}>[配图：此处无法预览，请查看导出文件]</div>)
  return <div className="preview-item"><div className="preview-item-head"><span>{String(item.order ?? index + 1).padStart(2, '0')}</span><b>{String(item.target_point_name ?? item.target_point_id ?? '考点')}</b><small>{question.difficulty_score === undefined ? '难度未确认' : `难度 ${question.difficulty_score}`}</small></div><div className="plain-content">{renderNodes(question.stem)}</div><details><summary>答案与解析</summary><div className="plain-content">{renderNodes(question.answer)}{renderNodes(question.solution)}</div></details></div>
}

function sanitizeHtmlForPreview(markup: string) {
  const parsed = new DOMParser().parseFromString(markup, 'text/html')
  const htmlNamespace = 'http://www.w3.org/1999/xhtml'
  const mathNamespace = 'http://www.w3.org/1998/Math/MathML'
  const allowedHtml = new Set(['p', 'div', 'span', 'br', 'b', 'strong', 'i', 'em', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'sub', 'sup', 'img'])
  const allowedMath = new Set(['math', 'mrow', 'mi', 'mn', 'mo', 'mtext', 'mfrac', 'msqrt', 'mroot', 'msup', 'msub', 'msubsup', 'munder', 'mover', 'munderover'])
  const allowedMathAttributes: Record<string, Record<string, Set<string>>> = {
    math: { xmlns: new Set([mathNamespace]) },
    mi: { mathvariant: new Set(['normal']) },
    mn: { mathvariant: new Set(['normal']) },
    mo: { mathvariant: new Set(['normal']), fence: new Set(['true', 'false']), stretchy: new Set(['true', 'false']) },
    mtext: { mathvariant: new Set(['normal']) },
    mover: { accent: new Set(['true', 'false']) },
    munder: { accentunder: new Set(['true', 'false']) },
  }
  const mathIsSupported = (element: Element): boolean => {
    const tag = element.localName.toLowerCase()
    if (element.namespaceURI !== mathNamespace || !allowedMath.has(tag)) return false
    const tagAttrs = allowedMathAttributes[tag] ?? {}
    if (Array.from(element.attributes).some((attribute) => !tagAttrs[attribute.name]?.has(attribute.value))) return false
    return Array.from(element.children).every(mathIsSupported)
  }
  // Replace an unsupported formula as a whole, outside <math>, so the placeholder is visible instead of silently dropped.
  for (const math of Array.from(parsed.body.getElementsByTagNameNS(mathNamespace, 'math'))) {
    if (math.isConnected && !mathIsSupported(math)) {
      const marker = parsed.createElement('span')
      marker.textContent = '［此处公式无法在网页预览，请以导出文件为准］'
      math.replaceWith(marker)
    }
  }
  const walk = (parent: ParentNode) => {
    for (const child of Array.from(parent.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) continue
      if (!(child instanceof Element)) { child.remove(); continue }
      const tag = child.localName.toLowerCase()
      const isMath = child.namespaceURI === mathNamespace
      if (isMath ? !allowedMath.has(tag) : child.namespaceURI !== htmlNamespace || !allowedHtml.has(tag)) {
        child.replaceWith(document.createTextNode(isMath ? '［含暂不支持的公式结构］' : child.textContent ?? ''))
        continue
      }
      const safeAttrs: Record<string, string> = {}
      if (isMath) {
        let valid = true
        const tagAttrs = allowedMathAttributes[tag] ?? {}
        for (const attribute of Array.from(child.attributes)) {
          const values = tagAttrs[attribute.name]
          if (!values || !values.has(attribute.value)) valid = false
          else safeAttrs[attribute.name] = attribute.value
        }
        if (!valid) {
          child.replaceWith(document.createTextNode('［含暂不支持的公式属性］'))
          continue
        }
      } else if (tag === 'img') {
        const src = child.getAttribute('src') ?? ''
        if (!/^data:image\/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/=\r\n]+$/i.test(src)) { child.replaceWith(document.createTextNode('[配图无法安全预览]')); continue }
        safeAttrs.src = src.replace(/\s+/g, '')
        safeAttrs.alt = child.getAttribute('alt') ?? '题目配图'
        for (const dimension of ['width', 'height']) {
          const value = child.getAttribute(dimension) ?? ''
          if (/^[1-9]\d{0,3}$/.test(value) && Number(value) <= 4096) safeAttrs[dimension] = value
        }
      }
      for (const attribute of Array.from(child.attributes)) child.removeAttribute(attribute.name)
      for (const [name, value] of Object.entries(safeAttrs)) child.setAttribute(name, value)
      walk(child)
    }
  }
  walk(parsed.body)
  return parsed.body.innerHTML
}

function SanitizedPreview({ html }: { html: string }) {
  const content = useMemo(() => sanitizeHtmlForPreview(html), [html])
  const srcDoc = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'"><style>html,body{font:16px/1.8 'Noto Sans SC',sans-serif;color:#25332f;margin:0;overflow-wrap:anywhere}body{padding:8px;overflow:auto}img{max-width:100%;height:auto;vertical-align:middle}table{border-collapse:collapse;width:100%}td,th{border:1px solid #d9dfdb;padding:6px}p{margin:.4em 0}</style></head><body>${content}</body></html>`
  const resizeToContent = (frame: HTMLIFrameElement) => {
    const doc = frame.contentDocument
    if (!doc) return
    const measure = () => {
      const height = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight) + 4
      frame.style.height = `${Math.min(Math.max(height, 96), 520)}px`
    }
    requestAnimationFrame(measure)
    for (const image of Array.from(doc.images)) image.addEventListener('load', measure, { once: true })
  }
  return <iframe className="sanitized-preview-frame" title="题目预览" sandbox="allow-same-origin" srcDoc={srcDoc} onLoad={(event) => resizeToContent(event.currentTarget)} />
}

function AdminView({ onNotice }: { onNotice: (kind: Notice['kind'], text: string) => void }) {
  const [tab, setTab] = useState<'users' | 'prompts' | 'billing'>('users')
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
    if (tab === 'billing') { setLoading(false); return }
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
    <div className="admin-tabs"><button className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}>教师账号</button><button className={tab === 'prompts' ? 'active' : ''} onClick={() => setTab('prompts')}>提示词</button><button className={tab === 'billing' ? 'active' : ''} onClick={() => setTab('billing')}>模型与额度</button></div>
    {tab === 'billing' ? <AdminModelBilling onNotice={onNotice} /> : loading ? <div className="panel"><Loading label="正在加载…" /></div> : tab === 'users' ? <div className="admin-grid"><section className="panel admin-form-panel"><div className="section-heading"><h2>创建教师账号</h2></div><p className="section-intro">没有公开注册入口。教师只能看到自己名下的学生和记录。</p><form onSubmit={createUser}><Field label="登录账号"><input required maxLength={100} autoComplete="off" value={userForm.username} onChange={(e) => setUserForm({ ...userForm, username: e.target.value })} /></Field><Field label="教师显示名称"><input required maxLength={100} value={userForm.display_name} onChange={(e) => setUserForm({ ...userForm, display_name: e.target.value })} /></Field><Field label="初始密码"><input required autoComplete="new-password" type="password" value={userForm.password} onChange={(e) => setUserForm({ ...userForm, password: e.target.value })} /><small>创建后请自行告知教师；系统不保存明文密码。</small></Field><Button type="submit" disabled={saving} icon={saving ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />}>{saving ? '创建中…' : '创建账号'}</Button></form></section><section className="panel admin-users-panel"><div className="section-heading"><h2>账号（{users.length}）</h2></div>{users.length ? users.map((user) => <div className="admin-user-row" key={user.id}><span className="avatar avatar-sm">{user.display_name.slice(0, 1) || '师'}</span><span><b>{user.display_name}</b><small>{user.username}</small></span><span className="role-badge">{user.role === 'admin' ? '管理员' : '教师'}</span></div>) : <EmptyState icon={<Users />} title="暂无账号" detail="在左侧创建教师账号。" />}</section></div> : <PromptAdmin prompts={prompts} selected={selectedPrompt} onSelect={setSelectedPrompt} body={promptBody} onBody={setPromptBody} onSave={savePrompt} onHistory={openHistory} saving={saving} />}
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

