import { api } from './api'

export type DeepSeekPrices = {
  input_cache_hit: string
  input_cache_miss: string
  output: string
}

export type DeepSeekConfig = {
  revision: number
  key_configured: boolean
  key_source: 'admin' | 'environment' | 'none'
  model: 'deepseek-flash'
  base_url: string
  prices: DeepSeekPrices
  currency: 'CNY'
  reservation_credit: string
  estimation_note: string
}

export type DeepSeekCheck = {
  ok: boolean
  is_available: boolean
  balance_infos: {
    currency: string
    total_balance: string
    granted_balance: string
    topped_up_balance: string
  }[]
}

export type DailyBillingSummary = {
  day: string
  timezone: 'Asia/Shanghai'
  currency: 'CNY'
  tracking_started_at: string
  coverage: 'not_tracked' | 'partial' | 'tracked'
  known_cost: string
  unknown_count: number
  call_count: number
  users: {
    user_id: string
    username: string
    display_name: string
    known_cost: string
    estimated_cost: string | null
    call_count: number
    unknown_count: number
    reserved_cost: string
  }[]
}

export type BillingWallet = {
  user_id: string
  total_credit: string
  used_credit: string
  reserved_credit: string
  remaining_credit: string
  frozen: boolean
  revision: number
  username: string
  display_name: string
  role: 'admin' | 'teacher'
}

export type BillingWalletList = {
  currency: 'CNY'
  users: BillingWallet[]
}

export type BillingDefaults = {
  currency: 'CNY'
  new_user_credit: string
  revision: number
}

export type UnresolvedBillingCall = {
  id: string
  user_id: string
  job_id: string | null
  stage: string
  started_at: string
  reserved_credit: string
  error_code: string | null
  status?: string
  can_resolve?: boolean
}

export type ResolvedBillingCall = {
  id: string
  user_id: string
  amount: string
  status: string
}

export function getDeepSeekConfig() {
  return api<DeepSeekConfig>('/admin/deepseek')
}

export function saveDeepSeekConfig(input: {
  expected_revision: number
  api_key: string | null
  model: 'deepseek-flash'
  prices: DeepSeekPrices
}) {
  return api<DeepSeekConfig>('/admin/deepseek', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function checkDeepSeekBalance() {
  return api<DeepSeekCheck>('/admin/deepseek/check', {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function getYesterdayBilling() {
  return api<DailyBillingSummary>('/admin/billing/yesterday')
}

export function getBillingWallets() {
  return api<BillingWalletList>('/admin/billing/wallets')
}

export function getBillingDefaults() {
  return api<BillingDefaults>('/admin/billing/defaults')
}

export function saveBillingDefaults(input: { new_user_credit: string; expected_revision: number }) {
  return api<BillingDefaults>('/admin/billing/defaults', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function addUserCredits(userId: string, input: { amount: string; request_id: string }) {
  return api<BillingWallet>(`/admin/billing/users/${encodeURIComponent(userId)}/credits`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function getUnresolvedBillingCalls() {
  return api<{ calls: UnresolvedBillingCall[] }>('/admin/billing/unresolved')
}

export function resolveBillingCall(
  callId: string,
  input: { amount: string; reason: string; request_id: string },
) {
  return api<ResolvedBillingCall>(`/admin/billing/calls/${encodeURIComponent(callId)}/resolve`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}
