import { useState } from 'react'
import { fmtUsd, portal } from '../api'
import { Async, Card, Pill, useAsync } from '../components/common'

const GATEWAY_HINT = `curl https://api.substantia.ai/v1/messages \\
  -H "x-api-key: <你的 sk-key>" -H "content-type: application/json" \\
  -d '{"model":"claude-sonnet-4","messages":[{"role":"user","content":"hello"}]}'`

export function UserDashboard({ newKey }: { newKey?: string }) {
  const [tab, setTab] = useState<'keys' | 'usage' | 'topups'>('keys')
  return (
    <>
      <div className="ak-tabs">
        {(['keys', 'usage', 'topups'] as const).map((t) => (
          <div key={t} className={`ak-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t === 'keys' ? '我的 Key' : t === 'usage' ? '用量明细' : '充值申请'}
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

  return (
    <>
      {banner && (
        <div className="ak-keybanner">
          <b>新 Key（仅显示一次，请妥善保存）：</b>
          <div className="ak-mono" style={{ marginTop: 6 }}>{banner}</div>
        </div>
      )}
      <Card title="新建 Key" actions={
        <div className="ak-row">
          <input className="ak-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="名称" />
          <button className="ak-btn primary" onClick={create} disabled={busy}>生成</button>
        </div>
      }>
        <p className="ak-muted">把 key 当作 Anthropic 的 <code>x-api-key</code> 用，base_url 指向本网关：</p>
        <pre className="ak-mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{GATEWAY_HINT}</pre>
      </Card>

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
                  <td>{k.status === 'active' &&
                    <button className="ak-btn danger" onClick={() => disable(k.id)}>禁用</button>}</td>
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
  const state = useAsync(() => portal.usage(), [])
  return (
    <Card title="用量明细">
      <Async state={state}>{(rows: any[]) => (
        <table className="ak-table">
          <thead><tr><th>时间</th><th>模型</th><th>slot</th><th>tokens</th><th>花费</th><th>状态</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.model}</td>
                <td className="ak-mono">{r.slot_id || '—'}</td>
                <td>{r.total_tokens} <span className="ak-muted">({r.prompt_tokens}+{r.completion_tokens})</span></td>
                <td>{fmtUsd(r.cost_micro_usd)}</td>
                <td><Pill kind={r.status === 'ok' ? 'ok' : 'bad'}>{r.status}</Pill></td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} className="ak-muted">还没有调用记录</td></tr>}
          </tbody>
        </table>
      )}</Async>
    </Card>
  )
}

function Topups() {
  const state = useAsync(() => portal.topups(), [])
  const [amount, setAmount] = useState(50)
  const [reason, setReason] = useState('')
  const [msg, setMsg] = useState<string | null>(null)

  async function submit() {
    setMsg(null)
    try {
      await portal.submitTopup(amount, reason)
      setMsg('已提交，等待管理员审核')
      state.reload()
    } catch (e: any) {
      setMsg(e?.message || '失败')
    }
  }
  return (
    <>
      <Card title="申请加额度 / 充值" actions={
        <div className="ak-row">
          <input className="ak-input" type="number" value={amount} min={1}
            onChange={(e) => setAmount(Number(e.target.value))} style={{ width: 100 }} />
          <span className="ak-muted">USD</span>
          <input className="ak-input" placeholder="理由（可选）" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="ak-btn primary" onClick={submit}>提交</button>
        </div>
      }>
        {msg && <div className="ak-ok">{msg}</div>}
      </Card>
      <Card title="我的申请">
        <Async state={state}>{(rows: any[]) => (
          <table className="ak-table">
            <thead><tr><th>时间</th><th>金额</th><th>理由</th><th>状态</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                  <td>{fmtUsd(r.requested_micro_usd)}</td>
                  <td className="ak-muted">{r.reason || '—'}</td>
                  <td><Pill kind={r.status === 'approved' ? 'ok' : r.status === 'rejected' ? 'bad' : 'warn'}>{r.status}</Pill></td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={4} className="ak-muted">暂无申请</td></tr>}
            </tbody>
          </table>
        )}</Async>
      </Card>
    </>
  )
}
