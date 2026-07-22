// 轻量 API 客户端。鉴权走后端下发的 httponly cookie，故所有请求 credentials:'include'。
// 开发期 vite 把 /api 反代到 :9999（同源），cookie 正常携带。

export type ApiError = { code: number; message: string; detail?: unknown }

async function req<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: 'include',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const msg = (data && (data.message || data.detail)) || res.statusText
    throw { code: res.status, message: typeof msg === 'string' ? msg : JSON.stringify(msg), detail: data } as ApiError
  }
  return data as T
}

export const api = {
  get: <T = any>(p: string) => req<T>('GET', p),
  post: <T = any>(p: string, b?: unknown) => req<T>('POST', p, b),
  put: <T = any>(p: string, b?: unknown) => req<T>('PUT', p, b),
  del: <T = any>(p: string) => req<T>('DELETE', p),
}

// 上传文件（multipart/form-data，字段名 file）。不设 Content-Type，让浏览器自带 boundary。
async function uploadFile<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await fetch(`/api${path}`, { method: 'POST', credentials: 'include', body: fd })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const msg = (data && (data.message || data.detail)) || res.statusText
    throw { code: res.status, message: typeof msg === 'string' ? msg : JSON.stringify(msg), detail: data } as ApiError
  }
  return data as T
}

// ---------- 鉴权 ----------
export type RegisterPayload = {
  email: string; password: string
  captcha_id: string; captcha_text: string
  email_code?: string; device_id?: string
}
export const auth = {
  signupConfig: () => api.get<{ captcha_required: boolean; email_verify_required: boolean }>('/auth/signup-config'),
  captcha: () => api.get<{ captcha_id: string; image: string }>('/auth/captcha'),
  sendEmailCode: (email: string, captcha_id: string, captcha_text: string) =>
    api.post('/auth/send-email-code', { email, captcha_id, captcha_text }),
  register: (p: RegisterPayload) => api.post('/auth/register', p),
  login: (email: string, password: string, captcha_id: string, captcha_text: string) =>
    api.post('/auth/login', { email, password, captcha_id, captcha_text }),
  logout: () => api.post('/auth/logout'),
  me: () => api.get('/portal/me'),
}

// ---------- 官网公开数据 ----------
export const publicApi = {
  prices: () => api.get('/public/prices'),
  fx: () => api.get<{ rate: number; date?: string; source?: string; live?: boolean }>('/public/fx'),
}

// ---------- 用户端 ----------
export const portal = {
  me: () => api.get('/portal/me'),
  changePassword: (old_password: string, new_password: string) =>
    api.post('/portal/change-password', { old_password, new_password }),
  prices: () => api.get('/portal/prices'),
  keys: () => api.get('/portal/keys'),
  newKey: (name: string, allowed_models?: string[]) => api.post('/portal/keys', { name, allowed_models }),
  disableKey: (id: number) => api.post(`/portal/keys/${id}/disable`),
  deleteKey: (id: number) => api.del(`/portal/keys/${id}`),
  keyUsage: (id: number) => api.get(`/portal/keys/${id}/usage`),
  usage: (limit = 50, offset = 0, days?: number) =>
    api.get(`/portal/usage?limit=${limit}&offset=${offset}${days ? `&days=${days}` : ''}`),
  billingSummary: (days = 7) => api.get(`/portal/billing/summary?days=${days}`),
  topups: () => api.get('/portal/topups'),
  submitTopup: (amount_usd: number, reason?: string, proof_url?: string) =>
    api.post('/portal/topups', { amount_usd, reason, proof_url }),
  // 上传转账凭证图片（multipart），返回 { url } 供 submitTopup 引用
  uploadProof: (file: File) => uploadFile('/portal/uploads/proof', file),
  // 自助充值（Polar 信用卡 / 虎皮椒 微信·支付宝）
  rechargeEnabled: () => api.get('/portal/recharge/enabled'),
  recharge: (amount_usd: number) => api.post('/portal/recharge', { amount_usd }),
  rechargeXunhupay: (amount_usd: number) => api.post('/portal/recharge/xunhupay', { amount_usd }),
  payments: (limit = 50, offset = 0) => api.get(`/portal/payments?limit=${limit}&offset=${offset}`),
}

// ---------- 管理端 ----------
export const admin = {
  topups: (status?: string) => api.get(`/admin/topups${status ? `?status=${status}` : ''}`),
  reviewTopup: (id: number, approve: boolean, note?: string) =>
    api.post(`/admin/topups/${id}/review`, { approve, note }),
  payments: (limit = 100, offset = 0, status?: string) =>
    api.get(`/admin/payments?limit=${limit}&offset=${offset}${status ? `&status=${status}` : ''}`),
  users: () => api.get('/admin/users'),
  createUser: (payload: { email: string; password: string; role: string; balance_usd: number }) =>
    api.post('/admin/users', payload),
  userDetail: (id: number) => api.get(`/admin/users/${id}/detail`),
  grant: (id: number, amount_usd: number) => api.post(`/admin/users/${id}/grant`, { amount_usd }),
  setRole: (id: number, role: string) => api.post(`/admin/users/${id}/role?role=${role}`),
  setUserStatus: (id: number, status: string) => api.post(`/admin/users/${id}/status?status=${status}`),
  setMultiplier: (id: number, multiplier: number) => api.post(`/admin/users/${id}/multiplier`, { multiplier }),
  setFullModelAccess: (id: number, enabled: boolean) =>
    api.post(`/admin/users/${id}/full-model-access?enabled=${enabled}`),
  issueKey: (payload: any) => api.post('/admin/keys', payload),
  keyStatus: (id: number, status: string) => api.post(`/admin/keys/${id}/status?status=${status}`),
  prices: () => api.get('/admin/model-prices'),
  upsertPrice: (payload: any) => api.post('/admin/model-prices', payload),
  usageSummary: (days = 7, start_date?: string, end_date?: string) => {
    const q = new URLSearchParams({ days: String(days) })
    if (start_date) q.set('start_date', start_date)
    if (end_date) q.set('end_date', end_date)
    return api.get(`/admin/usage/summary?${q}`)
  },
  usageDetails: (params: { email?: string; start_date?: string; end_date?: string; limit?: number; offset?: number }) => {
    const q = new URLSearchParams()
    if (params.email) q.set('email', params.email)
    if (params.start_date) q.set('start_date', params.start_date)
    if (params.end_date) q.set('end_date', params.end_date)
    q.set('limit', String(params.limit || 50)); q.set('offset', String(params.offset || 0))
    return api.get(`/admin/usage/details?${q}`)
  },
  usageExportUrl: (params: { email?: string; start_date?: string; end_date?: string; currency: string }) => {
    const q = new URLSearchParams({ currency: params.currency })
    if (params.email) q.set('email', params.email)
    if (params.start_date) q.set('start_date', params.start_date)
    if (params.end_date) q.set('end_date', params.end_date)
    return `/api/admin/usage/details/export?${q}`
  },
  moxingAccounting: (days = 30, limit = 100) => api.get(`/admin/moxing/accounting?days=${days}&limit=${limit}`),
  moxingTopup: (payload: any) => api.post('/admin/moxing/topups', payload),
  moxingAdjustment: (payload: any) => api.post('/admin/moxing/adjustments', payload),
  moxingTerms: (model: string, payload: any) => api.put(`/admin/moxing/terms/${encodeURIComponent(model)}`, payload),
  // 上游 slot / 容器（容器团队接口）
  slots: () => api.get('/admin/claude/slots'),
  upsertSlot: (id: string, payload: any) => api.put(`/admin/claude/slots/${id}`, payload),
  createSlot: (payload: { server_ip?: string; slot_id: string; type: string; weight: number; image?: string; creds_json: string }) =>
    api.post('/admin/claude/slots', payload),
  deleteSlot: (id: string, server_ip?: string) =>
    api.del(`/admin/claude/slots/${id}${server_ip ? `?server_ip=${encodeURIComponent(server_ip)}` : ''}`),
  setSlotEnabled: (id: string, server_ip: string, value: boolean) =>
    api.post(`/admin/claude/slots/${id}/enabled?server_ip=${encodeURIComponent(server_ip)}&value=${value}`),
  reassignSlot: (id: string, from: string, to: string) =>
    api.post(`/admin/claude/slots/${id}/server?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`),
  containers: () => api.get('/admin/claude/containers'),
  ensureContainers: () => api.post('/admin/claude/containers/ensure'),
  // 交互式登录新增订阅账号（网页终端 ↔ 服务器 PTY）
  loginStart: (account_id: string) => api.post('/admin/claude/login/start', { account_id }),
  loginRead: (session_id: string, offset: number) =>
    api.get(`/admin/claude/login/read?session_id=${session_id}&offset=${offset}`),
  loginWrite: (session_id: string, data: string) => api.post('/admin/claude/login/write', { session_id, data }),
  loginFinish: (session_id: string) => api.post('/admin/claude/login/finish', { session_id }),
  loginCancel: (session_id: string) => api.post('/admin/claude/login/cancel', { session_id }),

  // ChatGPT（codex 订阅）：门控状态 + 账号池 + device-auth 网页登录
  codexStatus: () => api.get('/admin/codex/status'),
  codexAccounts: () => api.get('/admin/codex/accounts'),
  codexDeleteAccount: (acc: string) => api.del(`/admin/codex/accounts/${encodeURIComponent(acc)}`),
  codexLoginStart: (account_id: string) => api.post('/admin/codex/login/start', { account_id }),
  codexLoginRead: (session_id: string, offset: number) =>
    api.get(`/admin/codex/login/read?session_id=${session_id}&offset=${offset}`),
  codexLoginWrite: (session_id: string, data: string) => api.post('/admin/codex/login/write', { session_id, data }),
  codexLoginFinish: (session_id: string) => api.post('/admin/codex/login/finish', { session_id }),
  codexLoginCancel: (session_id: string) => api.post('/admin/codex/login/cancel', { session_id }),
}

export const fmtUsd = (micro: number | null | undefined) => `$${((micro || 0) / 1e6).toFixed(2)}`

export const RMB_PER_USD_FALLBACK = 7.2

const CHINA_MODEL_PREFIXES = ['glm', 'kimi', 'qwen', 'deepseek', 'doubao', 'ernie', 'baichuan']

export const isChinaModel = (model: string | null | undefined) => {
  const name = (model || '').trim().toLowerCase()
  return CHINA_MODEL_PREFIXES.some((prefix) => name.startsWith(prefix))
}

export const fmtCnyFromMicroUsd = (
  micro: number | null | undefined,
  rate = RMB_PER_USD_FALLBACK,
  digits = 2,
) => `¥${(((micro || 0) / 1e6) * rate).toFixed(digits)}`

export const fmtModelCost = (
  model: string | null | undefined,
  micro: number | null | undefined,
  rate = RMB_PER_USD_FALLBACK,
) => isChinaModel(model) ? fmtCnyFromMicroUsd(micro, rate) : fmtUsd(micro)
