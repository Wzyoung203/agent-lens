<script setup lang="ts">
import { computed } from 'vue'
import type { EChartsCoreOption } from 'echarts'

import { api } from '@/api/endpoints'
import ChartCard from '@/components/ChartCard.vue'
import EChart from '@/components/EChart.vue'
import MetricCard from '@/components/MetricCard.vue'
import PageHeader from '@/components/PageHeader.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useAsync } from '@/composables/useAsync'
import { usePolling } from '@/composables/usePolling'
import { useRange } from '@/composables/useRange'
import { useTheme } from '@/composables/useTheme'
import { formatCost, formatDay, formatInt, formatPercent, formatTokens } from '@/utils/format'
import { CHART_COLORS, axisTheme } from '@/utils/palette'

const { days } = useRange()
const { theme } = useTheme()
const state = useAsync(() => api.overview(days.value), [days])
usePolling(state.reload)

const cards = computed(() => state.data.value?.cards ?? null)
const projects = computed(() => state.data.value?.projects ?? [])
const daily = computed(() => state.data.value?.daily ?? [])

const trendOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  return {
    grid: { left: 8, right: 8, top: 28, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
    },
    legend: { top: 0, textStyle: { color: palette.label }, itemWidth: 10, itemHeight: 10 },
    xAxis: {
      type: 'category',
      data: daily.value.map((point) => formatDay(point.day)),
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    yAxis: [
      {
        type: 'value',
        axisLine: { show: false },
        splitLine: { lineStyle: { color: palette.axis } },
        axisLabel: { color: palette.label, formatter: (value: number) => formatTokens(value) },
      },
      {
        type: 'value',
        axisLine: { show: false },
        splitLine: { show: false },
        axisLabel: { color: palette.label, formatter: (value: number) => `$${value}` },
      },
    ],
    series: [
      {
        name: 'input token',
        type: 'bar',
        stack: 'token',
        data: daily.value.map((point) => point.input_tokens - point.cached_input_tokens),
        itemStyle: { color: CHART_COLORS[0], borderRadius: [0, 0, 3, 3] },
      },
      {
        name: '命中缓存',
        type: 'bar',
        stack: 'token',
        data: daily.value.map((point) => point.cached_input_tokens),
        itemStyle: { color: CHART_COLORS[1], opacity: 0.6 },
      },
      {
        name: 'output token',
        type: 'bar',
        stack: 'token',
        data: daily.value.map((point) => point.output_tokens),
        itemStyle: { color: CHART_COLORS[2] },
      },
      {
        name: '成本',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        data: daily.value.map((point) => Number(point.cost.toFixed(4))),
        lineStyle: { color: CHART_COLORS[3], width: 2 },
        itemStyle: { color: CHART_COLORS[3] },
      },
    ],
  }
})

const projectOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  const ranked = [...projects.value].sort((a, b) => a.cost - b.cost)
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axis: 'y',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      valueFormatter: (value: number) => formatCost(value),
    },
    xAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    yAxis: {
      type: 'category',
      data: ranked.map((item) => item.project),
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    series: [
      {
        type: 'bar',
        data: ranked.map((item) => Number(item.cost.toFixed(6))),
        itemStyle: { color: CHART_COLORS[0], borderRadius: [0, 4, 4, 0] },
        barMaxWidth: 18,
      },
    ],
  }
})

const topProject = computed(() => projects.value[0] ?? null)
</script>

<template>
  <PageHeader
    title="总览"
    :description="`最近 ${days} 天 · UTC 分桶，界面按本地时区显示`"
  />

  <StateBlock
    :loading="state.loading.value && !cards"
    :error="state.error.value"
    :empty="!cards || cards.api_call_count === 0"
    @retry="state.reload"
  >
    <div class="cards">
      <MetricCard
        label="总花费"
        accent
        :value="formatCost(cards?.total_cost ?? 0, cards?.currency)"
        :hint="cards && cards.unpriced_calls > 0 ? `${cards.unpriced_calls} 次调用查不到价目表` : '按当前价目表重算'"
      />
      <MetricCard
        label="Token 总量"
        :value="formatTokens(cards?.total_tokens ?? 0)"
        :hint="
          cards
            ? `输入 ${formatTokens(cards.input_tokens)} · 输出 ${formatTokens(cards.output_tokens)}`
            : ''
        "
      />
      <MetricCard
        label="缓存命中率"
        :value="formatPercent(cards?.cache_hit_rate ?? 0)"
        :hint="cards ? `命中 ${formatTokens(cards.cached_input_tokens)} token` : ''"
      />
      <MetricCard
        label="会话 / 轮次"
        :value="`${formatInt(cards?.session_count ?? 0)} / ${formatInt(cards?.turn_count ?? 0)}`"
        :hint="cards ? `${formatInt(cards.api_call_count)} 次 API 调用` : ''"
      />
      <MetricCard
        label="工具失败率"
        :value="formatPercent(cards?.tool_failure_rate ?? 0)"
        :hint="cards ? `共 ${formatInt(cards.tool_call_count)} 次工具调用` : ''"
      />
      <MetricCard
        label="最贵项目"
        :value="topProject ? topProject.project : '—'"
        :hint="topProject ? formatCost(topProject.cost, cards?.currency) : ''"
      />
    </div>

    <div class="grid">
      <ChartCard
        title="按天 token 与成本"
        :subtitle="`${daily.length} 天有调用记录`"
      >
        <EChart :option="trendOption" label="按天 token 堆叠柱状图与成本折线图" height="320px" />
      </ChartCard>

      <ChartCard title="项目消费排行" subtitle="按当前价目表重算">
        <EChart :option="projectOption" label="项目消费排行榜" height="320px" />
      </ChartCard>
    </div>
  </StateBlock>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: var(--al-space-3);
  margin-bottom: var(--al-space-4);
}

.grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: var(--al-space-3);
}

@media (max-width: 1200px) {
  .cards {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .cards {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
