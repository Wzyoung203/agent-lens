// 金额、数量、百分比的统一格式化。

/**
 * 金额自适应精度：deepseek 单价是「每百万 token 零点几美元」，
 * 个人月账单常在 0.001–5 美元之间，直接 toFixed(2) 会把真实花费显示成 $0.00。
 */
export function formatCost(value: number, currency = 'USD'): string {
  const symbol = currency === 'CNY' ? '¥' : '$'
  const amount = Math.abs(value)
  if (amount === 0) return `${symbol}0`
  if (amount >= 1) return `${symbol}${value.toFixed(2)}`
  if (amount >= 0.01) return `${symbol}${value.toFixed(3)}`
  if (amount >= 0.0001) return `${symbol}${value.toFixed(4)}`
  return `${symbol}${value.toExponential(2)}`
}

/** token 数量按中文习惯缩写：万 / 亿。 */
export function formatTokens(value: number): string {
  const amount = Math.abs(value)
  if (amount >= 1e8) return `${(value / 1e8).toFixed(2)} 亿`
  if (amount >= 1e4) return `${(value / 1e4).toFixed(1)} 万`
  return String(Math.round(value))
}

export function formatInt(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(Math.round(value))
}

export function formatPercent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)} s`
  const minutes = Math.floor(ms / 60_000)
  const seconds = Math.round((ms % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

/** 后端一律给 UTC ISO8601，界面上换成浏览器本地时区。 */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

export function formatDay(value: string): string {
  return value.slice(5)
}

export function truncate(value: string | null | undefined, limit = 80): string {
  if (!value) return '—'
  return value.length > limit ? `${value.slice(0, limit)}…` : value
}
