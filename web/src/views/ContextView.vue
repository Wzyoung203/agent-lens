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
import { formatInt, formatPercent, formatTokens } from '@/utils/format'
import { CHART_COLORS, axisTheme } from '@/utils/palette'

const { days } = useRange()
const { theme } = useTheme()
const state = useAsync(() => api.context(days.value))
usePolling(state.reload)

const data = computed(() => state.data.value ?? null)

const BLOCK_LABELS: Record<string, string> = {
  fixed_instructions: '固定指令',
  skill_catalog: 'skill 目录',
  history: '历史对话',
  tool_output: '工具输出',
  unattributed: '未归因',
}

function blockLabel(block: string): string {
  return BLOCK_LABELS[block] ?? block
}

const pieOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      formatter: (params: { name: string; value: number; percent: number }) =>
        `${params.name}<br/>${formatTokens(params.value)}（${params.percent.toFixed(1)}%）`,
    },
    legend: { bottom: 0, textStyle: { color: palette.label } },
    color: CHART_COLORS,
    series: [
      {
        type: 'pie',
        radius: ['45%', '70%'],
        center: ['50%', '44%'],
        itemStyle: { borderColor: 'transparent', borderWidth: 2 },
        label: { color: palette.label },
        data: (data.value?.blocks ?? []).map((row) => ({
          name: blockLabel(row.block),
          value: row.tokens,
        })),
      },
    ],
  }
})
</script>

<template>
  <PageHeader
    title="上下文成本"
    description="把每次调用的 input 拆开看：固定指令、skill 目录、历史对话、工具输出，以及未归因部分"
  />

  <StateBlock
    :loading="state.loading.value && !data"
    :error="state.error.value"
    :empty="!data || data.analyzed_calls === 0"
    empty-text="还没有上下文分解数据，先运行 agent-lens analyze"
    @retry="state.reload"
  >
    <div class="cards">
      <MetricCard
        label="分析覆盖率"
        :value="formatPercent(data?.coverage ?? 0)"
        :hint="`${data?.analyzed_calls ?? 0} / ${data?.total_calls ?? 0} 次调用`"
      />
      <MetricCard
        label="cache 命中率"
        :value="formatPercent(data?.cache_hit_rate ?? 0)"
        hint="命中部分实际成本远低于数字本身"
      />
      <MetricCard
        label="input token 总量"
        :value="formatTokens(data?.input_tokens ?? 0)"
        hint="已分析的调用里，五块之和等于它们的真实 input"
        accent
      />
    </div>

    <ChartCard
      title="上下文构成"
      subtitle="锚定到真实 input_tokens；未归因是每次重发的工具定义与请求框架"
    >
      <EChart :option="pieOption" label="上下文构成占比" />
    </ChartCard>

    <div class="al-card panel">
      <header>
        <h3>按块明细</h3>
        <span class="al-dim small">CJK 字符按 0.6 token/字符、其他按 0.3 估算后锚定</span>
      </header>
      <el-table :data="data?.blocks ?? []" size="small">
        <el-table-column label="块">
          <template #default="{ row }">{{ blockLabel(row.block) }}</template>
        </el-table-column>
        <el-table-column label="token">
          <template #default="{ row }">{{ formatInt(row.tokens) }}</template>
        </el-table-column>
        <el-table-column label="占比">
          <template #default="{ row }">{{ formatPercent(row.share) }}</template>
        </el-table-column>
        <el-table-column label="CJK 字符">
          <template #default="{ row }">{{ formatInt(row.cjk_chars) }}</template>
        </el-table-column>
        <el-table-column label="其他字符">
          <template #default="{ row }">{{ formatInt(row.other_chars) }}</template>
        </el-table-column>
      </el-table>
    </div>
  </StateBlock>
</template>

<style scoped>
.cards {
  display: grid;
  gap: var(--al-space-4);
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  margin-bottom: var(--al-space-4);
}

.panel {
  margin-top: var(--al-space-4);
  padding: var(--al-space-4);
}

.panel header {
  display: flex;
  align-items: baseline;
  gap: var(--al-space-2);
  margin-bottom: var(--al-space-3);
}

.panel h3 {
  margin: 0;
  font-size: 15px;
}

.small {
  font-size: 12px;
}
</style>
