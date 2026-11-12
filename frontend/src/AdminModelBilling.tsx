import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError } from './api'
import {
  AlertCircle,
  CheckCircle2,
  CircleHelp,
  Coins,
  LoaderCircle,
  RefreshCw,
  Save,
  ShieldCheck,
  Wallet,
} from 'lucide-react'
import {
  addUserCredits,
  BillingDefaults,
  BillingWallet,
  checkDeepSeekBalance,
  DailyBillingSummary,
  DeepSeekCheck,
  DeepSeekConfig,
  DeepSeekPrices,
  getBillingWallets,
  getBillingDefaults,
  getDeepSeekConfig,
  getUnresolvedBillingCalls,
  getYesterdayBilling,
  resolveBillingCall,
  saveBillingDefaults,
  saveDeepSeekConfig,
  UnresolvedBillingCall,
} from './modelBillingApi'
import './admin-model-billing.css'

type NoticeKind = 'success' | 'error' | 'info'
type Props = { onNotice: (kind: NoticeKind, text: string) => void }

type ResolutionForm = { amount: string; reason: string; requestId: string }

function requestId() {
  return globalThis.crypto?.randomUUID?.() ?? `billing-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : '操作未完成，请稍后重试'
}

function toMicros(value: string | null | undefined): bigint | null {
  if (value == null || !/^\d+(?:\.\d{1,6})?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  return BigInt(whole) * 1_000_000n + BigInt(fraction.padEnd(6, '0'))
}

function amountLabel(value: string | null | undefined, currency = 'CNY') {
  if (value == null) return '未知'
  const micros = toMicros(value)
  if (micros === null) return '未知'
  const whole = micros / 1_000_000n
  const fraction = String(micros % 1_000_000n).padStart(6, '0')
  return `${currency} ${whole.toLocaleString('zh-CN')}.${fraction}`
}

function safeDay(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : '日期未记录'
}

function formatDateTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '时间未记录'
    : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function coverageLabel(value: DailyBillingSummary['coverage']) {
  return value === 'tracked' ? '全程记录' : value === 'partial' ? '部分记录' : '尚未开始记录'
}

function keySourceLabel(value: DeepSeekConfig['key_source']) {
  return value === 'admin' ? '管理员配置' : value === 'environment' ? '环境变量后备' : '未配置'
}

function callStageLabel(value: string) {
  const names: Record<string, string> = {
    paper: '试卷识别', response: '作答识别', diagnosis: '错因分析', reference: '参考答案识别', report: '教师报告',
  }
  return names[value] ?? value
}

function sortByKnownCost(rows: DailyBillingSummary['users']) {
  return [...rows].sort((left, right) => {
    const a = toMicros(left.known_cost)
    const b = toMicros(right.known_cost)
    if (a === null && b === null) return left.user_id.localeCompare(right.user_id)
    if (a === null) return 1
    if (b === null) return -1
    if (a === b) return left.user_id.localeCompare(right.user_id)
    return a > b ? -1 : 1
  })
}

export default function AdminModelBilling({ onNotice }: Props) {
  const [config, setConfig] = useState<DeepSeekConfig | null>(null)
  const [prices, setPrices] = useState<DeepSeekPrices>({ input_cache_hit: '', input_cache_miss: '', output: '' })
  const [apiKey, setApiKey] = useState('')
  const [daily, setDaily] = useState<DailyBillingSummary | null>(null)
  const [wallets, setWallets] = useState<BillingWallet[]>([])
  const [billingDefaults, setBillingDefaults] = useState<BillingDefaults | null>(null)
  const [defaultCreditDraft, setDefaultCreditDraft] = useState('')
  const [savingDefaults, setSavingDefaults] = useState(false)
  const [defaultError, setDefaultError] = useState('')
  const [calls, setCalls] = useState<UnresolvedBillingCall[]>([])
  const [balanceCheck, setBalanceCheck] = useState<DeepSeekCheck | null>(null)
  const [loading, setLoading] = useState(true)
  const [savingConfig, setSavingConfig] = useState(false)
  const [checking, setChecking] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [creditUserId, setCreditUserId] = useState('')
  const [creditAmount, setCreditAmount] = useState('')
  const [creditRequestId, setCreditRequestId] = useState(requestId)
  const [creditSaving, setCreditSaving] = useState(false)
  const [resolutionForms, setResolutionForms] = useState<Record<string, ResolutionForm>>({})
  const [resolvingId, setResolvingId] = useState<string | null>(null)

  const loadAll = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true)
    else setLoading(true)
    try {
      const [nextConfig, nextDaily, nextWallets, nextCalls, nextDefaults] = await Promise.all([
        getDeepSeekConfig(), getYesterdayBilling(), getBillingWallets(), getUnresolvedBillingCalls(), getBillingDefaults(),
      ])
      setConfig(nextConfig)
      setPrices(nextConfig.prices)
      setDaily(nextDaily)
      setWallets(nextWallets.users)
      setCalls(nextCalls.calls)
      setBillingDefaults(nextDefaults)
      setDefaultCreditDraft(nextDefaults.new_user_credit)
      setCreditUserId((current) => current || nextWallets.users.find((user) => user.role === 'teacher')?.user_id || '')
    } catch (error) {
      onNotice('error', `模型与额度数据读取失败：${errorText(error)}`)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [onNotice])

  useEffect(() => { void loadAll() }, [loadAll])

  useEffect(() => {
    setResolutionForms((current) => {
      const next = { ...current }
      for (const call of calls) {
        if (!next[call.id]) next[call.id] = { amount: call.reserved_credit, reason: '', requestId: requestId() }
      }
      return next
    })
  }, [calls])

  const rankedUsers = useMemo(() => daily ? sortByKnownCost(daily.users) : [], [daily])
  const selectedWallet = wallets.find((wallet) => wallet.user_id === creditUserId) ?? null
  const defaultCreditMicros = toMicros(defaultCreditDraft)

  const updatePrice = (key: keyof DeepSeekPrices, value: string) => {
    if (/^\d*(?:\.\d{0,6})?$/.test(value)) setPrices((current) => ({ ...current, [key]: value }))
  }

  const saveDefaults = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!billingDefaults || defaultCreditMicros === null) return
    setSavingDefaults(true)
    setDefaultError('')
    try {
      const saved = await saveBillingDefaults({
        new_user_credit: defaultCreditDraft,
        expected_revision: billingDefaults.revision,
      })
      setBillingDefaults(saved)
      setDefaultCreditDraft(saved.new_user_credit)
      onNotice('success', '新用户默认额度已保存')
    } catch (error) {
      const message = error instanceof ApiError && error.status === 409
        ? '配置版本已被其他管理员更新。请重新读取最新值后再保存。'
        : `默认额度未保存：${errorText(error)}`
      setDefaultError(message)
      onNotice('error', message)
    } finally { setSavingDefaults(false) }
  }

  const reloadDefaults = async () => {
    try {
      const latest = await getBillingDefaults()
      setBillingDefaults(latest)
      setDefaultCreditDraft(latest.new_user_credit)
      setDefaultError('')
    } catch (error) {
      setDefaultError(`读取最新默认额度失败：${errorText(error)}`)
    }
  }

  const saveConfig = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!config) return
    setSavingConfig(true)
    try {
      const saved = await saveDeepSeekConfig({
        expected_revision: config.revision,
        api_key: apiKey || null,
        model: 'deepseek-flash',
        prices,
      })
      setConfig(saved)
      setPrices(saved.prices)
      setApiKey('')
      setBalanceCheck(null)
      onNotice('success', 'DeepSeek 配置已保存；后续启动的模型处理使用新配置')
    } catch {
      // Never surface a response that could accidentally include submitted secret material.
      onNotice('error', '配置未保存，请检查费率或配置版本后重试')
    } finally { setSavingConfig(false) }
  }

  const runBalanceCheck = async () => {
    setChecking(true)
    setBalanceCheck(null)
    try {
      const result = await checkDeepSeekBalance()
      setBalanceCheck(result)
      onNotice(result.ok && result.is_available ? 'success' : 'info', result.ok
        ? (result.is_available ? 'DeepSeek 余额接口可用' : '已完成余额检查，请查看账户余额状态')
        : '余额检查未通过，请检查已保存的配置')
    } catch (error) {
      setBalanceCheck(null)
      onNotice('error', `余额检查失败：${errorText(error)}`)
    } finally { setChecking(false) }
  }

  const addCredit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!selectedWallet || !creditAmount.trim()) return
    setCreditSaving(true)
    try {
      const updated = await addUserCredits(selectedWallet.user_id, { amount: creditAmount.trim(), request_id: creditRequestId })
      setWallets((current) => current.map((wallet) => wallet.user_id === updated.user_id
        ? { ...wallet, ...updated, username: wallet.username, display_name: wallet.display_name, role: wallet.role }
        : wallet))
      setCreditAmount('')
      setCreditRequestId(requestId())
      onNotice('success', `已为 ${selectedWallet.display_name} 追加额度`)
    } catch (error) {
      onNotice('error', `额度未追加：${errorText(error)}`)
    } finally { setCreditSaving(false) }
  }

  const updateResolution = (call: UnresolvedBillingCall, patch: Partial<Omit<ResolutionForm, 'requestId'>>) => {
    setResolutionForms((current) => {
      const prior = current[call.id] ?? { amount: call.reserved_credit, reason: '', requestId: requestId() }
      return { ...current, [call.id]: { ...prior, ...patch, requestId: requestId() } }
    })
  }

  const resolveCall = async (event: React.FormEvent, call: UnresolvedBillingCall) => {
    event.preventDefault()
    const form = resolutionForms[call.id] ?? { amount: call.reserved_credit, reason: '', requestId: requestId() }
    if (!form.reason.trim()) return
    setResolvingId(call.id)
    try {
      await resolveBillingCall(call.id, { amount: form.amount.trim(), reason: form.reason.trim(), request_id: form.requestId })
      setCalls((current) => current.filter((item) => item.id !== call.id))
      setResolutionForms((current) => { const { [call.id]: _removed, ...remaining } = current; return remaining })
      await loadAll(true)
      onNotice('success', '待核实预占已记录核实结果')
    } catch (error) {
      onNotice('error', `核实结果未保存：${errorText(error)}`)
    } finally { setResolvingId(null) }
  }

  if (loading) return <section className="panel mb-loading"><LoaderCircle className="spin" size={20} /> 正在读取模型与额度信息…</section>
  if (!config || !daily) return <section className="panel mb-empty"><AlertCircle size={20} /><div><b>暂时无法读取模型与额度信息</b><p>请确认管理员权限及服务状态，然后重试。</p><button className="mb-button mb-button-secondary" onClick={() => void loadAll()}>重新读取</button></div></section>

  return <div className="mb-page">
    <div className="mb-toolbar">
      <div><p>额度按账户累计，金额单位为人民币；费用按本平台记录的 token 用量和当时费率估算，可能与 DeepSeek 实际账单不同。</p></div>
      <button className="mb-button mb-button-secondary" onClick={() => void loadAll(true)} disabled={refreshing}>
        {refreshing ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />} 刷新数据
      </button>
    </div>

    <section className="panel mb-panel">
      <div className="mb-section-heading"><div><h3>DeepSeek 配置</h3><p className="mb-subnote">业务模型</p></div><span className="mb-secure"><ShieldCheck size={14} /> 密钥不回显</span></div>
      <form className="mb-config-form" onSubmit={saveConfig}>
        <div className="mb-config-grid">
          <label className="mb-field"><span>视觉模型</span><input value="deepseek-flash" readOnly aria-readonly="true" /></label>
          <label className="mb-field"><span>API 地址</span><input value={config.base_url} readOnly aria-readonly="true" /></label>
          <label className="mb-field mb-key-field"><span>替换 API Key</span><input type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={config.key_configured ? '已配置；留空表示不更换' : '输入 DeepSeek API Key'} /><small>当前来源：{keySourceLabel(config.key_source)}。保存后页面会清空输入框。</small></label>
        </div>
        <div className="mb-prices">
          <div className="mb-prices-title"><div><b>费用估算单价</b><small>人民币 / 百万 tokens。保存影响后续启动的模型处理；已开始的处理保持原配置，旧账不重算。</small></div><span>配置版本 {config.revision}</span></div>
          <div className="mb-price-grid">
            <label className="mb-field"><span>输入缓存命中</span><span className="mb-input-prefix">¥<input inputMode="decimal" value={prices.input_cache_hit} onChange={(event) => updatePrice('input_cache_hit', event.target.value)} required /></span></label>
            <label className="mb-field"><span>输入缓存未命中</span><span className="mb-input-prefix">¥<input inputMode="decimal" value={prices.input_cache_miss} onChange={(event) => updatePrice('input_cache_miss', event.target.value)} required /></span></label>
            <label className="mb-field"><span>输出</span><span className="mb-input-prefix">¥<input inputMode="decimal" value={prices.output} onChange={(event) => updatePrice('output', event.target.value)} required /></span></label>
          </div>
        </div>
        <div className="mb-actions"><p>{config.estimation_note}<br />当前每次请求最大预占 {amountLabel(config.reservation_credit)}；余额低于所需预占时会拒绝新请求。</p><button className="mb-button mb-button-primary" type="submit" disabled={savingConfig}>{savingConfig ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />} 保存配置</button></div>
      </form>
      <div className="mb-check-row">
        <div><b>账户余额检查</b><small>只查询 DeepSeek 账户余额，不发起模型对话，也不用于用户费用分摊。</small></div>
        <button className="mb-button mb-button-secondary" onClick={() => void runBalanceCheck()} disabled={checking}>{checking ? <LoaderCircle className="spin" size={15} /> : <Wallet size={15} />} 检查余额</button>
      </div>
      {balanceCheck && <div className={`mb-check-result ${balanceCheck.ok && balanceCheck.is_available ? 'is-ok' : 'is-info'}`}>
        {balanceCheck.ok && balanceCheck.is_available ? <CheckCircle2 size={17} /> : <CircleHelp size={17} />}
        <div><b>{balanceCheck.ok ? (balanceCheck.is_available ? '余额接口检查通过' : '已返回余额信息') : '余额接口未通过'}</b>
          {balanceCheck.balance_infos.length > 0 && <div className="mb-balance-list">{balanceCheck.balance_infos.map((item, index) => <span key={`${item.currency}-${index}`}><b>{item.currency}</b> 总额 {item.total_balance} · 授予 {item.granted_balance} · 充值 {item.topped_up_balance}</span>)}</div>}
        </div>
      </div>}
    </section>

    <section className="panel mb-panel">
      <div className="mb-section-heading"><div><h3>昨日估算用量</h3><p className="mb-subnote">北京时间自然日</p></div><span className={`mb-coverage coverage-${daily.coverage}`}>{coverageLabel(daily.coverage)}</span></div>
      <div className="mb-daily-summary">
        <div><small>{safeDay(daily.day)} 已结算估算小计</small><b>{amountLabel(daily.known_cost, daily.currency)}</b></div>
        <div><small>请求次数</small><b>{daily.call_count.toLocaleString('zh-CN')}</b></div>
        <div><small>用量未知</small><b>{daily.unknown_count.toLocaleString('zh-CN')}</b></div>
      </div>
      <p className="mb-note">按请求开始时间归属昨日。表格按已结算估算金额降序；有未知请求的用户总估算显示“未知”，已结算小计与预占金额分开列示。记录开始于 {formatDateTime(daily.tracking_started_at)}。</p>
      {rankedUsers.length ? <div className="mb-table-wrap"><table className="mb-table"><thead><tr><th>#</th><th>用户</th><th>已结算估算</th><th>总估算</th><th>次数</th><th>未知次数</th><th>预占</th></tr></thead><tbody>
        {rankedUsers.map((user, index) => <tr key={user.user_id}><td>{index + 1}</td><td><b>{user.display_name}</b><small>{user.username}</small></td><td>{amountLabel(user.known_cost, daily.currency)}</td><td>{amountLabel(user.estimated_cost, daily.currency)}</td><td>{user.call_count}</td><td>{user.unknown_count}</td><td>{amountLabel(user.reserved_cost, daily.currency)}</td></tr>)}
      </tbody></table></div> : <div className="mb-table-empty">{daily.coverage === 'not_tracked' ? '此日期尚未开始计费记录。' : '昨日没有已记录的模型请求。'}</div>}
    </section>

    <section className="panel mb-panel">
      <div className="mb-section-heading"><div><h3>用户额度</h3><p className="mb-subnote">累计充值 · 无周期重置</p></div><span className="mb-wallet-icon"><Coins size={17} /> {wallets.length} 个账户</span></div>
      {billingDefaults && <form className="mb-default-credit" onSubmit={saveDefaults}>
        <div className="mb-default-heading"><div><b>新用户默认额度</b><small>新账号创建时一次性发放，修改仅影响之后创建的账号，不补发已有用户。</small></div><span>版本 {billingDefaults.revision}</span></div>
        <div className="mb-default-edit">
          <label className="mb-field"><span>创建账号时发放（{billingDefaults.currency}）</span><input inputMode="decimal" value={defaultCreditDraft} onChange={(event) => { if (/^\d*(?:\.\d{0,6})?$/.test(event.target.value)) { setDefaultCreditDraft(event.target.value); setDefaultError('') } }} required aria-invalid={defaultCreditMicros === null} /></label>
          <button className="mb-button mb-button-secondary" type="submit" disabled={savingDefaults || defaultCreditMicros === null}>{savingDefaults ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />} 保存默认额度</button>
        </div>
        {defaultError && <div className="mb-default-error" role="alert"><span>{defaultError}</span>{defaultError.includes('版本已被其他管理员更新') && <button type="button" className="mb-link-button" onClick={() => void reloadDefaults()}>重新读取</button>}</div>}
      </form>}
      <form className="mb-credit-form" onSubmit={addCredit}>
        <label className="mb-field"><span>选择用户</span><select value={creditUserId} onChange={(event) => { setCreditUserId(event.target.value); setCreditRequestId(requestId()) }} required>
          <option value="" disabled>选择用户</option>{wallets.map((wallet) => <option key={wallet.user_id} value={wallet.user_id}>{wallet.display_name}（{wallet.username}）</option>)}
        </select></label>
        <label className="mb-field"><span>追加金额（CNY）</span><input inputMode="decimal" value={creditAmount} onChange={(event) => { if (/^\d*(?:\.\d{0,6})?$/.test(event.target.value)) { setCreditAmount(event.target.value); setCreditRequestId(requestId()) } }} placeholder="例如 20.000000" required /></label>
        <button className="mb-button mb-button-primary" type="submit" disabled={creditSaving || !selectedWallet}>{creditSaving ? <LoaderCircle className="spin" size={15} /> : <Coins size={15} />} 追加额度</button>
      </form>
      <p className="mb-note">追加操作会增加累计总额度，不会覆盖、清零或重置已用额度。余额不足最大预占时，新模型请求会被拒绝；预占不是实际扣费，返回有效用量后会释放差额。</p>
      {wallets.length ? <div className="mb-wallet-list">{wallets.map((wallet) => <WalletCard key={wallet.user_id} wallet={wallet} />)}</div> : <div className="mb-table-empty">尚无额度账户。</div>}
    </section>

    <section className="panel mb-panel">
      <div className="mb-section-heading"><div><h3>待核实预占</h3><p className="mb-subnote">需要管理员确认</p></div><span className="mb-unresolved-count">{calls.length} 项</span></div>
      <p className="mb-note">无有效用量的请求保留预占，不按 0 元处理，也不会因任务租约过期自动退款。核实金额可在 0 至该次预占额之间；必须填写原因并保留审计记录。</p>
      {calls.length ? <div className="mb-unresolved-list">{calls.map((call) => <ResolutionCard
        key={call.id}
        call={call}
        wallet={wallets.find((wallet) => wallet.user_id === call.user_id)}
        form={resolutionForms[call.id] ?? { amount: call.reserved_credit, reason: '', requestId: '' }}
        saving={resolvingId === call.id}
        onChange={(patch) => updateResolution(call, patch)}
        onSubmit={(event) => void resolveCall(event, call)}
      />)}</div> : <div className="mb-table-empty">当前没有待核实预占。</div>}
    </section>
  </div>
}

function WalletCard({ wallet }: { wallet: BillingWallet }) {
  return <article className={`mb-wallet-card ${wallet.frozen ? 'is-frozen' : ''}`}>
    <div className="mb-wallet-user"><span className="mb-avatar">{wallet.display_name.slice(0, 1) || '师'}</span><span><b>{wallet.display_name}</b><small>{wallet.username}</small></span>{wallet.frozen && <span className="mb-frozen">已冻结</span>}</div>
    <div className="mb-wallet-values">
      <span><small>累计总额度</small><b>{amountLabel(wallet.total_credit)}</b></span>
      <span><small>已用额度</small><b>{amountLabel(wallet.used_credit)}</b></span>
      <span><small>预占额度</small><b>{amountLabel(wallet.reserved_credit)}</b></span>
      <span><small>可用额度</small><b className={toMicros(wallet.remaining_credit) === 0n ? 'is-zero' : ''}>{amountLabel(wallet.remaining_credit)}</b></span>
    </div>
  </article>
}

function ResolutionCard({
  call, wallet, form, saving, onChange, onSubmit,
}: {
  call: UnresolvedBillingCall
  wallet?: BillingWallet
  form: ResolutionForm
  saving: boolean
  onChange: (patch: Partial<Omit<ResolutionForm, 'requestId'>>) => void
  onSubmit: (event: React.FormEvent) => void
}) {
  const reservedMicros = toMicros(call.reserved_credit)
  const amountMicros = toMicros(form.amount)
  const invalidAmount = amountMicros === null || reservedMicros === null || amountMicros > reservedMicros
  return <article className="mb-call-card">
    <div className="mb-call-heading"><div><b>{wallet?.display_name ?? '未知用户'} · {callStageLabel(call.stage)}</b><small>{formatDateTime(call.started_at)} · 请求 {call.id}</small></div><span>预占 {amountLabel(call.reserved_credit)}</span></div>
    <div className="mb-call-meta"><span>任务：{call.job_id ?? '未关联'}</span><span>错误码：{call.error_code ?? '未记录'}</span></div>
    <form className="mb-resolve-form" onSubmit={onSubmit}>
      <label className="mb-field"><span>核实估算额（CNY，最高不超过预占）</span><input inputMode="decimal" value={form.amount} onChange={(event) => { if (/^\d*(?:\.\d{0,6})?$/.test(event.target.value)) onChange({ amount: event.target.value }) }} required aria-invalid={invalidAmount} /></label>
      <label className="mb-field"><span>核实原因</span><input value={form.reason} onChange={(event) => onChange({ reason: event.target.value })} maxLength={500} required placeholder="说明核实依据" /></label>
      {invalidAmount && <small className="mb-inline-error">金额需为 0 至 {call.reserved_credit} 之间、最多 6 位小数。</small>}
      {call.can_resolve === false && <small className="mb-inline-error">模型请求仍在处理中，暂不能核实该预占。</small>}
      <button className="mb-button mb-button-secondary" type="submit" disabled={saving || invalidAmount || !form.reason.trim() || call.can_resolve === false}>{saving ? <LoaderCircle className="spin" size={15} /> : <CheckCircle2 size={15} />} 确认核实并记账</button>
    </form>
  </article>
}
