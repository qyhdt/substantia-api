import { useI18n, LangToggle } from '../i18n'
import { BRAND } from '../brand'

// Anthropic 官网价（每百万 token，美元）。展示时同时给出 ×50% 的实付价。
const DISCOUNT = 0.5
const PRICES = [
  { model: 'claude-opus-4-8', in: 5, out: 25 },
  { model: 'claude-sonnet-4-6', in: 3, out: 15 },
  { model: 'claude-haiku-4-5', in: 1, out: 5 },
  { model: 'claude-fable-5', in: 10, out: 50 },
]
const usd = (n: number) => `$${n.toFixed(2)}`

export function Landing(
  { onAuth, loggedIn, onEnter }:
  { onAuth: (mode: 'login' | 'register') => void; loggedIn?: boolean; onEnter?: () => void }
) {
  const { t } = useI18n()
  return (
    <div className="lp">
      {/* 顶栏 */}
      <nav className="lp-nav">
        <div className="lp-brand" style={loggedIn ? { cursor: 'pointer' } : undefined}
          onClick={loggedIn ? onEnter : undefined}>{BRAND.name} <span>{t('brand_tag')}</span></div>
        <div className="lp-nav-links">
          <a href="#features">{t('nav_features')}</a>
          <a href="#pricing">{t('nav_pricing')}</a>
          <LangToggle />
          {loggedIn ? (
            <button className="ak-btn primary" onClick={onEnter}>{t('console')}</button>
          ) : (
            <>
              <button className="ak-btn" onClick={() => onAuth('login')}>{t('login')}</button>
              <button className="ak-btn primary" onClick={() => onAuth('register')}>{t('get_started')}</button>
            </>
          )}
        </div>
      </nav>

      {/* Hero */}
      <header className="lp-hero">
        <h1>{t('hero_title')}</h1>
        <p>{t('hero_sub')}</p>
        <div className="lp-hero-cta">
          {loggedIn ? (
            <button className="ak-btn primary lp-btn-lg" onClick={onEnter}>{t('console')}</button>
          ) : (
            <button className="ak-btn primary lp-btn-lg" onClick={() => onAuth('register')}>{t('hero_cta1')}</button>
          )}
          <a className="ak-btn lp-btn-lg" href="#pricing">{t('hero_cta2')}</a>
        </div>
        {!loggedIn && <div className="lp-trial">🎁 {t('free_trial_note')}</div>}
      </header>

      {/* 价格（放最前：用户一进来就能看到 5 折）*/}
      <section id="pricing" className="lp-section">
        <div className="lp-center" style={{ marginBottom: 10 }}>
          <span className="lp-badge">{t('pricing_badge')}</span>
        </div>
        <h2>{t('pricing_title')}</h2>
        <p className="ak-muted lp-center">{t('pricing_sub')}</p>
        <div className="lp-pricing">
          <table className="ak-table">
            <thead>
              <tr>
                <th>{t('pricing_col_model')}</th>
                <th>{t('pricing_col_in')}</th>
                <th>{t('pricing_col_out')}</th>
              </tr>
            </thead>
            <tbody>
              {PRICES.map((p) => (
                <tr key={p.model}>
                  <td className="ak-mono">{p.model}</td>
                  <td>
                    <span className="lp-off">{t('pricing_official')} {usd(p.in)}</span>{' '}
                    <b className="lp-now">{usd(p.in * DISCOUNT)}</b>
                  </td>
                  <td>
                    <span className="lp-off">{t('pricing_official')} {usd(p.out)}</span>{' '}
                    <b className="lp-now">{usd(p.out * DISCOUNT)}</b>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="ak-muted" style={{ fontSize: 12, marginTop: 10 }}>{t('pricing_note')}</p>
          <button className="ak-btn primary lp-btn-lg" style={{ marginTop: 12 }}
            onClick={loggedIn ? onEnter : () => onAuth('register')}>{loggedIn ? t('console') : t('pricing_cta')}</button>
        </div>
      </section>

      {/* 功能 */}
      <section id="features" className="lp-section">
        <h2>{t('features_title')}</h2>
        <div className="lp-features">
          {[
            { i: '🔌', t: t('feat1_t'), d: t('feat1_d') },
            { i: '⚡', t: t('feat2_t'), d: t('feat2_d') },
            { i: '💳', t: t('feat3_t'), d: t('feat3_d') },
            { i: '🔑', t: t('feat4_t'), d: t('feat4_d') },
          ].map((f, i) => (
            <div className="lp-feat" key={i}>
              <div className="lp-feat-i">{f.i}</div>
              <h3>{f.t}</h3>
              <p>{f.d}</p>
            </div>
          ))}
        </div>
      </section>

      <footer className="lp-footer">{t('footer_tag')}</footer>
    </div>
  )
}
