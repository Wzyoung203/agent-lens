<script setup lang="ts">
import { ref, toRef } from 'vue'
import type { EChartsCoreOption } from 'echarts'

import { useChart } from '@/composables/useChart'

const props = withDefaults(
  defineProps<{ option: EChartsCoreOption; label: string; height?: string }>(),
  { height: '280px' },
)

const host = ref<HTMLElement | null>(null)
useChart(host, toRef(props, 'option'))
</script>

<template>
  <div ref="host" class="chart" :style="{ height }" role="img" :aria-label="label" />
</template>

<style scoped>
.chart {
  width: 100%;
}
</style>
