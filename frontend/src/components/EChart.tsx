import { useEffect, useRef } from 'react'
import { init, use as registerEChartsModules, type EChartsCoreOption, type EChartsType } from 'echarts/core'
import { LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

registerEChartsModules([LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

export function EChart({ option, className = '', ariaLabel }: {
  option: EChartsCoreOption
  className?: string
  ariaLabel: string
}) {
  const elementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<EChartsType | null>(null)

  useEffect(() => {
    if (!elementRef.current) return
    const chart = init(elementRef.current, undefined, { renderer: 'canvas' })
    chartRef.current = chart
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(elementRef.current)
    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    chartRef.current?.setOption(option, { notMerge: true })
  }, [option])

  return <div ref={elementRef} className={`ak-echart ${className}`} role="img" aria-label={ariaLabel} />
}
