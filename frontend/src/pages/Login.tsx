import { useState } from 'react'
import { auth } from '../api'
import { useI18n, LangToggle } from '../i18n'

export function Login({ initialMode = 'login', onAuthed, onBack }: {
  initialMode?: 'login' | 'register'
  onAuthed: (user: any, firstKey?: string) => void
  onBack: () => void
}) {
  const { t } = useI18n()
  const [mode, setMode] = useState<'login' | 'register'>(initialMode)
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
      setError(err?.message || t('failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ak-auth">
      <div className="ak-row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
        <a onClick={onBack} style={{ cursor: 'pointer' }} className="ak-muted">{t('back_home')}</a>
        <LangToggle />
      </div>
      <div className="ak-card">
        <h2>Substantia <span style={{ color: 'var(--accent)' }}>{t('brand_tag')}</span></h2>
        <p className="ak-muted">{mode === 'login' ? t('login') : t('free_trial_note')}</p>
        <form onSubmit={submit}>
          <input className="ak-input" type="email" placeholder={t('email')} value={email}
            onChange={(e) => setEmail(e.target.value)} required />
          <input className="ak-input" type="password" placeholder={t('password')} value={password}
            onChange={(e) => setPassword(e.target.value)} required minLength={6} />
          {error && <div className="ak-err">{error}</div>}
          <button className="ak-btn primary" style={{ width: '100%' }} disabled={busy}>
            {busy ? t('submitting') : mode === 'login' ? t('login') : t('register')}
          </button>
        </form>
        <p className="ak-muted" style={{ marginTop: 14 }}>
          {mode === 'login' ? t('no_account') : t('has_account')}{' '}
          <a onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null) }}
            style={{ cursor: 'pointer' }}>
            {mode === 'login' ? t('to_register') : t('to_login')}
          </a>
        </p>
      </div>
    </div>
  )
}
