<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div class="head">
    <div>
      <h2>{{ title }}</h2>
      <p v-if="description" class="al-muted">{{ description }}</p>
    </div>
    <div class="spacer" />
    <slot />
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: flex-end;
  gap: var(--al-space-4);
  margin-bottom: var(--al-space-4);
}

h2 {
  margin: 0 0 var(--al-space-1);
  font-size: 18px;
  font-weight: 600;
}

p {
  margin: 0;
  font-size: 13px;
}

.spacer {
  flex: 1;
}
</style>
