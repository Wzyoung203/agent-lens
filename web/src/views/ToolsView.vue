<script setup lang="ts">
import { computed } from 'vue'
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
import {
  formatCost,
  formatDuration,
  formatInt,
  formatPercent,
  truncate,
} from '@/utils/format'
import { CHART_COLORS, axisTheme } from '@/utils/palette'

const { days } = useRange()
const { theme } = useTheme()
const state = useAsync(() => api.tools(days.value), [days])
usePolling(state.reload)

const stats = computed(() => state.data.value?.stats ?? [])
const failures = computed(() => state.data.value?.failures ?? [])
const byProject = computed(() => state.data.value?.by_project ?? [])
const failureCost = computed(() => state.data.value?.failure_cost ?? null)

const callOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  const ranked = [...stats.value].sort((a, b) => a.call_count - b.call_count).slice(-10)
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 4, containLabel: true },
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
      data: ranked.map((item) => item.name),
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label },
    },
    series: [
      {
        type: 'bar',
        data: ranked.map((item) => ({
          value: item.call_count,
          itemStyle: {
            color: item.failure_count > 0 ? CHART_COLORS[4] : CHART_COLORS[0],
            borderRadius: [0, 4, 4, 0],
          },
        })),
        barMaxWidth: 18,
      },
    ],
  }
})

const durationOption = computed<EChartsCoreOption>(() => {
  const palette = axisTheme(theme.value)
  const withTiming = stats.value.filter((item) => item.p95_duration_ms !== null)
  return {
    grid: { left: 8, right: 8, top: 24, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: palette.tooltipBg,
      borderColor: palette.tooltipBorder,
      textStyle: { color: palette.tooltipText, fontSize: 12 },
      valueFormatter: (value: number) => formatDuration(value),
    },
    legend: { top: 0, textStyle: { color: palette.label }, itemWidth: 10, itemHeight: 10 },
    xAxis: {
      type: 'category',
      data: withTiming.map((item) => item.name),
      axisLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, rotate: 20 },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      splitLine: { lineStyle: { color: palette.axis } },
      axisLabel: { color: palette.label, formatter: (value: number) => `${value / 1000}s` },
    },
    series: [
      {
        name: '平均',
        type: 'bar',
        data: withTiming.map((item) => item.avg_duration_ms),
        itemStyle: { color: CHART_COLORS[0], borderRadius: [4, 4, 0, 0] },
      },
      {
        name: 'P95',
        type: 'bar',
        data: withTiming.map((item) => item.p95_duration_ms),
        itemStyle: { color: CHART_COLORS[3], borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})
</script>

<template>
  <PageHeader title="工具调用" :description="`最近 ${days} 天，失败折算成本是本项目的独有视角`" />

  <StateBlock
    :loading="state.loading.value && stats.length === 0"
    :error="state.error.value"
    :empty="stats.length === 0"
    empty-text="这段时间没有工具调用"
    @retry="state.reload"
  >
    <div class="charts">
      <ChartCard title="调用次数" subtitle="Top 10，红色表示有失败">
        <EChart :option="callOption" label="工具调用次数条形图" height="300px" />
      </ChartCard>
      <ChartCard title="耗时：平均 vs P95" subtitle="来自工具输出里的 Wall time">
        <EChart :option="durationOption" label="工具耗时对比柱状图" height="300px" />
      </ChartCard>
    </div>

    <div class="grid">
      <ChartCard title="工具明细" :subtitle="`${stats.length} 种`">
        <el-table :data="stats" size="small" style="width: 100%">
          <el-table-column label="工具" prop="name" min-width="150" />
          <el-table-column label="调用" prop="call_count" width="80" align="right" />
          <el-table-column label="失败" width="90" align="right">
            <template #default="{ row }">
              <span :class="{ fail: row.failure_count > 0 }" class="al-num">
                {{ row.failure_count }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="失败率" width="100" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ formatPercent(row.failure_rate) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="平均耗时" width="110" align="right">
            <template #default="{ row }">{{ formatDuration(row.avg_duration_ms) }}</template>
          </el-table-column>
          <el-table-column label="P95" width="110" align="right">
            <template #default="{ row }">{{ formatDuration(row.p95_duration_ms) }}</template>
          </el-table-column>
          <el-table-column label="输出字符" width="110" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ formatInt(row.total_output_chars) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </ChartCard>

      <aside class="al-card side">
        <h3>失败折算成本</h3>
        <p class="al-dim small">
          定义：某轮出现工具失败后，该轮后续 api_call 的成本视为重试成本。
        </p>
        <strong class="al-num big">{{ formatCost(failureCost?.total ?? 0, failureCost?.currency) }}</strong>
        <div class="breakdown">
          <div><span class="al-dim">未命中输入</span><span class="al-num">{{ formatCost(failureCost?.uncached_input ?? 0) }}</span></div>
          <div><span class="al-dim">命中缓存</span><span class="al-num">{{ formatCost(failureCost?.cached_input ?? 0) }}</span></div>
          <div><span class="al-dim">输出</span><span class="al-num">{{ formatCost(failureCost?.output ?? 0) }}</span></div>
        </div>

        <h3 class="mt">跨项目失败率</h3>
        <div v-for="item in byProject" :key="item.project" class="proj">
          <span>{{ item.project }}</span>
          <span class="al-num">
            {{ formatPercent(item.failure_rate) }}（{{ item.failure_count }}/{{ item.call_count }}）
          </span>
        </div>
      </aside>
    </div>

    <ChartCard title="失败清单" :subtitle="`最近 ${days} 天 ${failures.length} 条`">
      <el-table :data="failures" size="small" style="width: 100%">
        <el-table-column label="工具" prop="name" width="140" />
        <el-table-column label="项目" prop="project" width="140" />
        <el-table-column label="会话" min-width="200">
          <template #default="{ row }">
            <RouterLink class="link" :to="`/sessions/${row.session_id}`">
              {{ row.session_id.slice(0, 8) }}…
            </RouterLink>
          </template>
        </el-table-column>
        <el-table-column label="退出码" width="90" align="right">
          <template #default="{ row }">{{ row.exit_code ?? '—' }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="100" align="right">
          <template #default="{ row }">
            {{ row.wall_time_seconds === null ? '—' : `${row.wall_time_seconds}s` }}
          </template>
        </el-table-column>
        <el-table-column label="结果摘要" min-width="320">
          <template #default="{ row }">
            <span class="al-dim">{{ truncate(row.result_summary, 140) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </ChartCard>
  </StateBlock>
</template>

<style scoped>
.charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--al-space-3);
  margin-bottom: var(--al-space-3);
}

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: var(--al-space-3);
  align-items: start;
  margin-bottom: var(--al-space-3);
}

.side {
  padding: var(--al-space-4);
}

h3 {
  margin: 0 0 var(--al-space-1);
  color: var(--al-text-2);
  font-size: 13px;
  font-weight: 500;
}

.mt {
  margin-top: var(--al-space-5);
}

.small {
  font-size: 12px;
}

.big {
  display: block;
  margin: var(--al-space-2) 0 var(--al-space-3);
  font-size: 26px;
  font-weight: 600;
}

.breakdown {
  display: grid;
  gap: var(--al-space-1);
  font-size: 12px;
}

.breakdown div,
.proj {
  display: flex;
  justify-content: space-between;
  gap: var(--al-space-2);
}

.proj {
  padding: var(--al-space-1) 0;
  font-size: 12px;
}

.fail {
  color: var(--al-danger);
}

.link {
  color: var(--al-accent);
  font-family: var(--al-font-mono);
}

@media (max-width: 1200px) {
  .charts,
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
