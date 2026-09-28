import { ref } from 'vue'

const STORAGE_KEY = 'al-range-days'
const ALLOWED = [1, 7, 30, 90, 365]

function readStored(): number {
  const stored = Number(localStorage.getItem(STORAGE_KEY))
  return ALLOWED.includes(stored) ? stored : 30
}

// 顶栏与页面共用同一个 ref，切时间范围时所有页面同步刷新。
const days = ref<number>(readStored())

export function useRange() {
  function setDays(next: number): void {
    days.value = next
    localStorage.setItem(STORAGE_KEY, String(next))
  }

  return { days, setDays }
}
