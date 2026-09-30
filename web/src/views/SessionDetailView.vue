<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/endpoints'
import PageHeader from '@/components/PageHeader.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useAsync } from '@/composables/useAsync'
import { usePolling } from '@/composables/usePolling'
import type { TurnDetail } from '@/api/types'
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatInt,
  formatPercent,
  formatTokens,
  truncate,
} from '@/utils/format'

const route = useRoute()
const sessionId = computed(() => String(route.params.sessionId))
const state = useAsync(() => api.sessionDetail(sessionId.value))
usePolling(state.reload)

const expanded = ref<string[]>([])
const details = ref<Record<string, TurnDetail>>({})

async function onExpand(row: { turn_id: string }, rows: { turn_id: string }[]): Promise<void> {
  expanded.value = rows.map((item) => item.turn_id)
  for (const turnId of expanded.value) {
    if (details.value[turnId]) continue
    try {
      details.value[turnId] = await api.turnDetail(sessionId.value, turnId)
    } catch {
      // 单轮明细拉取失败不影响时间线，展开时会再次尝试
    }
  }
  void row
}

const session = computed(() => state.data.value?.session)
const turns = computed(() => state.data.value?.turns ?? [])
const breakdown = computed(() => state.data.value?.cost_breakdown)

const breakdownRows = computed(() => {
  const value = breakdown.value
  if (!value) return []
  const items = [
    { label: '未命中缓存的输入', value: value.uncached_input },
    { label: '命中缓存的输入', value: value.cached_input },
    { label: '输出', value: value.output },
  ]
  return items.map((item) => ({
    ...item,
    ratio: value.total > 0 ? item.value / value.total : 0,
  }))
})
</script>

<template>
  <PageHeader :title="`会话 ${sessionId.slice(0, 8)}…`" :description="session?.cwd ?? ''">
    <el-button size="small" text @click="$router.push('/sessions')">返回列表</el-button>
  </PageHeader>

  <StateBlock
    :loading="state.loading.value && !session"
    :error="state.error.value"
    :empty="!session"
    empty-text="找不到这个会话"
    @retry="state.reload"
  >
    <div class="cards">
      <div class="al-card mini">
        <span class="al-dim">花费</span>
        <strong class="al-num">{{ formatCost(session?.cost ?? 0, session?.currency) }}</strong>
      </div>
      <div class="al-card mini">
        <span class="al-dim">Token</span>
        <strong class="al-num">{{ formatTokens(session?.total_tokens ?? 0) }}</strong>
      </div>
      <div class="al-card mini">
        <span class="al-dim">缓存命中率</span>
        <strong class="al-num">{{ formatPercent(session?.cache_hit_rate ?? 0) }}</strong>
      </div>
      <div class="al-card mini">
        <span class="al-dim">轮次 / 工具</span>
        <strong class="al-num">
          {{ formatInt(session?.turn_count ?? 0) }} / {{ formatInt(session?.tool_call_count ?? 0) }}
        </strong>
      </div>
    </div>

    <div class="grid">
      <section class="al-card table-card">
        <h3>轮次时间线</h3>
        <el-table
          :data="turns"
          size="small"
          row-key="turn_id"
          style="width: 100%"
          @expand-change="onExpand"
        >
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="detail">
                <div class="block">
                  <h4>API 调用</h4>
                  <el-table :data="details[row.turn_id]?.api_calls ?? []" size="small">
                    <el-table-column label="#" prop="ordinal" width="70" align="right" />
                    <el-table-column label="模型" prop="model" min-width="140" />
                    <el-table-column label="输入" width="100" align="right">
                      <template #default="{ row: call }">
                        {{ formatInt(call.input_tokens) }}
                      </template>
                    </el-table-column>
                    <el-table-column label="命中" width="100" align="right">
                      <template #default="{ row: call }">
                        {{ formatPercent(call.cache_hit_rate) }}
                      </template>
                    </el-table-column>
                    <el-table-column label="输出" width="90" align="right">
                      <template #default="{ row: call }">
                        {{ formatInt(call.output_tokens) }}
                      </template>
                    </el-table-column>
                    <el-table-column label="花费" width="100" align="right">
                      <template #default="{ row: call }">
                        <span class="al-num">{{ formatCost(call.cost, session?.currency) }}</span>
                      </template>
                    </el-table-column>
                  </el-table>
                </div>

                <div class="block">
                  <h4>工具调用</h4>
                  <el-table :data="details[row.turn_id]?.tool_calls ?? []" size="small">
                    <el-table-column label="工具" prop="name" min-width="140" />
                    <el-table-column label="耗时" width="100" align="right">
                      <template #default="{ row: call }">
                        {{ formatDuration(call.duration_ms) }}
                      </template>
                    </el-table-column>
                    <el-table-column label="退出码" width="90" align="right">
                      <template #default="{ row: call }">{{ call.exit_code ?? '—' }}</template>
                    </el-table-column>
                    <el-table-column label="结果摘要" min-width="260">
                      <template #default="{ row: call }">
                        <span class="al-dim">{{ truncate(call.result_summary, 120) }}</span>
                      </template>
                    </el-table-column>
                  </el-table>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="#" prop="index" width="60" align="right" />
          <el-table-column label="轮次" min-width="180">
            <template #default="{ row }">
              <div>{{ row.turn_id.slice(0, 12) }}…</div>
              <div class="al-dim small">
                {{ row.model ?? '—' }} · {{ row.effort ?? '—' }}
                <el-tag v-if="row.aborted_reason" size="small" type="warning">中断</el-tag>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="开始" width="150">
            <template #default="{ row }">
              <span class="al-dim">{{ formatDateTime(row.started_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="100" align="right">
            <template #default="{ row }">{{ formatDuration(row.duration_ms) }}</template>
          </el-table-column>
          <el-table-column label="调用" prop="api_call_count" width="70" align="right" />
          <el-table-column label="工具" width="90" align="right">
            <template #default="{ row }">
              {{ row.tool_call_count }}
              <span v-if="row.tool_failure_count" class="fail">({{ row.tool_failure_count }} 失败)</span>
            </template>
          </el-table-column>
          <el-table-column label="Token" width="110" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ formatTokens(row.input_tokens + row.output_tokens) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="膨胀" width="90" align="right">
            <template #default="{ row }">
              <span class="al-num">
                {{ row.context_growth ? `${row.context_growth.toFixed(2)}×` : '—' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="花费" width="110" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ formatCost(row.cost, breakdown?.currency) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <aside class="al-card table-card side">
        <h3>成本构成</h3>
        <div v-for="item in breakdownRows" :key="item.label" class="bar-row">
          <div class="bar-head">
            <span>{{ item.label }}</span>
            <span class="al-num">{{ formatCost(item.value, breakdown?.currency) }}</span>
          </div>
          <div class="bar">
            <div class="fill" :style="{ width: `${Math.max(2, item.ratio * 100)}%` }" />
          </div>
          <div class="al-dim small">{{ formatPercent(item.ratio) }}</div>
        </div>
        <div class="total">
          <span class="al-dim">合计</span>
          <strong class="al-num">{{ formatCost(breakdown?.total ?? 0, breakdown?.currency) }}</strong>
        </div>
      </aside>
    </div>
  </StateBlock>
</template>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--al-space-3);
  margin-bottom: var(--al-space-3);
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

.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: var(--al-space-3);
  align-items: start;
}

.table-card {
  padding: var(--al-space-3);
}

h3 {
  margin: 0 0 var(--al-space-3);
  color: var(--al-text-2);
  font-size: 13px;
  font-weight: 500;
}

h4 {
  margin: 0 0 var(--al-space-2);
  color: var(--al-text-3);
  font-size: 12px;
  font-weight: 500;
}

.detail {
  display: grid;
  gap: var(--al-space-4);
  padding: var(--al-space-2) var(--al-space-4) var(--al-space-4);
}

.small {
  font-size: 12px;
}

.fail {
  color: var(--al-danger);
}

.side {
  position: sticky;
  top: 72px;
}

.bar-row {
  margin-bottom: var(--al-space-3);
}

.bar-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
}

.bar {
  height: 6px;
  margin: var(--al-space-1) 0;
  border-radius: var(--al-radius-pill);
  background: var(--al-surface-3);
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: var(--al-radius-pill);
  background: linear-gradient(90deg, var(--al-accent), var(--al-accent-2));
  transition: width var(--al-dur-slow) var(--al-ease);
}

.total {
  display: flex;
  justify-content: space-between;
  padding-top: var(--al-space-3);
  border-top: 1px solid var(--al-border);
}

@media (max-width: 1200px) {
  .grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .side {
    position: static;
  }
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
