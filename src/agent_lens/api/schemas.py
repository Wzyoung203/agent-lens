"""HTTP 契约：响应模型从 queries 显式重导出，请求模型在这里定义。

P1.4b 的 `types.ts` 必须与这些字段逐字对齐（snake_case，不做驼峰转换）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..pricing import PriceEntry
from ..queries import (
    ApiCallRow,
    ContextBlockStat,
    ContextOverviewResponse,
    ContextTrendPoint,
    CostBreakdown,
    DailyPoint,
    MetricCards,
    ModelComparisonResponse,
    ModelEffortStat,
    OverviewResponse,
    Page,
    ProjectDetail,
    ProjectStat,
    ProjectToolFailure,
    RangeInfo,
    SessionDetail,
    SessionSummary,
    SkillsResponse,
    SkillStat,
    ToolCallRow,
    ToolFailure,
    ToolsResponse,
    ToolStat,
    TurnDetail,
    TurnSummary,
)

__all__ = [
    "ApiCallRow",
    "AppStatus",
    "ContextBlockStat",
    "ContextOverviewResponse",
    "ContextTrendPoint",
    "CostBreakdown",
    "DailyPoint",
    "ErrorBody",
    "HealthResponse",
    "MetricCards",
    "ModelComparisonResponse",
    "ModelEffortStat",
    "OverviewResponse",
    "Page",
    "PriceEntry",
    "ProjectDetail",
    "ProjectMapping",
    "ProjectMappingRequest",
    "ProjectStat",
    "ProjectToolFailure",
    "RangeInfo",
    "SessionDetail",
    "SessionSummary",
    "SkillStat",
    "SkillsResponse",
    "ToolCallRow",
    "ToolFailure",
    "ToolStat",
    "ToolsResponse",
    "TurnDetail",
    "TurnSummary",
]


class HealthResponse(BaseModel):
    status: str = "ok"
    schema_version: int


class ErrorBody(BaseModel):
    detail: str


class ProjectMapping(BaseModel):
    project: str
    prefixes: list[str] = Field(default_factory=list)
    session_count: int = 0


class ProjectMappingRequest(BaseModel):
    path_prefix: str
    project: str


class DeletedResponse(BaseModel):
    deleted: bool


class ReassignResponse(BaseModel):
    sessions_reassigned: int = 0
    deleted: bool = False


class AppStatus(BaseModel):
    db_path: str
    schema_version: int
    counts: dict[str, int] = Field(default_factory=dict)
    parse_errors: int = 0
    report_queue: dict[str, int] = Field(default_factory=dict)
    langfuse_enabled: bool = False
    sessions_dir: str = ""
