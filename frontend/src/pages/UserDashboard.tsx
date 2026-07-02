import { useState } from 'react'
import { fmtUsd, portal } from '../api'
import { Async, Card, Pager, Pill, useAsync } from '../components/common'
import { useI18n, type TKey } from '../i18n'

// 两种协议：Anthropic 兼容 + OpenAI 兼容。同一把 sk-key 通用。
type Fmt = 'anthropic' | 'openai'

// 示例可切换的模型：id 是 API 规范名（curl 里用），cursorName 是 Cursor 自定义模型名
// （避开 Cursor 内置模型名以免撞名走订阅；后台 normalize_model 会归一到 id）。
const MODELS = [
  { id: 'claude-opus-4-8', label: 'Opus 4.8', cursorName: 'claude4.8' },
  { id: 'claude-fable-5', label: 'Fable 5', cursorName: 'fable5' },
  { id: 'claude-sonnet-5', label: 'Sonnet 5', cursorName: 'sonnet5' },
  { id: 'claude-haiku-4-5', label: 'Haiku 4.5', cursorName: 'haiku4.5' },
] as const
type ModelInfo = (typeof MODELS)[number]

// title/note 走 i18n（用 TKey 引用），curl 与语言无关。
const ENDPOINTS: Record<Fmt, { titleKey: TKey; noteKey: TKey; curl: (k: string, model: string) => string }> = {
  anthropic: {
    titleKey: 'anthropic_compat' as TKey,
    noteKey: 'anthropic_note',
    curl: (k, model) => `curl https://api.substantia.ai/v1/messages \\
  -H "x-api-key: ${k}" -H "content-type: application/json" \\
  -d '{"model":"${model}","messages":[{"role":"user","content":"hello"}]}'`,
  },
  openai: {
    titleKey: 'openai_compat' as TKey,
    noteKey: 'openai_note',
    curl: (k, model) => `curl https://api.substantia.ai/v1/chat/completions \\
  -H "Authorization: Bearer ${k}" -H "content-type: application/json" \\
  -d '{"model":"${model}","messages":[{"role":"user","content":"hello"}]}'`,
  },
}

// Cursor 经 OpenAI 兼容接入：Base URL 必须带 /v1。
const CURSOR_BASE_URL = 'https://api.substantia.ai/v1'

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text; document.body.appendChild(ta); ta.select()
    try { document.execCommand('copy') } catch { /* ignore */ }
    document.body.removeChild(ta)
  }
}

function CopyBtn({ text, label }: { text: string; label?: string }) {
  const { t } = useI18n()
  const [done, setDone] = useState(false)
  return <button className="ak-btn" onClick={async () => {
    await copyText(text); setDone(true); setTimeout(() => setDone(false), 1500)
  }}>{done ? t('copied') : (label ?? t('copy'))}</button>
}

// 模型切换（示例 curl / Cursor 配置随之联动）
function ModelPicker({ model, onPick }: { model: ModelInfo; onPick: (m: ModelInfo) => void }) {
  const { t } = useI18n()
  return (
    <div className="ak-row" style={{ gap: 6, alignItems: 'center', flexWrap: 'wrap', margin: '4px 0 10px' }}>
      <span className="ak-muted" style={{ fontSize: 12 }}>{t('model_pick')}</span>
      {MODELS.map((m) => (
        <button key={m.id} className={`ak-btn ${model.id === m.id ? 'primary' : ''}`}
          style={{ fontSize: 12, padding: '2px 10px' }} onClick={() => onPick(m)}>
          {m.label}
        </button>
      ))}
    </div>
  )
}

// 从 URL ?tab=topups 读初始标签页，让「去充值」类深链能直接落到充值页（默认 keys）
function initialTab(): 'keys' | 'usage' | 'topups' {
  const q = new URLSearchParams(window.location.search).get('tab')
  return q === 'topups' || q === 'usage' ? q : 'keys'
}

export function UserDashboard({ newKey }: { newKey?: string }) {
  const { t } = useI18n()
  const [tab, setTab] = useState<'keys' | 'usage' | 'topups'>(initialTab)
  const tabLabel: Record<typeof tab, TKey> = { keys: 'tab_keys', usage: 'tab_usage', topups: 'tab_topups' }
  return (
    <>
      <div className="ak-tabs">
        {(['keys', 'usage', 'topups'] as const).map((k) => (
          <div key={k} className={`ak-tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>
            {t(tabLabel[k])}
          </div>
        ))}
      </div>
      {tab === 'keys' && <Keys justIssued={newKey} />}
      {tab === 'usage' && <Usage />}
      {tab === 'topups' && <Topups />}
    </>
  )
}

function Keys({ justIssued }: { justIssued?: string }) {
  const { t } = useI18n()
  const state = useAsync(() => portal.keys(), [])
  const [banner, setBanner] = useState<string | null>(justIssued || null)
  const [name, setName] = useState('default')
  const [busy, setBusy] = useState(false)
  const [pick, setPick] = useState<any[] | null>(null)   // 多 key 时弹窗候选
  const [pickFmt, setPickFmt] = useState<Fmt>('anthropic')
  const [open, setOpen] = useState<Fmt | null>('anthropic') // 当前展开的协议示例
  const [hint, setHint] = useState<string | null>(null)
  const [model, setModel] = useState<ModelInfo>(MODELS[0])  // 示例展示用的模型

  async function create() {
    setBusy(true)
    try {
      const r = await portal.newKey(name)
      setBanner(r.api_key)
      state.reload()
    } finally {
      setBusy(false)
    }
  }
  async function disable(id: number) {
    await portal.disableKey(id)
    state.reload()
  }
  async function del(id: number) {
    if (!window.confirm(t('confirm_del_key'))) return
    await portal.deleteKey(id)
    state.reload()
  }

  // 一键复制可直接运行的测试 curl（指定协议）：自动填入真实 key。多个可用 key 时弹窗选。
  async function copyTestCurl(fmt: Fmt) {
    const keys: any[] = (state.data as any[]) || []
    const usable = keys.filter((k) => k.key_plain && k.status === 'active')
    if (usable.length === 0) {
      setHint(t('copy_curl_nokey'))
      setTimeout(() => setHint(null), 4000)
      return
    }
    if (usable.length === 1) {
      await copyText(ENDPOINTS[fmt].curl(usable[0].key_plain, model.id))
      setHint(t('copy_curl_done').replace('{title}', t(ENDPOINTS[fmt].titleKey)).replace('{name}', usable[0].name))
      setTimeout(() => setHint(null), 2500)
      return
    }
    setPickFmt(fmt); setPick(usable)  // 多个 → 弹窗选
  }

  return (
    <>
      {banner && (
        <div className="ak-keybanner">
          <div className="ak-row" style={{ justifyContent: 'space-between' }}>
            <b>{t('newkey_title')}</b>
            <CopyBtn text={banner} label={t('copy_key')} />
          </div>
          <div className="ak-mono" style={{ marginTop: 6 }}>{banner}</div>
        </div>
      )}
      <Card title={t('card_newkey')} actions={
        <div className="ak-row">
          <input className="ak-input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('key_name_ph')} />
          <button className="ak-btn primary" onClick={create} disabled={busy}>{t('generate')}</button>
        </div>
      }>
        <p className="ak-muted">{t('newkey_desc_1')}<b>Anthropic</b>{t('newkey_desc_2')}<b>OpenAI</b>{t('newkey_desc_3')}</p>
        {hint && <div className="ak-ok" style={{ marginBottom: 8 }}>{hint}</div>}
        <ModelPicker model={model} onPick={setModel} />
        {(['anthropic', 'openai'] as Fmt[]).map((fmt) => {
          const e = ENDPOINTS[fmt]
          const expanded = open === fmt
          const sample = banner ? e.curl(banner, model.id) : e.curl('<你的 sk-key>', model.id)
          return (
            <div key={fmt} className="ak-accordion">
              <div className="ak-accordion-h" onClick={() => setOpen(expanded ? null : fmt)}>
                <b>{t(e.titleKey)}</b>
                <span className="ak-muted" style={{ fontSize: 12 }}>{expanded ? t('accordion_collapse') : t('accordion_expand')}</span>
              </div>
              {expanded && (
                <div className="ak-accordion-b">
                  <p className="ak-muted" style={{ fontSize: 12, margin: '0 0 8px' }}>{t(e.noteKey)}</p>
                  <div className="ak-row" style={{ justifyContent: 'flex-end', gap: 8, marginBottom: 6 }}>
                    <CopyBtn text={e.curl('<你的 sk-key>', model.id)} label={t('copy_sample')} />
                    <button className="ak-btn primary" onClick={() => copyTestCurl(fmt)}>{t('copy_real_key')}</button>
                  </div>
                  <pre className="ak-mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{sample}</pre>
                </div>
              )}
            </div>
          )
        })}
      </Card>

      <Card title={t('card_cursor')}>
        <p className="ak-muted">{t('cursor_desc_1')}<b>{t('openai_compat')}</b>{t('cursor_desc_2')}<b>Chat</b>{t('cursor_desc_3')}<b>Agent</b>{t('cursor_desc_4')}</p>
        <ModelPicker model={model} onPick={setModel} />
        <ol style={{ margin: '8px 0 4px', paddingLeft: 20, lineHeight: 1.9 }}>
          <li>{t('cursor_step1_1')}<b>Cursor Settings → Models</b>{t('cursor_step1_2')}<span className="ak-mono">Cmd&nbsp;+&nbsp;Shift&nbsp;+&nbsp;J</span>）</li>
          <li>{t('cursor_step2_1')}<b>API Keys</b>{t('cursor_step2_2')}<b>OpenAI API Key</b>{t('cursor_step2_3')}<span className="ak-mono">sk-substantia-…</span>{t('cursor_step2_4')}</li>
          <li>
            {t('cursor_step3_1')}<b>Override OpenAI Base URL</b>{t('cursor_step3_2')}<b>{t('cursor_step3_3')}<span className="ak-mono">/v1</span></b>{t('cursor_step3_4')}
            <div className="ak-row" style={{ gap: 8, margin: '6px 0', alignItems: 'center' }}>
              <code className="ak-mono">{CURSOR_BASE_URL}</code>
              <CopyBtn text={CURSOR_BASE_URL} />
            </div>
          </li>
          <li>{t('cursor_step4_1')}<b>{t('cursor_step4_2')}</b>{t('cursor_step4_3')}</li>
          <li>
            {t('cursor_step5_1')}<b>Add or search model</b>{t('cursor_step5_2')}<b>+ Add Custom Model</b>{t('cursor_step5_3')}
            <div className="ak-row" style={{ gap: 8, margin: '6px 0', alignItems: 'center' }}>
              <code className="ak-mono">{model.cursorName}</code>
              <CopyBtn text={model.cursorName} />
            </div>
          </li>
          <li>{t('cursor_step6_1')}<span className="ak-mono">{model.cursorName}</span>{t('cursor_step6_2')}<b>{t('cursor_step6_3')}</b>{t('cursor_step6_4')}</li>
        </ol>
        {banner && (
          <div className="ak-row" style={{ gap: 8, marginTop: 4 }}>
            <CopyBtn text={banner} label={t('copy_my_key')} />
          </div>
        )}
        <p className="ak-muted" style={{ fontSize: 12, marginTop: 8 }}>
          {t('cursor_foot_1')}<span className="ak-mono">{model.cursorName}</span>{t('cursor_foot_2')}<span className="ak-mono">{model.id}</span>{t('cursor_foot_3')}<span className="ak-mono">@文件 / @文件夹</span>{t('cursor_foot_4')}
        </p>
      </Card>

      {pick && (
        <div className="ak-modal-bg" onClick={() => setPick(null)}>
          <div className="ak-modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>{t('pick_title').replace('{title}', t(ENDPOINTS[pickFmt].titleKey))}</h3>
            {pick.map((k) => (
              <button key={k.id} className="ak-btn" style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 8 }}
                onClick={async () => {
                  await copyText(ENDPOINTS[pickFmt].curl(k.key_plain, model.id))
                  setPick(null)
                  setHint(t('copy_curl_done').replace('{title}', t(ENDPOINTS[pickFmt].titleKey)).replace('{name}', k.name))
                  setTimeout(() => setHint(null), 2500)
                }}>
                {k.name} · <span className="ak-mono">{k.key_prefix}</span>
              </button>
            ))}
            <button className="ak-btn" onClick={() => setPick(null)}>{t('cancel')}</button>
          </div>
        </div>
      )}

      <Card title={t('card_keylist')}>
        <Async state={state}>{(keys: any[]) => (
          <table className="ak-table">
            <thead><tr><th>{t('col_name')}</th><th>{t('col_prefix')}</th><th>{t('col_status')}</th><th>{t('col_spent')}</th><th>{t('col_cap')}</th><th>{t('col_created')}</th><th></th></tr></thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td className="ak-mono">{k.key_prefix}</td>
                  <td><Pill kind={k.status === 'active' ? 'ok' : 'bad'}>{k.status}</Pill></td>
                  <td>{fmtUsd(k.spent_micro_usd)}</td>
                  <td>{k.quota_cap_micro_usd == null ? '—' : fmtUsd(k.quota_cap_micro_usd)}</td>
                  <td className="ak-muted">{new Date(k.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className="ak-row" style={{ gap: 6, justifyContent: 'flex-end' }}>
                      {k.key_plain
                        ? <CopyBtn text={k.key_plain} label={t('copy')} />
                        : <button className="ak-btn" disabled title={t('copy_disabled_title')}>{t('copy')}</button>}
                      {k.status === 'active' &&
                        <button className="ak-btn" onClick={() => disable(k.id)}>{t('disable')}</button>}
                      <button className="ak-btn danger" onClick={() => del(k.id)}>{t('delete')}</button>
                    </div>
                  </td>
                </tr>
              ))}
              {keys.length === 0 && <tr><td colSpan={7} className="ak-muted">{t('empty_keys')}</td></tr>}
            </tbody>
          </table>
        )}</Async>
      </Card>
    </>
  )
}

function Usage() {
  const { t } = useI18n()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const state = useAsync(() => portal.usage(pageSize, (page - 1) * pageSize), [page, pageSize])
  return (
    <Card title={t('card_usage')}>
      <Async state={state}>{(data: any) => (<>
        <table className="ak-table">
          <thead><tr><th>{t('col_time')}</th><th>{t('col_model')}</th><th>{t('col_slot')}</th><th>{t('col_tokens')}</th><th>{t('col_cost')}</th><th>{t('col_status')}</th></tr></thead>
          <tbody>
            {(data.items || []).map((r: any) => (
              <tr key={r.id}>
                <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.model}</td>
                <td className="ak-mono">{r.slot_id || '—'}</td>
                <td>{r.total_tokens} <span className="ak-muted">({r.prompt_tokens}+{r.completion_tokens})</span></td>
                <td>{fmtUsd(r.cost_micro_usd)}</td>
                <td><Pill kind={r.status === 'ok' ? 'ok' : 'bad'}>{r.status}</Pill></td>
              </tr>
            ))}
            {(data.items || []).length === 0 && <tr><td colSpan={6} className="ak-muted">{t('empty_usage')}</td></tr>}
          </tbody>
        </table>
        <Pager total={data.total || 0} page={page} pageSize={pageSize}
          onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(1) }} />
      </>)}</Async>
    </Card>
  )
}

function Topups() {
  const { t } = useI18n()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const state = useAsync(() => portal.payments(pageSize, (page - 1) * pageSize), [page, pageSize])
  const enabled = useAsync(() => portal.rechargeEnabled(), [])
  const tiers: { threshold_usd: number; bonus_usd: number }[] = enabled.data?.bonus_tiers || [
    { threshold_usd: 20, bonus_usd: 2 }, { threshold_usd: 50, bonus_usd: 10 }, { threshold_usd: 100, bonus_usd: 25 },
  ]
  const presets = tiers.map((x) => x.threshold_usd)
  const [amount, setAmount] = useState(20)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const bonusFor = (usd: number) => {
    let b = 0
    for (const tr of tiers) if (usd + 1e-9 >= tr.threshold_usd) b = tr.bonus_usd
    return b
  }
  const curBonus = bonusFor(amount)

  async function go() {
    setBusy(true); setMsg(null)
    try {
      const r = await portal.recharge(amount)
      window.location.href = r.url           // 跳转 Polar 托管结账页
    } catch (e: any) {
      setMsg(e?.message || t('failed')); setBusy(false)
    }
  }
  const off = enabled.data && enabled.data.enabled === false
  return (
    <>
      <Card title={t('card_recharge')}>
        <div className="ak-row">
          {presets.map((v) => {
            const b = bonusFor(v)
            return (
              <button key={v} className={`ak-btn ${amount === v ? 'primary' : ''}`} onClick={() => setAmount(v)}>
                ${v}{b > 0 && <span style={{ color: '#16a34a', marginLeft: 4, fontSize: 12 }}>+${b}</span>}
              </button>
            )
          })}
          <input className="ak-input" type="number" value={amount} min={1}
            onChange={(e) => setAmount(Number(e.target.value))} style={{ width: 100 }} />
          <span className="ak-muted">USD</span>
          <button className="ak-btn primary" disabled={busy || !!off} onClick={go}>{busy ? t('recharge_going') : t('recharge_go')}</button>
        </div>
        {curBonus > 0 && (
          <div style={{ marginTop: 8, fontSize: 13 }}>
            {t('recharge_credited')} <b>${amount + curBonus}</b>
            <span style={{ color: '#16a34a', marginLeft: 6 }}>(+${curBonus} {t('bonus_word')})</span>
          </div>
        )}
        <div className="ak-muted" style={{ marginTop: 8, fontSize: 12 }}>
          {t('bonus_tiers_title')}: {tiers.map((tr) => `$${tr.threshold_usd}→+$${tr.bonus_usd}`).join(' · ')}
        </div>
        {off && <div className="ak-muted" style={{ marginTop: 8 }}>{t('recharge_off')}</div>}
        {msg && <div className="ak-err">{msg}</div>}
        <div className="ak-muted" style={{ marginTop: 10, fontSize: 12 }}>
          {t('recharge_note')}
        </div>
      </Card>
      <Card title={t('card_recharge_log')}>
        <Async state={state}>{(data: any) => (<>
          <table className="ak-table">
            <thead><tr><th>{t('col_time')}</th><th>{t('col_amount')}</th><th>{t('col_status')}</th></tr></thead>
            <tbody>
              {(data.items || []).map((r: any) => (
                <tr key={r.id}>
                  <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td>{fmtUsd(r.amount_micro_usd)}</td>
                  <td><Pill kind={r.status === 'paid' ? 'ok' : 'warn'}>{r.status === 'paid' ? t('topup_paid') : t('topup_pending')}</Pill></td>
                </tr>
              ))}
              {(data.items || []).length === 0 && <tr><td colSpan={3} className="ak-muted">{t('empty_topups')}</td></tr>}
            </tbody>
          </table>
          <Pager total={data.total || 0} page={page} pageSize={pageSize}
            onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(1) }} />
        </>)}</Async>
      </Card>
    </>
  )
}
