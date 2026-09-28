<script setup lang="ts">
import { ref } from 'vue'

import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'

const STORAGE_KEY = 'al-sidebar-collapsed'
const collapsed = ref(localStorage.getItem(STORAGE_KEY) === '1')

function toggleSidebar(): void {
  collapsed.value = !collapsed.value
  localStorage.setItem(STORAGE_KEY, collapsed.value ? '1' : '0')
}
</script>

<template>
  <div class="shell" :style="{ '--al-sidebar': collapsed ? '64px' : '240px' }">
    <SideNav :collapsed="collapsed" />
    <div class="main">
      <TopBar :collapsed="collapsed" @toggle-sidebar="toggleSidebar" />
      <main class="content">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--al-sidebar, 240px) 1fr;
  min-height: 100vh;
  transition: grid-template-columns var(--al-dur) var(--al-ease);
}

.main {
  display: flex;
  min-width: 0;
  flex-direction: column;
}

.content {
  width: 100%;
  max-width: 1440px;
  margin: 0 auto;
  padding: var(--al-space-5);
}

@media (max-width: 1200px) {
  .shell {
    --al-sidebar: 64px;
  }
}

@media (max-width: 640px) {
  .content {
    padding: var(--al-space-4);
  }
}
</style>
