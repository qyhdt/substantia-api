import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import '@xterm/xterm/css/xterm.css'
import { admin, fmtModelCost, fmtUsd, isChinaModel, RMB_PER_USD_FALLBACK } from '../api'
import { Async, Card, Pill, useAsync } from '../components/common'
import { readParam, pushParams, hrefFor } from '../nav'
import { useI18n, type TKey } from '../i18n'

const fmtTime = (t?: string | null) => (t ? new Date(t).toLocaleString() : '—')

const TABS = [
  ['topups', 'admin_tab_topups'],
  ['payments', 'admin_tab_payments'],
  ['users', 'admin_tab_users'],
  ['prices', 'admin_tab_prices'],
  ['slots', 'admin_tab_slots'],
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
        {tab === 'usage' && <UsageBoard />}
      </section>
    </div>
  )
}

function Topups() {
  const { t } = useI18n()
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
                <td>{fmtUsd(r.requested_micro_usd)}</td>
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
                  <td>{fmtUsd(r.amount_micro_usd)}</td>
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
  const state = useAsync(() => admin.users(), [])
  const [detailId, setDetailId] = useState<number | null>(null)
  const [nu, setNu] = useState({ email: '', role: 'user', balance: '0' })
  const [creating, setCreating] = useState(false)
  async function createUser() {
    if (!nu.email) return
    setCreating(true)
    try {
      // 默认密码 123456，后端置 must_change_password，用户首次登录须改密
      const r = await admin.createUser({ email: nu.email.trim(), password: '123456', role: nu.role, balance_usd: Number(nu.balance) || 0 })
      alert(`${t('admin_adduser_ok')}\n\n${r.api_key_plain}`)
      setNu({ email: '', role: 'user', balance: '0' })
      state.reload()
    } catch (e: any) {
      alert(t('admin_adduser_fail') + (e?.message || e))
    } finally { setCreating(false) }
  }
  async function grant(id: number) {
    const v = prompt(t('admin_grant_prompt'), '10')
    if (v == null) return
    await admin.grant(id, Number(v))
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
        <input className="ak-input" type="number" style={{ width: 140 }} placeholder={t('admin_adduser_balance')} value={nu.balance}
          onChange={(e) => setNu({ ...nu, balance: e.target.value })} />
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
                <td className="ak-balance">{fmtUsd(u.balance_micro_usd)}</td>
                <td><Pill kind={fmtMult(u.price_multiplier) !== '1' ? 'warn' : undefined}>×{fmtMult(u.price_multiplier)}</Pill></td>
                <td><Pill kind={u.full_model_access ? 'ok' : 'warn'}>{u.full_model_access ? t('admin_full_models') : t('admin_glm_only')}</Pill></td>
                <td>{u.trial_active
                  ? <span className="ak-muted">{fmtUsd(u.trial_micro_usd)} {t('admin_until')} {u.trial_expires_at ? new Date(u.trial_expires_at).toLocaleDateString() : '—'}</span>
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
  const state = useAsync(() => admin.userDetail(userId), [userId])
  return (
    <div className="ak-modal-overlay" onClick={onClose}>
      <div className="ak-modal" onClick={(e) => e.stopPropagation()}>
        <div className="ak-row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{t('admin_user_detail_title')}</h3>
          <button className="ak-btn" onClick={onClose}>{t('admin_close')}</button>
        </div>
        <Async state={state}>{(d: any) => {
          const u = d.user || {}
          const s = d.spend || {}
          const rmbPerUsd = Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK)
          return (
            <>
              <div className="ak-detail-grid">
                <div><span className="ak-muted">{t('admin_col_email')}</span><b>{u.email}</b></div>
                <div><span className="ak-muted">{t('admin_role_status')}</span><b>{u.role} / {u.status}</b></div>
                <div><span className="ak-muted">{t('admin_effective_balance')}</span><b className="ak-balance">{fmtUsd(u.effective_micro_usd)}</b></div>
                <div><span className="ak-muted">{t('admin_paid_bucket')}</span><b>{fmtUsd(u.paid_micro_usd)}</b></div>
                <div><span className="ak-muted">{t('admin_trial_bucket')}{u.trial_active ? t('admin_trial_valid') : t('admin_trial_invalid')}</span>
                  <b>{fmtUsd(u.trial_micro_usd)}{u.trial_expires_at ? ` · ${t('admin_until')} ${new Date(u.trial_expires_at).toLocaleDateString()}` : ''}</b></div>
                <div><span className="ak-muted">{t('admin_registered_at')}</span><b>{fmtTime(u.created_at)}</b></div>
              </div>

              <div className="ak-stat-row">
                <div className="ak-stat"><span className="ak-muted">{t('admin_total_cost')}</span><b className="ak-balance">{fmtUsd(s.total_cost_micro_usd)}</b></div>
                <div className="ak-stat"><span className="ak-muted">{t('admin_calls')}</span><b>{s.total_calls || 0}</b></div>
                <div className="ak-stat"><span className="ak-muted">{t('admin_total_tokens')}</span><b>{s.total_tokens || 0}</b></div>
              </div>

              <h4>{t('admin_by_model')}</h4>
              <table className="ak-table">
                <thead><tr><th>{t('admin_col_model')}</th><th>{t('admin_col_calls')}</th><th>{t('admin_col_in_tok')}</th><th>{t('admin_col_out_tok')}</th><th>{t('admin_col_cost')}</th></tr></thead>
                <tbody>
                  {(s.by_model || []).map((m: any, i: number) => (
                    <tr key={i}>
                      <td className="ak-mono">{m.model || '—'}<div className="ak-muted" style={{ fontSize: 11 }}>{isChinaModel(m.model) ? 'CNY' : 'USD'}</div></td>
                      <td>{m.calls}</td>
                      <td>{m.prompt_tokens || 0}</td>
                      <td>{m.completion_tokens || 0}</td>
                      <td>{fmtModelCost(m.model, m.cost, rmbPerUsd)}</td>
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
                      <td>{fmtUsd(k.spent_micro_usd)}</td>
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
                      <td className="ak-mono">{r.model || '—'}<div className="ak-muted" style={{ fontSize: 11 }}>{isChinaModel(r.model) ? 'CNY' : 'USD'}</div></td>
                      <td className="ak-mono ak-muted">{r.slot_id || '—'}</td>
                      <td>{r.total_tokens || 0}</td>
                      <td>{fmtModelCost(r.model, r.cost_micro_usd, rmbPerUsd)}</td>
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
  const state = useAsync(() => admin.prices(), [])
  const [f, setF] = useState({ model: '', display_name: '', input: 0, output: 0 })
  // 行内编辑：以「美元 / 1k」为单位（和表格显示一致），保存时 ×1e6 转微美元。
  const [edit, setEdit] = useState<Record<string, { input: number; output: number }>>({})
  async function save() {
    if (!f.model) return
    await admin.upsertPrice({
      model: f.model, display_name: f.display_name,
      input_micro_usd_per_1k: Math.round(f.input), output_micro_usd_per_1k: Math.round(f.output), enabled: true,
    })
    setF({ model: '', display_name: '', input: 0, output: 0 })
    state.reload()
  }
  function startEdit(p: any) {
    setEdit({ ...edit, [p.model]: { input: (p.input_micro_usd_per_1k || 0) / 1e6, output: (p.output_micro_usd_per_1k || 0) / 1e6 } })
  }
  function cancelEdit(model: string) {
    const e = { ...edit }; delete e[model]; setEdit(e)
  }
  async function saveRow(p: any) {
    const e = edit[p.model]; if (!e) return
    await admin.upsertPrice({
      model: p.model, display_name: p.display_name,
      input_micro_usd_per_1k: Math.round(e.input * 1e6), output_micro_usd_per_1k: Math.round(e.output * 1e6),
      cache_read_micro_usd_per_1k: p.cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k: p.cache_write_micro_usd_per_1k,
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
        <div className="ak-row">
          <input className="ak-input" placeholder="model id" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} />
          <input className="ak-input" placeholder={t('admin_ph_display_name')} value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <input className="ak-input" type="number" placeholder={t('admin_ph_input_price')} value={f.input} onChange={(e) => setF({ ...f, input: Number(e.target.value) })} style={{ width: 110 }} />
          <input className="ak-input" type="number" placeholder={t('admin_ph_output_price')} value={f.output} onChange={(e) => setF({ ...f, output: Number(e.target.value) })} style={{ width: 110 }} />
          <button className="ak-btn primary" onClick={save}>{t('admin_save')}</button>
        </div>
      </Card>
      <Card title={t('admin_price_table_title')}>
        <Async state={state}>{(rows: any[]) => (
          <table className="ak-table">
            <thead><tr><th>{t('admin_col_model')}</th><th>{t('admin_col_display_name')}</th><th>{t('admin_col_in_1k')}</th><th>{t('admin_col_out_1k')}</th><th>{t('admin_col_enabled')}</th><th></th></tr></thead>
            <tbody>
              {rows.map((p) => {
                const e = edit[p.model]
                return (
                  <tr key={p.id}>
                    <td className="ak-mono">{p.model}</td>
                    <td>{p.display_name || '—'}</td>
                    <td>{e
                      ? <input className="ak-input" type="number" step="0.0001" value={e.input} style={{ width: 100 }}
                          onChange={(ev) => setEdit({ ...edit, [p.model]: { ...e, input: Number(ev.target.value) } })} />
                      : fmtUsd(p.input_micro_usd_per_1k)}</td>
                    <td>{e
                      ? <input className="ak-input" type="number" step="0.0001" value={e.output} style={{ width: 100 }}
                          onChange={(ev) => setEdit({ ...edit, [p.model]: { ...e, output: Number(ev.target.value) } })} />
                      : fmtUsd(p.output_micro_usd_per_1k)}</td>
                    <td>
                      <a className="ak-link" style={{ cursor: 'pointer' }} onClick={() => toggleEnabled(p)}>
                        <Pill kind={p.enabled ? 'ok' : 'bad'}>{p.enabled ? 'on' : 'off'}</Pill>
                      </a>
                    </td>
                    <td>
                      <div className="ak-row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                        {e
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
          </table>
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

function UsageBoard() {
  const { t } = useI18n()
  const state = useAsync(() => admin.usageSummary(), [])
  return (
    <Async state={state}>{(d: any) => (
      <>
        <Card title={t('admin_group_model')}>
          <p className="ak-muted" style={{ marginTop: 0 }}>
            {t('admin_model_currency_note')} · 1 USD ≈ {Number(d.rmb_per_usd || RMB_PER_USD_FALLBACK).toFixed(4)} CNY
          </p>
          <Agg rows={d.by_model} keyCol="model" rmbPerUsd={d.rmb_per_usd} />
        </Card>
        <Card title={t('admin_group_user')}><Agg rows={d.by_user} keyCol="email" /></Card>
        <Card title={t('admin_group_slot')}><Agg rows={d.by_slot} keyCol="slot_id" /></Card>
      </>
    )}</Async>
  )
}

function Agg({ rows, keyCol, rmbPerUsd = RMB_PER_USD_FALLBACK }: { rows: any[]; keyCol: string; rmbPerUsd?: number }) {
  const { t } = useI18n()
  return (
    <table className="ak-table">
      <thead><tr><th>{keyCol}</th><th>{t('admin_calls')}</th><th>tokens</th><th>{t('admin_col_cost')}</th></tr></thead>
      <tbody>
        {(rows || []).map((r, i) => (
          <tr key={i}>
            <td className="ak-mono">
              {r[keyCol] || '—'}
              {keyCol === 'model' && <div className="ak-muted" style={{ fontSize: 11 }}>{isChinaModel(r.model) ? 'CNY' : 'USD'}</div>}
            </td>
            <td>{r.calls}</td>
            <td>{r.tokens || 0}</td>
            <td>{keyCol === 'model' ? fmtModelCost(r.model, r.cost, Number(rmbPerUsd)) : fmtUsd(r.cost)}</td>
          </tr>
        ))}
        {(rows || []).length === 0 && <tr><td colSpan={4} className="ak-muted">{t('admin_empty_data')}</td></tr>}
      </tbody>
    </table>
  )
}
