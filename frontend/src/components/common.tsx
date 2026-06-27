import { useCallback, useEffect, useState } from 'react'

export function Card({ title, children, actions }: { title?: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="ak-card">
      {(title || actions) && (
        <div className="ak-row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
          {title ? <h3 style={{ margin: 0 }}>{title}</h3> : <span />}
          {actions}
        </div>
      )}
      {children}
    </div>
  )
}

export function Pill({ kind, children }: { kind?: 'ok' | 'bad' | 'warn'; children: React.ReactNode }) {
  return <span className={`ak-pill ${kind || ''}`}>{children}</span>
}

// 简易异步数据钩子：返回 {data, loading, error, reload}
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const run = useCallback(() => {
    setLoading(true)
    setError(null)
    fn()
      .then((d) => setData(d))
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(run, [run])
  return { data, loading, error, reload: run }
}

export function Pager({ total, page, pageSize, onPage, onPageSize }: {
  total: number; page: number; pageSize: number
  onPage: (p: number) => void; onPageSize: (s: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const [jump, setJump] = useState('')
  const go = () => {
    const p = parseInt(jump, 10)
    if (!isNaN(p)) onPage(Math.min(Math.max(1, p), pages))
    setJump('')
  }
  return (
    <div className="ak-row" style={{ justifyContent: 'space-between', marginTop: 12, gap: 8 }}>
      <span className="ak-muted">共 {total} 条 · 第 {page}/{pages} 页</span>
      <div className="ak-row" style={{ gap: 6 }}>
        <select className="ak-select" value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))} style={{ minWidth: 90 }}>
          {[20, 50, 100, 200].map((s) => <option key={s} value={s}>{s} 条/页</option>)}
        </select>
        <button className="ak-btn" disabled={page <= 1} onClick={() => onPage(page - 1)}>上一页</button>
        <button className="ak-btn" disabled={page >= pages} onClick={() => onPage(page + 1)}>下一页</button>
        <input className="ak-input" value={jump} onChange={(e) => setJump(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && go()} placeholder="页码" style={{ width: 64 }} />
        <button className="ak-btn" onClick={go}>跳转</button>
      </div>
    </div>
  )
}

export function Async<T>({ state, children }: { state: ReturnType<typeof useAsync<T>>; children: (d: T) => React.ReactNode }) {
  if (state.loading) return <p className="ak-muted">加载中…</p>
  if (state.error) return <p className="ak-err">{state.error}</p>
  if (state.data == null) return null
  return <>{children(state.data)}</>
}
