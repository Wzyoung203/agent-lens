// 数据色板的唯一来源：图表与标签都从这里取色。
export const CHART_COLORS = [
  '#22d3ee',
  '#8b5cf6',
  '#34d399',
  '#fbbf24',
  '#f87171',
  '#60a5fa',
  '#2dd4bf',
  '#f472b6',
  '#a3e635',
  '#fb923c',
]

export const SEMANTIC = {
  success: '#34d399',
  warning: '#fbbf24',
  danger: '#f87171',
  info: '#60a5fa',
}

/** 图表要的是具体色值，不能直接用 CSS 变量，所以按主题给一套等价的坐标轴配色。 */
export function axisTheme(theme: 'dark' | 'light') {
  const dark = theme === 'dark'
  return {
    axis: dark ? 'rgba(255,255,255,0.10)' : 'rgba(15,23,42,0.10)',
    label: dark ? '#97a2b8' : '#475569',
    tooltipBg: dark ? '#161d2a' : '#ffffff',
    tooltipBorder: dark ? 'rgba(255,255,255,0.12)' : 'rgba(15,23,42,0.12)',
    tooltipText: dark ? '#e8edf7' : '#0f172a',
  }
}
