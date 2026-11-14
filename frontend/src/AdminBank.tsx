import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, pollJob, readableJobError, Job } from './api'
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, Clock3, CloudUpload, FileCheck2, FileText, History, LoaderCircle, RefreshCw, RotateCcw, Save, Search, ShieldCheck, TriangleAlert } from 'lucide-react'
import {
  BankQuestionDetail, BankQuestionSummary, BankSummary, checkBank, getBankQuestion, getBankQuestions,
  getBankSummary, importBank, patchBankQuestion, restoreBank,
} from './adminBankApi'
import { SanitizedPreview } from './SafePreview'
import './admin-bank.css'

type Notice = (kind: 'success' | 'error' | 'info', text: string) => void
type Props = { onNotice: Notice }
type StatusFilter = 'all' | 'eligible' | 'blocked' | 'disabled'
type Draft = { enabled: boolean; point_ids: string[]; prerequisite_ids: string[]; difficulty_score: string; reason: string; note: string }

function errorText(error: unknown) { return error instanceof Error ? error.message : '操作未完成，请稍后重试' }
function str(value: unknown, fallback = '未记录') { return typeof value === 'string' && value ? value : fallback }
function list(value: unknown) { return Array.isArray(value) ? value : [] }
function record(value: unknown) { return value && typeof value === 'object' ? value as Record<string, unknown> : {} }
function dateText(value: unknown) {
  if (typeof value !== 'string') return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
function questionStatus(question: BankQuestionSummary) {
  if (question.disabled || question.status === 'disabled') return ['已停用', 'disabled']
  if (question.status === 'eligible') return ['可组卷', 'eligible']
  return ['受阻', 'blocked']
}
function metaValue(source: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) if (source[key] !== undefined && source[key] !== null) return source[key]
  return undefined
}
function parseIds(value: unknown) { return list(value).filter((item): item is string => typeof item === 'string') }
function makeDraft(detail: BankQuestionDetail): Draft {
  const adjustment = record(detail.adjustment)
  const enabled = metaValue(adjustment, 'enabled')
  const difficulty = metaValue(adjustment, 'difficulty_score') ?? detail.difficulty_score
  return {
    enabled: typeof enabled === 'boolean' ? enabled : !detail.disabled,
    point_ids: parseIds(metaValue(adjustment, 'point_ids') ?? detail.point_ids),
    prerequisite_ids: parseIds(metaValue(adjustment, 'prerequisite_ids') ?? detail.prerequisite_ids),
    difficulty_score: difficulty == null ? '' : String(difficulty), reason: '',
    note: String(metaValue(adjustment, 'note') ?? ''),
  }
}
function pretty(value: unknown) { return value == null ? '暂无记录' : JSON.stringify(value, null, 2) }
function blockReason(value: string) {
  const labels: Record<string, string> = {
    admin_disabled: '管理员已停用', semantic_warnings: 'AI 结果含不确定项',
    source_association_unconfirmed: '题目与答案未确认关联', source_structure_incomplete: '来源结构不完整',
    source_answer_missing: '缺少可用答案', semantic_not_assessed: '考点难度尚未完成评估',
    source_media_missing_or_mismatched: '来源图片缺失或不一致',
  }
  return labels[value] ?? (/[/\\]|OMML|OLE|native/i.test(value) ? `检查异常（${value}）` : value)
}
function safeJobError(value: string | null | undefined) {
  const text = value ?? ''
  if (/(?:^|[\s:(])(?:\/|[A-Za-z]:\\)[^\s)]*/.test(text) || /(?:sk-|Bearer |api_key|TEASSISTANT_DATA_DIR)/i.test(text)) {
    return '任务处理失败；详细诊断保存在服务端，请检查导入配置后重试'
  }
  return readableJobError(text)
}

export default function AdminBank({ onNotice }: Props) {
  const [summary, setSummary] = useState<BankSummary | null>(null)
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [pointId, setPointId] = useState('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState<BankQuestionSummary[]>([])
  const [total, setTotal] = useState(0)
  const [listLoading, setListLoading] = useState(false)
  const [selectedId, setSelectedId] = useState('')
  const [detail, setDetail] = useState<BankQuestionDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const selectedIdRef = useRef('')
  const detailRequestId = useRef(0)
  const detailMounted = useRef(true)
  const [saving, setSaving] = useState(false)
  const [checking, setChecking] = useState(false)
  const [checkResult, setCheckResult] = useState<BankSummary | null>(null)
  const [files, setFiles] = useState<File[]>([])
  const [importReason, setImportReason] = useState('')
  const [importing, setImporting] = useState(false)
  const [restoreTarget, setRestoreTarget] = useState('')
  const [restoreReason, setRestoreReason] = useState('')
  const [restoring, setRestoring] = useState(false)
  const [activeJobs, setActiveJobs] = useState<Record<string, Job>>({})
  const pollers = useRef<Map<string, AbortController>>(new Map())
  const fileInput = useRef<HTMLInputElement>(null)
  const limit = 25

  const selectQuestion = useCallback((id: string) => {
    if (id === selectedIdRef.current) return
    selectedIdRef.current = id
    detailRequestId.current += 1
    setSelectedId(id)
    setDetail(null)
    setDraft(null)
    setDetailLoading(Boolean(id))
  }, [])

  useEffect(() => {
    detailMounted.current = true
    return () => {
      detailMounted.current = false
      detailRequestId.current += 1
    }
  }, [])

  const loadSummary = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true); else setLoading(true)
    setLoadError('')
    try {
      const next = await getBankSummary()
      setSummary(next)
      return next
    } catch (error) {
      setLoadError(errorText(error))
      throw error
    } finally { setLoading(false); setRefreshing(false) }
  }, [])

  useEffect(() => { void loadSummary().catch(() => undefined) }, [loadSummary])

  useEffect(() => {
    if (!summary?.configured) { setRows([]); setTotal(0); return }
    let active = true
    setListLoading(true)
    const timer = window.setTimeout(() => {
      getBankQuestions({ q: query.trim(), status, point_id: pointId, offset: page * limit, limit })
        .then((result) => { if (active) { setRows(result.items); setTotal(result.total) } })
        .catch((error) => { if (active) onNotice('error', `题目列表读取失败：${errorText(error)}`) })
        .finally(() => { if (active) setListLoading(false) })
    }, 180)
    return () => { active = false; window.clearTimeout(timer) }
  }, [summary?.configured, summary?.revision, query, status, pointId, page, onNotice])

  const loadDetail = useCallback(async (id: string, quiet = false) => {
    if (!id || !detailMounted.current || selectedIdRef.current !== id) return
    const requestId = ++detailRequestId.current
    if (!quiet) setDetailLoading(true)
    try {
      const next = await getBankQuestion(id)
      if (detailMounted.current && detailRequestId.current === requestId && selectedIdRef.current === id) {
        setDetail(next)
        setDraft(makeDraft(next))
      }
    } catch (error) {
      if (detailMounted.current && detailRequestId.current === requestId && selectedIdRef.current === id) {
        onNotice('error', `题目详情读取失败：${errorText(error)}`)
      }
    } finally {
      if (detailMounted.current && detailRequestId.current === requestId && selectedIdRef.current === id) setDetailLoading(false)
    }
  }, [onNotice])

  useEffect(() => {
    if (!selectedId || !summary?.configured) {
      detailRequestId.current += 1
      setDetail(null); setDraft(null); setDetailLoading(false)
      return
    }
    void loadDetail(selectedId)
  }, [selectedId, summary?.revision, summary?.configured, loadDetail])

  const startPolling = useCallback((jobId: string) => {
    if (!jobId || pollers.current.has(jobId)) return
    const controller = new AbortController()
    pollers.current.set(jobId, controller)
    void pollJob(jobId, (job) => setActiveJobs((current) => ({ ...current, [jobId]: job })), controller.signal)
      .then((job) => {
        if (job.status === 'completed' || job.status === 'partial') onNotice('success', job.status === 'completed' ? '题库导入已完成，新版本已自动生效' : '题库导入已完成，部分条目受阻；新版本已按服务结果生效')
        else onNotice('error', `题库导入失败：${safeJobError(job.error)}`)
        void loadSummary(true).catch(() => undefined)
      })
      .catch((error) => { if (!(error instanceof DOMException && error.name === 'AbortError')) onNotice('error', `导入任务状态读取失败：${errorText(error)}`) })
      .finally(() => { pollers.current.delete(jobId) })
  }, [loadSummary, onNotice])

  useEffect(() => {
    for (const raw of summary?.imports ?? []) {
      const item = record(raw)
      const job = record(item.job)
      const jobId = String(metaValue(job, 'id') ?? '')
      const state = String(metaValue(job, 'status') ?? '')
      if (jobId && ['queued', 'running', 'processing'].includes(state)) startPolling(jobId)
    }
  }, [summary?.imports, startPolling])

  useEffect(() => () => {
    for (const controller of pollers.current.values()) controller.abort()
    pollers.current.clear()
  }, [])

  const pageCount = Math.max(1, Math.ceil(total / limit))
  const selectedLabel = useMemo(() => rows.find((row) => row.id === selectedId)?.title ?? detail?.title ?? '', [rows, selectedId, detail])

  const refresh = async () => {
    try { await loadSummary(true); onNotice('success', '题库数据已刷新') }
    catch (error) { onNotice('error', `题库数据读取失败：${errorText(error)}`) }
  }

  const saveAdjustment = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!summary || !detail || !draft) return
    const score = Number(draft.difficulty_score)
    if (!Number.isInteger(score) || score < 0 || score > 100) { onNotice('error', '难度分数须为 0 到 100 的整数'); return }
    if (draft.reason.trim().length < 1 || draft.reason.trim().length > 500) { onNotice('error', '请填写 1 到 500 字的调整原因'); return }
    setSaving(true)
    try {
      const updated = await patchBankQuestion(detail.id, {
        expected_revision: summary.revision, enabled: draft.enabled, point_ids: draft.point_ids,
        prerequisite_ids: draft.prerequisite_ids, difficulty_score: score, reason: draft.reason.trim(), note: draft.note,
      })
      if (selectedIdRef.current === detail.id) { setDetail(updated); setDraft(makeDraft(updated)) }
      await loadSummary(true)
      onNotice('success', '管理员调整已保存，原 AI assessment 与调整记录分别保留')
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) onNotice('error', '题库版本已变化，已重新读取最新版本；请核对后再次提交')
      else onNotice('error', `调整未保存：${errorText(error)}`)
      await loadSummary(true).catch(() => undefined)
      if (detail && detailMounted.current && selectedIdRef.current === detail.id) await loadDetail(detail.id, true)
    } finally { setSaving(false) }
  }

  const pickFiles = (picked: FileList | null) => {
    const next = Array.from(picked ?? [])
    if (fileInput.current) fileInput.current.value = ''
    const errors: string[] = []
    if (next.length < 2 || next.length > 40) errors.push('请选择 2 到 40 份 DOCX')
    if (next.some((file) => !file.name.toLowerCase().endsWith('.docx'))) errors.push('只接受 .docx 文件')
    if (next.some((file) => file.size > 30 * 1024 * 1024)) errors.push('单份文件不能超过 30 MiB')
    if (next.reduce((sum, file) => sum + file.size, 0) > 200 * 1024 * 1024) errors.push('所选文件总量不能超过 200 MiB')
    setFiles(errors.length ? [] : next)
    if (errors.length) onNotice('error', errors.join('；'))
  }

  const submitImport = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!summary || files.length < 2 || files.length > 40 || !importReason.trim()) return
    setImporting(true)
    try {
      const job = await importBank({ expected_revision: summary.revision, reason: importReason.trim(), files })
      setActiveJobs((current) => ({ ...current, [job.id]: job }))
      setFiles([]); setImportReason('')
      onNotice('info', `已提交完整题库重加工任务（${job.id}）。这会对合并后的全库重新运行 AI，并计入管理员额度；旧版本在新版本完成前继续使用。`)
      await loadSummary(true).catch(() => undefined)
      startPolling(job.id)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) onNotice('error', '题库版本已变化，文件未提交；刷新后确认最新版本再重试')
      else onNotice('error', `导入任务未创建：${errorText(error)}`)
    } finally { setImporting(false) }
  }

  const runCheck = async () => {
    if (!summary) return
    setChecking(true); setCheckResult(null)
    try {
      const result = await checkBank({ expected_revision: summary.revision })
      setCheckResult(result); setSummary(result)
      onNotice('success', '全库程序校验已完成；此次校验未调用 AI')
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) onNotice('error', '题库版本已变化，请刷新后重新运行校验')
      else onNotice('error', `全库校验未完成：${errorText(error)}`)
      await loadSummary(true).catch(() => undefined)
    } finally { setChecking(false) }
  }

  const submitRestore = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!summary || !restoreTarget || !restoreReason.trim()) return
    setRestoring(true)
    try {
      const restored = await restoreBank({ expected_revision: summary.revision, version: restoreTarget, reason: restoreReason.trim() })
      setSummary(restored); setRestoreTarget(''); setRestoreReason(''); selectQuestion('')
      onNotice('success', '已恢复该保留版本；来源和历史未覆盖，恢复版本仍执行现有门禁')
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) onNotice('error', '题库版本已变化，请刷新后重新选择恢复目标')
      else onNotice('error', `版本恢复未完成：${errorText(error)}`)
      await loadSummary(true).catch(() => undefined)
    } finally { setRestoring(false) }
  }

  if (loading) return <div className="panel"><div className="loading-state"><LoaderCircle className="spin" size={22} /><span>正在读取共享题库…</span></div></div>
  if (loadError && !summary) return <div className="panel bank-load-error"><AlertCircle /><div><b>题库状态暂时无法读取</b><p>{loadError}</p><button className="button button-secondary" onClick={() => void refresh()}>重试</button></div></div>
  if (!summary) return null

  return <div className="admin-bank">
    <section className="bank-summary panel">
      <div className="bank-heading"><div><h2>共享题库</h2><p>管理员维护的题目会用于后续组卷。题目启用由程序门禁决定，不设人工审批状态。</p></div><button className="button button-secondary button-sm" onClick={() => void refresh()} disabled={refreshing} aria-label="刷新题库">{refreshing ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}刷新</button></div>
      {loadError && <div className="bank-inline-error"><AlertCircle size={16} />{loadError}</div>}
      {!summary.configured ? <div className="bank-unconfigured"><TriangleAlert size={21} /><div><b>共享题库尚未配置</b><p>当前没有可供预览和调整的题库。导入原始试卷及解析 DOCX 可创建新题库版本。</p></div></div> : <>
        <div className="bank-stats"><Stat label="题目" value={summary.question_count} /><Stat label="可组卷" value={summary.eligible_count} tone="good" /><Stat label="受阻" value={summary.blocked_count} tone="warn" /><Stat label="已停用" value={summary.disabled_count} /><Stat label="来源" value={summary.source_count} /></div>
        <div className="bank-version-line"><span>当前版本 <b>{String(summary.active_version ?? '未记录')}</b></span><span className="bank-count-note">已停用题目属于受阻题目子集，两个统计有交叉，不应相加。</span><span className="bank-cost-note"><ShieldCheck size={14} />全库校验仅执行程序规则，不调用 AI</span></div>
      </>}
    </section>

    <section className="bank-ingest panel">
      <div className="section-heading"><div><h2>导入 Word 来源</h2><p>上传 2–40 份原始试卷与解析 DOCX，成功通过检查后新版本自动生效。</p></div></div>
      <div className="bank-import-warning"><AlertCircle size={17} /><div><b>提交后会对合并后的全库重新运行 AI</b><p>这是整库重新加工，调用按管理员账户预占并结算额度。新版本完成前继续使用旧版本；不会等待人工审批。新版本生效时会重置当前题目管理员调整，旧版及其调整仍可通过版本恢复取回。</p></div></div>
      <div className="bank-import-hint"><b>文件对应建议</b><span>题干、答案及公式通过重新上传 Word 来源更新，网页不提供可能丢失公式结构的纯文本覆盖。按同一试卷名配对原卷和解析，例如 <code>某次数学考试（原卷版）.docx</code> 与 <code>某次数学考试（解析版）.docx</code>。同套资料的文件名请保持一致；重复角色或命名歧义会被拒绝。</span></div>
      <form className="bank-import-form" onSubmit={submitImport}>
        <label className="bank-file-picker"><input ref={fileInput} type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" multiple disabled={importing} onChange={(event) => pickFiles(event.target.files)} /><CloudUpload size={18} /><span>{files.length ? `已选择 ${files.length} 份文件` : '选择 DOCX 文件'}</span><small>每份最多 30 MiB，总量最多 200 MiB</small></label>
        {files.length > 0 && <div className="bank-selected-files">{files.map((file) => <span key={`${file.name}-${file.size}-${file.lastModified}`}><FileText size={14} />{file.name} · {formatSize(file.size)}</span>)}</div>}
        <label className="bank-field"><span>本次导入原因（必填）</span><input required maxLength={500} value={importReason} onChange={(event) => setImportReason(event.target.value)} placeholder="例如：新增 2026 秋季单元卷及配套解析" /></label>
        <div className="bank-form-foot"><span>如果提交期间题库被其他管理员更新，系统会提示刷新核对后重试</span><button className="button button-primary" type="submit" disabled={importing || files.length < 2 || files.length > 40 || !importReason.trim()}>{importing ? <><LoaderCircle className="spin" size={15} />提交中…</> : <><CloudUpload size={15} />提交全库重加工</>}</button></div>
      </form>
      {Object.values(activeJobs).filter((job) => ['queued', 'running'].includes(job.status)).map((job) => <div className="bank-job" key={job.id}><LoaderCircle className="spin" size={16} /><span>导入任务 {job.id}</span><b>{job.status === 'queued' ? '排队中' : '处理中'}</b><small>离开此页后任务仍继续；再次打开题库页可从导入记录查看状态。</small></div>)}
      {summary.imports.length > 0 && <div className="bank-import-history"><b>最近导入任务</b>{summary.imports.map((raw, index) => { const item = record(raw); const job = record(item.job); const jobId = String(metaValue(job, 'id') ?? ''); const currentJob = activeJobs[jobId] ?? job as unknown as Job; const jobStatus = String(currentJob.status ?? 'unknown'); return <div className="bank-import-history-row" key={String(item.id ?? index)}><span><b>{String(metaValue(item, 'filenames') ? list(item.filenames).join('、') : '导入资料')}</b><small>{dateText(item.created_at)} · {str(item.reason, '原因未记录')}</small></span><span className={`bank-import-status ${jobStatus}`}>{jobStatus === 'completed' ? '已完成' : jobStatus === 'partial' ? '部分受阻' : jobStatus === 'failed' ? '失败' : jobStatus === 'queued' ? '排队中' : jobStatus === 'running' ? '处理中' : '状态未知'}</span>{jobStatus === 'failed' && <small className="bank-import-error">{safeJobError(typeof currentJob.error === 'string' ? currentJob.error : null)}</small>}</div> })}</div>}
    </section>

    {summary.configured && <div className="bank-workspace">
      <section className="bank-question-list panel">
        <div className="section-heading"><div><h2>题目库存</h2><p>{total.toLocaleString('zh-CN')} 道 · 每页 {limit} 道</p></div><button className="button button-secondary button-sm" onClick={() => void runCheck()} disabled={checking}><ShieldCheck size={15} />{checking ? '校验中…' : '全库校验'}</button></div>
        <div className="bank-filters"><label className="bank-search"><Search size={16} /><input aria-label="搜索题目" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0) }} placeholder="搜索题号或题目内容" /></label><select aria-label="状态过滤" value={status} onChange={(event) => { setStatus(event.target.value as StatusFilter); setPage(0) }}><option value="all">全部状态</option><option value="eligible">可组卷</option><option value="blocked">受阻</option><option value="disabled">已停用</option></select><select aria-label="考点过滤" value={pointId} onChange={(event) => { setPointId(event.target.value); setPage(0) }}><option value="">全部考点</option>{summary.points.map((point) => <option value={point.id} key={point.id}>{point.name}</option>)}</select></div>
        {listLoading ? <div className="bank-list-loading"><LoaderCircle className="spin" size={18} />正在读取题目…</div> : rows.length === 0 ? <div className="bank-list-empty"><FileCheck2 size={22} /><b>没有符合条件的题目</b><span>{query || status !== 'all' || pointId ? '调整搜索或筛选条件后重试。' : '当前题库没有题目。'}</span></div> : <div className="bank-rows">{rows.map((row) => { const [label, tone] = questionStatus(row); return <button className={`bank-question-row ${selectedId === row.id ? 'selected' : ''}`} key={row.id} onClick={() => selectQuestion(row.id)}><span className={`bank-status ${tone}`}>{label}</span><b>{row.number || '未编号'} · {row.title || '未命名题目'}</b><small>{row.point_ids.map((id) => summary.points.find((point) => point.id === id)?.name ?? id).join('、') || '未标注考点'}{row.difficulty_score != null ? ` · 难度 ${row.difficulty_score}` : ' · 难度未设置'}</small>{row.block_reasons.length > 0 && <small className="bank-block-reason">{row.block_reasons.map(blockReason).join('；')}</small>}</button> })}</div>}
        <div className="bank-pagination"><span>{total ? `${page + 1} / ${pageCount} 页` : '0 条结果'}</span><button className="button button-quiet button-sm" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page <= 0 || listLoading}><ChevronLeft size={15} />上一页</button><button className="button button-quiet button-sm" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))} disabled={page + 1 >= pageCount || listLoading}>下一页<ChevronRight size={15} /></button></div>
      </section>

      <section className="bank-detail panel">
        {!selectedId ? <div className="bank-list-empty bank-detail-empty"><FileCheck2 size={24} /><b>选择一道题目</b><span>查看原题内容、AI 评估和管理员调整记录。</span></div> : detailLoading || !detail || !draft ? <div className="bank-list-loading"><LoaderCircle className="spin" size={18} />正在读取题目详情…</div> : <>
          <div className="bank-detail-heading"><div><span className="bank-eyebrow">题目详情</span><h2>{detail.number || '未编号'} · {detail.title || selectedLabel || '未命名题目'}</h2><p>题目编号：{detail.id}</p></div><span className={`bank-status ${questionStatus(detail)[1]}`}>{questionStatus(detail)[0]}</span></div>
          <div className="bank-preview-block"><h3>题干</h3><SanitizedPreview html={detail.question_html ?? ''} title="题干预览" /></div>
          <div className="bank-preview-block"><h3>答案</h3><SanitizedPreview html={detail.answer_html ?? ''} title="答案预览" /></div>
          {detail.analysis_html && <div className="bank-preview-block"><h3>解析</h3><SanitizedPreview html={detail.analysis_html} title="解析预览" /></div>}
          {detail.block_reasons.length > 0 && <div className="bank-block-box"><AlertCircle size={16} /><div><b>当前阻断原因</b><ul>{detail.block_reasons.map((reason, index) => <li key={`${reason}-${index}`}>{blockReason(reason)}</li>)}</ul></div></div>}
          <form className="bank-adjust-form" onSubmit={saveAdjustment}>
            <div className="bank-subheading"><div><h3>管理员调整</h3><p>原 AI 结果保留，覆盖值单独记录并要求填写原因。</p></div></div>
            <label className="bank-toggle"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} /><span>{draft.enabled ? '管理员启用' : '管理员停用'}</span><small>停用题目仍显示原有阻断原因</small></label>
            <PointPicker label="目标考点" points={summary.points} selected={draft.point_ids} onChange={(values) => setDraft({ ...draft, point_ids: values })} />
            <PointPicker label="前置考点" points={summary.points} selected={draft.prerequisite_ids} onChange={(values) => setDraft({ ...draft, prerequisite_ids: values })} />
            <label className="bank-field"><span>难度分数（0–100）</span><input type="number" min="0" max="100" step="1" required value={draft.difficulty_score} onChange={(event) => setDraft({ ...draft, difficulty_score: event.target.value })} /></label>
            <label className="bank-field"><span>调整原因（必填，最多 500 字）</span><input required maxLength={500} value={draft.reason} onChange={(event) => setDraft({ ...draft, reason: event.target.value })} placeholder="说明本次启停、标签或难度调整原因" /></label>
            <label className="bank-field"><span>备注（最多 2000 字）</span><textarea maxLength={2000} rows={3} value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} placeholder="补充管理员备注" /></label>
            <div className="bank-form-foot"><span>如果期间有其他管理员保存，系统会提示重新核对后再提交</span><button className="button button-primary" type="submit" disabled={saving}>{saving ? <><LoaderCircle className="spin" size={15} />保存中…</> : <><Save size={15} />保存调整</>}</button></div>
          </form>
          <div className="bank-assessments"><h3>AI assessment（原始）</h3><p>这是原始模型评估及版本信息；管理员覆盖值单独保存在下方。</p><pre>{pretty(detail.ai_assessment)}</pre><h3>管理员 override（当前）</h3><pre>{pretty(detail.adjustment)}</pre></div>
          <div className="bank-history"><h3><History size={16} />题目历史</h3>{detail.history.length ? detail.history.map((item, index) => <HistoryEntry item={record(item)} key={String(metaValue(record(item), 'revision', 'id') ?? index)} />) : <p className="bank-muted">暂无管理员调整历史。</p>}</div>
        </>}
      </section>
    </div>}

    {checkResult && <section className="bank-check-result panel"><div className="section-heading"><div><h2><CheckCircle2 size={18} />最近一次程序校验</h2><p>仅程序校验，没有调用语言模型，也没有人工审批步骤。</p></div><button className="icon-button" onClick={() => setCheckResult(null)} aria-label="关闭校验结果">×</button></div><pre>{pretty(checkResult)}</pre></section>}

    <section className="bank-history-panel panel"><div className="section-heading"><div><h2><History size={18} />题库版本历史</h2><p>恢复版本后仍会执行自动检查，原始资料和历史保留。</p></div></div>{summary.history.length ? <div className="bank-version-history">{summary.history.map((raw, index) => { const item = record(raw); const version = String(metaValue(item, 'version', 'active_version', 'revision') ?? ''); const active = Boolean(metaValue(item, 'active', 'is_active')) || version === String(summary.active_version); return <div className="bank-version-row" key={`${version}-${index}`}><div><b>版本 {version || '未记录'} {active && <span className="bank-active-label">当前使用</span>}</b><small>{dateText(metaValue(item, 'created_at', 'activated_at'))} · {str(metaValue(item, 'reason', 'created_by'), '原因未记录')}</small><small>{String(metaValue(item, 'question_count') ?? '—')} 道题</small></div>{!active && version && <button className="button button-secondary button-sm" onClick={() => { setRestoreTarget(version); setRestoreReason('') }}><RotateCcw size={14} />选择恢复</button>}</div> })}</div> : <div className="bank-history-empty"><Clock3 size={18} />暂无题库版本历史。</div>}
      {restoreTarget && <form className="bank-restore-form" onSubmit={submitRestore}><b>恢复到版本 {restoreTarget}</b><label className="bank-field"><span>恢复原因（必填）</span><input required maxLength={500} value={restoreReason} onChange={(event) => setRestoreReason(event.target.value)} /></label><div className="bank-form-foot"><button className="button button-quiet button-sm" type="button" onClick={() => setRestoreTarget('')}>取消</button><button className="button button-secondary" type="submit" disabled={restoring}>{restoring ? '恢复中…' : '确认恢复'}</button></div></form>}
    </section>
  </div>
}

function Stat({ label, value, tone = '' }: { label: string; value: number; tone?: string }) { return <div className={`bank-stat ${tone}`}><span>{label}</span><b>{value.toLocaleString('zh-CN')}</b></div> }
function formatSize(size: number) { return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MiB` : `${Math.max(1, Math.ceil(size / 1024))} KiB` }
function PointPicker({ label, points, selected, onChange }: { label: string; points: BankSummary['points']; selected: string[]; onChange: (values: string[]) => void }) {
  return <fieldset className="bank-point-picker"><legend>{label}</legend>{points.length ? <div>{points.map((point) => <label key={point.id}><input type="checkbox" checked={selected.includes(point.id)} onChange={(event) => onChange(event.target.checked ? [...selected, point.id] : selected.filter((id) => id !== point.id))} /><span>{point.name}</span></label>)}</div> : <small>课程目录没有可选考点。</small>}</fieldset>
}
function HistoryEntry({ item }: { item: Record<string, unknown> }) {
  const adjustment = metaValue(item, 'adjustment', 'admin_override')
  return <details className="bank-question-history"><summary><span>{str(metaValue(item, 'reason'), '调整原因未记录')}</span><small>{dateText(metaValue(item, 'created_at', 'timestamp'))} · {str(metaValue(item, 'created_by', 'actor'), '管理员未记录')} · 版本 {String(metaValue(item, 'version') ?? '未记录')}</small></summary><pre>{pretty(adjustment ?? item)}</pre></details>
}
