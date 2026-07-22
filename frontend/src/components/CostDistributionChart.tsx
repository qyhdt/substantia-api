import { lazy, Suspense } from 'react'
import { fmtDisplayCurrency, type DisplayCurrency } from '../currency'
import { useI18n } from '../i18n'

const EChart = lazy(() => import('./EChart').then((module) => ({ default: module.EChart })))

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
  const chartLabel = labelKey === 'model' ? t('admin_model_distribution') : t('admin_channel_distribution')

  const chartData = top.map((row, index) => ({
    name: String(row[labelKey] || '—'),
    value: Number(row.cost || 0),
    itemStyle: { color: palette[index % palette.length] },
  }))
  const byName = new Map(chartData.map((row) => [row.name, row.value]))
  const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char] || char))
  const option = {
    animationDuration: 700,
    animationEasing: 'cubicOut' as const,
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(17, 24, 39, .94)',
      borderWidth: 0,
      textStyle: { color: '#f9fafb' },
      formatter: (params: any) => {
        const name = String(params?.name || '—')
        const value = byName.get(name) || 0
        return `<b>${escapeHtml(name)}</b><br/>${Number(params?.percent || 0).toFixed(1)}% · ${fmtDisplayCurrency(value, currency, rmbPerUsd)}`
      },
    },
    series: [{
      type: 'pie',
      radius: ['58%', '82%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: true,
      label: { show: false },
      labelLine: { show: false },
      itemStyle: { borderColor: '#ffffff', borderWidth: 3, borderRadius: 6 },
      emphasis: { scale: true, scaleSize: 8 },
      data: chartData,
    }],
  }

  return <div className="ak-donut-layout">
    <div className="ak-echart-donut-wrap">
      <Suspense fallback={<div className="ak-echart ak-echart-donut" />}>
        <EChart option={option} className="ak-echart-donut" ariaLabel={chartLabel} />
      </Suspense>
      <div className="ak-donut-center"><b>{fmtCount(top.length)}</b><small>{t('admin_categories')}</small></div>
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
