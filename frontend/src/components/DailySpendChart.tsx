import { type DisplayCurrency } from '../currency'
import { useI18n } from '../i18n'

export function DailySpendChart({ rows, previousRows = [], currency, rmbPerUsd }: {
  rows: any[]; previousRows?: any[]; currency: DisplayCurrency; rmbPerUsd: number
}) {
  const { t } = useI18n()
  const data = rows.map((row) => ({
    day: String(row.day).slice(0, 10),
    value: ((Number(row.china_cost || 0) + Number(row.overseas_cost || 0)) / 1e6) * (currency === 'rmb' ? rmbPerUsd : 1),
  }))
  const previous = previousRows.map((row) => ({
    value: (Number(row.cost || 0) / 1e6) * (currency === 'rmb' ? rmbPerUsd : 1),
  }))
  if (data.length === 0) return <p className="ak-muted">{t('admin_empty_data')}</p>

  const width = 900
  const height = 250
  const left = 62
  const right = 20
  const top = 16
  const bottom = 42
  const plotWidth = width - left - right
  const plotHeight = height - top - bottom
  const max = Math.max(0.01, ...data.map((row) => row.value), ...previous.map((row) => row.value))
  const xAt = (index: number) => left + (data.length === 1 ? plotWidth / 2 : index * plotWidth / (data.length - 1))
  const yAt = (value: number) => top + plotHeight - value / max * plotHeight
  const points = data.map((row, index) => `${xAt(index)},${yAt(row.value)}`).join(' ')
  const previousPoints = previous.map((row, index) => `${xAt(index)},${yAt(row.value)}`).join(' ')
  const area = `${xAt(0)},${top + plotHeight} ${points} ${xAt(data.length - 1)},${top + plotHeight}`
  const labelEvery = Math.max(1, Math.ceil(data.length / 7))
  const symbol = currency === 'rmb' ? '¥' : '$'

  return (
    <div className="ak-admin-chart">
      <div className="ak-chart-legend">
        <span><i className="current" />{t('admin_current_period')}</span>
        {previous.length > 0 && <span><i className="previous" />{t('admin_previous_period')}</span>}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t('billing_daily_trend')}>
        {[0, 0.5, 1].map((ratio) => {
          const y = top + plotHeight * ratio
          const value = max * (1 - ratio)
          return <g key={ratio}>
            <line x1={left} y1={y} x2={width - right} y2={y} className="ak-chart-grid" />
            <text x={left - 8} y={y + 4} textAnchor="end" className="ak-chart-label">{symbol}{value.toFixed(value >= 100 ? 0 : 2)}</text>
          </g>
        })}
        <polygon points={area} className="ak-chart-area" />
        {previousPoints && <polyline points={previousPoints} className="ak-chart-line previous" />}
        <polyline points={points} className="ak-chart-line" />
        {data.map((row, index) => (
          <g key={row.day}>
            <circle cx={xAt(index)} cy={yAt(row.value)} r="4" className="ak-chart-point"><title>{row.day} · {symbol}{row.value.toFixed(2)}</title></circle>
            {(index % labelEvery === 0 || index === data.length - 1) &&
              <text x={xAt(index)} y={height - 14} textAnchor="middle" className="ak-chart-label">{row.day.slice(5)}</text>}
          </g>
        ))}
      </svg>
    </div>
  )
}
