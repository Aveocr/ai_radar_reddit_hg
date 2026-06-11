from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field


# ─── Sources ───
class SourceCreate(BaseModel):
    id: str = Field(..., max_length=20)
    name: str = Field(..., max_length=100)
    code: str = Field(..., max_length=20)
    api_base_url: Optional[str] = None
    api_doc_url: Optional[str] = None
    auth_type: Optional[str] = None
    rate_limit: Optional[Dict[str, Any]] = None
    is_active: bool = True


class SourceOut(BaseModel):
    id: str
    name: str
    code: str
    api_base_url: Optional[str]
    api_doc_url: Optional[str]
    auth_type: Optional[str]
    rate_limit: Optional[Dict[str, Any]]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Parser Tasks ───
class TaskCreate(BaseModel):
    parser_name: str
    date_from: datetime
    date_to: datetime
    filters: Optional[Dict[str, Any]] = None
    max_items: Optional[int] = 1000


class HuggingFaceTaskCreate(BaseModel):
    date_from: datetime
    date_to: datetime
    filters: Optional[Dict[str, Any]] = None
    max_items: Optional[int] = 1000


class RedditTaskCreate(BaseModel):
    date_from: datetime
    date_to: datetime
    filters: Dict[str, Any] = Field(
        default_factory=lambda: {"subreddit": "MachineLearning", "sort": "hot"}
    )
    max_items: Optional[int] = 100


class ParserRunCreate(BaseModel):
    date_from: datetime
    date_to: datetime
    filters: Optional[Dict[str, Any]] = None
    max_items: Optional[int] = 1000


class TaskOut(BaseModel):
    id: UUID
    parser_name: str
    status: str
    date_from: datetime
    date_to: datetime
    items_collected: int
    items_new: int
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    created_at: datetime
    max_items: Optional[int]
    error_log: Optional[str]
    retry_count: int
    triggered_by: str

    class Config:
        from_attributes = True


class TaskRetry(BaseModel):
    task_id: UUID


# ─── Parser Logs ───
class LogFilter(BaseModel):
    parser_name: Optional[str] = None
    status: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    limit: int = 100


class LogOut(BaseModel):
    id: UUID
    task_id: Optional[UUID]
    parser_name: str
    run_at: datetime
    duration_sec: Optional[int]
    items_count: int
    errors_count: int
    status: str
    details: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


# ─── DB Stats ───
class TableStat(BaseModel):
    table_name: str
    row_count: int
    last_updated: Optional[datetime]


class StatsOut(BaseModel):
    tables: List[TableStat]
    total_raw: int
    total_enriched: int
    total_vectors: int
    pending_tasks: int
    failed_tasks: int


# ─── DAG / Pipeline Status ───
class PipelineNode(BaseModel):
    node_id: str
    label: str
    status: str  # idle | running | completed | failed
    count: int
    last_run: Optional[datetime]


class PipelineEdge(BaseModel):
    from_node: str
    to_node: str


class PipelineStatus(BaseModel):
    nodes: List[PipelineNode]
    edges: List[PipelineEdge]


# ─── Scheduler ───
class SchedulerConfigOut(BaseModel):
    enabled: bool
    interval_hours: int
    start_date: Optional[datetime]
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    updated_at: datetime
    created_at: datetime
    llm_summary_enabled: bool = True

    class Config:
        from_attributes = True


class SchedulerConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_hours: Optional[int] = None
    start_date: Optional[datetime] = None
    llm_summary_enabled: Optional[bool] = None


class LlmSummaryConfigUpdate(BaseModel):
    enabled: bool
