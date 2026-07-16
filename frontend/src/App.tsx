import { useEffect, useState } from 'react'
import './ui.css'
import { auth, fmtUsd, portal } from './api'
import { BRAND } from './brand'
import { useI18n, LangToggle } from './i18n'
import { Landing } from './pages/Landing'
import { Login } from './pages/Login'
import { UserDashboard } from './pages/UserDashboard'
import { AdminDashboard } from './pages/AdminDashboard'
import { readParam, pushParams, hrefFor } from './nav'

type User = {
  id: number; email: string; role: string; balance_micro_usd?: number
  trial_active?: boolean; trial_permanent?: boolean; trial_expires_at?: string; trial_usd?: string
  must_change_password?: boolean
}

export default function App() {
  const { t } = useI18n()
  const [user, setUser] = useState<User | null>(null)
  const [booting, setBooting] = useState(true)
  const [firstKey, setFirstKey] = useState<string | undefined>()
  // 已登录用户默认停在落地页(home)，点「控制台」才进 user/admin。带 ?tab 深链(如「去充值」)直达控制台。
  const resolveView = (): 'home' | 'user' | 'admin' => {
    const v = readParam('view', ['home', 'user', 'admin'], '')
    if (v) return v as 'home' | 'user' | 'admin'
    return new URLSearchParams(window.location.search).get('tab') ? 'user' : 'home'
  }
  const [view, setView] = useState<'home' | 'user' | 'admin'>(resolveView)
  // 未登录时的页面：先看落地页，点登录/注册再进表单
  const [authView, setAuthView] = useState<'landing' | 'auth'>('landing')
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')

  // 启动时只在「本浏览器登录过」时才探会话；匿名访客直接进落地页，不打 /portal/me（避免 401 噪音）
  useEffect(() => {
    if (localStorage.getItem('sa_session') !== '1') {
      setBooting(false)
      return
    }
    portal.me()
      .then((me) => setUser(me))
      .catch(() => { setUser(null); localStorage.removeItem('sa_session') })
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

  // 顶部「控制台/Admin」切换：同步到 ?view=，让强制刷新留在本视图；支持浏览器前进/后退。
  function goView(v: 'home' | 'user' | 'admin') {
    setView(v)
    pushParams({ view: v })
  }
  useEffect(() => {
    const onPop = () => setView(resolveView())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function logout() {
    await auth.logout().catch(() => {})
    localStorage.removeItem('sa_session')
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
        onAuthed={(u, key) => { localStorage.setItem('sa_session', '1'); setUser(u); setFirstKey(key); goView('user'); refreshBalance() }}
      />
    )
  }

  // 首次登录（默认密码）强制改密：不改不让进
  if (user.must_change_password) {
    return <ForceChangePassword onDone={async () => { const me = await portal.me(); setUser(me) }} onLogout={logout} />
  }

  // 已登录默认停在落地页；点「控制台」进 user/admin。
  if (view === 'home') {
    return <Landing loggedIn onEnter={() => goView('user')} onAuth={() => goView('user')} />
  }

  const isAdmin = user.role === 'admin'
  return (
    <div className="ak-app">
      <div className="ak-top">
        <div className="ak-brand" style={{ cursor: 'pointer' }} title={t('view_home')}
          onClick={() => goView('home')}>{BRAND.name} <span>{t('brand_tag')}</span></div>
        <div className="ak-userbox">
          {isAdmin && (
            <div className="ak-tabs" style={{ margin: 0 }}>
              <a className={`ak-tab ${view === 'user' ? 'active' : ''}`} href={hrefFor({ view: 'user' })}
                onClick={(e) => { e.preventDefault(); goView('user') }}>{t('console')}</a>
              <a className={`ak-tab ${view === 'admin' ? 'active' : ''}`} href={hrefFor({ view: 'admin' })}
                onClick={(e) => { e.preventDefault(); goView('admin') }}>Admin</a>
            </div>
          )}
          <span className="ak-balance">{fmtUsd(user.balance_micro_usd)}</span>
          <span>{user.email}</span>
          <LangToggle />
          <button className="ak-btn" onClick={logout}>{t('logout')}</button>
        </div>
      </div>

      {user.trial_active && !user.trial_permanent && (
        <div className="ak-keybanner" style={{ background: '#eff6ff', borderColor: 'var(--accent)' }}>
          🎁 {t('trial_banner_1')} <b>{user.trial_usd}</b>{t('trial_banner_2')}{' '}
          <b>{user.trial_expires_at ? new Date(user.trial_expires_at).toLocaleDateString() : '—'}</b>
          {t('trial_banner_3')}<b>{t('trial_permanent')}</b>。
        </div>
      )}

      {view === 'admin' && isAdmin ? <AdminDashboard /> : <UserDashboard newKey={firstKey} />}
    </div>
  )
}

// ForceChangePassword 首次登录（默认密码）强制改密门，不改不放行。
function ForceChangePassword({ onDone, onLogout }: { onDone: () => void; onLogout: () => void }) {
  const { t } = useI18n()
  const [oldPw, setOldPw] = useState('123456')
  const [newPw, setNewPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  async function submit() {
    setErr(null)
    if (newPw.length < 6) { setErr(t('chpw_new')); return }
    if (newPw !== confirm) { setErr(t('chpw_mismatch')); return }
    setBusy(true)
    try {
      await portal.changePassword(oldPw, newPw)
      onDone()
    } catch (e: any) { setErr(t('chpw_fail') + (e?.message || e)) } finally { setBusy(false) }
  }
  return (
    <div className="ak-app">
      <div className="ak-top">
        <div className="ak-brand">{BRAND.name} <span>{t('brand_tag')}</span></div>
        <div className="ak-userbox"><LangToggle /><button className="ak-btn" onClick={onLogout}>{t('logout')}</button></div>
      </div>
      <div className="ak-card" style={{ maxWidth: 420, margin: '40px auto', padding: 24 }}>
        <h2 style={{ marginTop: 0 }}>{t('chpw_title')}</h2>
        <p className="ak-muted" style={{ marginTop: 0 }}>{t('chpw_desc')}</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span className="ak-muted" style={{ fontSize: 13 }}>{t('chpw_old')}</span>
            <input className="ak-input" style={{ width: '100%' }} type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span className="ak-muted" style={{ fontSize: 13 }}>{t('chpw_new')}</span>
            <input className="ak-input" style={{ width: '100%' }} type="password" autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
          </label>
          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span className="ak-muted" style={{ fontSize: 13 }}>{t('chpw_confirm')}</span>
            <input className="ak-input" style={{ width: '100%' }} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </label>
        </div>
        {err && <div className="ak-err" style={{ marginTop: 10 }}>{err}</div>}
        <div style={{ marginTop: 16 }}>
          <button className="ak-btn primary" style={{ width: '100%' }} disabled={busy || !newPw || !confirm} onClick={submit}>
            {busy ? t('chpw_submitting') : t('chpw_submit')}
          </button>
        </div>
      </div>
    </div>
  )
}
