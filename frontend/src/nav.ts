// 极简 URL 状态同步：把当前菜单/标签页写进 query，让强制刷新能留在本页、菜单可作为真链接。
// 不引 react-router，够用即可。

// readParam 从 query 读取一个受限取值；非法/缺失回落 def。
export function readParam(name: string, allowed: readonly string[], def: string): string {
  const v = new URLSearchParams(window.location.search).get(name)
  return v && allowed.includes(v) ? v : def
}

// pushParams 合并若干 query 参数并 pushState（不触发整页刷新）。
export function pushParams(params: Record<string, string>) {
  const q = new URLSearchParams(window.location.search)
  for (const [k, v] of Object.entries(params)) q.set(k, v)
  window.history.pushState(null, '', `${window.location.pathname}?${q.toString()}`)
}

// hrefFor 基于当前 query 叠加参数，生成给 <a href> 用的相对地址（中键/复制/刷新皆可）。
export function hrefFor(params: Record<string, string>): string {
  const q = new URLSearchParams(window.location.search)
  for (const [k, v] of Object.entries(params)) q.set(k, v)
  return `?${q.toString()}`
}
