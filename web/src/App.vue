<script setup lang="ts">
import AppShell from './layout/AppShell.vue'
</script>

<template>
  <AppShell>
    <RouterView v-slot="{ Component, route }">
      <Transition name="al-fade" mode="out-in">
        <!--
          Transition 只接受单个根节点，而每个 views/*.vue 的模板都是多根片段
          （PageHeader + 内容兄弟节点）。所以这里补一层按路由 key 的容器当作
          过渡元素；去掉这层容器会让路由切换后新视图根本不挂载（KI-1）。
        -->
        <div :key="route.path" class="route-view">
          <component :is="Component" />
        </div>
      </Transition>
    </RouterView>
  </AppShell>
</template>

<style scoped>
/* 布局中立：只作为 Transition 的落点，不引入新的盒模型影响 */
.route-view {
  display: block;
}
</style>
