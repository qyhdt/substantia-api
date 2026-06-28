import { useState } from 'react'
import { fmtUsd, portal } from '../api'
import { Async, Card, Pager, Pill, useAsync } from '../components/common'

// 两种协议：Anthropic 兼容 + OpenAI 兼容。同一把 sk-key 通用。
type Fmt = 'anthropic' | 'openai'
const ENDPOINTS: Record<Fmt, { title: string; note: string; curl: (k: string) => string }> = {
  anthropic: {
    title: 'Anthropic 兼容',
    note: '官方 anthropic SDK：base_url = https://api.substantia.ai，key 当作 x-api-key',
    curl: (k) => `curl https://api.substantia.ai/v1/messages \\
  -H "x-api-key: ${k}" -H "content-type: application/json" \\
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"hello"}]}'`,
  },
  openai: {
    title: 'OpenAI 兼容',
    note: '官方 openai SDK：base_url = https://api.substantia.ai/v1，key 当作 api_key（Bearer）',
    curl: (k) => `curl https://api.substantia.ai/v1/chat/completions \\
  -H "Authorization: Bearer ${k}" -H "content-type: application/json" \\
  -d '{"model":"claude-opus-4-8","messages":[{"role":"user","content":"hello"}]}'`,
  },
}

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

function CopyBtn({ text, label = '复制' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return <button className="ak-btn" onClick={async () => {
    await copyText(text); setDone(true); setTimeout(() => setDone(false), 1500)
  }}>{done ? '已复制 ✓' : label}</button>
}

export function UserDashboard({ newKey }: { newKey?: string }) {
  const [tab, setTab] = useState<'keys' | 'usage' | 'topups'>('keys')
  return (
    <>
      <div className="ak-tabs">
        {(['keys', 'usage', 'topups'] as const).map((t) => (
          <div key={t} className={`ak-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t === 'keys' ? '我的 Key' : t === 'usage' ? '用量明细' : '充值'}
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
  const state = useAsync(() => portal.keys(), [])
  const [banner, setBanner] = useState<string | null>(justIssued || null)
  const [name, setName] = useState('default')
  const [busy, setBusy] = useState(false)
  const [pick, setPick] = useState<any[] | null>(null)   // 多 key 时弹窗候选
  const [pickFmt, setPickFmt] = useState<Fmt>('anthropic')
  const [open, setOpen] = useState<Fmt | null>('anthropic') // 当前展开的协议示例
  const [hint, setHint] = useState<string | null>(null)

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
    if (!window.confirm('确认删除这把 key？删除后用它的请求会立即失效，且不可恢复。')) return
    await portal.deleteKey(id)
    state.reload()
  }

  // 一键复制可直接运行的测试 curl（指定协议）：自动填入真实 key。多个可用 key 时弹窗选。
  async function copyTestCurl(fmt: Fmt) {
    const keys: any[] = (state.data as any[]) || []
    const usable = keys.filter((k) => k.key_plain && k.status === 'active')
    if (usable.length === 0) {
      setHint('没有可自动填入的 key —— 旧 key 不保存明文，请先「生成」一个新 key')
      setTimeout(() => setHint(null), 4000)
      return
    }
    if (usable.length === 1) {
      await copyText(ENDPOINTS[fmt].curl(usable[0].key_plain))
      setHint('已复制 ' + ENDPOINTS[fmt].title + ' curl（含 ' + usable[0].name + '）✓')
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
            <b>新 Key（仅显示一次，请妥善保存）：</b>
            <CopyBtn text={banner} label="复制 Key" />
          </div>
          <div className="ak-mono" style={{ marginTop: 6 }}>{banner}</div>
        </div>
      )}
      <Card title="新建 Key" actions={
        <div className="ak-row">
          <input className="ak-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="名称" />
          <button className="ak-btn primary" onClick={create} disabled={busy}>生成</button>
        </div>
      }>
        <p className="ak-muted">把 key 当作下游网关的密钥用，同时支持 <b>Anthropic</b> 和 <b>OpenAI</b> 两种协议，点开看示例：</p>
        {hint && <div className="ak-ok" style={{ marginBottom: 8 }}>{hint}</div>}
        {(['anthropic', 'openai'] as Fmt[]).map((fmt) => {
          const e = ENDPOINTS[fmt]
          const expanded = open === fmt
          const sample = banner ? e.curl(banner) : e.curl('<你的 sk-key>')
          return (
            <div key={fmt} className="ak-accordion">
              <div className="ak-accordion-h" onClick={() => setOpen(expanded ? null : fmt)}>
                <b>{e.title}</b>
                <span className="ak-muted" style={{ fontSize: 12 }}>{expanded ? '收起 ▲' : '点开查看 ▼'}</span>
              </div>
              {expanded && (
                <div className="ak-accordion-b">
                  <p className="ak-muted" style={{ fontSize: 12, margin: '0 0 8px' }}>{e.note}</p>
                  <div className="ak-row" style={{ justifyContent: 'flex-end', gap: 8, marginBottom: 6 }}>
                    <CopyBtn text={e.curl('<你的 sk-key>')} label="复制示例" />
                    <button className="ak-btn primary" onClick={() => copyTestCurl(fmt)}>一键复制（填入真实 key）</button>
                  </div>
                  <pre className="ak-mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{sample}</pre>
                </div>
              )}
            </div>
          )
        })}
      </Card>

      {pick && (
        <div className="ak-modal-bg" onClick={() => setPick(null)}>
          <div className="ak-modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>选择一个 Key 生成 {ENDPOINTS[pickFmt].title} curl</h3>
            {pick.map((k) => (
              <button key={k.id} className="ak-btn" style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 8 }}
                onClick={async () => {
                  await copyText(ENDPOINTS[pickFmt].curl(k.key_plain))
                  setPick(null)
                  setHint('已复制 ' + ENDPOINTS[pickFmt].title + ' curl（含 ' + k.name + '）✓')
                  setTimeout(() => setHint(null), 2500)
                }}>
                {k.name} · <span className="ak-mono">{k.key_prefix}</span>
              </button>
            ))}
            <button className="ak-btn" onClick={() => setPick(null)}>取消</button>
          </div>
        </div>
      )}

      <Card title="我的 Key 列表">
        <Async state={state}>{(keys: any[]) => (
          <table className="ak-table">
            <thead><tr><th>名称</th><th>前缀</th><th>状态</th><th>已花费</th><th>封顶</th><th>创建</th><th></th></tr></thead>
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
                        ? <CopyBtn text={k.key_plain} label="复制" />
                        : <button className="ak-btn" disabled title="旧 key 未存明文，无法复制完整 key">复制</button>}
                      {k.status === 'active' &&
                        <button className="ak-btn" onClick={() => disable(k.id)}>禁用</button>}
                      <button className="ak-btn danger" onClick={() => del(k.id)}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
              {keys.length === 0 && <tr><td colSpan={7} className="ak-muted">暂无 key</td></tr>}
            </tbody>
          </table>
        )}</Async>
      </Card>
    </>
  )
}

function Usage() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const state = useAsync(() => portal.usage(pageSize, (page - 1) * pageSize), [page, pageSize])
  return (
    <Card title="用量明细">
      <Async state={state}>{(data: any) => (<>
        <table className="ak-table">
          <thead><tr><th>时间</th><th>模型</th><th>slot</th><th>tokens</th><th>花费</th><th>状态</th></tr></thead>
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
            {(data.items || []).length === 0 && <tr><td colSpan={6} className="ak-muted">还没有调用记录</td></tr>}
          </tbody>
        </table>
        <Pager total={data.total || 0} page={page} pageSize={pageSize}
          onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(1) }} />
      </>)}</Async>
    </Card>
  )
}

function Topups() {
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const state = useAsync(() => portal.payments(pageSize, (page - 1) * pageSize), [page, pageSize])
  const enabled = useAsync(() => portal.rechargeEnabled(), [])
  const [amount, setAmount] = useState(10)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  async function go() {
    setBusy(true); setMsg(null)
    try {
      const r = await portal.recharge(amount)
      window.location.href = r.url           // 跳转 Polar 托管结账页
    } catch (e: any) {
      setMsg(e?.message || '失败'); setBusy(false)
    }
  }
  const off = enabled.data && enabled.data.enabled === false
  return (
    <>
      <Card title="充值（信用卡 / 支付宝 / 微信）">
        <div className="ak-row">
          {[10, 50, 100].map((v) => (
            <button key={v} className={`ak-btn ${amount === v ? 'primary' : ''}`} onClick={() => setAmount(v)}>${v}</button>
          ))}
          <input className="ak-input" type="number" value={amount} min={1}
            onChange={(e) => setAmount(Number(e.target.value))} style={{ width: 100 }} />
          <span className="ak-muted">USD</span>
          <button className="ak-btn primary" disabled={busy || !!off} onClick={go}>{busy ? '跳转中…' : '去支付'}</button>
        </div>
        {off && <div className="ak-muted" style={{ marginTop: 8 }}>充值暂未开通</div>}
        {msg && <div className="ak-err">{msg}</div>}
        <div className="ak-muted" style={{ marginTop: 10, fontSize: 12 }}>
          支付由 Polar 处理，成功后余额自动到账（可能有几秒延迟）。
        </div>
      </Card>
      <Card title="充值记录">
        <Async state={state}>{(data: any) => (<>
          <table className="ak-table">
            <thead><tr><th>时间</th><th>金额</th><th>状态</th></tr></thead>
            <tbody>
              {(data.items || []).map((r: any) => (
                <tr key={r.id}>
                  <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td>{fmtUsd(r.amount_micro_usd)}</td>
                  <td><Pill kind={r.status === 'paid' ? 'ok' : 'warn'}>{r.status === 'paid' ? '已到账' : '待支付'}</Pill></td>
                </tr>
              ))}
              {(data.items || []).length === 0 && <tr><td colSpan={3} className="ak-muted">暂无记录</td></tr>}
            </tbody>
          </table>
          <Pager total={data.total || 0} page={page} pageSize={pageSize}
            onPage={setPage} onPageSize={(s) => { setPageSize(s); setPage(1) }} />
        </>)}</Async>
      </Card>
    </>
  )
}
