import { fmtDisplayCurrency, type DisplayCurrency } from '../currency'
import { useI18n } from '../i18n'

const fmtCount = (value: unknown) => new Intl.NumberFormat().format(Number(value || 0))

export function CostDistributionChart({ rows, labelKey, currency, rmbPerUsd }: {
  rows: any[]; labelKey: string; currency: DisplayCurrency; rmbPerUsd: number
}) {
  const { t } = useI18n()
  const palette = ['#fb7185', '#fb923c', '#fbbf24', '#60a5fa', '#34d399', '#a78bfa', '#22d3ee']
  const ranked = [...rows].filter((row) => Number(row.cost || 0) > 0).sort((a, b) => Number(b.cost) - Number(a.cost))
  const top = ranked.slice(0, 6)
  if (ranked.length > 6) {
    top.push({
      [labelKey]: t('admin_other'),
      cost: ranked.slice(6).reduce((sum, row) => sum + Number(row.cost || 0), 0),
    })
  }
  const total = top.reduce((sum, row) => sum + Number(row.cost || 0), 0)
  if (!total) return <p className="ak-muted">{t('admin_empty_data')}</p>

  let cursor = 0
  const stops = top.map((row, index) => {
    const start = cursor
    cursor += Number(row.cost || 0) / total * 100
    return `${palette[index % palette.length]} ${start}% ${cursor}%`
  }).join(', ')

  return <div className="ak-donut-layout">
    <div className="ak-donut" style={{ background: `conic-gradient(${stops})` }}>
      <div><b>{fmtCount(top.length)}</b><small>{t('admin_categories')}</small></div>
    </div>
    <div className="ak-donut-legend">{top.map((row, index) => {
      const percent = Number(row.cost || 0) / total * 100
      return <div key={`${row[labelKey]}-${index}`}>
        <i style={{ background: palette[index % palette.length] }} />
        <span className="ak-mono" title={row[labelKey] || '—'}>{row[labelKey] || '—'}</span>
        <b>{percent.toFixed(1)}%</b>
        <small>{fmtDisplayCurrency(row.cost, currency, rmbPerUsd)}</small>
      </div>
    })}</div>
  </div>
}
