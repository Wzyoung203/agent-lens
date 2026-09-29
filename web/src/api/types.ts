// 字段名与后端 JSON 逐字一致（snake_case），不做驼峰转换。
// 契约来源：docs/superpowers/plans/2026-09-28-p1-4a-query-api.md 的「前端契约」一节。

export interface RangeInfo {
  start: string
  end: string
  days: number
  timezone: string
}

export interface MetricCards {
  total_cost: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  cached_input_tokens: number
  cache_hit_rate: number
  session_count: number
  turn_count: number
  api_call_count: number
  tool_call_count: number
  tool_failure_rate: number
  unpriced_calls: number
  currency: string
}

export interface DailyPoint {
  day: string
  cost: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  api_calls: number
  cache_hit_rate: number
}

export interface ProjectStat {
  project: string
  cost: number
  total_tokens: number
  input_tokens: number
  output_tokens: number
  cache_hit_rate: number
  session_count: number
  api_call_count: number
  tool_failure_rate: number
}

export interface CostBreakdown {
  uncached_input: number
  cached_input: number
  output: number
  total: number
  currency: string
}

export interface OverviewResponse {
  range: RangeInfo
  cards: MetricCards
  daily: DailyPoint[]
  projects: ProjectStat[]
}

export interface SessionSummary {
  session_id: string
  project: string
  cwd: string | null
  cli_version: string | null
  model_provider: string | null
  first_seen_at: string | null
  updated_at: string | null
  recorded_at: string | null
  turn_count: number
  api_call_count: number
  tool_call_count: number
  total_tokens: number
  cost: number
  cache_hit_rate: number
  first_api_at: string | null
  last_api_at: string | null
}

export interface TurnSummary {
  turn_id: string
  index: number
  model: string | null
  effort: string | null
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  aborted_reason: string | null
  api_call_count: number
  tool_call_count: number
  tool_failure_count: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  cache_hit_rate: number
  cost: number
  context_growth: number | null
}

export interface ApiCallRow {
  ordinal: number
  file_path: string
  response_id: string | null
  timestamp: string | null
  model: string | null
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  reasoning_output_tokens: number
  total_tokens: number
  cache_hit_rate: number
  cost: number
  priced: boolean
}

export interface ToolCallRow {
  ordinal: number
  call_id: string | null
  name: string
  kind: string
  arguments_chars: number
  output_chars: number
  exit_code: number | null
  wall_time_seconds: number | null
  success: boolean | null
  duration_ms: number | null
  result_summary: string | null
}

export interface SessionDetail {
  session: SessionSummary
  turns: TurnSummary[]
  cost_breakdown: CostBreakdown
}

export interface TurnDetail {
  turn: TurnSummary
  api_calls: ApiCallRow[]
  tool_calls: ToolCallRow[]
}

export interface ProjectDetail {
  range: RangeInfo
  project: ProjectStat
  daily: DailyPoint[]
  sessions: SessionSummary[]
  cost_breakdown: CostBreakdown
}

export interface ToolStat {
  name: string
  call_count: number
  failure_count: number
  failure_rate: number
  avg_duration_ms: number | null
  p95_duration_ms: number | null
  total_output_chars: number
}

export interface ToolFailure {
  session_id: string
  turn_id: string | null
  project: string
  call_id: string | null
  name: string
  exit_code: number | null
  wall_time_seconds: number | null
  result_summary: string | null
  ordinal: number
  file_path: string
}

export interface ProjectToolFailure {
  project: string
  call_count: number
  failure_count: number
  failure_rate: number
}

export interface ToolsResponse {
  range: RangeInfo
  stats: ToolStat[]
  failures: ToolFailure[]
  failure_cost: CostBreakdown
  by_project: ProjectToolFailure[]
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface HealthResponse {
  status: string
  schema_version: number
}

export type TimeWindow = 'any' | 'peak' | 'idle'

export interface PriceEntry {
  provider: string
  model: string
  effective_from: string
  time_window: TimeWindow
  input_price_per_mtok: number
  cached_input_price_per_mtok: number
  output_price_per_mtok: number
  reasoning_output_price_per_mtok: number | null
  currency: string
}

export interface ProjectMapping {
  project: string
  prefixes: string[]
  session_count: number
}

export interface AppStatus {
  db_path: string
  schema_version: number
  counts: Record<string, number>
  parse_errors: number
  report_queue: Record<string, number>
  langfuse_enabled: boolean
  sessions_dir: string
}

// P2.1 上下文成本分解（字段与后端 ContextOverviewResponse 逐字对齐）
export interface ContextBlockStat {
  block: string
  tokens: number
  share: number
  cjk_chars: number
  other_chars: number
  estimated_tokens: number
}

export interface ContextTrendPoint {
  day: string
  block: string
  tokens: number
}

export interface ContextOverviewResponse {
  range: { start: string; end: string; days: number }
  blocks: ContextBlockStat[]
  trend: ContextTrendPoint[]
  input_tokens: number
  analyzed_calls: number
  total_calls: number
  coverage: number
  cache_hit_rate: number
}

// P2.2 skill 命中（字段与后端 SkillsResponse 逐字对齐）
export interface SkillStat {
  skill_name: string
  loads: number
  session_count: number
  tool_names: string[]
  first_seen: string | null
  last_seen: string | null
}

export interface SkillsResponse {
  range: { start: string; end: string; days: number }
  skills: SkillStat[]
  total_loads: number
}
