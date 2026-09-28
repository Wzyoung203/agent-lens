<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'

import { api } from '@/api/endpoints'
import type { PriceEntry } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import StateBlock from '@/components/StateBlock.vue'
import { useAsync } from '@/composables/useAsync'
import { formatDateTime, formatInt } from '@/utils/format'

const pricingState = useAsync(() => api.pricing())
const mappingState = useAsync(() => api.projectMappings())
const statusState = useAsync(() => api.status())

const priceDialog = ref(false)
const mappingDialog = ref(false)
const saving = ref(false)

function todayIso(): string {
  const now = new Date()
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())).toISOString()
}

const priceForm = reactive<PriceEntry>({
  provider: 'deepseek',
  model: 'deepseek-v4-pro',
  effective_from: todayIso(),
  time_window: 'any',
  input_price_per_mtok: 0,
  cached_input_price_per_mtok: 0,
  output_price_per_mtok: 0,
  reasoning_output_price_per_mtok: null,
  currency: 'USD',
})

const mappingForm = reactive({ path_prefix: '', project: '' })

const counts = computed(() => statusState.data.value?.counts ?? {})
const queue = computed(() => statusState.data.value?.report_queue ?? {})

function openPriceDialog(entry?: PriceEntry): void {
  Object.assign(
    priceForm,
    entry ?? {
      provider: 'deepseek',
      model: 'deepseek-v4-pro',
      effective_from: todayIso(),
      time_window: 'any',
      input_price_per_mtok: 0,
      cached_input_price_per_mtok: 0,
      output_price_per_mtok: 0,
      reasoning_output_price_per_mtok: null,
      currency: 'USD',
    },
  )
  priceDialog.value = true
}

async function savePrice(): Promise<void> {
  saving.value = true
  try {
    await api.putPricing({ ...priceForm })
    ElMessage.success('价目表已更新，历史趋势会按新价重算')
    priceDialog.value = false
    await pricingState.reload()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removePrice(entry: PriceEntry): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除 ${entry.provider} / ${entry.model} / ${entry.time_window} 这条价目？`,
      '确认删除',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await api.deletePricing({
    provider: entry.provider,
    model: entry.model,
    effective_from: entry.effective_from,
    time_window: entry.time_window,
  })
  ElMessage.success('已删除')
  await pricingState.reload()
}

async function saveMapping(): Promise<void> {
  if (!mappingForm.path_prefix || !mappingForm.project) {
    ElMessage.warning('路径前缀与项目名都要填')
    return
  }
  saving.value = true
  try {
    await api.putProjectMapping({ ...mappingForm })
    ElMessage.success('映射已保存，并重算了会话归属')
    mappingDialog.value = false
    mappingForm.path_prefix = ''
    mappingForm.project = ''
    await Promise.all([mappingState.reload(), statusState.reload()])
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeMapping(prefix: string): Promise<void> {
  const result = await api.deleteProjectMapping(prefix)
  ElMessage.success(`已删除映射，${result.sessions_reassigned} 个会话被重新归属`)
  await Promise.all([mappingState.reload(), statusState.reload()])
}

async function refreshProjects(): Promise<void> {
  const result = await api.refreshProjects()
  ElMessage.success(`重算完成，${result.sessions_reassigned} 个会话归属发生变化`)
  await statusState.reload()
}
</script>

<template>
  <PageHeader title="设置" description="价目表、项目映射与运行状态。金额永远在查询时按价目表计算。" />

  <div class="grid">
    <section class="al-card panel">
      <header>
        <h3>价目表</h3>
        <span class="al-dim small">deepseek 按高峰 / 空闲两档计价；空闲价是高峰价的一半</span>
        <div class="spacer" />
        <el-button size="small" :icon="Plus" @click="openPriceDialog()">新增</el-button>
      </header>

      <StateBlock
        :loading="pricingState.loading.value && !pricingState.data.value"
        :error="pricingState.error.value"
        :empty="!pricingState.data.value?.length"
        empty-text="还没有价目表，先加一条"
        @retry="pricingState.reload"
      >
        <el-table :data="pricingState.data.value ?? []" size="small" style="width: 100%">
          <el-table-column label="provider" prop="provider" width="110" />
          <el-table-column label="model" prop="model" min-width="170" />
          <el-table-column label="时段" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.time_window === 'peak' ? 'warning' : 'info'">
                {{ row.time_window }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="输入/百万" width="120" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ row.input_price_per_mtok }}</span>
            </template>
          </el-table-column>
          <el-table-column label="命中/百万" width="120" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ row.cached_input_price_per_mtok }}</span>
            </template>
          </el-table-column>
          <el-table-column label="输出/百万" width="120" align="right">
            <template #default="{ row }">
              <span class="al-num">{{ row.output_price_per_mtok }}</span>
            </template>
          </el-table-column>
          <el-table-column label="生效时间" width="150">
            <template #default="{ row }">
              <span class="al-dim">{{ formatDateTime(row.effective_from) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" align="right">
            <template #default="{ row }">
              <el-button size="small" text @click="openPriceDialog(row)">编辑</el-button>
              <el-button size="small" text type="danger" @click="removePrice(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </StateBlock>
    </section>

    <section class="al-card panel">
      <header>
        <h3>项目映射</h3>
        <span class="al-dim small">路径前缀匹配，优先级最高</span>
        <div class="spacer" />
        <el-button size="small" :icon="Refresh" @click="refreshProjects">重算归属</el-button>
        <el-button size="small" :icon="Plus" @click="mappingDialog = true">新增</el-button>
      </header>

      <StateBlock
        :loading="mappingState.loading.value && !mappingState.data.value"
        :error="mappingState.error.value"
        :empty="!mappingState.data.value?.length"
        empty-text="还没有映射，全部会话会归到「未归类」"
        @retry="mappingState.reload"
      >
        <el-table :data="mappingState.data.value ?? []" size="small" style="width: 100%">
          <el-table-column label="项目" prop="project" min-width="160" />
          <el-table-column label="会话" prop="session_count" width="90" align="right" />
          <el-table-column label="路径前缀" min-width="300">
            <template #default="{ row }">
              <div v-if="row.prefixes.length === 0" class="al-dim">（无，自动推断/未归类）</div>
              <div v-for="prefix in row.prefixes" :key="prefix" class="prefix">
                <code>{{ prefix }}</code>
                <el-button size="small" text type="danger" @click="removeMapping(prefix)">
                  删除
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </StateBlock>
    </section>

    <section class="al-card panel">
      <header>
        <h3>运行状态</h3>
        <span class="al-dim small">数据在本地 SQLite；Langfuse 只是可重建的视图</span>
      </header>
      <StateBlock
        :loading="statusState.loading.value && !statusState.data.value"
        :error="statusState.error.value"
        @retry="statusState.reload"
      >
        <dl class="status">
          <div><dt>数据库</dt><dd>{{ statusState.data.value?.db_path }}</dd></div>
          <div><dt>会话目录</dt><dd>{{ statusState.data.value?.sessions_dir }}</dd></div>
          <div><dt>schema 版本</dt><dd>v{{ statusState.data.value?.schema_version }}</dd></div>
          <div><dt>解析错误</dt><dd>{{ formatInt(statusState.data.value?.parse_errors ?? 0) }}</dd></div>
          <div>
            <dt>Langfuse</dt>
            <dd>
              <el-tag size="small" :type="statusState.data.value?.langfuse_enabled ? 'success' : 'info'">
                {{ statusState.data.value?.langfuse_enabled ? '已启用' : '未启用' }}
              </el-tag>
            </dd>
          </div>
          <div><dt>上报队列</dt><dd>待发 {{ queue.pending ?? 0 }} · 已发 {{ queue.sent ?? 0 }} · 失败 {{ queue.failed ?? 0 }}</dd></div>
        </dl>

        <div class="counts">
          <div v-for="(value, table) in counts" :key="table" class="count">
            <span class="al-dim">{{ table }}</span>
            <span class="al-num">{{ formatInt(value) }}</span>
          </div>
        </div>
      </StateBlock>
    </section>
  </div>

  <el-dialog v-model="priceDialog" title="价目表记录" width="520px">
    <el-form label-width="140px" size="small">
      <el-form-item label="provider"><el-input v-model="priceForm.provider" /></el-form-item>
      <el-form-item label="model"><el-input v-model="priceForm.model" /></el-form-item>
      <el-form-item label="时段">
        <el-select v-model="priceForm.time_window">
          <el-option value="any" label="any（通用）" />
          <el-option value="peak" label="peak（高峰）" />
          <el-option value="idle" label="idle（空闲）" />
        </el-select>
      </el-form-item>
      <el-form-item label="生效时间">
        <el-input v-model="priceForm.effective_from" placeholder="ISO8601，如 2026-01-01T00:00:00+00:00" />
      </el-form-item>
      <el-form-item label="输入价 /Mtok">
        <el-input-number v-model="priceForm.input_price_per_mtok" :step="0.01" :precision="4" />
      </el-form-item>
      <el-form-item label="命中价 /Mtok">
        <el-input-number
          v-model="priceForm.cached_input_price_per_mtok"
          :step="0.001"
          :precision="4"
        />
      </el-form-item>
      <el-form-item label="输出价 /Mtok">
        <el-input-number v-model="priceForm.output_price_per_mtok" :step="0.01" :precision="4" />
      </el-form-item>
      <el-form-item label="币种"><el-input v-model="priceForm.currency" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="priceDialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="savePrice">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="mappingDialog" title="新增项目映射" width="460px">
    <el-form label-width="100px" size="small">
      <el-form-item label="路径前缀">
        <el-input v-model="mappingForm.path_prefix" placeholder="/Users/me/work/my-project" />
      </el-form-item>
      <el-form-item label="项目名">
        <el-input v-model="mappingForm.project" placeholder="my-project" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="mappingDialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="saveMapping">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.grid {
  display: grid;
  gap: var(--al-space-3);
}

.panel {
  padding: var(--al-space-4);
}

header {
  display: flex;
  align-items: center;
  gap: var(--al-space-2);
  margin-bottom: var(--al-space-3);
}

h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.spacer {
  flex: 1;
}

.small {
  font-size: 12px;
}

.prefix {
  display: flex;
  align-items: center;
  gap: var(--al-space-2);
}

code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--al-surface-3);
  font-family: var(--al-font-mono);
  font-size: 12px;
}

.status {
  display: grid;
  gap: var(--al-space-2);
  margin: 0 0 var(--al-space-4);
}

.status div {
  display: grid;
  grid-template-columns: 110px minmax(0, 1fr);
  gap: var(--al-space-3);
}

dt {
  color: var(--al-text-2);
}

dd {
  margin: 0;
  word-break: break-all;
}

.counts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--al-space-2);
}

.count {
  display: flex;
  justify-content: space-between;
  padding: var(--al-space-2) var(--al-space-3);
  border: 1px solid var(--al-border);
  border-radius: var(--al-radius-sm);
  background: var(--al-surface-2);
  font-size: 12px;
}
</style>
