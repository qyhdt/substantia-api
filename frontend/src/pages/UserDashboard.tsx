import { useEffect, useState } from 'react'
import {
  fmtCnyFromMicroUsd, fmtModelCost, fmtUsd, isChinaModel, portal, RMB_PER_USD_FALLBACK,
} from '../api'
import { Async, Card, Pager, Pill, useAsync } from '../components/common'
import { useI18n, type TKey } from '../i18n'
import { BRAND } from '../brand'
import { readParam, pushParams, hrefFor } from '../nav'

// 两种协议：Anthropic 兼容 + OpenAI 兼容。同一把 sk-key 通用。
type Fmt = 'anthropic' | 'openai'

// 示例可切换的模型：id 是 API 规范名（curl 里用），cursorName 是 Cursor 自定义模型名
// （避开 Cursor 内置模型名以免撞名走订阅；后台 normalize_model 会归一到 id）。
const MODELS = [
  { id: 'claude-opus-4-8', label: 'Opus 4.8', cursorName: 'claude4.8' },
  { id: 'claude-fable-5', label: 'Fable 5', cursorName: 'fable5' },
  { id: 'claude-sonnet-5', label: 'Sonnet 5', cursorName: 'sonnet5' },
  { id: 'claude-haiku-4-5', label: 'Haiku 4.5', cursorName: 'haiku4.5' },
  { id: 'glm-5.2', label: 'GLM 5.2', cursorName: 'glm-5.2' },
  { id: 'kimi-k3', label: 'Kimi K3', cursorName: 'kimi-k3' },
] as const
type ModelInfo = (typeof MODELS)[number]

// title/note 走 i18n（用 TKey 引用），curl 与语言无关。
const ENDPOINTS: Record<Fmt, { titleKey: TKey; noteKey: TKey; curl: (k: string, model: string) => string }> = {
  anthropic: {
    titleKey: 'anthropic_compat' as TKey,
    noteKey: 'anthropic_note',
    curl: (k, model) => `curl https://${BRAND.apiHost}/v1/messages \\
  -H "x-api-key: ${k}" -H "content-type: application/json" \\
  -d '{"model":"${model}","max_tokens":1024,"messages":[{"role":"user","content":"hello"}]}'`,
  },
  openai: {
    titleKey: 'openai_compat' as TKey,
    noteKey: 'openai_note',
    curl: (k, model) => `curl https://${BRAND.apiHost}/v1/chat/completions \\
  -H "Authorization: Bearer ${k}" -H "content-type: application/json" \\
  -d '{"model":"${model}","messages":[{"role":"user","content":"hello"}]}'`,
  },
}

// Cursor 经 OpenAI 兼容接入：Base URL 必须带 /v1。
const CURSOR_BASE_URL = `https://${BRAND.apiHost}/v1`
// Claude Code CLI 接入：ANTHROPIC_BASE_URL 不带 /v1（CLI 自己拼 /v1/messages）。
const CLI_BASE_URL = CURSOR_BASE_URL.replace(/\/v1$/, '')
const cliSnippet = (k: string, model: string) =>
  `export ANTHROPIC_BASE_URL=${CLI_BASE_URL}
export ANTHROPIC_AUTH_TOKEN=${k}
export ANTHROPIC_MODEL=${model}
claude`
// 排坑用：--settings 命令行参数优先级最高，可无视 ~/.claude/settings.json 里残留的旧网关配置
const cliSettingsSnippet = (k: string, model: string) =>
  `claude --settings '{"env":{"ANTHROPIC_BASE_URL":"${CLI_BASE_URL}","ANTHROPIC_AUTH_TOKEN":"${k}","ANTHROPIC_MODEL":"${model}"}}'`

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

const USER_TABS = ['keys', 'pricing', 'usage', 'topups'] as const
type UTab = typeof USER_TABS[number]
const KEY_SECTIONS = ['manage', 'guide'] as const
type KeySection = typeof KEY_SECTIONS[number]

export function UserDashboard({ newKey }: { newKey?: string }) {
  const { t } = useI18n()
  // 从 URL ?tab= 读初始标签页（「去充值」深链、强制刷新留在本页都靠它），默认 keys
  const [tab, setTab] = useState<UTab>(
    () => readParam('tab', USER_TABS, 'keys') as UTab)
  const tabLabel: Record<UTab, TKey> = { keys: 'tab_keys', pricing: 'tab_pricing', usage: 'tab_usage', topups: 'tab_topups' }
  useEffect(() => {
    const onPop = () => setTab(readParam('tab', USER_TABS, 'keys') as UTab)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  function go(k: UTab) {
    setTab(k)
    pushParams({ view: 'user', tab: k })
  }
  return (
    <div className="ak-sidelayout">
      <aside className="ak-sidebar">
        {USER_TABS.map((k) => (
          <a key={k} className={`ak-side-item ${tab === k ? 'active' : ''}`}
            href={hrefFor({ view: 'user', tab: k })}
            onClick={(e) => { e.preventDefault(); go(k) }}>
            {t(tabLabel[k])}
          </a>
        ))}
      </aside>
      <section className="ak-sidecontent">
        {tab === 'keys' && <Keys justIssued={newKey} />}
        {tab === 'pricing' && <Prices />}
        {tab === 'usage' && <Bills />}
        {tab === 'topups' && <Wallet />}
      </section>
    </div>
  )
}

function Keys({ justIssued }: { justIssued?: string }) {
  const { t } = useI18n()
  const [keySection, setKeySection] = useState<KeySection>(() =>
    justIssued ? 'manage' : readParam('keytab', KEY_SECTIONS, 'manage') as KeySection)
  const state = useAsync(() => portal.keys(), [])
  const [banner, setBanner] = useState<string | null>(justIssued || null)
  const [name, setName] = useState('default')
  const [busy, setBusy] = useState(false)
  const [pick, setPick] = useState<any[] | null>(null)   // 多 key 时弹窗候选
  // 弹窗选中 key 后如何生成复制内容（curl / export / --settings 通用）
  const [pickBuild, setPickBuild] = useState<{ build: (k: string) => string; title: string; btnKey: string } | null>(null)
  const [open, setOpen] = useState<Fmt | null>('anthropic') // 当前展开的协议示例
  const [hint, setHint] = useState<string | null>(null)
  const [copiedBtn, setCopiedBtn] = useState<string | null>(null) // 哪个「一键复制」刚成功（按钮旁内联提示）
  const [model, setModel] = useState<ModelInfo>(MODELS[0])  // 示例展示用的模型

  useEffect(() => {
    const onPop = () => setKeySection(readParam('keytab', KEY_SECTIONS, 'manage') as KeySection)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  function goKeySection(section: KeySection) {
    setKeySection(section)
    pushParams({ view: 'user', tab: 'keys', keytab: section })
  }

  function flashCopied(btnKey: string) {
    setCopiedBtn(btnKey)
    setTimeout(() => setCopiedBtn((c) => (c === btnKey ? null : c)), 2000)
  }

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

  // 一键复制可直接运行的片段（curl / export / --settings）：自动填入真实 key。多个可用 key 时弹窗选。
  // btnKey 用于在对应按钮旁内联提示「复制成功」。
  async function copyWithKey(build: (k: string) => string, title: string, btnKey: string) {
    const keys: any[] = (state.data as any[]) || []
    const usable = keys.filter((k) => k.key_plain && k.status === 'active')
    if (usable.length === 0) {
      // 没有可用 key → 确认后自动生成一个再复制
      if (!window.confirm(t('copy_autokey_confirm'))) return
      try {
        const r = await portal.newKey(name || 'default')
        const plain = r.api_key
        setBanner(plain)
        state.reload()
        await copyText(build(plain))
        flashCopied(btnKey)
      } catch (err: any) {
        setHint(t('copy_curl_nokey'))
        setTimeout(() => setHint(null), 4000)
      }
      return
    }
    if (usable.length === 1) {
      await copyText(build(usable[0].key_plain))
      flashCopied(btnKey)
      return
    }
    setPickBuild({ build, title, btnKey }); setPick(usable)  // 多个 → 弹窗选
  }

  return (
    <>
      <div className="ak-tabs ak-key-subtabs">
        {KEY_SECTIONS.map((section) => (
          <a key={section} className={`ak-tab ${keySection === section ? 'active' : ''}`}
            href={hrefFor({ view: 'user', tab: 'keys', keytab: section })}
            onClick={(event) => { event.preventDefault(); goKeySection(section) }}>
            {t(section === 'manage' ? 'keytab_manage' : 'keytab_guide')}
          </a>
        ))}
      </div>

      {banner && (
        <div className="ak-keybanner">
          <div className="ak-row" style={{ justifyContent: 'space-between' }}>
            <b>{t('newkey_title')}</b>
            <CopyBtn text={banner} label={t('copy_key')} />
          </div>
          <div className="ak-mono" style={{ marginTop: 6 }}>{banner}</div>
        </div>
      )}
      {hint && <div className="ak-ok" style={{ marginBottom: 8 }}>{hint}</div>}

      {keySection === 'manage' && <>
      <Card title={t('card_newkey')} actions={
        <div className="ak-row">
          <input className="ak-input" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('key_name_ph')} />
          <button className="ak-btn primary" onClick={create} disabled={busy}>{t('generate')}</button>
        </div>
      }>
        <p className="ak-muted">{t('newkey_desc_1')}<b>Anthropic</b>{t('newkey_desc_2')}<b>OpenAI</b>{t('newkey_desc_3')}</p>
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
                    <button className="ak-btn primary" onClick={() => copyWithKey((k) => e.curl(k, model.id), t(e.titleKey), fmt)}>{t('copy_real_key')}</button>
                    {copiedBtn === fmt && <span className="ak-ok" style={{ fontSize: 13, alignSelf: 'center' }}>✓ {t('copy_success')}</span>}
                  </div>
                  <pre className="ak-mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{sample}</pre>
                </div>
              )}
            </div>
          )
        })}
      </Card>

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

      </>}

      {keySection === 'guide' && <>
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

      <Card title={t('card_claudecli')}>
        <p className="ak-muted">
          {t('claudecli_desc_1')}<span className="ak-mono">ANTHROPIC_BASE_URL</span>{t('claudecli_desc_2')}<span className="ak-mono">ANTHROPIC_AUTH_TOKEN</span>{t('claudecli_desc_3')}<span className="ak-mono">claude</span>{t('claudecli_desc_4')}
        </p>
        <ModelPicker model={model} onPick={setModel} />
        <div className="ak-row" style={{ justifyContent: 'flex-end', gap: 8, marginBottom: 6 }}>
          <CopyBtn text={cliSnippet('<你的 sk-key>', model.id)} label={t('copy_sample')} />
          <button className="ak-btn primary" onClick={() => copyWithKey((k) => cliSnippet(k, model.id), 'Claude Code', 'cli')}>{t('copy_real_key')}</button>
          {copiedBtn === 'cli' && <span className="ak-ok" style={{ fontSize: 13, alignSelf: 'center' }}>✓ {t('copy_success')}</span>}
        </div>
        <pre className="ak-mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{banner ? cliSnippet(banner, model.id) : cliSnippet('<你的 sk-key>', model.id)}</pre>
        <p className="ak-muted" style={{ fontSize: 12, marginTop: 8 }}>
          {t('claudecli_note')}<span className="ak-mono">ANTHROPIC_MODEL</span>{t('claudecli_note_2')}
        </p>
        <p className="ak-muted" style={{ fontSize: 12, marginTop: 4 }}>
          {t('claudecli_warn_1')}<span className="ak-mono">~/.claude/settings.json</span>{t('claudecli_warn_2')}<span className="ak-mono">env</span>{t('claudecli_warn_3')}
        </p>
        <pre className="ak-mono" style={{ whiteSpace: 'pre-wrap', margin: '6px 0 0', fontSize: 12 }}>{cliSettingsSnippet(banner || '<你的 sk-key>', model.id)}</pre>
        <div className="ak-row" style={{ gap: 8, marginTop: 6 }}>
          <CopyBtn text={cliSettingsSnippet('<你的 sk-key>', model.id)} label={t('copy_sample')} />
          <button className="ak-btn primary" onClick={() => copyWithKey((k) => cliSettingsSnippet(k, model.id), 'Claude Code --settings', 'cli-settings')}>{t('copy_real_key')}</button>
          {copiedBtn === 'cli-settings' && <span className="ak-ok" style={{ fontSize: 13, alignSelf: 'center' }}>✓ {t('copy_success')}</span>}
        </div>
      </Card>
      </>}

      {pick && pickBuild && (
        <div className="ak-modal-bg" onClick={() => setPick(null)}>
          <div className="ak-modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>{t('pick_title').replace('{title}', pickBuild.title)}</h3>
            {pick.map((k) => (
              <button key={k.id} className="ak-btn" style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 8 }}
                onClick={async () => {
                  await copyText(pickBuild.build(k.key_plain))
                  const bk = pickBuild.btnKey
                  setPick(null)
                  flashCopied(bk)
                }}>
                {k.name} · <span className="ak-mono">{k.key_prefix}</span>
              </button>
            ))}
            <button className="ak-btn" onClick={() => setPick(null)}>{t('cancel')}</button>
          </div>
        </div>
      )}

    </>
  )
}

const fmtCount = (value: any) => new Intl.NumberFormat().format(Number(value || 0))

function Bills() {
  const { t } = useI18n()
  const [days, setDays] = useState(7)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const summary = useAsync(() => portal.billingSummary(days), [days])
  const state = useAsync(() => portal.usage(pageSize, (page - 1) * pageSize, days), [page, pageSize, days])
  const pickDays = (value: number) => { setDays(value); setPage(1) }
  return (
    <>
      <Card title={t('billing_overview')} actions={
        <div className="ak-row" style={{ gap: 6 }}>
          {[7, 30, 90].map((value) => (
            <button key={value} className={`ak-btn ${days === value ? 'primary' : ''}`}
              onClick={() => pickDays(value)}>{t(`billing_days_${value}` as TKey)}</button>
          ))}
        </div>
      }>
        <Async state={summary}>{(data: any) => {
          const rate = Number(data.rmb_per_usd || RMB_PER_USD_FALLBACK)
          const models = data.by_model || []
          const maxCost = Math.max(1, ...models.map((row: any) => Number(row.cost || 0)))
          return (<>
            <div className="ak-billing-stats">
              <div className="ak-billing-stat featured">
                <span>{t('billing_total_cost')}</span>
                <b>{fmtUsd(data.overseas_cost_micro_usd)}</b>
                <strong>+ {fmtCnyFromMicroUsd(data.china_cost_micro_usd, rate, 2)}</strong>
                <small>{t('billing_currency_note')}</small>
              </div>
              <div className="ak-billing-stat"><span>{t('billing_calls')}</span><b>{fmtCount(data.total_calls)}</b></div>
              <div className="ak-billing-stat"><span>{t('billing_tokens')}</span><b>{fmtCount(data.total_tokens)}</b></div>
              <div className="ak-billing-stat"><span>{t('billing_exchange_rate')}</span><b>1 USD ≈ {rate} CNY</b></div>
            </div>

            <div className="ak-billing-panels">
              <section>
                <h4>{t('billing_by_model')}</h4>
                <div className="ak-model-cost-list">
                  {models.map((row: any) => (
                    <div className="ak-model-cost" key={row.model}>
                      <div className="ak-row" style={{ justifyContent: 'space-between' }}>
                        <span><b>{row.model}</b> <small>{fmtCount(row.calls)} {t('billing_calls_unit')}</small></span>
                        <strong>{fmtModelCost(row.model, row.cost, rate)}</strong>
                      </div>
                      <div className="ak-cost-track"><i style={{ width: `${Math.max(2, Number(row.cost || 0) / maxCost * 100)}%` }} /></div>
                    </div>
                  ))}
                  {models.length === 0 && <p className="ak-muted">{t('empty_usage')}</p>}
                </div>
              </section>
              <section>
                <h4>{t('billing_daily')}</h4>
                <div className="ak-table-scroll">
                  <table className="ak-table">
                    <thead><tr><th>{t('billing_date')}</th><th>{t('billing_calls')}</th><th>{t('billing_overseas')}</th><th>{t('billing_china')}</th></tr></thead>
                    <tbody>
                      {(data.daily || []).map((row: any) => (
                        <tr key={String(row.day)}>
                          <td>{String(row.day).slice(0, 10)}</td>
                          <td>{fmtCount(row.calls)}</td>
                          <td>{fmtUsd(row.overseas_cost)}</td>
                          <td>{fmtCnyFromMicroUsd(row.china_cost, rate)}</td>
                        </tr>
                      ))}
                      {(data.daily || []).length === 0 && <tr><td colSpan={4} className="ak-muted">{t('empty_usage')}</td></tr>}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </>)
        }}</Async>
      </Card>
      <Card title={t('billing_detail')}>
      <Async state={state}>{(data: any) => (<>
        <div className="ak-table-scroll">
          <table className="ak-table">
            <thead><tr><th>{t('col_time')}</th><th>{t('col_model')}</th><th>{t('col_slot')}</th><th>{t('col_tokens')}</th><th>{t('col_cost')}</th><th>{t('col_status')}</th></tr></thead>
            <tbody>
              {(data.items || []).map((r: any) => (
                <tr key={r.id}>
                  <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td><b>{r.model}</b><div className="ak-muted" style={{ fontSize: 11 }}>{isChinaModel(r.model) ? 'CNY' : 'USD'}</div></td>
                  <td className="ak-mono">{r.slot_id || '—'}</td>
                  <td>{fmtCount(r.total_tokens)} <span className="ak-muted">({fmtCount(r.prompt_tokens)}+{fmtCount(r.completion_tokens)})</span></td>
                  <td><b>{fmtModelCost(r.model, r.cost_micro_usd, summary.data?.rmb_per_usd)}</b></td>
                  <td><Pill kind={r.status === 'ok' ? 'ok' : 'bad'}>{r.status}</Pill></td>
                </tr>
              ))}
              {(data.items || []).length === 0 && <tr><td colSpan={6} className="ak-muted">{t('empty_usage')}</td></tr>}
            </tbody>
          </table>
        </div>
        <Pager total={data.total || 0} page={page} pageSize={pageSize}
          onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(1) }} />
      </>)}</Async>
      </Card>
    </>
  )
}

// 控制台展示完整的当前可用模型；首页营销价格表可按运营需要隐藏部分型号。
const PRICE_MODELS: Array<{ id: string; multiplier: number; noteKey?: TKey }> = [
  { id: 'claude-opus-4-8', multiplier: 0.8 },
  { id: 'claude-sonnet-5', multiplier: 0.8 },
  { id: 'claude-sonnet-4-6', multiplier: 0.8 },
  { id: 'claude-haiku-4-5', multiplier: 0.8 },
  { id: 'claude-fable-5', multiplier: 0.8 },
  { id: 'glm-5.2', multiplier: 0.8, noteKey: 'pricing_glm_note' },
  { id: 'kimi-k3', multiplier: 1, noteKey: 'pricing_kimi_note' },
]

function Prices() {
  const { t } = useI18n()
  const state = useAsync(() => portal.prices(), [])
  const config = useAsync(() => portal.rechargeEnabled(), [])
  const rmbPerUsd = Number(config.data?.rmb_per_usd || RMB_PER_USD_FALLBACK)
  // 库里存的是 micro-USD / 1k token（已是实付价）；换算成 $ / 百万 token：micro_per_1k / 1000。
  const now = (v: any) => Number(v || 0) / 1000
  const perMillion = (micro: any, model: string) => {
    const value = now(micro)
    return isChinaModel(model) ? `¥${(value * rmbPerUsd).toFixed(2)}` : `$${value.toFixed(2)}`
  }
  const PriceCell = ({ micro, multiplier, model }: { micro: any; multiplier: number; model: string }) => {
    const n = now(micro)
    const factor = isChinaModel(model) ? rmbPerUsd : 1
    const symbol = isChinaModel(model) ? '¥' : '$'
    return (
      <span>
        {multiplier < 1 && <><span className="lp-off">{t('pricing_official')} {symbol}{(n / multiplier * factor).toFixed(2)}</span>{' '}</>}
        <b className="lp-now">{symbol}{(n * factor).toFixed(2)}</b>
      </span>
    )
  }
  return (
    <Card title={t('card_prices')}>
      <p className="ak-muted" style={{ marginTop: 0 }}>{t('prices_note')}</p>
      <Async state={state}>{(rows: any[]) => {
        const byId: Record<string, any> = Object.fromEntries(rows.map((r) => [r.model, r]))
        const list = PRICE_MODELS.map((meta) => {
          const row = byId[meta.id]
          return row ? { ...row, priceMeta: meta } : null
        }).filter(Boolean) as any[]
        return (
          <table className="ak-table">
            <thead><tr>
              <th>{t('price_col_model')}</th>
              <th>{t('price_col_in')}</th>
              <th>{t('price_col_out')}</th>
              <th>{t('price_col_cache_read')}</th>
              <th>{t('price_col_cache_write')}</th>
            </tr></thead>
            <tbody>
              {list.map((r) => (
                <tr key={r.model}>
                  <td>
                    <b>{r.display_name || r.model}</b>
                    <div className="ak-mono ak-muted" style={{ fontSize: 12 }}>{r.model}</div>
                    <div className="ak-muted" style={{ fontSize: 11 }}>{isChinaModel(r.model) ? 'CNY' : 'USD'}</div>
                    {r.priceMeta.noteKey && <div className="ak-muted" style={{ fontSize: 11 }}>{t(r.priceMeta.noteKey)}</div>}
                  </td>
                  <td><PriceCell micro={r.input_micro_usd_per_1k} multiplier={r.priceMeta.multiplier} model={r.model} /></td>
                  <td><PriceCell micro={r.output_micro_usd_per_1k} multiplier={r.priceMeta.multiplier} model={r.model} /></td>
                  <td className="ak-muted">{perMillion(r.cache_read_micro_usd_per_1k, r.model)}</td>
                  <td className="ak-muted">{perMillion(r.cache_write_micro_usd_per_1k, r.model)}</td>
                </tr>
              ))}
              {list.length === 0 && <tr><td colSpan={5} className="ak-muted">—</td></tr>}
            </tbody>
          </table>
        )
      }}</Async>
    </Card>
  )
}

function Wallet() {
  const { t } = useI18n()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const state = useAsync(() => portal.payments(pageSize, (page - 1) * pageSize), [page, pageSize])
  const account = useAsync(() => portal.me(), [])
  const enabled = useAsync(() => portal.rechargeEnabled(), [])
  const tiers: { threshold_usd: number; bonus_usd: number }[] = enabled.data?.bonus_tiers || [
    { threshold_usd: 20, bonus_usd: 2 }, { threshold_usd: 50, bonus_usd: 10 }, { threshold_usd: 100, bonus_usd: 25 },
  ]
  const presets = tiers.map((x) => x.threshold_usd)
  const [amount, setAmount] = useState(20)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  // 支付渠道：polar=信用卡(美元) / xunhupay=微信·支付宝(人民币)。默认选第一个可用的。
  const polarOn = enabled.data ? enabled.data.enabled !== false : true
  const xunhupayOn = !!enabled.data?.xunhupay_enabled
  const rmbPerUsd = enabled.data?.rmb_per_usd || 7.2
  const [method, setMethod] = useState<'polar' | 'xunhupay'>('polar')
  useEffect(() => {
    if (!enabled.data) return
    if (!polarOn && xunhupayOn) setMethod('xunhupay')
    else if (polarOn) setMethod('polar')
  }, [enabled.data, polarOn, xunhupayOn])

  const bonusFor = (usd: number) => {
    let b = 0
    for (const tr of tiers) if (usd + 1e-9 >= tr.threshold_usd) b = tr.bonus_usd
    return b
  }
  const curBonus = bonusFor(amount)
  const rmb = Math.round(amount * rmbPerUsd * 100) / 100

  async function go() {
    setBusy(true); setMsg(null)
    try {
      const r = method === 'xunhupay' ? await portal.rechargeXunhupay(amount) : await portal.recharge(amount)
      window.location.href = r.url           // 跳转托管结账页（Polar / 虎皮椒收银台）
    } catch (e: any) {
      setMsg(e?.message || t('failed')); setBusy(false)
    }
  }
  const off = method === 'polar' ? !polarOn : !xunhupayOn
  return (
    <>
      <Card title={t('wallet_overview')}>
        <Async state={account}>{(me: any) => (
          <div className="ak-wallet-grid">
            <div className="ak-wallet-balance">
              <span>{t('wallet_available')}</span>
              <b>{fmtUsd(me.balance_micro_usd)}</b>
              <strong>≈ {fmtCnyFromMicroUsd(me.balance_micro_usd, rmbPerUsd, 2)}</strong>
              <small>{t('wallet_balance_note')}</small>
            </div>
            <div className="ak-wallet-stat"><span>{t('wallet_paid')}</span><b>{fmtUsd(me.paid_micro_usd)}</b></div>
            <div className="ak-wallet-stat"><span>{t('wallet_trial')}</span><b>{fmtUsd(me.trial_active ? me.trial_micro_usd : 0)}</b></div>
            <div className="ak-wallet-stat"><span>{t('wallet_model_currency')}</span><b>GLM / Kimi · CNY</b><small>Claude / GPT · USD</small></div>
          </div>
        )}</Async>
      </Card>
      <Card title={t('card_recharge')}>
        {(polarOn && xunhupayOn) && (
          <div className="ak-row" style={{ marginBottom: 12 }}>
            <button className={`ak-btn ${method === 'polar' ? 'primary' : ''}`} onClick={() => setMethod('polar')}>
              💳 {t('pay_card')}
            </button>
            <button className={`ak-btn ${method === 'xunhupay' ? 'primary' : ''}`} onClick={() => setMethod('xunhupay')}>
              🟢 {t('pay_wx_alipay')}
            </button>
          </div>
        )}
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
        {method === 'xunhupay' && (
          <div style={{ marginTop: 8, fontSize: 13 }}>
            {t('xunhupay_charge')} <b>¥{rmb}</b>
            <span className="ak-muted" style={{ marginLeft: 6, fontSize: 12 }}>(1 USD ≈ {rmbPerUsd} CNY)</span>
          </div>
        )}
        <div className="ak-muted" style={{ marginTop: 8, fontSize: 12 }}>
          {t('bonus_tiers_title')}: {tiers.map((tr) => `$${tr.threshold_usd}→+$${tr.bonus_usd}`).join(' · ')}
        </div>
        {off && <div className="ak-muted" style={{ marginTop: 8 }}>{t('recharge_off')}</div>}
        {msg && <div className="ak-err">{msg}</div>}
        <div className="ak-muted" style={{ marginTop: 10, fontSize: 12 }}>
          {method === 'xunhupay' ? t('recharge_note_wx') : t('recharge_note')}
        </div>
      </Card>
      <Card title={t('card_recharge_log')}>
        <Async state={state}>{(data: any) => (<>
          <table className="ak-table">
            <thead><tr><th>{t('col_time')}</th><th>{t('wallet_channel')}</th><th>{t('col_amount')}</th><th>{t('col_status')}</th></tr></thead>
            <tbody>
              {(data.items || []).map((r: any) => (
                <tr key={r.id}>
                  <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td>{r.provider === 'xunhupay' ? t('pay_wx_alipay') : t('pay_card')}</td>
                  <td><b>{r.provider === 'xunhupay'
                    ? `¥${Number(r.amount_rmb || (Number(r.amount_micro_usd || 0) / 1e6 * rmbPerUsd)).toFixed(2)}`
                    : fmtUsd(r.amount_micro_usd)}</b></td>
                  <td><Pill kind={r.status === 'paid' ? 'ok' : 'warn'}>{r.status === 'paid' ? t('topup_paid') : t('topup_pending')}</Pill></td>
                </tr>
              ))}
              {(data.items || []).length === 0 && <tr><td colSpan={4} className="ak-muted">{t('empty_topups')}</td></tr>}
            </tbody>
          </table>
          <Pager total={data.total || 0} page={page} pageSize={pageSize}
            onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(1) }} />
        </>)}</Async>
      </Card>
      <ManualTopup />
    </>
  )
}

// 人工充值申请：线下转账后填金额 + 上传凭证 → 提交，等 admin 审核。下方列出自己的申请记录。
function ManualTopup() {
  const { t } = useI18n()
  const list = useAsync(() => portal.topups(), [])
  const [amount, setAmount] = useState(20)
  const [reason, setReason] = useState('')
  const [proofUrl, setProofUrl] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  async function onPick(f?: File) {
    if (!f) return
    setUploading(true); setErr(null)
    try {
      const r = await portal.uploadProof(f)
      setProofUrl(r.url)
    } catch (e: any) {
      setErr(e?.message || t('failed'))
    } finally {
      setUploading(false)
    }
  }
  async function submit() {
    if (amount <= 0) return
    setBusy(true); setMsg(null); setErr(null)
    try {
      await portal.submitTopup(amount, reason || undefined, proofUrl || undefined)
      setMsg(t('topup_submitted')); setReason(''); setProofUrl(null)
      list.reload()
    } catch (e: any) {
      setErr(e?.message || t('failed'))
    } finally {
      setBusy(false)
    }
  }
  const statusLabel = (s: string) =>
    s === 'approved' ? t('topup_status_approved') : s === 'rejected' ? t('topup_status_rejected') : t('topup_status_pending')

  return (
    <>
      <Card title={t('card_manual_topup')}>
        <p className="ak-muted">{t('manual_topup_desc')}</p>
        <div className="ak-row" style={{ alignItems: 'center' }}>
          <span className="ak-muted">{t('label_amount_usd')}</span>
          <input className="ak-input" type="number" min={1} value={amount}
            onChange={(e) => setAmount(Number(e.target.value))} style={{ width: 120 }} />
        </div>
        <textarea className="ak-input" placeholder={t('manual_reason_ph')} value={reason}
          onChange={(e) => setReason(e.target.value)} rows={2}
          style={{ width: '100%', marginTop: 10, resize: 'vertical' }} />
        <div className="ak-row" style={{ marginTop: 10, alignItems: 'center' }}>
          <label className="ak-btn" style={{ cursor: 'pointer' }}>
            {uploading ? t('proof_uploading') : t('proof_choose')}
            <input type="file" accept="image/*" hidden disabled={uploading}
              onChange={(e) => onPick(e.target.files?.[0])} />
          </label>
          {proofUrl && <span className="ak-ok" style={{ fontSize: 12 }}>{t('proof_uploaded')}</span>}
          <button className="ak-btn primary" disabled={busy || uploading} onClick={submit}>
            {busy ? t('submitting') : t('submit_topup')}
          </button>
        </div>
        {proofUrl && (
          <img src={proofUrl} alt="proof"
            style={{ maxHeight: 120, marginTop: 10, borderRadius: 8, border: '1px solid var(--border)' }} />
        )}
        <div className="ak-muted" style={{ marginTop: 8, fontSize: 12 }}>{t('proof_optional')}</div>
        {msg && <div className="ak-ok" style={{ marginTop: 8 }}>{msg}</div>}
        {err && <div className="ak-err" style={{ marginTop: 8 }}>{err}</div>}
      </Card>

      <Card title={t('card_my_topups')}>
        <Async state={list}>{(rows: any[]) => (
          <table className="ak-table">
            <thead><tr><th>{t('col_time')}</th><th>{t('col_amount')}</th><th>{t('col_reason')}</th><th>{t('col_proof')}</th><th>{t('col_status')}</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td>{fmtUsd(r.requested_micro_usd)}</td>
                  <td className="ak-muted">{r.reason || '—'}</td>
                  <td>{r.proof_url
                    ? <a className="ak-link" href={r.proof_url} target="_blank" rel="noreferrer">{t('view_proof')}</a>
                    : '—'}</td>
                  <td><Pill kind={r.status === 'approved' ? 'ok' : r.status === 'rejected' ? 'bad' : 'warn'}>{statusLabel(r.status)}</Pill></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={5} className="ak-muted">{t('empty_my_topups')}</td></tr>}
            </tbody>
          </table>
        )}</Async>
      </Card>
    </>
  )
}
