// 唯一发请求的地方。所有调用都走 /api 相对路径：开发由 Vite 代理，生产同源。

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    // 响应不是 JSON，退回状态码
  }
  return `请求失败（HTTP ${response.status}）`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    throw new ApiError(await parseError(response), response.status)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const http = {
  get: <T>(path: string) => request<T>(path),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  post: <T>(path: string) => request<T>(path, { method: 'POST' }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
