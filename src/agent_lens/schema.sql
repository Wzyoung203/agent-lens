-- agent-lens SQLite schema v3
--
-- 约定
--   * 幂等键统一为 (file_path, ordinal)：同一行日志重复写入不会产生新行
--   * 时间一律存 UTC ISO8601 文本（形如 2026-09-27T13:00:00.123456+00:00），字典序即时间序
--   * 正文不入库：只存长度、状态与「原始文件 + ordinal」指针，需要原文时回 JSONL 取
--   * 成本、缓存命中率是派生值，不落库，查询时由 pricing.py 计算
--   * v3：pricing 增加 time_window（any / peak / idle）；api_call_view 补 model_provider 与
--     occurred_at（实测 token_usage_record 不带时间戳，只能回落到 turn 起始时间，见设计文档 4.11）

CREATE TABLE IF NOT EXISTS sessions (
    session_id              TEXT PRIMARY KEY,
    cli_version             TEXT,
    cwd                     TEXT,
    model_provider          TEXT,
    base_instructions_chars INTEGER NOT NULL DEFAULT 0,
    recorded_at             TEXT,
    project                 TEXT NOT NULL DEFAULT '未归类',
    first_seen_at           TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_state (
    file_path         TEXT PRIMARY KEY,
    session_id        TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    cli_version       TEXT,
    last_ordinal      INTEGER NOT NULL DEFAULT -1,
    byte_offset       INTEGER NOT NULL DEFAULT 0,
    file_size         INTEGER NOT NULL DEFAULT 0,
    mtime             REAL NOT NULL DEFAULT 0,
    parse_error_count INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    session_id     TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    turn_id        TEXT NOT NULL,
    cwd            TEXT,
    model          TEXT,
    effort         TEXT,
    started_at     TEXT,
    completed_at   TEXT,
    duration_ms    INTEGER,
    aborted_reason TEXT,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_id)
);

CREATE TABLE IF NOT EXISTS api_calls (
    file_path                TEXT NOT NULL,
    ordinal                  INTEGER NOT NULL,
    session_id               TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    turn_id                  TEXT,
    response_id              TEXT,
    timestamp                TEXT,
    input_tokens             INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_write_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens            INTEGER NOT NULL DEFAULT 0,
    reasoning_output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens             INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    file_path       TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    session_id      TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    turn_id         TEXT,
    call_id         TEXT,
    name            TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'function_call',
    arguments_chars INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS tool_results (
    file_path         TEXT NOT NULL,
    ordinal           INTEGER NOT NULL,
    session_id        TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    call_id           TEXT,
    output_chars      INTEGER NOT NULL DEFAULT 0,
    exit_code         INTEGER,
    wall_time_seconds REAL,
    success           INTEGER,
    result_summary    TEXT,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS items (
    file_path       TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    session_id      TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    item_type       TEXT NOT NULL,
    started_at_ms   INTEGER NOT NULL,
    completed_at_ms INTEGER NOT NULL,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS events (
    file_path    TEXT NOT NULL,
    ordinal      INTEGER NOT NULL,
    session_id   TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    event_type   TEXT,
    payload_keys TEXT,
    PRIMARY KEY (file_path, ordinal)
);

CREATE TABLE IF NOT EXISTS projects (
    name       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_paths (
    path_prefix  TEXT PRIMARY KEY,
    project_name TEXT NOT NULL REFERENCES projects(name) ON DELETE CASCADE,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pricing (
    provider                        TEXT NOT NULL,
    model                           TEXT NOT NULL,
    effective_from                  TEXT NOT NULL,
    time_window                     TEXT NOT NULL DEFAULT 'any',
    input_price_per_mtok            REAL NOT NULL,
    cached_input_price_per_mtok     REAL NOT NULL,
    output_price_per_mtok           REAL NOT NULL,
    reasoning_output_price_per_mtok REAL,
    currency                        TEXT NOT NULL DEFAULT 'USD',
    PRIMARY KEY (provider, model, effective_from, time_window)
);

CREATE INDEX IF NOT EXISTS idx_api_calls_session ON api_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_api_calls_timestamp ON api_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_lookup ON tool_calls(file_path, call_id);
CREATE INDEX IF NOT EXISTS idx_tool_results_lookup ON tool_results(file_path, call_id);
CREATE INDEX IF NOT EXISTS idx_items_session ON items(session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_pricing_lookup
    ON pricing(provider, model, time_window, effective_from DESC);

CREATE VIEW IF NOT EXISTS api_call_view AS
SELECT
    a.file_path,
    a.ordinal,
    a.session_id,
    a.turn_id,
    a.response_id,
    a.timestamp,
    a.input_tokens,
    a.cached_input_tokens,
    a.cache_write_input_tokens,
    a.output_tokens,
    a.reasoning_output_tokens,
    a.total_tokens,
    s.project,
    s.model_provider,
    s.cli_version,
    t.model,
    t.effort,
    COALESCE(a.timestamp, t.started_at, s.recorded_at, s.first_seen_at) AS occurred_at,
    CAST(a.cached_input_tokens AS REAL) / NULLIF(a.input_tokens, 0) AS cache_hit_rate
FROM api_calls a
LEFT JOIN sessions s ON s.session_id = a.session_id
LEFT JOIN turns t ON t.session_id = a.session_id AND t.turn_id = a.turn_id;

CREATE VIEW IF NOT EXISTS tool_call_details AS
SELECT
    c.file_path,
    c.ordinal AS call_ordinal,
    c.session_id,
    c.turn_id,
    c.call_id,
    c.name,
    c.kind,
    c.arguments_chars,
    r.ordinal AS result_ordinal,
    r.output_chars,
    r.exit_code,
    r.wall_time_seconds,
    r.success,
    r.result_summary
FROM tool_calls c
LEFT JOIN tool_results r ON r.file_path = c.file_path AND r.call_id = c.call_id;

-- P1.3：待上报队列。落盘即持久化，进程被杀后重启自动重投（设计文档 5.5 / 10 节）
-- payload 里的正文已脱敏，report_id 是幂等键，重复入队被忽略
CREATE TABLE IF NOT EXISTS report_queue (
    report_id       TEXT PRIMARY KEY,
    session_id      TEXT,
    turn_id         TEXT,
    kind            TEXT NOT NULL,
    granularity     TEXT NOT NULL DEFAULT 'full',
    payload         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_queue_pending
    ON report_queue(status, next_attempt_at, created_at);

-- P2.1：上下文构成投影。正文不入库，这里只存字符数与锚定后的 token。
-- unattributed 块的 cjk_chars / other_chars 恒为 0：它是真实 input_tokens 与字符估算
-- 之间的差额（每次调用重发的工具定义与请求框架）。每个调用五行，五行之和等于 input_tokens。
CREATE TABLE IF NOT EXISTS context_breakdown (
    file_path         TEXT NOT NULL,
    ordinal           INTEGER NOT NULL,
    block             TEXT NOT NULL,
    session_id        TEXT,
    turn_id           TEXT,
    input_tokens      INTEGER NOT NULL DEFAULT 0,
    cjk_chars         INTEGER NOT NULL DEFAULT 0,
    other_chars       INTEGER NOT NULL DEFAULT 0,
    estimated_tokens  REAL NOT NULL DEFAULT 0,
    attributed_tokens REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (file_path, ordinal, block)
);

CREATE INDEX IF NOT EXISTS idx_context_breakdown_session
    ON context_breakdown(session_id);

-- P2.2：skill 命中。一次工具调用加载一个 skill 记一行；正文不入库。
-- 这里不存 session_id / turn_id：工具调用行本身不带这两个字段，硬猜会污染归属，
-- 查询时用 (file_path, ordinal) 关联 tool_calls（它的主键正是这两列）即可。
CREATE TABLE IF NOT EXISTS skill_hits (
    file_path  TEXT NOT NULL,
    ordinal    INTEGER NOT NULL,
    skill_name TEXT NOT NULL,
    skill_path TEXT NOT NULL,
    tool_name  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (file_path, ordinal, skill_name)
);

CREATE INDEX IF NOT EXISTS idx_skill_hits_name ON skill_hits(skill_name);
