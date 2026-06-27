import { useI18n, LangToggle } from '../i18n'

const PRICES = [
  { model: 'claude-opus-4', in: '$15.00', out: '$75.00' },
  { model: 'claude-sonnet-4', in: '$3.00', out: '$15.00' },
  { model: 'claude-haiku', in: '$0.80', out: '$4.00' },
]

export function Landing({ onAuth }: { onAuth: (mode: 'login' | 'register') => void }) {
  const { t } = useI18n()
  return (
    <div className="lp">
      {/* 顶栏 */}
      <nav className="lp-nav">
        <div className="lp-brand">Substantia <span>{t('brand_tag')}</span></div>
        <div className="lp-nav-links">
          <a href="#features">{t('nav_features')}</a>
          <a href="#pricing">{t('nav_pricing')}</a>
          <LangToggle />
          <button className="ak-btn" onClick={() => onAuth('login')}>{t('login')}</button>
          <button className="ak-btn primary" onClick={() => onAuth('register')}>{t('get_started')}</button>
        </div>
      </nav>

      {/* Hero */}
      <header className="lp-hero">
        <h1>{t('hero_title')}</h1>
        <p>{t('hero_sub')}</p>
        <div className="lp-hero-cta">
          <button className="ak-btn primary lp-btn-lg" onClick={() => onAuth('register')}>{t('hero_cta1')}</button>
          <a className="ak-btn lp-btn-lg" href="#pricing">{t('hero_cta2')}</a>
        </div>
        <div className="lp-trial">🎁 {t('free_trial_note')}</div>
      </header>

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

      {/* 价格 */}
      <section id="pricing" className="lp-section">
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
                  <td>{p.in}</td>
                  <td>{p.out}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="ak-muted" style={{ fontSize: 12, marginTop: 10 }}>{t('pricing_note')}</p>
          <button className="ak-btn primary lp-btn-lg" style={{ marginTop: 12 }}
            onClick={() => onAuth('register')}>{t('pricing_cta')}</button>
        </div>
      </section>

      <footer className="lp-footer">{t('footer_tag')}</footer>
    </div>
  )
}
