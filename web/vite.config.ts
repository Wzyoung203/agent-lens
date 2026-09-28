import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时 /api 代理到 FastAPI（P1.4a 的 `agent-lens serve`），
// 生产时前端产物由同一个 FastAPI 托管，因此两边都是同源、不需要 CORS。
// 代理目标可覆盖：在容器里跑 dev server 时后端是 compose 的 `web` 服务名。
const proxyTarget = process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: proxyTarget, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Element Plus 全量引入约 1.1MB（gzip 后 ~340KB）。这是一个本地单机仪表盘，
    // 首屏从本机磁盘加载，按需引入带来的收益不值得多一层构建插件，故显式放宽阈值。
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          echarts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          element: ['element-plus'],
          vue: ['vue', 'vue-router'],
        },
      },
    },
  },
})
