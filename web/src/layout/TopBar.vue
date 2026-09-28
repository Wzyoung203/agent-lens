<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { Expand, Fold, Moon, Refresh, Sunny } from '@element-plus/icons-vue'

import { useTheme } from '@/composables/useTheme'
import { useRange } from '@/composables/useRange'

defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ 'toggle-sidebar': [] }>()

const route = useRoute()
const { theme, toggle } = useTheme()
const { days, setDays } = useRange()

const title = computed(() => (route.meta.title as string | undefined) ?? '总览')
const pending = computed(() => route.meta.title === undefined)
void pending
</script>

<template>
  <header class="topbar">
    <el-button
      text
      :aria-label="collapsed ? '展开侧栏' : '折叠侧栏'"
      @click="emit('toggle-sidebar')"
    >
      <el-icon><component :is="collapsed ? Expand : Fold" /></el-icon>
    </el-button>

    <h1>{{ title }}</h1>

    <div class="spacer" />

    <el-select
      :model-value="days"
      size="small"
      style="width: 116px"
      aria-label="时间范围"
      @update:model-value="setDays(Number($event))"
    >
      <el-option :value="1" label="最近 1 天" />
      <el-option :value="7" label="最近 7 天" />
      <el-option :value="30" label="最近 30 天" />
      <el-option :value="90" label="最近 90 天" />
      <el-option :value="365" label="最近一年" />
    </el-select>

    <span class="live" title="每 10 秒自动刷新">
      <span class="pulse" />自动刷新
    </span>

    <el-button text aria-label="刷新" @click="$router.go(0)">
      <el-icon><Refresh /></el-icon>
    </el-button>

    <el-button
      text
      :aria-label="theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
      @click="toggle()"
    >
      <el-icon><component :is="theme === 'dark' ? Sunny : Moon" /></el-icon>
    </el-button>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  height: 56px;
  align-items: center;
  gap: var(--al-space-2);
  padding: 0 var(--al-space-5);
  border-bottom: 1px solid var(--al-border);
  background: color-mix(in srgb, var(--al-bg) 78%, transparent);
  backdrop-filter: blur(12px);
}

h1 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.spacer {
  flex: 1;
}

.live {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 var(--al-space-2);
  color: var(--al-text-3);
  font-size: 12px;
}

.pulse {
  width: 6px;
  height: 6px;
  border-radius: var(--al-radius-pill);
  background: var(--al-success);
  animation: al-pulse 2.4s var(--al-ease) infinite;
}

@keyframes al-pulse {
  0%,
  100% {
    opacity: 0.35;
    transform: scale(0.9);
  }
  50% {
    opacity: 1;
    transform: scale(1.15);
  }
}

@media (max-width: 900px) {
  .live {
    display: none;
  }
}
</style>
