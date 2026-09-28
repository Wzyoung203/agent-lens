<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    label: string
    value: string
    hint?: string
    accent?: boolean
    loading?: boolean
  }>(),
  { hint: '', accent: false, loading: false },
)

// 数字变化时做一个极短的淡入位移，避免整块布局跳动。
const tick = ref(0)
watch(
  () => props.value,
  () => {
    tick.value += 1
  },
)
const key = computed(() => tick.value)
</script>

<template>
  <div class="card al-card" :class="{ accent }">
    <div class="label">{{ label }}</div>
    <div v-if="loading" class="value skeleton" />
    <Transition name="al-value" mode="out-in">
      <div :key="key" class="value al-num">{{ value }}</div>
    </Transition>
    <div v-if="hint" class="hint al-dim">{{ hint }}</div>
  </div>
</template>

<style scoped>
.card {
  padding: var(--al-space-4) var(--al-space-4) calc(var(--al-space-4) + 2px);
  transition:
    border-color var(--al-dur) var(--al-ease),
    transform var(--al-dur) var(--al-ease);
}

.card:hover {
  border-color: var(--al-border-strong);
  transform: translateY(-2px);
}

.accent {
  box-shadow: var(--al-shadow), 0 0 0 1px color-mix(in srgb, var(--al-accent) 22%, transparent);
}

.label {
  color: var(--al-text-2);
  font-size: 13px;
}

.value {
  min-height: 36px;
  margin-top: var(--al-space-2);
  font-size: 30px;
  font-weight: 600;
  line-height: 36px;
}

.skeleton {
  width: 60%;
  border-radius: var(--al-radius-pill);
  background: var(--al-surface-2);
}

.hint {
  margin-top: var(--al-space-1);
  font-size: 12px;
}

.al-value-enter-active,
.al-value-leave-active {
  transition:
    opacity var(--al-dur-fast) var(--al-ease),
    transform var(--al-dur-fast) var(--al-ease);
}

.al-value-enter-from {
  opacity: 0;
  transform: translateY(4px);
}

.al-value-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
