<script setup lang="ts">
import { computed, ref } from 'vue'

import { api } from '@/api/endpoints'
import PageHeader from '@/components/PageHeader.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useAsync } from '@/composables/useAsync'
import { usePolling } from '@/composables/usePolling'
import { useRange } from '@/composables/useRange'
import { formatCost, formatDateTime, formatPercent, formatTokens } from '@/utils/format'

const { days } = useRange()
const project = ref('')
const search = ref('')
const page = ref(0)
const pageSize = 25

const state = useAsync(
  () =>
    api.sessions(days.value, {
      project: project.value || undefined,
      search: search.value || undefined,
      limit: pageSize,
      offset: page.value * pageSize,
    }),
  [days],
)
usePolling(state.reload)

const projectsState = useAsync(() => api.projects(days.value))
const items = computed(() => state.data.value?.items ?? [])
const total = computed(() => state.data.value?.total ?? 0)
</script>

<template>
  <PageHeader title="会话" :description="`最近 ${days} 天，字段与后端契约一致`">
    <el-input
      v-model="search"
      placeholder="搜索会话 ID / 路径 / 项目"
      clearable
      size="small"
      style="width: 240px"
      @keyup.enter="((page = 0), state.reload())"
      @clear="((page = 0), state.reload())"
    />
    <el-select
      v-model="project"
      placeholder="全部项目"
      clearable
      size="small"
      style="width: 180px"
      @change="((page = 0), state.reload())"
    >
      <el-option
        v-for="item in projectsState.data.value ?? []"
        :key="item.project"
        :label="item.project"
        :value="item.project"
      />
    </el-select>
  </PageHeader>

  <StateBlock
    :loading="state.loading.value && items.length === 0"
    :error="state.error.value"
    :empty="items.length === 0"
    empty-text="没有匹配的会话"
    @retry="state.reload"
  >
    <div class="al-card table-card">
      <el-table :data="items" size="small" style="width: 100%">
        <el-table-column label="会话" min-width="260">
          <template #default="{ row }">
            <RouterLink class="link" :to="`/sessions/${row.session_id}`">
              {{ row.session_id }}
            </RouterLink>
            <div class="al-dim small">{{ row.cwd ?? '—' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="项目" prop="project" width="140" />
        <el-table-column label="CLI" prop="cli_version" width="100" />
        <el-table-column label="轮次" prop="turn_count" width="80" align="right" />
        <el-table-column label="工具" prop="tool_call_count" width="80" align="right" />
        <el-table-column label="Token" width="110" align="right">
          <template #default="{ row }">
            <span class="al-num">{{ formatTokens(row.total_tokens) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="缓存" width="90" align="right">
          <template #default="{ row }">
            <span class="al-num">{{ formatPercent(row.cache_hit_rate) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="花费" width="110" align="right">
          <template #default="{ row }">
            <span class="al-num">{{ formatCost(row.cost) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="最后调用" width="150">
          <template #default="{ row }">
            <span class="al-dim">{{ formatDateTime(row.last_api_at) }}</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <span class="al-dim">共 {{ total }} 个会话</span>
        <el-button
          size="small"
          text
          :disabled="page === 0"
          @click="((page -= 1), state.reload())"
        >
          上一页
        </el-button>
        <span class="al-dim">第 {{ page + 1 }} 页</span>
        <el-button
          size="small"
          text
          :disabled="(page + 1) * pageSize >= total"
          @click="((page += 1), state.reload())"
        >
          下一页
        </el-button>
      </div>
    </div>
  </StateBlock>
</template>

<style scoped>
.table-card {
  padding: var(--al-space-2) var(--al-space-3) var(--al-space-3);
}

.link {
  color: var(--al-accent);
  font-family: var(--al-font-mono);
  font-size: 13px;
}

.small {
  font-size: 12px;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--al-space-3);
  padding-top: var(--al-space-3);
  font-size: 12px;
}
</style>
