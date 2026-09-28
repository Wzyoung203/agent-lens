import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/tokens.css'
import './styles/element-overrides.css'
import './styles/base.css'

import App from './App.vue'
import { applyStoredTheme } from './composables/useTheme'
import { router } from './router'

// 挂载前先定主题，避免首屏闪白。
applyStoredTheme()

const app = createApp(App)
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}
app.use(router)
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
