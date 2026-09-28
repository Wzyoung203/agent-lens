<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import {
  Coin,
  DataAnalysis,
  Histogram,
  Notebook,
  Setting,
} from '@element-plus/icons-vue'

defineProps<{ collapsed: boolean }>()

const route = useRoute()

const items = [
  { path: '/', label: '总览', icon: DataAnalysis },
  { path: '/projects', label: '项目', icon: Histogram },
  { path: '/sessions', label: '会话', icon: Notebook },
  { path: '/tools', label: '工具调用', icon: Coin },
  { path: '/settings', label: '设置', icon: Setting },
]

const active = computed(() => {
  const match = items.find((item) => item.path !== '/' && route.path.startsWith(item.path))
  return match?.path ?? '/'
})
</script>

<template>
  <aside class="nav" :class="{ collapsed }">
    <div class="brand">
      <span class="dot" />
      <span v-if="!collapsed" class="name">agent-lens</span>
    </div>
    <nav>
      <RouterLink
        v-for="item in items"
        :key="item.path"
        :to="item.path"
        class="item"
        :class="{ active: active === item.path }"
        :aria-label="item.label"
        :title="collapsed ? item.label : undefined"
      >
        <el-icon><component :is="item.icon" /></el-icon>
        <span v-if="!collapsed">{{ item.label }}</span>
      </RouterLink>
    </nav>
    <div v-if="!collapsed" class="foot al-dim">本地数据 · 只读分析</div>
  </aside>
</template>

<style scoped>
.nav {
  position: sticky;
  top: 0;
  display: flex;
  height: 100vh;
  flex-direction: column;
  padding: var(--al-space-4) var(--al-space-3);
  border-right: 1px solid var(--al-border);
  background: color-mix(in srgb, var(--al-surface) 72%, transparent);
  backdrop-filter: blur(12px);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--al-space-2);
  height: 32px;
  margin: 0 var(--al-space-2) var(--al-space-5);
  font-weight: 600;
  letter-spacing: 0.02em;
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: var(--al-radius-pill);
  background: var(--al-accent);
  box-shadow: var(--al-glow);
}

nav {
  display: flex;
  flex-direction: column;
  gap: var(--al-space-1);
}

.item {
  display: flex;
  align-items: center;
  gap: var(--al-space-3);
  padding: 9px var(--al-space-3);
  border-radius: var(--al-radius-sm);
  color: var(--al-text-2);
  transition:
    background var(--al-dur-fast) var(--al-ease),
    color var(--al-dur-fast) var(--al-ease);
}

.item:hover {
  background: var(--al-surface-2);
  color: var(--al-text);
}

.item.active {
  background: var(--al-accent-soft);
  color: var(--al-text);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--al-accent) 28%, transparent);
}

.collapsed .item {
  justify-content: center;
  padding: 9px 0;
}

.foot {
  margin-top: auto;
  padding: var(--al-space-2);
  font-size: 12px;
}
</style>
