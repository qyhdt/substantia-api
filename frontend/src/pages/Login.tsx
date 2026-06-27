import { useState } from 'react'
import { auth } from '../api'

export function Login({ onAuthed }: { onAuthed: (user: any, firstKey?: string) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'register') {
        const r = await auth.register(email, password)
        onAuthed(r.user, r.api_key)
      } else {
        const r = await auth.login(email, password)
        onAuthed(r.user)
      }
    } catch (err: any) {
      setError(err?.message || '失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ak-auth">
      <div className="ak-card">
        <h2>Substantia <span style={{ color: 'var(--accent)' }}>API</span></h2>
        <p className="ak-muted">{mode === 'login' ? '登录' : '注册即送 $20 试用额度'}</p>
        <form onSubmit={submit}>
          <input className="ak-input" type="email" placeholder="邮箱" value={email}
            onChange={(e) => setEmail(e.target.value)} required />
          <input className="ak-input" type="password" placeholder="密码（≥6 位）" value={password}
            onChange={(e) => setPassword(e.target.value)} required minLength={6} />
          {error && <div className="ak-err">{error}</div>}
          <button className="ak-btn primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? '…' : mode === 'login' ? '登录' : '注册'}
          </button>
        </form>
        <p className="ak-muted" style={{ marginTop: 14 }}>
          {mode === 'login' ? '没有账号？' : '已有账号？'}{' '}
          <a onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null) }}
            style={{ cursor: 'pointer' }}>
            {mode === 'login' ? '注册' : '去登录'}
          </a>
        </p>
      </div>
    </div>
  )
}
