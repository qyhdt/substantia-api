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

// ---------- 用户端 ----------
export const portal = {
  me: () => api.get('/portal/me'),
  keys: () => api.get('/portal/keys'),
  newKey: (name: string, allowed_models?: string[]) => api.post('/portal/keys', { name, allowed_models }),
  disableKey: (id: number) => api.post(`/portal/keys/${id}/disable`),
  deleteKey: (id: number) => api.del(`/portal/keys/${id}`),
  keyUsage: (id: number) => api.get(`/portal/keys/${id}/usage`),
  usage: (limit = 50, offset = 0) => api.get(`/portal/usage?limit=${limit}&offset=${offset}`),
  topups: () => api.get('/portal/topups'),
  submitTopup: (amount_usd: number, reason?: string) => api.post('/portal/topups', { amount_usd, reason }),
  // 自助充值（Polar）
  rechargeEnabled: () => api.get('/portal/recharge/enabled'),
  recharge: (amount_usd: number) => api.post('/portal/recharge', { amount_usd }),
  payments: (limit = 50, offset = 0) => api.get(`/portal/payments?limit=${limit}&offset=${offset}`),
}

// ---------- 管理端 ----------
export const admin = {
  topups: (status?: string) => api.get(`/admin/topups${status ? `?status=${status}` : ''}`),
  reviewTopup: (id: number, approve: boolean, note?: string) =>
    api.post(`/admin/topups/${id}/review`, { approve, note }),
  users: () => api.get('/admin/users'),
  grant: (id: number, amount_usd: number) => api.post(`/admin/users/${id}/grant`, { amount_usd }),
  setRole: (id: number, role: string) => api.post(`/admin/users/${id}/role?role=${role}`),
  setUserStatus: (id: number, status: string) => api.post(`/admin/users/${id}/status?status=${status}`),
  issueKey: (payload: any) => api.post('/admin/keys', payload),
  keyStatus: (id: number, status: string) => api.post(`/admin/keys/${id}/status?status=${status}`),
  prices: () => api.get('/admin/model-prices'),
  upsertPrice: (payload: any) => api.post('/admin/model-prices', payload),
  usageSummary: () => api.get('/admin/usage/summary'),
  // 上游 slot / 容器（容器团队接口）
  slots: () => api.get('/admin/claude/slots'),
  upsertSlot: (id: string, payload: any) => api.put(`/admin/claude/slots/${id}`, payload),
  deleteSlot: (id: string) => api.del(`/admin/claude/slots/${id}`),
  containers: () => api.get('/admin/claude/containers'),
  ensureContainers: () => api.post('/admin/claude/containers/ensure'),
}

export const fmtUsd = (micro: number | null | undefined) => `$${((micro || 0) / 1e6).toFixed(4)}`
