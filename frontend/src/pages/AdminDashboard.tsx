import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { admin, RMB_PER_USD_FALLBACK } from '../api'
import { Async, Card, Pager, Pill, useAsync } from '../components/common'
import { CostDistributionChart } from '../components/CostDistributionChart'
import { DailySpendChart } from '../components/DailySpendChart'
import { readParam, pushParams, hrefFor } from '../nav'
import { useI18n, type TKey } from '../i18n'
import { fmtDisplayCurrency, useDisplayCurrency, useRmbPerUsd, type DisplayCurrency } from '../currency'

const fmtTime = (t?: string | null) => (t ? new Date(t).toLocaleString() : '—')
const fmtCount = (value: any) => new Intl.NumberFormat().format(Number(value || 0))

const TABS = [
  ['topups', 'admin_tab_topups'],
  ['payments', 'admin_tab_payments'],
  ['users', 'admin_tab_users'],
  ['prices', 'admin_tab_prices'],
  ['slots', 'admin_tab_slots'],
  ['moxing', 'admin_tab_moxing'],
  ['usage', 'admin_tab_usage'],
] as const satisfies ReadonlyArray<readonly [string, TKey]>

const SECTIONS = TABS.map(([k]) => k)

export function AdminDashboard() {
  const { t } = useI18n()
  const [tab, setTab] = useState<typeof TABS[number][0]>(
    () => readParam('section', SECTIONS, 'topups') as typeof TABS[number][0])
  // 菜单同步到 ?section=，强制刷新留在本页；支持浏览器前进/后退。
  useEffect(() => {
    const onPop = () => setTab(readParam('section', SECTIONS, 'topups') as typeof TABS[number][0])
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  function go(k: typeof TABS[number][0]) {
    setTab(k)
    pushParams({ view: 'admin', section: k })
  }
  return (
    <div className="ak-sidelayout">
      <aside className="ak-sidebar">
        {TABS.map(([k, label]) => (
          <a key={k} className={`ak-side-item ${tab === k ? 'active' : ''}`}
            href={hrefFor({ view: 'admin', section: k })}
            onClick={(e) => { e.preventDefault(); go(k) }}>{t(label)}</a>
        ))}
      </aside>
      <section className="ak-sidecontent">
        {tab === 'topups' && <Topups />}
        {tab === 'payments' && <Payments />}
        {tab === 'users' && <Users />}
        {tab === 'prices' && <Prices />}
        {tab === 'slots' && <Slots />}
        {tab === 'moxing' && <MoxingAccounting />}
        {tab === 'usage' && <UsageBoard />}
      </section>
    </div>
  )
}

function Topups() {
  const { t } = useI18n()
  const [currency] = useDisplayCurrency()
  const rmbPerUsd = useRmbPerUsd()
  const state = useAsync(() => admin.topups(), [])
  async function review(id: number, approve: boolean) {
    await admin.reviewTopup(id, approve)
    state.reload()
  }
  return (
    <Card title={t('admin_topups_title')}>
      <Async state={state}>{(rows: any[]) => (
        <table className="ak-table">
          <thead><tr><th>{t('admin_col_time')}</th><th>{t('admin_col_user')}</th><th>{t('admin_col_amount')}</th><th>{t('admin_col_reason')}</th><th>{t('admin_col_proof')}</th><th>{t('admin_col_status')}</th><th></th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.email}</td>
                <td>{fmtDisplayCurrency(r.requested_micro_usd, currency, rmbPerUsd)}</td>
                <td className="ak-muted">{r.reason || '—'}</td>
                <td>{r.proof_url
                  ? <a className="ak-link" href={r.proof_url} target="_blank" rel="noreferrer">
                      <img src={r.proof_url} alt={t('admin_col_proof')} style={{ height: 36, borderRadius: 4, border: '1px solid var(--border)', verticalAlign: 'middle' }} />
                    </a>
                  : <span className="ak-muted">—</span>}</td>
                <td><Pill kind={r.status === 'approved' ? 'ok' : r.status === 'rejected' ? 'bad' : 'warn'}>{r.status}</Pill></td>
                <td>{r.status === 'pending' && (
                  <div className="ak-row">
                    <button className="ak-btn primary" onClick={() => review(r.id, true)}>{t('admin_approve')}</button>
                    <button className="ak-btn danger" onClick={() => review(r.id, false)}>{t('admin_reject')}</button>
                  </div>
                )}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={7} className="ak-muted">{t('admin_empty_topups')}</td></tr>}
          </tbody>
        </table>
      )}</Async>
    </Card>
  )
}

function Payments() {
  const { t } = useI18n()
  const [currency] = useDisplayCurrency()
  const rmbPerUsd = useRmbPerUsd()
  const state = useAsync(() => admin.payments(), [])
  const provLabel = (p: string) => p === 'xunhupay' ? '虎皮椒' : p === 'polar' ? 'Polar' : (p || '—')
  return (
    <Card title={t('admin_payments_title')}>
      <Async state={state}>{(data: any) => {
        const rows: any[] = data?.items || []
        return (
          <table className="ak-table">
            <thead><tr>
              <th>{t('admin_col_time')}</th><th>{t('admin_col_user')}</th>
              <th>{t('admin_col_provider')}</th><th>{t('admin_col_amount')}</th>
              <th>{t('admin_col_rmb')}</th><th>{t('admin_col_order')}</th>
              <th>{t('admin_col_status')}</th><th>{t('admin_col_paid')}</th>
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="ak-muted">{fmtTime(r.created_at)}</td>
                  <td>{r.user_email}</td>
                  <td>{provLabel(r.provider)}</td>
                  <td>{fmtDisplayCurrency(r.amount_micro_usd, currency, rmbPerUsd)}</td>
                  <td className="ak-muted">{r.amount_rmb != null ? `¥${Number(r.amount_rmb).toFixed(2)}` : '—'}</td>
                  <td className="ak-mono ak-muted" style={{ fontSize: 12 }}>{r.out_trade_no}</td>
                  <td><Pill kind={r.status === 'paid' ? 'ok' : 'warn'}>{r.status}</Pill></td>
                  <td className="ak-muted">{fmtTime(r.paid_at)}</td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={8} className="ak-muted">{t('admin_empty_payments')}</td></tr>}
            </tbody>
          </table>
        )
      }}</Async>
    </Card>
  )
}

function Users() {
  const { t } = useI18n()
  const [currency] = useDisplayCurrency()
  const rmbPerUsd = useRmbPerUsd()
  const state = useAsync(() => admin.users(), [])
  const [detailId, setDetailId] = useState<number | null>(null)
  const [nu, setNu] = useState({ email: '', role: 'user', balance: '0' })
  const [creating, setCreating] = useState(false)
  async function createUser() {
    if (!nu.email) return
    setCreating(true)
    try {
      // 默认密码 123456，后端置 must_change_password，用户首次登录须改密
      const r = await admin.createUser({
        email: nu.email.trim(), password: '123456', role: nu.role,
        balance_usd: Number(nu.balance) || 0,
      })
      alert(`${t('admin_adduser_ok')}\n\n${r.api_key_plain}`)
      setNu({ email: '', role: 'user', balance: '0' })
      state.reload()
    } catch (e: any) {
      alert(t('admin_adduser_fail') + (e?.message || e))
    } finally { setCreating(false) }
  }
  async function grant(id: number) {
    const v = prompt(t('admin_grant_prompt').replace('USD', currency.toUpperCase()), '10')
    if (v == null) return
    await admin.grant(id, currency === 'rmb' ? Number(v) / rmbPerUsd : Number(v))
    state.reload()
  }
  async function toggleStatus(u: any) {
    await admin.setUserStatus(u.id, u.status === 'active' ? 'disabled' : 'active')
    state.reload()
  }
  async function toggleRole(u: any) {
    await admin.setRole(u.id, u.role === 'admin' ? 'user' : 'admin')
    state.reload()
  }
  async function setMult(u: any) {
    const cur = u.price_multiplier == null ? 1 : Number(u.price_multiplier)
    const v = prompt(t('admin_mult_prompt'), String(cur))
    if (v == null) return
    const n = Number(v)
    if (!isFinite(n) || n < 0 || n > 100) { alert(t('admin_mult_range')); return }
    await admin.setMultiplier(u.id, n)
    state.reload()
  }
  async function toggleFullModelAccess(u: any) {
    await admin.setFullModelAccess(u.id, !u.full_model_access)
    state.reload()
  }
  const fmtMult = (m: any) => (m == null ? '1' : Number(m).toString())
  return (
    <Card title={t('admin_users_title')}>
      <p className="ak-muted">{t('admin_users_desc')}</p>
      <div className="ak-row" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 4 }}>
        <input className="ak-input" placeholder={t('admin_adduser_email')} value={nu.email}
          onChange={(e) => setNu({ ...nu, email: e.target.value })} />
        <select className="ak-input" value={nu.role} onChange={(e) => setNu({ ...nu, role: e.target.value })}>
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
        <input className="ak-input" type="number" style={{ width: 140 }} placeholder={`${t('admin_col_balance')} (${currency.toUpperCase()})`}
          value={Number((Number(nu.balance || 0) * (currency === 'rmb' ? rmbPerUsd : 1)).toFixed(4))}
          onChange={(e) => setNu({ ...nu, balance: String(Number(e.target.value) / (currency === 'rmb' ? rmbPerUsd : 1)) })} />
        <button className="ak-btn primary" disabled={creating || !nu.email} onClick={createUser}>
          {creating ? t('admin_adduser_creating') : t('admin_adduser_btn')}
        </button>
      </div>
      <div className="ak-muted" style={{ fontSize: 12, marginBottom: 14 }}>{t('admin_adduser_defpw')}</div>
      <Async state={state}>{(rows: any[]) => (
        <table className="ak-table">
          <thead><tr><th>{t('admin_col_id')}</th><th>{t('admin_col_email')}</th><th>{t('admin_col_role')}</th><th>{t('admin_col_status')}</th><th>{t('admin_col_balance')}</th><th>{t('admin_col_mult')}</th><th>{t('admin_col_model_access')}</th><th>{t('admin_col_trial')}</th><th></th></tr></thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>
                  <a className="ak-link" onClick={() => setDetailId(u.id)} style={{ cursor: 'pointer' }}>{u.email}</a>
                </td>
                <td><Pill kind={u.role === 'admin' ? 'warn' : undefined}>{u.role}</Pill></td>
                <td><Pill kind={u.status === 'active' ? 'ok' : 'bad'}>{u.status}</Pill></td>
                <td className="ak-balance">{fmtDisplayCurrency(u.balance_micro_usd, currency, rmbPerUsd)}</td>
                <td><Pill kind={fmtMult(u.price_multiplier) !== '1' ? 'warn' : undefined}>×{fmtMult(u.price_multiplier)}</Pill></td>
                <td><Pill kind={u.full_model_access ? 'ok' : 'warn'}>{u.full_model_access ? t('admin_full_models') : t('admin_glm_only')}</Pill></td>
                <td>{u.trial_active
                  ? <span className="ak-muted">{fmtDisplayCurrency(u.trial_micro_usd, currency, rmbPerUsd)} {t('admin_until')} {u.trial_expires_at ? new Date(u.trial_expires_at).toLocaleDateString() : '—'}</span>
                  : <span className="ak-muted">—</span>}</td>
                <td>
                  <div className="ak-row">
                    <button className="ak-btn" onClick={() => grant(u.id)}>{t('admin_btn_grant')}</button>
                    <button className="ak-btn" onClick={() => setMult(u)}>{t('admin_btn_mult')}</button>
                    <button className="ak-btn" onClick={() => toggleFullModelAccess(u)}>{u.full_model_access ? t('admin_set_glm_only') : t('admin_set_full_models')}</button>
                    <button className="ak-btn" onClick={() => toggleRole(u)}>{u.role === 'admin' ? t('admin_demote') : t('admin_promote')}</button>
                    <button className="ak-btn danger" onClick={() => toggleStatus(u)}>{u.status === 'active' ? t('admin_disable') : t('admin_enable')}</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}</Async>
      {detailId != null && <UserDetailModal userId={detailId} onClose={() => setDetailId(null)} />}
    </Card>
  )
}

function UserDetailModal({ userId, onClose }: { userId: number; onClose: () => void }) {
  const { t } = useI18n()
  const [currency, setCurrency] = useDisplayCurrency()
  const state = useAsync(() => admin.userDetail(userId), [userId])
  return (
    <div className="ak-modal-overlay" onClick={onClose}>
      <div className="ak-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ak-row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{t('admin_user_detail_title')}</h3>
          <div className="ak-row" style={{ gap: 6 }}>
            {(['rmb', 'usd'] as const).map((value) => (
              <button key={value} className={`ak-btn ${currency === value ? 'primary' : ''}`}
                onClick={() => setCurrency(value)}>{value.toUpperCase()}</button>
            ))}
            <button className="ak-btn" onClick={onClose}>{t('admin_close')}</button>
          </div>
        </div>
        <Async state={state}>{(d: any) => {
          const u = d.user || {}
          const s = d.spend || {}
          const rmbPerUsd = Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)
          const money = (value: any) => fmtDisplayCurrency(value, currency, rmbPerUsd)
          return (
            <>
              <div className="ak-detail-grid">
                <div><span className="ak-muted">{t('admin_col_email')}</span><b>{u.email}</b></div>
                <div><span className="ak-muted">{t('admin_role_status')}</span><b>{u.role} / {u.status}</b></div>
                <div><span className="ak-muted">{t('admin_effective_balance')}</span><b className="ak-balance">{money(u.effective_micro_usd)}</b></div>
                <div><span className="ak-muted">{t('admin_paid_bucket')}</span><b>{money(u.paid_micro_usd)}</b></div>
                <div><span className="ak-muted">{t('admin_trial_bucket')}{u.trial_active ? t('admin_trial_valid') : t('admin_trial_invalid')}</span>
                  <b>{money(u.trial_micro_usd)}{u.trial_expires_at ? ` · ${t('admin_until')} ${new Date(u.trial_expires_at).toLocaleDateString()}` : ''}</b></div>
                <div><span className="ak-muted">{t('admin_registered_at')}</span><b>{fmtTime(u.created_at)}</b></div>
              </div>

              <div className="ak-stat-row">
                <div className="ak-stat"><span className="ak-muted">{t('admin_total_cost')}</span><b className="ak-balance">{money(s.total_cost_micro_usd)}</b></div>
                <div className="ak-stat"><span className="ak-muted">{t('admin_calls')}</span><b>{s.total_calls || 0}</b></div>
                <div className="ak-stat"><span className="ak-muted">{t('admin_total_tokens')}</span><b>{s.total_tokens || 0}</b></div>
              </div>

              <h4>{t('admin_by_model')}</h4>
              <table className="ak-table">
                <thead><tr><th>{t('admin_col_model')}</th><th>{t('admin_col_calls')}</th><th>{t('admin_col_in_tok')}</th><th>{t('admin_col_out_tok')}</th><th>{t('admin_col_cost')}</th></tr></thead>
                <tbody>
                  {(s.by_model || []).map((m: any, i: number) => (
                    <tr key={i}>
                      <td className="ak-mono">{m.model || '—'}</td>
                      <td>{m.calls}</td>
                      <td>{m.prompt_tokens || 0}</td>
                      <td>{m.completion_tokens || 0}</td>
                      <td>{money(m.cost)}</td>
                    </tr>
                  ))}
                  {(s.by_model || []).length === 0 && <tr><td colSpan={5} className="ak-muted">{t('admin_empty_spend')}</td></tr>}
                </tbody>
              </table>

              <h4>{t('admin_apikeys_n').replace('{n}', String((d.keys || []).length))}</h4>
              <table className="ak-table">
                <thead><tr><th>{t('admin_col_name')}</th><th>{t('admin_col_prefix')}</th><th>{t('admin_col_status')}</th><th>{t('admin_total_cost')}</th><th>{t('admin_col_last_used')}</th></tr></thead>
                <tbody>
                  {(d.keys || []).map((k: any) => (
                    <tr key={k.id}>
                      <td>{k.name}</td>
                      <td className="ak-mono ak-muted">{k.key_prefix}</td>
                      <td><Pill kind={k.status === 'active' ? 'ok' : 'bad'}>{k.status}</Pill></td>
                      <td>{money(k.spent_micro_usd)}</td>
                      <td className="ak-muted">{fmtTime(k.last_used_at)}</td>
                    </tr>
                  ))}
                  {(d.keys || []).length === 0 && <tr><td colSpan={5} className="ak-muted">{t('admin_no_key')}</td></tr>}
                </tbody>
              </table>

              <h4>{t('admin_recent_usage_n').replace('{n}', String((d.recent_usage || []).length)).replace('{total}', String(d.recent_total || 0))}</h4>
              <table className="ak-table">
                <thead><tr><th>{t('admin_col_time')}</th><th>{t('admin_col_model')}</th><th>slot</th><th>tokens</th><th>{t('admin_col_cost')}</th><th>{t('admin_col_status')}</th></tr></thead>
                <tbody>
                  {(d.recent_usage || []).map((r: any) => (
                    <tr key={r.id}>
                      <td className="ak-muted">{fmtTime(r.created_at)}</td>
                      <td className="ak-mono">{r.model || '—'}</td>
                      <td className="ak-mono ak-muted">{r.slot_id || '—'}</td>
                      <td>{r.total_tokens || 0}</td>
                      <td>{money(r.cost_micro_usd)}</td>
                      <td><Pill kind={r.status === 'ok' ? 'ok' : 'bad'}>{r.status || '—'}</Pill></td>
                    </tr>
                  ))}
                  {(d.recent_usage || []).length === 0 && <tr><td colSpan={6} className="ak-muted">{t('admin_empty_usage')}</td></tr>}
                </tbody>
              </table>
            </>
          )
        }}</Async>
      </div>
    </div>
  )
}

function Prices() {
  const { t } = useI18n()
  const [currency] = useDisplayCurrency()
  const rmbPerUsd = useRmbPerUsd()
  const factor = currency === 'rmb' ? rmbPerUsd : 1
  const state = useAsync(() => admin.prices(), [])
  const [f, setF] = useState({ model: '', display_name: '', input: 0, output: 0, cacheRead: 0, cacheWrite: 0 })
  // 编辑状态保留 USD/百万 token，输入框按当前显示币种换算；保存时转回 micro-USD/1k。
  const [edit, setEdit] = useState<Record<string, { input: number; output: number; cacheRead: number; cacheWrite: number }>>({})
  async function save() {
    if (!f.model) return
    await admin.upsertPrice({
      model: f.model, display_name: f.display_name,
      input_micro_usd_per_1k: Math.round(f.input * 1000), output_micro_usd_per_1k: Math.round(f.output * 1000),
      cache_read_micro_usd_per_1k: Math.round(f.cacheRead * 1000), cache_write_micro_usd_per_1k: Math.round(f.cacheWrite * 1000), enabled: true,
    })
    setF({ model: '', display_name: '', input: 0, output: 0, cacheRead: 0, cacheWrite: 0 })
    state.reload()
  }
  function startEdit(p: any) {
    setEdit({ ...edit, [p.model]: {
      input: (p.input_micro_usd_per_1k || 0) / 1000,
      output: (p.output_micro_usd_per_1k || 0) / 1000,
      cacheRead: (p.cache_read_micro_usd_per_1k || 0) / 1000,
      cacheWrite: (p.cache_write_micro_usd_per_1k || 0) / 1000,
    } })
  }
  function cancelEdit(model: string) {
    const e = { ...edit }; delete e[model]; setEdit(e)
  }
  async function saveRow(p: any) {
    const e = edit[p.model]; if (!e) return
    await admin.upsertPrice({
      model: p.model, display_name: p.display_name,
      input_micro_usd_per_1k: Math.round(e.input * 1000), output_micro_usd_per_1k: Math.round(e.output * 1000),
      cache_read_micro_usd_per_1k: Math.round(e.cacheRead * 1000), cache_write_micro_usd_per_1k: Math.round(e.cacheWrite * 1000),
      enabled: p.enabled,
    })
    cancelEdit(p.model)
    state.reload()
  }
  async function toggleEnabled(p: any) {
    await admin.upsertPrice({
      model: p.model, display_name: p.display_name,
      input_micro_usd_per_1k: p.input_micro_usd_per_1k, output_micro_usd_per_1k: p.output_micro_usd_per_1k,
      cache_read_micro_usd_per_1k: p.cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k: p.cache_write_micro_usd_per_1k,
      enabled: !p.enabled,
    })
    state.reload()
  }
  return (
    <>
      <Card title={t('admin_price_add_title')}>
        <div className="ak-row" style={{ flexWrap: 'wrap' }}>
          <input className="ak-input" placeholder="model id" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} />
          <input className="ak-input" placeholder={t('admin_ph_display_name')} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <input className="ak-input" type="number" placeholder={`${t('admin_ph_input_price')} ${currency.toUpperCase()}`} value={Number((f.input * factor).toFixed(6))} onChange={(e) => setF({ ...f, input: Number(e.target.value) / factor })} style={{ width: 130 }} />
          <input className="ak-input" type="number" placeholder={`${t('admin_ph_output_price')} ${currency.toUpperCase()}`} value={Number((f.output * factor).toFixed(6))} onChange={(e) => setF({ ...f, output: Number(e.target.value) / factor })} style={{ width: 130 }} />
          <input className="ak-input" type="number" placeholder={`${t('admin_cache_read')} ${currency.toUpperCase()}`} value={Number((f.cacheRead * factor).toFixed(6))} onChange={(e) => setF({ ...f, cacheRead: Number(e.target.value) / factor })} style={{ width: 130 }} />
          <input className="ak-input" type="number" placeholder={`${t('admin_cache_write')} ${currency.toUpperCase()}`} value={Number((f.cacheWrite * factor).toFixed(6))} onChange={(e) => setF({ ...f, cacheWrite: Number(e.target.value) / factor })} style={{ width: 130 }} />
          <button className="ak-btn primary" onClick={save}>{t('admin_save')}</button>
        </div>
      </Card>
      <Card title={t('admin_price_table_title')}>
        <Async state={state}>{(rows: any[]) => (
          <div className="ak-table-scroll"><table className="ak-table model-price-table">
            <thead><tr><th>{t('admin_col_model')}</th><th>{t('admin_col_display_name')}</th><th>{t('admin_col_in_million')}</th><th>{t('admin_col_out_million')}</th><th>{t('admin_cache_read')}</th><th>{t('admin_cache_write')}</th><th>{t('admin_col_enabled')}</th><th></th></tr></thead>
            <tbody>
              {rows.map((p) => {
                const e = edit[p.model]
                return (
                  <tr key={p.id}>
                    <td className="ak-mono">{p.model}</td>
                    <td>{p.display_name || '—'}</td>
                    <td>{e
                      ? <input className="ak-input" type="number" step="0.0001" value={Number((e.input * factor).toFixed(6))} style={{ width: 110, minWidth: 0 }}
                          onChange={(ev) => setEdit({ ...edit, [p.model]: { ...e, input: Number(ev.target.value) / factor } })} />
                      : fmtDisplayCurrency(Number(p.input_micro_usd_per_1k || 0) * 1000, currency, rmbPerUsd, 2)}</td>
                    <td>{e
                      ? <input className="ak-input" type="number" step="0.0001" value={Number((e.output * factor).toFixed(6))} style={{ width: 110, minWidth: 0 }}
                          onChange={(ev) => setEdit({ ...edit, [p.model]: { ...e, output: Number(ev.target.value) / factor } })} />
                      : fmtDisplayCurrency(Number(p.output_micro_usd_per_1k || 0) * 1000, currency, rmbPerUsd, 2)}</td>
                    <td>{e
                      ? <input className="ak-input" type="number" step="0.0001" value={Number((e.cacheRead * factor).toFixed(6))} style={{ width: 110, minWidth: 0 }}
                          onChange={(ev) => setEdit({ ...edit, [p.model]: { ...e, cacheRead: Number(ev.target.value) / factor } })} />
                      : fmtDisplayCurrency(Number(p.cache_read_micro_usd_per_1k || 0) * 1000, currency, rmbPerUsd, 2)}</td>
                    <td>{e
                      ? <input className="ak-input" type="number" step="0.0001" value={Number((e.cacheWrite * factor).toFixed(6))} style={{ width: 110, minWidth: 0 }}
                          onChange={(ev) => setEdit({ ...edit, [p.model]: { ...e, cacheWrite: Number(ev.target.value) / factor } })} />
                      : fmtDisplayCurrency(Number(p.cache_write_micro_usd_per_1k || 0) * 1000, currency, rmbPerUsd, 2)}</td>
                    <td>{p.supplier_managed
                      ? <Pill kind="warn">{t('admin_supplier_managed')}</Pill>
                      : <a className="ak-link" style={{ cursor: 'pointer' }} onClick={() => toggleEnabled(p)}>
                          <Pill kind={p.enabled ? 'ok' : 'bad'}>{p.enabled ? 'on' : 'off'}</Pill>
                        </a>}
                    </td>
                    <td>
                      <div className="ak-row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                        {p.supplier_managed
                          ? <span className="ak-muted" style={{ fontSize: 12 }}>{t('admin_go_moxing_terms')}</span>
                          : e
                          ? <>
                              <button className="ak-btn primary" onClick={() => saveRow(p)}>{t('admin_save')}</button>
                              <button className="ak-btn" onClick={() => cancelEdit(p.model)}>{t('admin_cancel')}</button>
                            </>
                          : <button className="ak-btn" onClick={() => startEdit(p)}>{t('admin_edit_price')}</button>}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table></div>
        )}</Async>
      </Card>
    </>
  )
}

function Slots() {
  const { t } = useI18n()
  const state = useAsync(() => admin.slots(), [])
  const [msg, setMsg] = useState<string | null>(null)
  const [na, setNa] = useState({ server_ip: '', slot_id: '', creds_json: '', weight: '1' })
  const [adding, setAdding] = useState(false)
  async function ensure() {
    setMsg(t('admin_ensuring'))
    try {
      await admin.ensureContainers()
      setMsg(t('admin_ensure_done'))
    } catch (e: any) {
      setMsg(e?.message || t('admin_ensure_fail'))
    }
  }
  async function del(id: string, server_ip?: string) {
    if (!confirm(t('admin_confirm_del_slot').replace('{id}', id))) return
    await admin.deleteSlot(id, server_ip)
    state.reload()
  }
  async function toggle(s: any) {
    await admin.setSlotEnabled(s.id, s.server_ip, !s.enabled)
    state.reload()
  }
  async function reassign(s: any) {
    const to = prompt(t('admin_slot_reassign_prompt'), s.server_ip)
    if (to == null) return
    const dst = to.trim()
    if (!dst || dst === s.server_ip) return
    try {
      await admin.reassignSlot(s.id, s.server_ip, dst)
      state.reload()
    } catch (e: any) { alert(e?.message || e) }
  }
  async function addAccount() {
    if (!na.slot_id || !na.creds_json) return
    setAdding(true)
    try {
      await admin.createSlot({ server_ip: na.server_ip.trim(), slot_id: na.slot_id.trim(), type: 'subscription', weight: Number(na.weight) || 1, creds_json: na.creds_json })
      setMsg(t('admin_slot_add_ok'))
      setNa({ server_ip: '', slot_id: '', creds_json: '', weight: '1' })
      state.reload()
    } catch (e: any) {
      setMsg(t('admin_slot_add_fail') + (e?.message || e))
    } finally { setAdding(false) }
  }
  return (
    <>
    <LoginNewAccount onDone={() => state.reload()} />
    <CodexAccounts />
    <Card title={t('admin_slots_title')} actions={<button className="ak-btn" onClick={ensure}>{t('admin_ensure_all')}</button>}>
      {msg && <div className="ak-ok">{msg}</div>}
      <div className="ak-muted" style={{ marginBottom: 14 }}>
        <p style={{ margin: '0 0 4px' }}>
          <b>{t('admin_slot_route_order')}</b>: <b>subscription</b> → <b>moxing</b> → <b>Gemini</b>
        </p>
        <p style={{ margin: '0 0 4px' }}>{t('admin_slot_route_detail')}</p>
        <p style={{ margin: 0 }}>{t('admin_slot_billing_note')} {t('admin_slot_secret_note')}</p>
      </div>
      <Async state={state}>{(d: any) => {
        const isDB = d.source === 'db'
        const slots = d.slots || []
        const hasPriority = slots.some((s: any) => s.priority != null)
        return (
        <>
        <table className="ak-table">
          <thead><tr>
            {isDB && <th>{t('admin_col_server')}</th>}
            <th>{t('admin_col_id')}</th><th>{t('admin_col_type')}</th>
            {hasPriority && <th title={t('admin_priority_hint')}>{t('admin_col_priority')}</th>}
            <th>{t('admin_col_weight')}</th>
            {isDB && <th>{t('admin_col_enabled')}</th>}
            <th>{t('admin_col_health')}</th><th>{t('admin_col_routable')}</th><th>{t('admin_col_image_env')}</th><th></th>
          </tr></thead>
          <tbody>
            {slots.map((s: any) => {
              const managed = s.managed === true || s.id === 'fallback-moxing' || s.id === 'fallback-gemini'
              return (
              <tr key={`${s.server_ip || ''}/${s.id}`}>
                {isDB && <td className="ak-mono">{s.server_ip}</td>}
                <td className="ak-mono">{s.id}</td>
                <td><Pill kind={s.type === 'subscription' ? 'ok' : 'warn'}>{s.type}</Pill></td>
                {hasPriority && <td title={t('admin_priority_hint')}>{s.priority ?? '—'}</td>}
                <td>{s.weight}</td>
                {isDB && <td><Pill kind={s.enabled ? 'ok' : 'bad'}>{s.enabled ? '✓' : '✕'}</Pill></td>}
                <td><Pill kind={s.health === 'healthy' ? 'ok' : s.health === 'unknown' ? undefined : 'bad'}>{s.health}</Pill></td>
                <td>{s.routable ? '✓' : '✕'}</td>
                <td className="ak-mono ak-muted">{s.image || (s.env_keys?.length ? s.env_keys.join(',') : '—')}</td>
                <td>{managed
                  ? <Pill>{t('admin_slot_env_managed')}</Pill>
                  : <div className="ak-row">
                      {isDB && <button className="ak-btn" onClick={() => reassign(s)}>{t('admin_slot_reassign')}</button>}
                      {isDB && <button className="ak-btn" onClick={() => toggle(s)}>{s.enabled ? t('admin_slot_disable') : t('admin_slot_enable')}</button>}
                      <button className="ak-btn danger" onClick={() => del(s.id, s.server_ip)}>{t('admin_delete')}</button>
                    </div>}
                </td>
              </tr>
              )
            })}
            {slots.length === 0 && <tr><td colSpan={(isDB ? 9 : 7) + (hasPriority ? 1 : 0)} className="ak-muted">{t('admin_empty_slots')}</td></tr>}
          </tbody>
        </table>
        {isDB && (
          <details style={{ marginTop: 18 }}>
            <summary className="ak-muted" style={{ cursor: 'pointer' }}>{t('admin_slot_add_title')}</summary>
            <div className="ak-row" style={{ flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
              <input className="ak-input" style={{ width: 160 }} placeholder={t('admin_slot_add_server')} value={na.server_ip}
                onChange={(e) => setNa({ ...na, server_ip: e.target.value })} />
              <input className="ak-input" style={{ width: 130 }} placeholder={t('admin_slot_add_id')} value={na.slot_id}
                onChange={(e) => setNa({ ...na, slot_id: e.target.value })} />
              <input className="ak-input" type="number" style={{ width: 90 }} placeholder={t('admin_col_weight')} value={na.weight}
                onChange={(e) => setNa({ ...na, weight: e.target.value })} />
            </div>
            <textarea className="ak-input" style={{ marginTop: 8, width: '100%', minHeight: 90 }} placeholder={t('admin_slot_add_creds')} value={na.creds_json}
              onChange={(e) => setNa({ ...na, creds_json: e.target.value })} />
            <div className="ak-muted" style={{ fontSize: 12, marginTop: 4 }}>{t('admin_slot_add_hint')}</div>
            <div style={{ marginTop: 8 }}>
              <button className="ak-btn primary" disabled={adding || !na.slot_id || !na.creds_json} onClick={addAccount}>{t('admin_slot_add_btn')}</button>
            </div>
          </details>
        )}
        </>
        )
      }}</Async>
    </Card>
    </>
  )
}

// 登录 API 组：claude（claude auth login）与 codex（codex login --device-auth）同架子，只换端点。
type LoginApi = {
  start: (id: string) => Promise<any>
  read: (sid: string, off: number) => Promise<any>
  write: (sid: string, d: string) => Promise<any>
  finish: (sid: string) => Promise<any>
  cancel: (sid: string) => Promise<any>
}
const CLAUDE_LOGIN: LoginApi = {
  start: admin.loginStart, read: admin.loginRead, write: admin.loginWrite,
  finish: admin.loginFinish, cancel: admin.loginCancel,
}
const CODEX_LOGIN: LoginApi = {
  start: admin.codexLoginStart, read: admin.codexLoginRead, write: admin.codexLoginWrite,
  finish: admin.codexLoginFinish, cancel: admin.codexLoginCancel,
}

// 交互式登录新增订阅账号：网页终端(xterm) 直连服务器上的登录容器，自己跑 claude auth login / codex login。
function LoginNewAccount({ onDone, provider = 'claude' }: { onDone: () => void; provider?: 'claude' | 'codex' }) {
  const { t } = useI18n()
  const isCodex = provider === 'codex'
  const lapi = isCodex ? CODEX_LOGIN : CLAUDE_LOGIN
  const [open, setOpen] = useState(false)
  const [accId, setAccId] = useState('')
  const [sid, setSid] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [credsReady, setCredsReady] = useState(false)
  const termRef = useRef<HTMLDivElement | null>(null)
  const termObj = useRef<Terminal | null>(null)
  const offsetRef = useRef(0)
  const pollRef = useRef<number | null>(null)
  const sidRef = useRef<string | null>(null)

  function stopPoll() {
    if (pollRef.current != null) { window.clearInterval(pollRef.current); pollRef.current = null }
  }

  // 终端 + 轮询的生命周期跟着 sid 走。之前用 setTimeout(0) 等渲染提交，
  // 会和 React 的调度赛跑：timeout 先跑到时 termRef 还是 null，终端和轮询都没起来。
  // useEffect 保证在 DOM 提交后执行，不再有这个竞态。
  useEffect(() => {
    if (!sid || !termRef.current) return
    let term: Terminal | null = null
    try {
      term = new Terminal({ fontSize: 13, cols: 120, rows: 40, convertEol: true,
        theme: { background: '#0b1020' } })
      term.open(termRef.current)
      term.onData((d) => {
        if (!sidRef.current) return
        lapi.write(sidRef.current, d).catch(() => {})
        // 「Paste code here」这步 claude CLI 不回显；粘贴的长串本地打码回显（开头明文+中间****），让用户知道粘上了
        const clean = d.replace(/\x1b\[20[01]~/g, '').replace(/[\r\n]/g, '')
        if (clean.length > 8) {
          const head = clean.slice(0, 12)
          term!.write(head + '*'.repeat(Math.min(24, clean.length - 12)))
        }
      })
      term.focus()
      termObj.current = term
    } catch (e: any) {
      setErr(t('admin_term_init_fail').replace('{msg}', String(e?.message || e)))
    }
    pollRef.current = window.setInterval(poll, 350)
    return () => {
      stopPoll()
      termObj.current = null
      try { term?.dispose() } catch { /* 已销毁 */ }
    }
  }, [sid])

  // 把 Anthropic 账号邮箱转成安全的 slot ID（要当容器名，只能字母数字 _-）：cja@gmail.com → cja-gmail-com
  function toSlotId(s: string) {
    return s.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64)
  }
  async function start() {
    // codex：账号名可留空自动编号，不做 slotId 转换；claude：邮箱→slotId 且必填。
    const id = isCodex ? accId.trim() : toSlotId(accId)
    if (!isCodex && !id) return
    setBusy(true); setErr(null); setMsg(null); setCredsReady(false)
    try {
      const r = await lapi.start(id)
      setSid(r.session_id); sidRef.current = r.session_id; offsetRef.current = 0
      setMsg(t('admin_login_connected'))
    } catch (e: any) { setErr(e?.message || t('failed')) } finally { setBusy(false) }
  }

  async function poll() {
    const id = sidRef.current
    if (!id) return
    try {
      const r = await lapi.read(id, offsetRef.current)
      if (r.data) {
        const bin = atob(r.data)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
        termObj.current?.write(bytes)
      }
      offsetRef.current = r.offset
      if (r.creds_ready) setCredsReady(true)
    } catch { /* ignore transient */ }
  }

  async function finish() {
    if (!sid) return
    setBusy(true); setErr(null)
    try {
      const r = await lapi.finish(sid)
      setMsg(isCodex
        ? t('codex_account_added').replace('{id}', String(r.account_id))
        : t('admin_account_added').replace('{id}', String(r.account_id)).replace('{health}', String(r.health)))
      teardown()
      onDone()
    } catch (e: any) { setErr(e?.message || t('failed')) } finally { setBusy(false) }
  }
  async function cancel() {
    if (sid) await lapi.cancel(sid).catch(() => {})
    teardown(); setMsg(null); setErr(null)
  }
  function teardown() {
    // 终端和轮询由上面的 useEffect 在 sid 置空时清理
    sidRef.current = null; setSid(null); setAccId(''); setOpen(false); setCredsReady(false)
  }

  if (!open) {
    return (
      <div style={{ marginBottom: 12 }}>
        <button className="ak-btn primary" onClick={() => setOpen(true)}>{t(isCodex ? 'codex_login_add_btn' : 'admin_login_add_btn')}</button>
      </div>
    )
  }
  return (
    <Card title={t(isCodex ? 'codex_login_card_title' : 'admin_login_card_title')}>
      {isCodex
        ? <p className="ak-muted">{t('codex_login_desc')}</p>
        : <p className="ak-muted">{t('admin_login_desc_1')}<span className="ak-mono">name@gmail.com</span>{t('admin_login_desc_2')}<span className="ak-mono">claude auth login</span>{t('admin_login_desc_3')}</p>}
      {!sid ? (
        <div className="ak-row">
          <input className="ak-input" placeholder={t(isCodex ? 'codex_ph_acc_id' : 'admin_ph_acc_id')} value={accId} onChange={(e) => setAccId(e.target.value)} />
          <button className="ak-btn primary" disabled={busy || (!isCodex && !accId)} onClick={start}>{busy ? t('admin_connecting') : t('admin_start_login')}</button>
          <button className="ak-btn" onClick={() => setOpen(false)}>{t('admin_cancel')}</button>
        </div>
      ) : (
        <>
          <div ref={termRef} style={{ marginTop: 4, borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)' }} />
          <div className="ak-row" style={{ marginTop: 10 }}>
            <button className="ak-btn primary" disabled={busy || !credsReady} onClick={finish}
              title={credsReady ? '' : t('admin_finish_hint')}>
              {busy ? t('admin_publishing') : credsReady ? t(isCodex ? 'codex_finish_ok' : 'admin_finish_ok') : t(isCodex ? 'codex_finish_wait' : 'admin_finish_wait')}
            </button>
            <button className="ak-btn danger" onClick={cancel}>{t('admin_abort')}</button>
          </div>
        </>
      )}
      {msg && <div className="ak-ok" style={{ marginTop: 8 }}>{msg}</div>}
      {err && <div className="ak-err" style={{ marginTop: 8 }}>{err}</div>}
    </Card>
  )
}

// ChatGPT（codex 订阅）面板：门控状态 + device-auth 登录 + 账号池管理。
function CodexAccounts() {
  const { t } = useI18n()
  const state = useAsync(() => admin.codexStatus(), [])
  async function del(acc: string) {
    if (!window.confirm(acc)) return
    try { await admin.codexDeleteAccount(acc) } catch { /* ignore */ }
    state.reload()
  }
  return (
    <Card title={t('codex_section_title')}>
      <LoginNewAccount provider="codex" onDone={() => state.reload()} />
      <Async state={state}>{(d: any) => (
        <>
          <div className={d.enabled ? 'ak-ok' : 'ak-err'} style={{ marginBottom: 8 }}>
            {d.enabled ? t('codex_status_on') : t('codex_status_off')}
          </div>
          <p className="ak-muted" style={{ marginTop: 0 }}>
            {d.openai_key_enabled ? t('codex_openai_key_on') : t('codex_openai_key_off')}
          </p>
          <div style={{ fontWeight: 600, margin: '10px 0 6px' }}>{t('codex_accounts_title')}</div>
          <table className="ak-table">
            <tbody>
              {(d.codex_accounts || []).map((acc: string) => (
                <tr key={acc}>
                  <td className="ak-mono">{acc}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button className="ak-btn danger" onClick={() => del(acc)}>{t('codex_del')}</button>
                  </td>
                </tr>
              ))}
              {(d.codex_accounts || []).length === 0 && (
                <tr><td className="ak-muted">{t('codex_empty')}</td></tr>
              )}
            </tbody>
          </table>
        </>
      )}</Async>
    </Card>
  )
}

function MoxingAccounting() {
  const { t } = useI18n()
  const [currency, setCurrency] = useDisplayCurrency()
  const [days, setDays] = useState(30)
  const state = useAsync(() => admin.moxingAccounting(days), [days])
  const [entry, setEntry] = useState({ type: 'topup', amount: '', reference: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  async function submitEntry() {
    const cny = Number(entry.amount)
    const rate = Number(state.data?.rmb_per_usd || RMB_PER_USD_FALLBACK)
    const amount = currency === 'rmb' ? cny : cny / rate
    if (!isFinite(amount) || amount === 0 || (entry.type === 'topup' && amount < 0)) return
    if (!confirm(t('moxing_entry_confirm'))) return
    setBusy(true); setMsg(null)
    try {
      const payload = {
        amount,
        currency: currency === 'rmb' ? 'RMB' : 'USD',
        reference: entry.reference || null,
        note: entry.note || null,
      }
      if (entry.type === 'topup') await admin.moxingTopup(payload)
      else await admin.moxingAdjustment(payload)
      setEntry({ ...entry, amount: '', reference: '', note: '' })
      setMsg(t('moxing_saved'))
      state.reload()
    } catch (e: any) { setMsg(`${t('failed')}: ${e?.message || e}`) }
    finally { setBusy(false) }
  }

  return (
    <Async state={state}>{(d: any) => {
      const rate = Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)
      const money = (value: any, digits = 2) => currency === 'rmb'
        ? `¥${(Number(value || 0) / 1_000_000).toFixed(digits)}`
        : `$${(Number(value || 0) / 1_000_000 / rate).toFixed(digits)}`
      const account = d.account || {}
      const period = d.period || {}
      const totals = d.ledger_totals || {}
      return <>
        <Card title={t('moxing_reconcile_title')} actions={
          <div className="ak-row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {(['rmb', 'usd'] as const).map((value) => (
              <button key={value} className={`ak-btn ${currency === value ? 'primary' : ''}`}
                onClick={() => setCurrency(value)}>{value.toUpperCase()}</button>
            ))}
            {[7, 30, 90].map((value) => (
              <button key={value} className={`ak-btn ${days === value ? 'primary' : ''}`}
                onClick={() => setDays(value)}>{t(`billing_days_${value}` as TKey)}</button>
            ))}
          </div>
        }>
          <p className="ak-muted" style={{ marginTop: 0 }}>{t('moxing_reconcile_desc')}</p>
          <div className="ak-billing-stats">
            <div className="ak-billing-stat featured"><span>{t('moxing_book_balance')}</span><b>{money(account.balance_micro_cny)}</b><small>{t('moxing_tracking_since')} {fmtTime(account.tracking_started_at)}</small></div>
            <div className="ak-billing-stat"><span>{t('moxing_total_topups')}</span><b>{money(totals.topups)}</b><small>{t('moxing_adjustments')} {money(totals.adjustments)}</small></div>
            <div className="ak-billing-stat"><span>{t('moxing_supplier_cost')}</span><b>{money(period.supplier_cost)}</b><small>{fmtCount(period.calls)} {t('billing_calls_unit')}</small></div>
            <div className="ak-billing-stat"><span>{t('moxing_sales')}</span><b>{money(period.sales)}</b><small>{t('moxing_paid_sales')} {money(period.paid_sales)} · {t('moxing_trial_sales')} {money(period.trial_sales)}</small></div>
            <div className="ak-billing-stat"><span>{t('moxing_gross_profit')}</span><b>{money(period.gross_profit_micro_cny)}</b><small>{t('moxing_cash_contribution')} {money(period.cash_contribution_micro_cny)}</small></div>
          </div>
          {(Number(totals.internal_variance_micro_cny || 0) !== 0 || Number(period.accounting_issue_calls || 0) > 0) &&
            <div className="ak-err" style={{ marginTop: 12 }}>
              {t('moxing_reconcile_warning')}
              {Number(totals.internal_variance_micro_cny || 0) !== 0 ? ` · ${t('moxing_internal_variance')} ${money(totals.internal_variance_micro_cny)}` : ''}
              {Number(period.accounting_issue_calls || 0) > 0 ? ` · ${t('moxing_unpriced')} ${period.accounting_issue_calls}` : ''}
            </div>}
        </Card>

        <Card title={t('moxing_funds_entry')}>
            <div className="ak-row" style={{ flexWrap: 'wrap' }}>
              <select className="ak-input" value={entry.type} onChange={(e) => setEntry({ ...entry, type: e.target.value })}>
                <option value="topup">{t('moxing_topup')}</option>
                <option value="adjustment">{t('moxing_adjustment')}</option>
              </select>
              <input className="ak-input" type="number"
                value={entry.amount === '' ? '' : Number((Number(entry.amount) / (currency === 'rmb' ? 1 : rate)).toFixed(4))}
                placeholder={t('moxing_amount')}
                onChange={(e) => setEntry({ ...entry, amount: e.target.value === '' ? '' : String(Number(e.target.value) * (currency === 'rmb' ? 1 : rate)) })} />
              <b>{currency === 'rmb' ? 'RMB' : 'USD'}</b>
              <input className="ak-input" value={entry.reference} placeholder={t('moxing_reference')}
                onChange={(e) => setEntry({ ...entry, reference: e.target.value })} />
              <input className="ak-input" value={entry.note} placeholder={t('moxing_note')}
                onChange={(e) => setEntry({ ...entry, note: e.target.value })} />
              <button className="ak-btn primary" disabled={busy || !entry.amount} onClick={submitEntry}>{t('admin_save')}</button>
            </div>
            <p className="ak-muted" style={{ fontSize: 12 }}>{t('moxing_immutable_hint')}</p>
        </Card>
        {msg && <div className={msg.startsWith(t('failed')) ? 'ak-err' : 'ak-ok'} style={{ marginBottom: 12 }}>{msg}</div>}

        <Card title={t('moxing_terms_title')}>
          <p className="ak-muted" style={{ marginTop: 0 }}>{t('moxing_terms_desc')}</p>
          <div className="ak-table-scroll">
            <table className="ak-table moxing-terms-table">
              <colgroup>
                <col className="moxing-term-model" />
                <col span={4} className="moxing-term-price" />
                <col span={2} className="moxing-term-discount" />
                <col className="moxing-term-action" />
              </colgroup>
              <thead><tr><th>{t('admin_col_model')}</th><th>{t('moxing_official_in')} ({currency === 'rmb' ? '¥/百万' : '$/百万'})</th><th>{t('moxing_official_out')} ({currency === 'rmb' ? '¥/百万' : '$/百万'})</th><th>{t('moxing_cache_read')} ({currency === 'rmb' ? '¥/百万' : '$/百万'})</th><th>{t('moxing_cache_write')} ({currency === 'rmb' ? '¥/百万' : '$/百万'})</th><th>{t('moxing_supplier_discount')}</th><th>{t('moxing_sale_discount')}</th><th></th></tr></thead>
              <tbody>{(d.terms || []).map((term: any) => <MoxingTermRow key={term.model} term={term} currency={currency} rmbPerUsd={rate} />)}</tbody>
            </table>
          </div>
        </Card>

        <Card title={t('moxing_daily_reconcile')}>
          <div className="ak-table-scroll"><table className="ak-table">
            <thead><tr><th>{t('billing_date')}</th><th>{t('billing_calls')}</th><th>tokens</th><th>{t('moxing_sales')}</th><th>{t('moxing_supplier_cost')}</th><th>{t('moxing_gross_profit')}</th><th>{t('moxing_paid_sales')}</th><th>{t('moxing_trial_sales')}</th></tr></thead>
            <tbody>{(d.daily || []).map((row: any) => <tr key={String(row.day)}>
              <td>{String(row.day).slice(0, 10)}</td><td>{fmtCount(row.calls)}</td><td>{fmtCount(row.tokens)}</td>
              <td>{money(row.sales, 4)}</td><td>{money(row.supplier_cost, 4)}</td><td><b>{money(Number(row.sales || 0) - Number(row.supplier_cost || 0), 4)}</b></td>
              <td>{money(row.paid_sales, 4)}</td><td>{money(row.trial_sales, 4)}</td>
            </tr>)}</tbody>
          </table></div>
        </Card>

        <Card title={t('moxing_model_reconcile')}>
          <div className="ak-table-scroll"><table className="ak-table">
            <thead><tr><th>{t('admin_col_model')}</th><th>{t('moxing_upstream_model')}</th><th>{t('billing_calls')}</th><th>tokens</th><th>{t('moxing_sales')}</th><th>{t('moxing_supplier_cost')}</th><th>{t('moxing_gross_profit')}</th></tr></thead>
            <tbody>{(d.by_model || []).map((row: any, index: number) => <tr key={`${row.model}-${row.upstream_model}-${index}`}>
              <td className="ak-mono">{row.model || '—'}</td><td className="ak-mono">{row.upstream_model || '—'}</td><td>{fmtCount(row.calls)}</td><td>{fmtCount(row.tokens)}</td>
              <td>{money(row.sales, 4)}</td><td>{money(row.supplier_cost, 4)}</td><td><b>{money(Number(row.sales || 0) - Number(row.supplier_cost || 0), 4)}</b></td>
            </tr>)}</tbody>
          </table></div>
        </Card>

        <Card title={t('moxing_request_reconcile')}>
          <div className="ak-table-scroll"><table className="ak-table">
            <thead><tr><th>{t('admin_col_time')}</th><th>request_id</th><th>{t('admin_col_user')}</th><th>{t('admin_col_model')}</th><th>{t('moxing_upstream_model')}</th><th>tokens</th><th>{t('moxing_sales')}</th><th>{t('moxing_supplier_cost')}</th><th>{t('moxing_gross_profit')}</th><th>{t('moxing_discount_snapshot')}</th><th>{t('admin_col_status')}</th></tr></thead>
            <tbody>{(d.recent_usage || []).map((row: any) => <tr key={row.id}>
              <td className="ak-muted">{fmtTime(row.created_at)}</td><td className="ak-mono ak-muted">{row.request_id || '—'}</td><td>{row.email || '—'}</td><td className="ak-mono">{row.model}</td><td className="ak-mono">{row.upstream_model}</td><td>{fmtCount(row.total_tokens)}</td>
              <td>{money(row.sales, 4)}</td><td>{money(row.supplier_cost, 4)}</td><td><b>{money(Number(row.sales || 0) - Number(row.supplier_cost || 0), 4)}</b></td>
              <td className="ak-muted" style={{ fontSize: 11 }}>{t('moxing_supplier_short')} ×{row.supplier_multiplier ?? '—'} · {t('moxing_sale_short')} ×{row.sale_multiplier ?? '—'} · user ×{row.user_multiplier ?? 1}</td>
              <td><Pill kind={row.supplier_accounting_status === 'posted' ? 'ok' : 'warn'}>{row.supplier_accounting_status}</Pill></td>
            </tr>)}</tbody>
          </table></div>
        </Card>

        <Card title={t('moxing_ledger_title')}>
          <div className="ak-table-scroll"><table className="ak-table">
            <thead><tr><th>{t('admin_col_time')}</th><th>{t('admin_col_type')}</th><th>{t('admin_col_amount')}</th><th>{t('moxing_balance_after')}</th><th>{t('admin_col_model')}</th><th>{t('moxing_original_amount')}</th><th>{t('moxing_reference')}</th><th>{t('moxing_note')}</th></tr></thead>
            <tbody>{(d.ledger || []).map((row: any) => <tr key={row.id}>
              <td className="ak-muted">{fmtTime(row.created_at)}</td><td>{row.entry_type}</td><td><b>{money(row.amount_micro_cny, 4)}</b></td><td>{money(row.balance_after_micro_cny, 4)}</td><td className="ak-mono">{row.model || '—'}</td>
              <td>{row.original_amount != null ? `${row.original_currency} ${row.original_amount}` : '—'}</td><td className="ak-mono">{row.reference || row.request_id || '—'}</td><td className="ak-muted">{row.note || '—'}</td>
            </tr>)}</tbody>
          </table></div>
        </Card>
      </>
    }}</Async>
  )
}

function MoxingTermRow({ term, currency, rmbPerUsd }: {
  term: any; currency: DisplayCurrency; rmbPerUsd: number
}) {
  const { t } = useI18n()
  const [form, setForm] = useState({
    input: Number(term.official_input_micro_cny_per_million || 0) / 1_000_000,
    output: Number(term.official_output_micro_cny_per_million || 0) / 1_000_000,
    cacheRead: Number(term.official_cache_read_micro_cny_per_million || 0) / 1_000_000,
    cacheWrite: Number(term.official_cache_write_micro_cny_per_million || 0) / 1_000_000,
    supplierTenths: Number(term.supplier_multiplier || 0) * 10,
    saleTenths: Number(term.sale_multiplier || 0) * 10,
  })
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  async function save() {
    setSaving(true); setSaved(false)
    try {
      await admin.moxingTerms(term.model, {
        display_name: term.display_name,
        official_input_cny_per_million: form.input,
        official_output_cny_per_million: form.output,
        official_cache_read_cny_per_million: form.cacheRead,
        official_cache_write_cny_per_million: form.cacheWrite,
        supplier_multiplier: form.supplierTenths / 10,
        sale_multiplier: form.saleTenths / 10,
      })
      setSaved(true)
    } finally { setSaving(false) }
  }
  const input = (key: keyof typeof form, isMoney = false) => {
    const factor = isMoney && currency === 'usd' ? 1 / rmbPerUsd : 1
    const displayed = form[key] * factor
    return <input className="ak-input" type="number" step={isMoney && currency === 'rmb' ? '1' : '0.01'} min="0"
      value={isMoney && currency === 'rmb' ? Math.round(displayed) : Number(displayed.toFixed(6))}
      style={{ width: '100%', minWidth: 0 }}
      onChange={(e) => { setSaved(false); setForm({ ...form, [key]: Number(e.target.value) / factor }) }} />
  }
  return <tr>
    <td><b>{term.display_name || term.model}</b><div className="ak-mono ak-muted">{term.model}</div></td>
    <td>{input('input', true)}</td><td>{input('output', true)}</td><td>{input('cacheRead', true)}</td><td>{input('cacheWrite', true)}</td>
    <td><div className="moxing-discount-field">{input('supplierTenths')}<span>{t('moxing_tenths')}</span></div></td>
    <td><div className="moxing-discount-field">{input('saleTenths')}<span>{t('moxing_tenths')}</span></div></td>
    <td className="moxing-term-save"><button className="ak-btn primary" disabled={saving} onClick={save}>{saving ? t('submitting') : t('admin_save')}</button>{saved && <small className="ak-ok">{t('moxing_term_saved')}</small>}</td>
  </tr>
}

function UsageBoard() {
  const { t } = useI18n()
  const [currency, setCurrency] = useDisplayCurrency()
  const localDate = (value: Date) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`
  const initialRange = (() => {
    const end = new Date(); const start = new Date(end); start.setDate(end.getDate() - 6)
    return { start: localDate(start), end: localDate(end) }
  })()
  const [draftRange, setDraftRange] = useState(initialRange)
  const [range, setRange] = useState(initialRange)
  const days = Math.max(1, Math.round((new Date(range.end).getTime() - new Date(range.start).getTime()) / 86400000) + 1)
  const state = useAsync(() => admin.usageSummary(days, range.start, range.end), [range.start, range.end])
  function quickRange(kind: '7d' | 'week' | 'month' | 'lastMonth') {
    const today = new Date(); let start = new Date(today); let end = new Date(today)
    if (kind === '7d') start.setDate(today.getDate() - 6)
    if (kind === 'week') {
      const weekday = today.getDay() || 7; start.setDate(today.getDate() - weekday + 1)
    }
    if (kind === 'month') start = new Date(today.getFullYear(), today.getMonth(), 1)
    if (kind === 'lastMonth') {
      start = new Date(today.getFullYear(), today.getMonth() - 1, 1)
      end = new Date(today.getFullYear(), today.getMonth(), 0)
    }
    const next = { start: localDate(start), end: localDate(end) }
    setDraftRange(next); setRange(next)
  }
  return (
    <Async state={state}>{(d: any) => (
      <>
        <Card title={t('admin_billing_overview')} actions={
          <div className="ak-row" style={{ gap: 6 }}>
            {(['rmb', 'usd'] as const).map((value) => (
              <button key={value} className={`ak-btn ${currency === value ? 'primary' : ''}`}
                onClick={() => setCurrency(value)}>{value.toUpperCase()}</button>
            ))}
          </div>
        }>
          <div className="ak-row admin-usage-range" style={{ flexWrap: 'wrap', marginBottom: 14 }}>
            <label className="ak-muted">{t('admin_start_date')} <input className="ak-input" type="date" value={draftRange.start}
              onChange={(e) => setDraftRange({ ...draftRange, start: e.target.value })} /></label>
            <label className="ak-muted">{t('admin_end_date')} <input className="ak-input" type="date" value={draftRange.end}
              onChange={(e) => setDraftRange({ ...draftRange, end: e.target.value })} /></label>
            <button className="ak-btn primary" disabled={!draftRange.start || !draftRange.end || draftRange.start > draftRange.end}
              onClick={() => setRange({ ...draftRange })}>{t('admin_search')}</button>
            <button className="ak-btn" onClick={() => quickRange('7d')}>{t('billing_days_7')}</button>
            <button className="ak-btn" onClick={() => quickRange('week')}>{t('admin_this_week')}</button>
            <button className="ak-btn" onClick={() => quickRange('month')}>{t('admin_this_month')}</button>
            <button className="ak-btn" onClick={() => quickRange('lastMonth')}>{t('admin_last_month')}</button>
          </div>
          {(() => {
            const rate = Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)
            const total = Number(d.total?.cost || 0)
            const previous = Number(d.previous_total?.cost || 0)
            const comparison = previous > 0 ? ((total - previous) / previous) * 100 : null
            const peak = [...(d.daily || [])].sort((a: any, b: any) =>
              (Number(b.china_cost || 0) + Number(b.overseas_cost || 0)) - (Number(a.china_cost || 0) + Number(a.overseas_cost || 0)))[0]
            const peakCost = Number(peak?.china_cost || 0) + Number(peak?.overseas_cost || 0)
            return <div className="ak-billing-stats">
              <div className="ak-billing-stat featured"><span>{t('admin_period_spend')}</span><b>{fmtDisplayCurrency(total, currency, rate)}</b><small>{range.start} — {range.end}</small></div>
              <div className="ak-billing-stat"><span>{t('admin_period_comparison')}</span><b>{comparison == null ? '—' : `${comparison >= 0 ? '+' : ''}${comparison.toFixed(1)}%`}</b><small>{t('admin_previous_period')} {fmtDisplayCurrency(previous, currency, rate)}</small></div>
              <div className="ak-billing-stat"><span>{t('admin_daily_average')}</span><b>{fmtDisplayCurrency(total / Math.max(1, Number(d.days || days)), currency, rate)}</b><small>{t('admin_peak')} {fmtDisplayCurrency(peakCost, currency, rate)} · {String(peak?.day || '').slice(5, 10)}</small></div>
              <div className="ak-billing-stat"><span>{t('billing_calls')}</span><b>{fmtCount(d.total?.calls)}</b><small>{fmtCount(d.total?.tokens)} tokens</small></div>
            </div>
          })()}
        </Card>

        <Card title={t('admin_daily_trend')}>
          <p className="ak-muted" style={{ marginTop: 0 }}>{t('admin_daily_trend_note')}</p>
          <DailySpendChart rows={d.daily || []} previousRows={d.previous_daily || []} currency={currency} rmbPerUsd={Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)} />
        </Card>

        <div className="ak-billing-panels admin-distribution-panels">
          <section><h4>{t('admin_model_distribution')}</h4><CostDistributionChart rows={d.by_model || []} labelKey="model" currency={currency} rmbPerUsd={Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)} /></section>
          <section><h4>{t('admin_channel_distribution')}</h4><CostDistributionChart rows={d.by_slot || []} labelKey="slot_id" currency={currency} rmbPerUsd={Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)} /></section>
        </div>

        <Card title={t('admin_daily_bill')}>
          <div className="ak-table-scroll">
            <table className="ak-table">
              <thead><tr><th>{t('billing_date')}</th><th>{t('billing_calls')}</th><th>tokens</th><th>{t('col_cost')} ({currency.toUpperCase()})</th></tr></thead>
              <tbody>
                {[...(d.daily || [])].reverse().map((row: any) => {
                  const rate = Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)
                  return (
                    <tr key={String(row.day)}>
                      <td>{String(row.day).slice(0, 10)}</td>
                      <td>{fmtCount(row.calls)}</td>
                      <td>{fmtCount(row.tokens)}</td>
                      <td><b>{fmtDisplayCurrency(Number(row.china_cost || 0) + Number(row.overseas_cost || 0), currency, rate)}</b></td>
                    </tr>
                  )
                })}
                {(d.daily || []).length === 0 && <tr><td colSpan={4} className="ak-muted">{t('admin_empty_data')}</td></tr>}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title={t('admin_group_model')}>
          <p className="ak-muted" style={{ marginTop: 0 }}>
            {t('admin_model_currency_note')} · 1 USD ≈ {Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK).toFixed(2)} CNY
          </p>
          <Agg rows={d.by_model} keyCol="model" currency={currency} rmbPerUsd={d.rmb_per_usd} />
        </Card>
        <Card title={t('admin_group_user')}><Agg rows={d.by_user} keyCol="email" currency={currency} rmbPerUsd={d.rmb_per_usd} /></Card>
        <Card title={t('admin_group_slot')}><Agg rows={d.by_slot} keyCol="slot_id" currency={currency} rmbPerUsd={d.rmb_per_usd} /></Card>
        <AdminUsageDetails currency={currency} rmbPerUsd={Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)} />
      </>
    )}</Async>
  )
}

function AdminUsageDetails({ currency, rmbPerUsd }: { currency: DisplayCurrency; rmbPerUsd: number }) {
  const { t } = useI18n()
  const emptyFilters = { email: '', start_date: '', end_date: '' }
  const [filters, setFilters] = useState(emptyFilters)
  const [applied, setApplied] = useState(emptyFilters)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const state = useAsync(() => admin.usageDetails({
    ...applied, limit: pageSize, offset: (page - 1) * pageSize,
  }), [applied.email, applied.start_date, applied.end_date, page, pageSize])
  const search = () => { setPage(1); setApplied({ ...filters }) }
  const reset = () => { setFilters(emptyFilters); setApplied(emptyFilters); setPage(1) }
  const exportExcel = () => {
    window.location.href = admin.usageExportUrl({ ...applied, currency: currency.toUpperCase() })
  }
  return <Card title={t('admin_usage_details_title')} actions={
    <button className="ak-btn" onClick={exportExcel}>{t('admin_export_excel')}</button>
  }>
    <div className="ak-row" style={{ flexWrap: 'wrap', marginBottom: 14 }}>
      <input className="ak-input" type="email" placeholder={t('admin_email_search')} value={filters.email}
        onChange={(e) => setFilters({ ...filters, email: e.target.value })}
        onKeyDown={(e) => e.key === 'Enter' && search()} />
      <label className="ak-muted">{t('admin_start_date')} <input className="ak-input" type="date" value={filters.start_date}
        onChange={(e) => setFilters({ ...filters, start_date: e.target.value })} /></label>
      <label className="ak-muted">{t('admin_end_date')} <input className="ak-input" type="date" value={filters.end_date}
        onChange={(e) => setFilters({ ...filters, end_date: e.target.value })} /></label>
      <button className="ak-btn primary" onClick={search}>{t('admin_search')}</button>
      <button className="ak-btn" onClick={reset}>{t('admin_reset')}</button>
    </div>
    <Async state={state}>{(data: any) => <>
      <div className="ak-table-scroll"><table className="ak-table admin-usage-details-table">
        <thead><tr><th>{t('col_time')}</th><th>{t('admin_col_user')}</th><th>{t('col_model')}</th><th>{t('col_slot')}</th><th>{t('col_input_tokens')}</th><th>{t('col_output_tokens')}</th><th>{t('col_cache_tokens')}</th><th>{t('col_tokens')}</th><th>{t('col_cost')} ({currency.toUpperCase()})</th><th>{t('col_status')}</th></tr></thead>
        <tbody>{(data.items || []).map((row: any) => <tr key={row.id}>
          <td className="ak-muted">{fmtTime(row.created_at)}</td><td>{row.email}</td><td className="ak-mono">{row.model}</td><td className="ak-mono">{row.slot_id || '—'}</td>
          <td>{fmtCount(row.prompt_tokens)}</td><td>{fmtCount(row.completion_tokens)}</td>
          <td title={`${t('cache_read_short')} ${fmtCount(row.cache_read_tokens || 0)} · ${t('cache_write_short')} ${fmtCount(row.cache_write_tokens || 0)}`}>{fmtCount(Number(row.cache_read_tokens || 0) + Number(row.cache_write_tokens || 0))}</td>
          <td>{fmtCount(row.total_tokens)}</td><td><b>{fmtDisplayCurrency(row.cost_micro_usd, currency, rmbPerUsd)}</b></td>
          <td><Pill kind={row.status === 'ok' ? 'ok' : 'bad'}>{row.status}</Pill></td>
        </tr>)}
        {(data.items || []).length === 0 && <tr><td colSpan={10} className="ak-muted">{t('admin_empty_data')}</td></tr>}
        </tbody>
      </table></div>
      <Pager total={data.total || 0} page={page} pageSize={pageSize} onPage={setPage}
        onPageSize={(size) => { setPageSize(size); setPage(1) }} />
    </>}</Async>
  </Card>
}

function Agg({ rows, keyCol, currency, rmbPerUsd = RMB_PER_USD_FALLBACK }: { rows: any[]; keyCol: string; currency: DisplayCurrency; rmbPerUsd?: number }) {
  const { t } = useI18n()
  return (
    <table className="ak-table">
      <thead><tr><th>{keyCol}</th><th>{t('admin_calls')}</th><th>tokens</th><th>{t('admin_col_cost')}</th></tr></thead>
      <tbody>
        {(rows || []).map((r, i) => (
          <tr key={i}>
            <td className="ak-mono">{r[keyCol] || '—'}</td>
            <td>{r.calls}</td>
            <td>{r.tokens || 0}</td>
            <td>{fmtDisplayCurrency(r.cost, currency, Number(rmbPerUsd))}</td>
          </tr>
        ))}
        {(rows || []).length === 0 && <tr><td colSpan={4} className="ak-muted">{t('admin_empty_data')}</td></tr>}
      </tbody>
    </table>
  )
}
