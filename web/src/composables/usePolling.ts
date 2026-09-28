import { onBeforeUnmount, onMounted } from 'vue'

/** 10 秒轮询（设计文档 9.4）；页面失焦时暂停，回来立刻补一次。 */
export function usePolling(task: () => void | Promise<void>, intervalMs = 10_000): void {
  let timer: number | undefined

  function start(): void {
    stop()
    timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void task()
    }, intervalMs)
  }

  function stop(): void {
    if (timer !== undefined) window.clearInterval(timer)
    timer = undefined
  }

  function onVisibility(): void {
    if (document.visibilityState === 'visible') void task()
  }

  onMounted(() => {
    start()
    document.addEventListener('visibilitychange', onVisibility)
  })
  onBeforeUnmount(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisibility)
  })
}
