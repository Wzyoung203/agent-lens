<script setup lang="ts">
import AppShell from './layout/AppShell.vue'
</script>

<template>
  <AppShell>
    <RouterView v-slot="{ Component }">
      <Transition name="al-fade" mode="out-in">
        <component :is="Component" />
      </Transition>
    </RouterView>
  </AppShell>
</template>
