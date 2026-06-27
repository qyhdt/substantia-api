import { useEffect, useState } from 'react'
import './ui.css'
import { auth, fmtUsd, portal } from './api'
import { Login } from './pages/Login'
import { UserDashboard } from './pages/UserDashboard'
import { AdminDashboard } from './pages/AdminDashboard'

type User = { id: number; email: string; role: string; balance_micro_usd?: number }

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [firstKey, setFirstKey] = useState<string | undefined>()
  const [view, setView] = useState<'user' | 'admin'>('user')

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
    const t = setInterval(refreshBalance, 15000)
    return () => clearInterval(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id])

  async function logout() {
    await auth.logout().catch(() => {})
    setUser(null)
    setFirstKey(undefined)
  }

  if (booting) return <div className="ak-app"><p className="ak-muted" style={{ marginTop: 40 }}>加载中…</p></div>
  if (!user) return <Login onAuthed={(u, key) => { setUser(u); setFirstKey(key); setView('user') }} />

  const isAdmin = user.role === 'admin'
  return (
    <div className="ak-app">
      <div className="ak-top">
        <div className="ak-brand">Substantia <span>API</span></div>
        <div className="ak-userbox">
          {isAdmin && (
            <div className="ak-tabs" style={{ margin: 0 }}>
              <div className={`ak-tab ${view === 'user' ? 'active' : ''}`} onClick={() => setView('user')}>用户端</div>
              <div className={`ak-tab ${view === 'admin' ? 'active' : ''}`} onClick={() => setView('admin')}>管理端</div>
            </div>
          )}
          <span className="ak-balance">{fmtUsd(user.balance_micro_usd)}</span>
          <span>{user.email}</span>
          <button className="ak-btn" onClick={logout}>退出</button>
        </div>
      </div>

      {view === 'admin' && isAdmin ? <AdminDashboard /> : <UserDashboard newKey={firstKey} />}
    </div>
  )
}
