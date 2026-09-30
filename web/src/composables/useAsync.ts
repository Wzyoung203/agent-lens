import { ref, watch, type Ref, type WatchSource } from 'vue'

export interface AsyncState<T> {
  data: Ref<T | null>
  error: Ref<string | null>
  loading: Ref<boolean>
  reload: () => Promise<void>
}

/**
 * 统一的「加载中 / 出错 / 空」三态来源。
 *
 * `sources` 是取数所依赖的响应式输入（筛选条件、当前选中项……）。**fetcher 里读了哪个
 * 响应式值，就要把它传进来**——否则那个值变了不会重新取数，页面只能等下一次轮询，
 * 表现就是「点了没反应 / 卡顿」（2026-09-30 的项目页事故）。传了 sources 就不再在
 * setup 里额外取一次，避免首屏发两个请求。
 */
export function useAsync<T>(
  fetcher: () => Promise<T>,
  sources: WatchSource[] = [],
): AsyncState<T> {
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<string | null>(null)
  const loading = ref(false)

  async function reload(): Promise<void> {
    loading.value = true
    try {
      data.value = await fetcher()
      error.value = null
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : String(caught)
    } finally {
      loading.value = false
    }
  }

  if (sources.length > 0) {
    watch(sources, () => void reload(), { immediate: true })
  } else {
    void reload()
  }
  return { data, error, loading, reload }
}
