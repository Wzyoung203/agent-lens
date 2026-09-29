// 类型化的端点封装。页面只调用这里的函数，不直接 fetch。
import { http } from './client'
import type {
  AppStatus,
  ContextOverviewResponse,
  HealthResponse,
  OverviewResponse,
  Page,
  PriceEntry,
  ProjectDetail,
  ProjectMapping,
  ProjectStat,
  SessionDetail,
  SessionSummary,
  ToolFailure,
  ToolsResponse,
  TurnDetail,
} from './types'

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export const api = {
  health: () => http.get<HealthResponse>('/health'),

  overview: (days: number, project?: string) =>
    http.get<OverviewResponse>(`/overview${query({ days, project })}`),

  projects: (days: number) => http.get<ProjectStat[]>(`/projects${query({ days })}`),
  projectDetail: (name: string, days: number) =>
    http.get<ProjectDetail>(`/projects/${encodeURIComponent(name)}${query({ days })}`),

  sessions: (days: number, params: { project?: string; search?: string; limit?: number; offset?: number } = {}) =>
    http.get<Page<SessionSummary>>(`/sessions${query({ days, ...params })}`),
  sessionDetail: (sessionId: string) =>
    http.get<SessionDetail>(`/sessions/${encodeURIComponent(sessionId)}`),
  turnDetail: (sessionId: string, turnId: string) =>
    http.get<TurnDetail>(
      `/sessions/${encodeURIComponent(sessionId)}/turns/${encodeURIComponent(turnId)}`,
    ),

  tools: (days: number, project?: string) =>
    http.get<ToolsResponse>(`/tools${query({ days, project })}`),
  toolFailures: (days: number, params: { project?: string; limit?: number; offset?: number } = {}) =>
    http.get<Page<ToolFailure>>(`/tools/failures${query({ days, ...params })}`),

  pricing: (params: { provider?: string; model?: string } = {}) =>
    http.get<PriceEntry[]>(`/settings/pricing${query(params)}`),
  putPricing: (entry: PriceEntry) => http.put<PriceEntry>('/settings/pricing', entry),
  deletePricing: (entry: Pick<PriceEntry, 'provider' | 'model' | 'effective_from' | 'time_window'>) =>
    http.delete<{ deleted: boolean }>(`/settings/pricing${query(entry)}`),

  projectMappings: () => http.get<ProjectMapping[]>('/settings/projects'),
  putProjectMapping: (body: { path_prefix: string; project: string }) =>
    http.put<ProjectMapping>('/settings/projects', body),
  deleteProjectMapping: (pathPrefix: string) =>
    http.delete<{ deleted: boolean; sessions_reassigned: number }>(
      `/settings/projects${query({ path_prefix: pathPrefix })}`,
    ),
  refreshProjects: () =>
    http.post<{ sessions_reassigned: number }>('/settings/projects/refresh'),

  status: () => http.get<AppStatus>('/settings/status'),

  context: (days: number, project?: string) =>
    http.get<ContextOverviewResponse>(`/context${query({ days, project })}`),
}
