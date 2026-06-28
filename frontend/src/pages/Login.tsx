import { useEffect, useState } from 'react'
import { auth } from '../api'
import { getDeviceId } from '../device'
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

  // 图形验证码
  const [capRequired, setCapRequired] = useState(true)
  const [emailVerifyOn, setEmailVerifyOn] = useState(false)
  const [capId, setCapId] = useState('')
  const [capImg, setCapImg] = useState('')
  const [capText, setCapText] = useState('')

  // 邮箱验证码（注册）
  const [emailCode, setEmailCode] = useState('')
  const [sendingCode, setSendingCode] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const [codeMsg, setCodeMsg] = useState('')

  async function refreshCaptcha() {
    try {
      const r = await auth.captcha()
      setCapId(r.captcha_id); setCapImg(r.image); setCapText('')
    } catch { /* ignore */ }
  }

  useEffect(() => {
    auth.signupConfig()
      .then((c) => { setCapRequired(c.captcha_required); setEmailVerifyOn(c.email_verify_required) })
      .catch(() => {})
    refreshCaptcha()
  }, [])

  useEffect(() => {
    if (countdown <= 0) return
    const id = setTimeout(() => setCountdown((n) => n - 1), 1000)
    return () => clearTimeout(id)
  }, [countdown])

  async function sendCode() {
    setCodeMsg('')
    if (!email.trim()) { setCodeMsg(t('enter_email_first')); return }
    if (capRequired && !capText.trim()) { setCodeMsg(t('enter_captcha_first')); return }
    setSendingCode(true)
    try {
      await auth.sendEmailCode(email.trim(), capId, capText)
      setCodeMsg(t('code_sent'))
      setCountdown(60)
      // 不刷新图形码：发码只校验未消费，留到注册时用
    } catch (err: any) {
      setCodeMsg(err?.message || t('failed'))
    } finally {
      setSendingCode(false)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'register') {
        const device_id = await getDeviceId()
        const r = await auth.register({
          email, password, captcha_id: capId, captcha_text: capText,
          email_code: emailVerifyOn ? emailCode.trim() : undefined, device_id,
        })
        onAuthed(r.user, r.api_key)
      } else {
        const r = await auth.login(email, password, capId, capText)
        onAuthed(r.user)
      }
    } catch (err: any) {
      setError(err?.message || t('failed'))
      if (capRequired) refreshCaptcha()  // 验证码单次消费，失败后换一张
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

          {mode === 'register' && emailVerifyOn && (
            <div className="ak-row" style={{ gap: 8 }}>
              <input className="ak-input" style={{ flex: 1 }} inputMode="numeric" maxLength={6}
                placeholder={t('email_code')} value={emailCode}
                onChange={(e) => setEmailCode(e.target.value.replace(/[^0-9]/g, ''))} required />
              <button type="button" className="ak-btn" style={{ width: 130, flexShrink: 0 }}
                onClick={sendCode} disabled={sendingCode || countdown > 0}>
                {countdown > 0 ? `${countdown}s` : sendingCode ? t('sending') : t('send_code')}
              </button>
            </div>
          )}
          {mode === 'register' && emailVerifyOn && codeMsg && (
            <div className="ak-muted" style={{ fontSize: 12, marginTop: -4, marginBottom: 6 }}>{codeMsg}</div>
          )}

          {capRequired && (
            <div className="ak-row" style={{ gap: 8 }}>
              <input className="ak-input" style={{ flex: 1 }} placeholder={t('captcha')} value={capText}
                autoComplete="off" onChange={(e) => setCapText(e.target.value)} required />
              <img src={capImg} alt="captcha" title={t('captcha_refresh')} onClick={refreshCaptcha}
                style={{ width: 130, height: 44, cursor: 'pointer', borderRadius: 8, flexShrink: 0 }} />
            </div>
          )}

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
