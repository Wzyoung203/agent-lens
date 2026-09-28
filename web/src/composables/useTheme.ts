import { ref } from 'vue'

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'al-theme'

function readStoredTheme(): Theme {
  if (typeof localStorage === 'undefined') return 'dark'
  return localStorage.getItem(STORAGE_KEY) === 'light' ? 'light' : 'dark'
}

const theme = ref<Theme>(readStoredTheme())

function paint(next: Theme): void {
  document.documentElement.classList.toggle('dark', next === 'dark')
}

/** 在 main.ts 挂载前调用一次，避免首屏白闪。 */
export function applyStoredTheme(): void {
  paint(theme.value)
}

export function useTheme() {
  function setTheme(next: Theme): void {
    theme.value = next
    localStorage.setItem(STORAGE_KEY, next)
    paint(next)
  }

  return {
    theme,
    setTheme,
    toggle: () => setTheme(theme.value === 'dark' ? 'light' : 'dark'),
  }
}
