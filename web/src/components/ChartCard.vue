<script setup lang="ts">
withDefaults(
  defineProps<{ title: string; subtitle?: string; tall?: boolean }>(),
  { subtitle: '', tall: false },
)
</script>

<template>
  <section class="al-card card" :class="{ tall }">
    <header>
      <h2>{{ title }}</h2>
      <span v-if="subtitle" class="al-dim">{{ subtitle }}</span>
      <div class="spacer" />
      <slot name="actions" />
    </header>
    <slot />
  </section>
</template>

<style scoped>
.card {
  padding: var(--al-space-4);
}

header {
  display: flex;
  align-items: baseline;
  gap: var(--al-space-2);
  margin-bottom: var(--al-space-3);
}

h2 {
  margin: 0;
  color: var(--al-text-2);
  font-size: 13px;
  font-weight: 500;
  line-height: 20px;
}

.spacer {
  flex: 1;
}

.tall {
  min-height: 100%;
}
</style>
