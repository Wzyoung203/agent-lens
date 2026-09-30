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
import { formatCost, formatInt, formatPercent, formatTokens } from '@/utils/format'
import { CHART_COLORS, axisTheme } from '@/utils/palette'

const { days } = useRange()
const { theme } = useTheme()
const state = useAsync(() => api.context(days.value), [days])
const skillsState = useAsync(() => api.skills(days.value), [days])
const modelsState = useAsync(() => api.models(days.value), [days])
usePolling(state.reload)

const data = computed(() => state.data.value ?? null)
const skills = computed(() => skillsState.data.value?.skills ?? [])
const modelRows = computed(() => modelsState.data.value?.rows ?? [])
const modelCurrency = computed(() => modelsState.data.value?.currency ?? 'CNY')

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

function modelKey(row: { model: string; effort: string }): string {
  return `${row.model} / ${row.effort}`
}

const costOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  return {
    grid: { left: 8, right: 24, top: 16, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axis: 'y',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
    },
    xAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    yAxis: {
      type: 'category',
      data: modelRows.value.map(modelKey),
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    color: CHART_COLORS,
    series: [
      {
        type: 'bar',
        data: modelRows.value.map((row) => row.cost),
        itemStyle: { borderRadius: [0, 6, 6, 0] },
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

    <div class="al-card panel">
      <header>
        <h3>skill 命中</h3>
        <span class="al-dim small">
          共 {{ skillsState.data.value?.total_loads ?? 0 }} 次加载；来自工具调用参数里的 SKILL.md 路径
        </span>
      </header>
      <el-table :data="skills" size="small" empty-text="这段时间没有识别到 skill 加载">
        <el-table-column label="skill">
          <template #default="{ row }">{{ row.skill_name }}</template>
        </el-table-column>
        <el-table-column label="加载次数">
          <template #default="{ row }">{{ formatInt(row.loads) }}</template>
        </el-table-column>
        <el-table-column label="覆盖会话">
          <template #default="{ row }">{{ formatInt(row.session_count) }}</template>
        </el-table-column>
        <el-table-column label="工具">
          <template #default="{ row }">{{ row.tool_names.join('、') }}</template>
        </el-table-column>
      </el-table>
    </div>

    <ChartCard title="模型与推理强度成本对比" subtitle="成本在查询时按价目表现算，改价后历史一致重算">
      <EChart :option="costOption" label="按模型与推理强度的成本" />
    </ChartCard>

    <div class="al-card panel">
      <header>
        <h3>按模型与强度的明细</h3>
        <span v-if="modelRows.some((row) => row.unpriced_calls > 0)" class="al-dim small">
          有调用查不到价目表，金额不含它们
        </span>
      </header>
      <el-table :data="modelRows" size="small" empty-text="这段时间没有调用数据">
        <el-table-column label="模型 / 强度">
          <template #default="{ row }">{{ modelKey(row) }}</template>
        </el-table-column>
        <el-table-column label="调用次数">
          <template #default="{ row }">{{ formatInt(row.calls) }}</template>
        </el-table-column>
        <el-table-column label="轮次数">
          <template #default="{ row }">{{ formatInt(row.turn_count) }}</template>
        </el-table-column>
        <el-table-column label="平均 input/次">
          <template #default="{ row }">{{ formatTokens(row.avg_input_tokens) }}</template>
        </el-table-column>
        <el-table-column label="cache 命中率">
          <template #default="{ row }">{{ formatPercent(row.cache_hit_rate) }}</template>
        </el-table-column>
        <el-table-column label="总成本">
          <template #default="{ row }">{{ formatCost(row.cost, modelCurrency) }}</template>
        </el-table-column>
        <el-table-column label="平均成本/次">
          <template #default="{ row }">{{ formatCost(row.avg_cost_per_call, modelCurrency) }}</template>
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
