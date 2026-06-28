// 轻量设备指纹：用于注册赠送去重（同一设备只送一次 $20）。
// 不依赖第三方库；综合浏览器/硬件/canvas 信号哈希成稳定 id，并在 localStorage 落一份兜底。

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s))
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, '0')).join('')
}

function canvasFp(): string {
  try {
    const c = document.createElement('canvas')
    const ctx = c.getContext('2d')
    if (!ctx) return ''
    ctx.textBaseline = 'top'
    ctx.font = '14px Arial'
    ctx.fillStyle = '#f60'; ctx.fillRect(125, 1, 62, 20)
    ctx.fillStyle = '#069'; ctx.fillText('Substantia,\u{1F600}', 2, 15)
    ctx.fillStyle = 'rgba(102,204,0,0.7)'; ctx.fillText('Substantia,\u{1F600}', 4, 17)
    return c.toDataURL()
  } catch {
    return ''
  }
}

/** 返回稳定的设备 id（同一浏览器/硬件下尽量一致）。失败回落 localStorage 随机 id。 */
export async function getDeviceId(): Promise<string> {
  const n = navigator as any
  const signals = [
    n.userAgent, n.language, (n.languages || []).join(','), n.platform,
    n.hardwareConcurrency, n.deviceMemory,
    screen.width, screen.height, screen.colorDepth,
    Intl.DateTimeFormat().resolvedOptions().timeZone,
    canvasFp(),
  ].join('|')
  try {
    const id = (await sha256Hex(signals)).slice(0, 32)
    try { localStorage.setItem('sa_device', id) } catch { /* ignore */ }
    return id
  } catch {
    // crypto.subtle 不可用（非安全上下文）→ 回落 localStorage 随机 id
    let id = ''
    try { id = localStorage.getItem('sa_device') || '' } catch { /* ignore */ }
    if (!id) {
      id = Math.random().toString(36).slice(2) + Date.now().toString(36)
      try { localStorage.setItem('sa_device', id) } catch { /* ignore */ }
    }
    return id
  }
}
