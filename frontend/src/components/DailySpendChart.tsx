import { lazy, Suspense } from 'react'
import { type DisplayCurrency } from '../currency'
import { useI18n } from '../i18n'

const EChart = lazy(() => import('./EChart').then((module) => ({ default: module.EChart })))

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

  const symbol = currency === 'rmb' ? '¥' : '$'
  const currentName = t('admin_current_period')
  const previousName = t('admin_previous_period')
  const series: any[] = [{
    name: currentName,
    type: 'line',
    smooth: 0.35,
    symbol: 'circle',
    symbolSize: 8,
    showSymbol: data.length <= 31,
    lineStyle: { width: 3, color: '#f59e0b', shadowColor: 'rgba(245, 158, 11, .25)', shadowBlur: 8 },
    itemStyle: { color: '#f59e0b', borderColor: '#ffffff', borderWidth: 2 },
    areaStyle: {
      color: {
        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [{ offset: 0, color: 'rgba(245, 158, 11, .32)' }, { offset: 1, color: 'rgba(245, 158, 11, .02)' }],
      },
    },
    data: data.map((row) => row.value),
  }]
  if (previous.length > 0) series.push({
    name: previousName,
    type: 'line',
    smooth: 0.35,
    symbol: 'none',
    lineStyle: { width: 2, type: 'dashed', color: '#94a3b8' },
    data: previous.map((row) => row.value),
  })
  const option = {
    animationDuration: 800,
    animationEasing: 'cubicOut' as const,
    color: ['#f59e0b', '#94a3b8'],
    grid: { left: 12, right: 18, top: 46, bottom: 12, containLabel: true },
    legend: {
      show: true,
      top: 4,
      right: 4,
      itemWidth: 20,
      itemHeight: 3,
      textStyle: { color: '#667085', fontSize: 12 },
      data: previous.length > 0 ? [currentName, previousName] : [currentName],
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(17, 24, 39, .94)',
      borderWidth: 0,
      textStyle: { color: '#f9fafb' },
      valueFormatter: (value: any) => `${symbol}${Number(value || 0).toFixed(2)}`,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: data.map((row) => row.day.slice(5)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#d0d5dd' } },
      axisLabel: { color: '#667085', hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      min: 0,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#667085', formatter: (value: number) => `${symbol}${Number(value).toFixed(value >= 100 ? 0 : 2)}` },
      splitLine: { lineStyle: { color: '#eaecf0', type: 'dashed' } },
    },
    series,
  }

  return <Suspense fallback={<div className="ak-echart ak-echart-line" />}>
    <EChart option={option} className="ak-echart-line" ariaLabel={t('billing_daily_trend')} />
  </Suspense>
}
