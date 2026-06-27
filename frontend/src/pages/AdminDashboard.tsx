import { useState } from 'react'
import { admin, fmtUsd } from '../api'
import { Async, Card, Pill, useAsync } from '../components/common'

const TABS = [
  ['topups', '充值审核'],
  ['users', '用户'],
  ['prices', '模型定价'],
  ['slots', '上游凭据/容器'],
  ['usage', '用量看板'],
] as const

export function AdminDashboard() {
  const [tab, setTab] = useState<typeof TABS[number][0]>('topups')
  return (
    <>
      <div className="ak-tabs">
        {TABS.map(([k, label]) => (
          <div key={k} className={`ak-tab ${tab === k ? 'active' : ''}`} onClick={() => setTab(k)}>{label}</div>
        ))}
      </div>
      {tab === 'topups' && <Topups />}
      {tab === 'users' && <Users />}
      {tab === 'prices' && <Prices />}
      {tab === 'slots' && <Slots />}
      {tab === 'usage' && <UsageBoard />}
    </>
  )
}

function Topups() {
  const state = useAsync(() => admin.topups(), [])
  async function review(id: number, approve: boolean) {
    await admin.reviewTopup(id, approve)
    state.reload()
  }
  return (
    <Card title="充值申请审核">
      <Async state={state}>{(rows: any[]) => (
        <table className="ak-table">
          <thead><tr><th>时间</th><th>用户</th><th>金额</th><th>理由</th><th>状态</th><th></th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="ak-muted">{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.email}</td>
                <td>{fmtUsd(r.requested_micro_usd)}</td>
                <td className="ak-muted">{r.reason || '—'}</td>
                <td><Pill kind={r.status === 'approved' ? 'ok' : r.status === 'rejected' ? 'bad' : 'warn'}>{r.status}</Pill></td>
                <td>{r.status === 'pending' && (
                  <div className="ak-row">
                    <button className="ak-btn primary" onClick={() => review(r.id, true)}>批准</button>
                    <button className="ak-btn danger" onClick={() => review(r.id, false)}>驳回</button>
                  </div>
                )}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={6} className="ak-muted">暂无申请</td></tr>}
          </tbody>
        </table>
      )}</Async>
    </Card>
  )
}

function Users() {
  const state = useAsync(() => admin.users(), [])
  async function grant(id: number) {
    const v = prompt('调整余额 (USD，可负数)：', '10')
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
  return (
    <Card title="用户管理">
      <Async state={state}>{(rows: any[]) => (
        <table className="ak-table">
          <thead><tr><th>ID</th><th>邮箱</th><th>角色</th><th>状态</th><th>余额</th><th></th></tr></thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td>{u.email}</td>
                <td><Pill kind={u.role === 'admin' ? 'warn' : undefined}>{u.role}</Pill></td>
                <td><Pill kind={u.status === 'active' ? 'ok' : 'bad'}>{u.status}</Pill></td>
                <td className="ak-balance">{fmtUsd(u.balance_micro_usd)}</td>
                <td>
                  <div className="ak-row">
                    <button className="ak-btn" onClick={() => grant(u.id)}>调额</button>
                    <button className="ak-btn" onClick={() => toggleRole(u)}>{u.role === 'admin' ? '降为 user' : '设 admin'}</button>
                    <button className="ak-btn danger" onClick={() => toggleStatus(u)}>{u.status === 'active' ? '禁用' : '启用'}</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}</Async>
    </Card>
  )
}

function Prices() {
  const state = useAsync(() => admin.prices(), [])
  const [f, setF] = useState({ model: '', display_name: '', input: 0, output: 0 })
  async function save() {
    if (!f.model) return
    await admin.upsertPrice({
      model: f.model, display_name: f.display_name,
      input_micro_usd_per_1k: Math.round(f.input), output_micro_usd_per_1k: Math.round(f.output), enabled: true,
    })
    setF({ model: '', display_name: '', input: 0, output: 0 })
    state.reload()
  }
  return (
    <>
      <Card title="新增 / 更新模型定价（单位：微美元 / 1k token）">
        <div className="ak-row">
          <input className="ak-input" placeholder="model id" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} />
          <input className="ak-input" placeholder="显示名" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
          <input className="ak-input" type="number" placeholder="输入价" value={f.input} onChange={(e) => setF({ ...f, input: Number(e.target.value) })} style={{ width: 110 }} />
          <input className="ak-input" type="number" placeholder="输出价" value={f.output} onChange={(e) => setF({ ...f, output: Number(e.target.value) })} style={{ width: 110 }} />
          <button className="ak-btn primary" onClick={save}>保存</button>
        </div>
      </Card>
      <Card title="模型定价表">
        <Async state={state}>{(rows: any[]) => (
          <table className="ak-table">
            <thead><tr><th>模型</th><th>显示名</th><th>输入 /1k</th><th>输出 /1k</th><th>启用</th></tr></thead>
            <tbody>
              {rows.map((p) => (
                <tr key={p.id}>
                  <td className="ak-mono">{p.model}</td>
                  <td>{p.display_name || '—'}</td>
                  <td>{fmtUsd(p.input_micro_usd_per_1k)}</td>
                  <td>{fmtUsd(p.output_micro_usd_per_1k)}</td>
                  <td><Pill kind={p.enabled ? 'ok' : 'bad'}>{p.enabled ? 'on' : 'off'}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}</Async>
      </Card>
    </>
  )
}

function Slots() {
  const state = useAsync(() => admin.slots(), [])
  const [msg, setMsg] = useState<string | null>(null)
  async function ensure() {
    setMsg('拉起容器中…')
    try {
      await admin.ensureContainers()
      setMsg('已触发容器拉起')
    } catch (e: any) {
      setMsg(e?.message || '失败（docker 可能不可达）')
    }
  }
  async function del(id: string) {
    if (!confirm(`删除 slot ${id}？`)) return
    await admin.deleteSlot(id)
    state.reload()
  }
  return (
    <Card title="上游凭据 (slot) / 容器" actions={<button className="ak-btn" onClick={ensure}>拉起所有容器</button>}>
      {msg && <div className="ak-ok">{msg}</div>}
      <p className="ak-muted">slot = 一份上游算力身份：<b>subscription</b>（Claude 订阅）或 <b>api_key</b>（GLM / 千问 / ChatGPT 等）。
        用户按 hash 固定路由到某 slot；某 sub 用光(401/限额)自动转移到其它健康 slot（含 api_key 兜底）。</p>
      <Async state={state}>{(d: any) => (
        <table className="ak-table">
          <thead><tr><th>ID</th><th>类型</th><th>权重</th><th>健康</th><th>可路由</th><th>镜像 / env</th><th></th></tr></thead>
          <tbody>
            {(d.slots || []).map((s: any) => (
              <tr key={s.id}>
                <td className="ak-mono">{s.id}</td>
                <td><Pill kind={s.type === 'subscription' ? 'ok' : 'warn'}>{s.type}</Pill></td>
                <td>{s.weight}</td>
                <td><Pill kind={s.health === 'healthy' ? 'ok' : 'bad'}>{s.health}</Pill></td>
                <td>{s.routable ? '✓' : '✕'}</td>
                <td className="ak-mono ak-muted">{s.image || (s.env_keys?.length ? s.env_keys.join(',') : '—')}</td>
                <td><button className="ak-btn danger" onClick={() => del(s.id)}>删除</button></td>
              </tr>
            ))}
            {(d.slots || []).length === 0 && <tr><td colSpan={7} className="ak-muted">还没有配置 slot（由容器团队 /admin/claude/slots 维护）</td></tr>}
          </tbody>
        </table>
      )}</Async>
    </Card>
  )
}

function UsageBoard() {
  const state = useAsync(() => admin.usageSummary(), [])
  return (
    <Async state={state}>{(d: any) => (
      <>
        <Card title="按模型"><Agg rows={d.by_model} keyCol="model" /></Card>
        <Card title="按用户"><Agg rows={d.by_user} keyCol="email" /></Card>
        <Card title="按 slot"><Agg rows={d.by_slot} keyCol="slot_id" /></Card>
      </>
    )}</Async>
  )
}

function Agg({ rows, keyCol }: { rows: any[]; keyCol: string }) {
  return (
    <table className="ak-table">
      <thead><tr><th>{keyCol}</th><th>调用数</th><th>tokens</th><th>花费</th></tr></thead>
      <tbody>
        {(rows || []).map((r, i) => (
          <tr key={i}>
            <td className="ak-mono">{r[keyCol] || '—'}</td>
            <td>{r.calls}</td>
            <td>{r.tokens || 0}</td>
            <td>{fmtUsd(r.cost)}</td>
          </tr>
        ))}
        {(rows || []).length === 0 && <tr><td colSpan={4} className="ak-muted">暂无数据</td></tr>}
      </tbody>
    </table>
  )
}
