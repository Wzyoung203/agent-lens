import { onBeforeUnmount, onMounted, watch, type Ref } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { useTheme } from './useTheme'

// 按需注册：只打包用到的图表与组件，整包体积从 ~1MB 降到 ~400KB。
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])

/**
 * ECharts 生命周期：挂在 ref 上、option 变化时重绘、容器变化时 resize。
 * 图表容器必须带 role="img" 与 aria-label（无障碍要求）。
 */
export function useChart(
  target: Ref<HTMLElement | null>,
  option: Ref<echarts.EChartsCoreOption>,
): void {
  let chart: echarts.ECharts | undefined
  const { theme } = useTheme()

  const onResize = () => chart?.resize()

  function render(): void {
    if (!target.value) return
    chart ??= echarts.init(target.value, undefined, { renderer: 'canvas' })
    chart.setOption(option.value, true)
  }

  onMounted(() => {
    render()
    window.addEventListener('resize', onResize)
  })

  // option 由调用方按当前主题构建，所以主题一变就重新 setOption 即可。
  watch(option, render, { deep: true })
  watch(theme, render)

  onBeforeUnmount(() => {
    window.removeEventListener('resize', onResize)
    chart?.dispose()
    chart = undefined
  })
}
