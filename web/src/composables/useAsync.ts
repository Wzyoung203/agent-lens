import { ref, type Ref } from 'vue'

export interface AsyncState<T> {
  data: Ref<T | null>
  error: Ref<string | null>
  loading: Ref<boolean>
  reload: () => Promise<void>
}

/** 统一的「加载中 / 出错 / 空」三态来源。 */
export function useAsync<T>(fetcher: () => Promise<T>): AsyncState<T> {
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

  void reload()
  return { data, error, loading, reload }
}
