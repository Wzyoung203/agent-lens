<script setup lang="ts">
import { ElButton, ElIcon } from 'element-plus'
import { WarnTriangleFilled } from '@element-plus/icons-vue'

withDefaults(
  defineProps<{
    loading?: boolean
    error?: string | null
    empty?: boolean
    emptyText?: string
    rows?: number
  }>(),
  { loading: false, error: null, empty: false, emptyText: '还没有数据', rows: 3 },
)

defineEmits<{ retry: [] }>()
</script>

<template>
  <div v-if="error" class="block error" role="alert">
    <el-icon><WarnTriangleFilled /></el-icon>
    <span>{{ error }}</span>
    <el-button size="small" text @click="$emit('retry')">重试</el-button>
  </div>

  <div v-else-if="loading" class="skeleton" aria-busy="true" aria-label="加载中">
    <div v-for="row in rows" :key="row" class="shimmer" :style="{ width: `${92 - row * 12}%` }" />
  </div>

  <div v-else-if="empty" class="block empty">
    <span>{{ emptyText }}</span>
    <span class="al-dim">换个时间范围，或者先跑一次 <code>uv run agent-lens collect</code></span>
  </div>

  <slot v-else />
</template>

<style scoped>
.block {
  display: flex;
  align-items: center;
  gap: var(--al-space-3);
  padding: var(--al-space-5);
  border: 1px dashed var(--al-border-strong);
  border-radius: var(--al-radius);
  color: var(--al-text-2);
}

.error {
  border-style: solid;
  border-color: color-mix(in srgb, var(--al-danger) 40%, transparent);
  color: var(--al-danger);
}

.empty {
  flex-direction: column;
  align-items: flex-start;
  gap: var(--al-space-1);
}

.skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--al-space-3);
  padding: var(--al-space-4) 0;
}

.shimmer {
  height: 16px;
  border-radius: var(--al-radius-pill);
  background: linear-gradient(
    90deg,
    var(--al-surface-2) 0%,
    var(--al-surface-3) 50%,
    var(--al-surface-2) 100%
  );
  background-size: 200% 100%;
  animation: al-shimmer 1.4s var(--al-ease) infinite;
}

@keyframes al-shimmer {
  from {
    background-position: 120% 0;
  }
  to {
    background-position: -20% 0;
  }
}

code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--al-surface-3);
  font-family: var(--al-font-mono);
  font-size: 12px;
}
</style>
