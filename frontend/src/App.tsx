import { useEffect, useState } from 'react'
import './ui.css'
import { auth, fmtUsd, portal } from './api'
import { useI18n, LangToggle } from './i18n'
import { Landing } from './pages/Landing'
import { Login } from './pages/Login'
import { UserDashboard } from './pages/UserDashboard'
import { AdminDashboard } from './pages/AdminDashboard'

type User = { id: number; email: string; role: string; balance_micro_usd?: number }

export default function App() {
  const { t } = useI18n()
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [firstKey, setFirstKey] = useState<string | undefined>()
  const [view, setView] = useState<'user' | 'admin'>('user')
  // 未登录时的页面：先看落地页，点登录/注册再进表单
  const [authView, setAuthView] = useState<'landing' | 'auth'>('landing')
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')

  // 启动时用 cookie 探一下是否已登录
  useEffect(() => {
    portal.me()
      .then((me) => setUser(me))
      .catch(() => setUser(null))
      .finally(() => setBooting(false))
  }, [])

  async function refreshBalance() {
    try {
      const me = await portal.me()
      setUser((u) => (u ? { ...u, ...me } : u))
    } catch { /* ignore */ }
  }
  useEffect(() => {
    if (!user) return
    const tm = setInterval(refreshBalance, 15000)
    return () => clearInterval(tm)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id])

  async function logout() {
    await auth.logout().catch(() => {})
    setUser(null)
    setFirstKey(undefined)
    setAuthView('landing')
  }

  if (booting) return <div className="ak-app"><p className="ak-muted" style={{ marginTop: 40 }}>…</p></div>

  if (!user) {
    if (authView === 'landing') {
      return <Landing onAuth={(m) => { setAuthMode(m); setAuthView('auth') }} />
    }
    return (
      <Login
        initialMode={authMode}
        onBack={() => setAuthView('landing')}
        onAuthed={(u, key) => { setUser(u); setFirstKey(key); setView('user') }}
      />
    )
  }

  const isAdmin = user.role === 'admin'
  return (
    <div className="ak-app">
      <div className="ak-top">
        <div className="ak-brand">Substantia <span>{t('brand_tag')}</span></div>
        <div className="ak-userbox">
          {isAdmin && (
            <div className="ak-tabs" style={{ margin: 0 }}>
              <div className={`ak-tab ${view === 'user' ? 'active' : ''}`} onClick={() => setView('user')}>{t('console')}</div>
              <div className={`ak-tab ${view === 'admin' ? 'active' : ''}`} onClick={() => setView('admin')}>Admin</div>
            </div>
          )}
          <span className="ak-balance">{fmtUsd(user.balance_micro_usd)}</span>
          <span>{user.email}</span>
          <LangToggle />
          <button className="ak-btn" onClick={logout}>{t('logout')}</button>
        </div>
      </div>

      {view === 'admin' && isAdmin ? <AdminDashboard /> : <UserDashboard newKey={firstKey} />}
    </div>
  )
}
