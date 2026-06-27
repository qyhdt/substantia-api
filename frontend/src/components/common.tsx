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

export function Async<T>({ state, children }: { state: ReturnType<typeof useAsync<T>>; children: (d: T) => React.ReactNode }) {
  if (state.loading) return <p className="ak-muted">加载中…</p>
  if (state.error) return <p className="ak-err">{state.error}</p>
  if (state.data == null) return null
  return <>{children(state.data)}</>
}
