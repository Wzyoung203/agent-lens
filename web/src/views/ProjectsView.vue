<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { EChartsCoreOption } from 'echarts'

import { api } from '@/api/endpoints'
import ChartCard from '@/components/ChartCard.vue'
import EChart from '@/components/EChart.vue'
import PageHeader from '@/components/PageHeader.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useAsync } from '@/composables/useAsync'
import { usePolling } from '@/composables/usePolling'
import { useRange } from '@/composables/useRange'
import { useTheme } from '@/composables/useTheme'
import { formatCost, formatDay, formatPercent, formatTokens } from '@/utils/format'
import { CHART_COLORS, axisTheme } from '@/utils/palette'

const { days } = useRange()
const { theme } = useTheme()
const selected = ref<string | null>(null)

const listState = useAsync(() => api.projects(days.value))
const detailState = useAsync(async () => {
  if (!selected.value) return null
  return api.projectDetail(selected.value, days.value)
})

usePolling(async () => {
  await listState.reload()
  await detailState.reload()
})

watch(
  () => listState.data.value,
  (projects) => {
    if (!projects?.length) {
      selected.value = null
      return
    }
    if (!selected.value || !projects.some((item) => item.project === selected.value)) {
      selected.value = projects[0].project
    }
  },
  { immediate: true },
)

const detail = computed(() => detailState.data.value)

const trendOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  const daily = detail.value?.daily ?? []
  return {
    grid: { left: 8, right: 8, top: 24, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
    },
    xAxis: {
      type: 'category',
      data: daily.map((point) => formatDay(point.day)),
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, formatter: (value: number) => formatTokens(value) },
    },
    series: [
      {
        name: 'token',
        type: 'line',
        smooth: true,
        areaStyle: { color: 'rgba(34,211,238,0.14)' },
        lineStyle: { color: CHART_COLORS[0], width: 2 },
        itemStyle: { color: CHART_COLORS[0] },
        data: daily.map((point) => point.input_tokens + point.output_tokens),
      },
    ],
  }
})

const breakdownOption = computed<EChartsCoreOption>(() => {
  const breakdown = detail.value?.cost_breakdown
  const palette = axisTheme(theme.value)
  const data = [
    { name: '未命中输入', value: breakdown?.uncached_input ?? 0 },
    { name: '命中缓存', value: breakdown?.cached_input ?? 0 },
    { name: '输出', value: breakdown?.output ?? 0 },
  ]
  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      valueFormatter: (value: number) => formatCost(value),
    },
    legend: {
      bottom: 0,
      textStyle: { color: palette.label },
      itemWidth: 10,
      itemHeight: 10,
    },
    series: [
      {
        type: 'pie',
        radius: ['52%', '74%'],
        center: ['50%', '44%'],
        label: { show: false },
        itemStyle: { borderWidth: 0 },
        data: data.map((item, index) => ({
          ...item,
          itemStyle: { color: [CHART_COLORS[0], CHART_COLORS[1], CHART_COLORS[2]][index] },
        })),
      },
    ],
  }
})
</script>

<template>
  <PageHeader title="项目" :description="`最近 ${days} 天，按路径前缀映射归属`" />

  <div class="split">
    <aside class="al-card list">
      <StateBlock
        :loading="listState.loading.value && !listState.data.value"
        :error="listState.error.value"
        :empty="!listState.data.value?.length"
        empty-text="还没有项目数据"
        @retry="listState.reload"
      >
        <button
          v-for="project in listState.data.value"
          :key="project.project"
          class="row"
          :class="{ active: project.project === selected }"
          type="button"
          @click="selected = project.project"
        >
          <span class="name">{{ project.project }}</span>
          <span class="cost al-num">{{ formatCost(project.cost) }}</span>
          <span class="meta al-dim">
            {{ project.session_count }} 会话 · {{ formatTokens(project.total_tokens) }} token
          </span>
        </button>
      </StateBlock>
    </aside>

    <section class="detail">
      <StateBlock
        :loading="detailState.loading.value && !detail"
        :error="detailState.error.value"
        :empty="!detail"
        empty-text="左侧选一个项目"
        @retry="detailState.reload"
      >
        <div class="cards">
          <div class="al-card mini">
            <span class="al-dim">花费</span>
            <strong class="al-num">{{ formatCost(detail?.project.cost ?? 0) }}</strong>
          </div>
          <div class="al-card mini">
            <span class="al-dim">Token</span>
            <strong class="al-num">{{ formatTokens(detail?.project.total_tokens ?? 0) }}</strong>
          </div>
          <div class="al-card mini">
            <span class="al-dim">缓存命中率</span>
            <strong class="al-num">{{ formatPercent(detail?.project.cache_hit_rate ?? 0) }}</strong>
          </div>
          <div class="al-card mini">
            <span class="al-dim">工具失败率</span>
            <strong class="al-num">
              {{ formatPercent(detail?.project.tool_failure_rate ?? 0) }}
            </strong>
          </div>
        </div>

        <div class="charts">
          <ChartCard title="该项目的 token 趋势">
            <EChart :option="trendOption" label="项目 token 趋势折线图" height="260px" />
          </ChartCard>
          <ChartCard title="成本构成">
            <EChart :option="breakdownOption" label="成本构成环形图" height="260px" />
          </ChartCard>
        </div>

        <ChartCard title="会话列表" :subtitle="`${detail?.sessions.length ?? 0} 个`">
          <el-table :data="detail?.sessions ?? []" size="small" style="width: 100%">
            <el-table-column label="会话" min-width="220">
              <template #default="{ row }">
                <RouterLink class="link" :to="`/sessions/${row.session_id}`">
                  {{ row.session_id.slice(0, 8) }}…
                </RouterLink>
                <div class="al-dim small">{{ row.cwd ?? '—' }}</div>
              </template>
            </el-table-column>
            <el-table-column label="轮次" prop="turn_count" width="80" align="right" />
            <el-table-column label="调用" prop="api_call_count" width="80" align="right" />
            <el-table-column label="Token" width="110" align="right">
              <template #default="{ row }">{{ formatTokens(row.total_tokens) }}</template>
            </el-table-column>
            <el-table-column label="花费" width="110" align="right">
              <template #default="{ row }">
                <span class="al-num">{{ formatCost(row.cost) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </ChartCard>
      </StateBlock>
    </section>
  </div>
</template>

<style scoped>
.split {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: var(--al-space-3);
}

.list {
  padding: var(--al-space-2);
  align-self: start;
  position: sticky;
  top: 72px;
}

.row {
  display: grid;
  width: 100%;
  gap: 2px;
  padding: var(--al-space-3);
  border: 0;
  border-radius: var(--al-radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition: background var(--al-dur-fast) var(--al-ease);
}

.row:hover {
  background: var(--al-surface-2);
}

.row.active {
  background: var(--al-accent-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--al-accent) 28%, transparent);
}

.name {
  font-weight: 500;
}

.cost {
  font-size: 13px;
}

.meta,
.small {
  font-size: 12px;
}

.detail {
  display: grid;
  gap: var(--al-space-3);
  min-width: 0;
}

.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--al-space-3);
}

.mini {
  display: grid;
  gap: 4px;
  padding: var(--al-space-3) var(--al-space-4);
}

.mini strong {
  font-size: 20px;
  font-weight: 600;
}

.charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--al-space-3);
}

.link {
  color: var(--al-accent);
}

@media (max-width: 1200px) {
  .split {
    grid-template-columns: minmax(0, 1fr);
  }
  .list {
    position: static;
  }
  .charts {
    grid-template-columns: minmax(0, 1fr);
  }
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
