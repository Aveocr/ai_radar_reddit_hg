from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4

from sqlalchemy import select, func, update, desc

from config import get_settings

settings = get_settings()

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    Source, RawItem, EnrichedItem, Vector, ParserTask, ParserLog, SchedulerConfig
)
from admin.schemas import (
    SourceCreate, SourceOut, TaskCreate, TaskOut, LogOut,
    TableStat, StatsOut, PipelineNode, PipelineEdge, PipelineStatus,
    SchedulerConfigOut, SchedulerConfigUpdate
)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class SourceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(self) -> List[SourceOut]:
        result = await self.db.execute(select(Source).order_by(Source.name))
        return [SourceOut.model_validate(s) for s in result.scalars().all()]

    async def get(self, code: str) -> Optional[SourceOut]:
        result = await self.db.execute(select(Source).where(Source.code == code))
        source = result.scalar_one_or_none()
        return SourceOut.model_validate(source) if source else None

    async def create(self, data: SourceCreate) -> SourceOut:
        source = Source(**data.model_dump())
        self.db.add(source)
        await self.db.commit()
        await self.db.refresh(source)
        return SourceOut.model_validate(source)

    async def update(self, code: str, data: Dict[str, Any]) -> Optional[SourceOut]:
        result = await self.db.execute(
            update(Source).where(Source.code == code).values(**data).returning(Source)
        )
        await self.db.commit()
        source = result.scalar_one_or_none()
        return SourceOut.model_validate(source) if source else None

    async def toggle(self, code: str) -> Optional[SourceOut]:
        result = await self.db.execute(select(Source).where(Source.code == code))
        source = result.scalar_one_or_none()
        if not source:
            return None
        source.is_active = not source.is_active
        await self.db.commit()
        await self.db.refresh(source)
        return SourceOut.model_validate(source)


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(self, limit: int = 100) -> List[TaskOut]:
        result = await self.db.execute(
            select(ParserTask).order_by(desc(ParserTask.created_at)).limit(limit)
        )
        return [TaskOut.model_validate(t) for t in result.scalars().all()]

    async def list_by_parser(self, parser_name: str, limit: int = 50) -> List[TaskOut]:
        result = await self.db.execute(
            select(ParserTask)
            .where(ParserTask.parser_name == parser_name)
            .order_by(desc(ParserTask.created_at))
            .limit(limit)
        )
        return [TaskOut.model_validate(t) for t in result.scalars().all()]

    async def list_running(self, limit: int = 50) -> List[TaskOut]:
        result = await self.db.execute(
            select(ParserTask)
            .where(ParserTask.status == "running")
            .order_by(desc(ParserTask.created_at))
            .limit(limit)
        )
        return [TaskOut.model_validate(t) for t in result.scalars().all()]

    async def get(self, task_id: UUID) -> Optional[TaskOut]:
        result = await self.db.execute(select(ParserTask).where(ParserTask.id == task_id))
        task = result.scalar_one_or_none()
        return TaskOut.model_validate(task) if task else None

    async def create(self, data: TaskCreate, triggered_by: str = "admin") -> TaskOut:
        task = ParserTask(
            id=uuid4(),
            parser_name=data.parser_name,
            status="pending",
            date_from=_utc_naive(data.date_from),
            date_to=_utc_naive(data.date_to),
            filters=data.filters,
            max_items=data.max_items,
            triggered_by=triggered_by,
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        return TaskOut.model_validate(task)

    async def retry(self, task_id: UUID) -> Optional[TaskOut]:
        result = await self.db.execute(select(ParserTask).where(ParserTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return None
        task.status = "pending"
        task.retry_count += 1
        task.error_log = None
        task.started_at = None
        task.finished_at = None
        task.items_collected = 0
        task.items_new = 0
        await self.db.commit()
        await self.db.refresh(task)
        return TaskOut.model_validate(task)

    async def update_status(
        self, task_id: UUID, status: str, items_collected: int = 0, items_new: int = 0, error: Optional[str] = None
    ) -> None:
        values = {
            "status": status,
            "items_collected": items_collected,
            "items_new": items_new,
        }
        if status == "running":
            values["started_at"] = datetime.utcnow()
        if status in ("completed", "failed"):
            values["finished_at"] = datetime.utcnow()
        if error:
            values["error_log"] = error

        await self.db.execute(update(ParserTask).where(ParserTask.id == task_id).values(**values))
        await self.db.commit()


class LogService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list(
        self,
        parser_name: Optional[str] = None,
        status: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[LogOut]:
        query = select(ParserLog).order_by(desc(ParserLog.run_at))
        if parser_name:
            query = query.where(ParserLog.parser_name == parser_name)
        if status:
            query = query.where(ParserLog.status == status)
        if date_from:
            query = query.where(ParserLog.run_at >= date_from)
        if date_to:
            query = query.where(ParserLog.run_at <= date_to)
        result = await self.db.execute(query.limit(limit))
        return [LogOut.model_validate(l) for l in result.scalars().all()]

    async def create(
        self,
        parser_name: str,
        task_id: Optional[UUID] = None,
        status: str = "running",
        items_count: int = 0,
        errors_count: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> LogOut:
        log = ParserLog(
            parser_name=parser_name,
            task_id=task_id,
            status=status,
            items_count=items_count,
            errors_count=errors_count,
            details=details,
        )
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(log)
        return LogOut.model_validate(log)


class StatsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self) -> StatsOut:
        tables = [
            ("raw_items", RawItem),
            ("enriched_items", EnrichedItem),
            ("vectors", Vector),
            ("parser_tasks", ParserTask),
            ("parser_logs", ParserLog),
        ]
        stats = []
        total_raw = 0
        total_enriched = 0
        total_vectors = 0
        pending_tasks = 0
        failed_tasks = 0

        for name, model in tables:
            count_result = await self.db.execute(select(func.count()).select_from(model))
            count = count_result.scalar() or 0
            stats.append(TableStat(table_name=name, row_count=count, last_updated=datetime.utcnow()))

            if name == "raw_items":
                total_raw = count
            elif name == "enriched_items":
                total_enriched = count
            elif name == "vectors":
                total_vectors = count

        pending_result = await self.db.execute(
            select(func.count()).select_from(ParserTask).where(ParserTask.status == "pending")
        )
        pending_tasks = pending_result.scalar() or 0

        failed_result = await self.db.execute(
            select(func.count()).select_from(ParserTask).where(ParserTask.status == "failed")
        )
        failed_tasks = failed_result.scalar() or 0

        return StatsOut(
            tables=stats,
            total_raw=total_raw,
            total_enriched=total_enriched,
            total_vectors=total_vectors,
            pending_tasks=pending_tasks,
            failed_tasks=failed_tasks,
        )


class PipelineService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_status(self) -> PipelineStatus:
        nodes = []
        edges = []

        # Scheduler node
        scheduler_config = await self.db.execute(
            select(SchedulerConfig).where(SchedulerConfig.id == 1)
        )
        sc = scheduler_config.scalar_one_or_none()
        scheduler_status = "running" if sc and sc.enabled else "idle"
        nodes.append(
            PipelineNode(
                node_id="scheduler",
                label="Шедулер (48ч)",
                status=scheduler_status,
                count=sc.interval_hours if sc else 48,
                last_run=sc.last_run if sc else None,
            )
        )

        # Sources status
        sources_result = await self.db.execute(select(Source))
        sources = sources_result.scalars().all()
        for src in sources:
            last_task = await self.db.execute(
                select(ParserTask)
                .where(ParserTask.parser_name == src.code)
                .order_by(desc(ParserTask.started_at))
                .limit(1)
            )
            task = last_task.scalar_one_or_none()
            status = task.status if task else "idle"
            nodes.append(
                PipelineNode(
                    node_id=f"source_{src.code}",
                    label=src.name,
                    status=status,
                    count=0,
                    last_run=task.started_at if task else None,
                )
            )

        # Collect node
        collect_raw = await self.db.execute(
            select(func.count()).select_from(RawItem).where(RawItem.status == "raw")
        )
        collect_val = collect_raw.scalar() or 0
        nodes.append(
            PipelineNode(
                node_id="collect",
                label="Сбор данных",
                status="running" if collect_val > 0 else "idle",
                count=collect_val,
                last_run=None,
            )
        )

        # Dedup node
        parsed_raw = await self.db.execute(
            select(func.count()).select_from(RawItem).where(RawItem.status == "parsed")
        )
        parsed_val = parsed_raw.scalar() or 0
        nodes.append(
            PipelineNode(
                node_id="dedup",
                label="Дедупликация",
                status="running" if parsed_val > 0 else "idle",
                count=parsed_val,
                last_run=None,
            )
        )

        # LLM node
        enrich_raw = await self.db.execute(
            select(func.count()).select_from(EnrichedItem).where(EnrichedItem.processing_status == "pending")
        )
        enrich_val = enrich_raw.scalar() or 0
        nodes.append(
            PipelineNode(
                node_id="llm",
                label="LLM Обработка",
                status="running" if enrich_val > 0 else "idle",
                count=enrich_val,
                last_run=None,
            )
        )

        # Vector node
        vector_raw = await self.db.execute(select(func.count()).select_from(Vector))
        vector_val = vector_raw.scalar() or 0
        nodes.append(
            PipelineNode(
                node_id="vector",
                label="FAISS Индексация",
                status="running" if vector_val > 0 else "idle",
                count=vector_val,
                last_run=None,
            )
        )

        edges = [PipelineEdge(from_node="scheduler", to_node=f"source_{src.code}") for src in sources]
        edges.extend([PipelineEdge(from_node=f"source_{src.code}", to_node="collect") for src in sources])
        edges.extend(
            [
                PipelineEdge(from_node="collect", to_node="dedup"),
                PipelineEdge(from_node="dedup", to_node="llm"),
                PipelineEdge(from_node="llm", to_node="vector"),
            ]
        )

        return PipelineStatus(nodes=nodes, edges=edges)


class SchedulerService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_config(self) -> Optional[SchedulerConfigOut]:
        result = await self.db.execute(select(SchedulerConfig).where(SchedulerConfig.id == 1))
        config = result.scalar_one_or_none()
        if not config:
            return None
        return SchedulerConfigOut.model_validate(config)

    async def create_default(self) -> SchedulerConfigOut:
        config = SchedulerConfig(
            id=1,
            enabled=settings.scheduler_enabled,
            interval_hours=settings.scheduler_interval_hours,
            start_date=None,
        )
        self.db.add(config)
        await self.db.commit()
        await self.db.refresh(config)
        return SchedulerConfigOut.model_validate(config)

    async def update_config(self, data: SchedulerConfigUpdate) -> SchedulerConfigOut:
        result = await self.db.execute(select(SchedulerConfig).where(SchedulerConfig.id == 1))
        config = result.scalar_one_or_none()
        if not config:
            config = await self.create_default()
            result = await self.db.execute(select(SchedulerConfig).where(SchedulerConfig.id == 1))
            config = result.scalar_one_or_none()

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(config, key, value)

        if config.enabled and config.start_date is None:
            config.start_date = datetime.utcnow()

        if config.enabled and config.last_run is None:
            config.next_run = config.start_date or datetime.utcnow()

        await self.db.commit()
        await self.db.refresh(config)
        return SchedulerConfigOut.model_validate(config)

    async def update_last_run(self, last_run: datetime, next_run: datetime) -> None:
        await self.db.execute(
            update(SchedulerConfig)
            .where(SchedulerConfig.id == 1)
            .values(last_run=last_run, next_run=next_run)
        )
        await self.db.commit()

    async def ensure_config_exists(self) -> SchedulerConfigOut:
        config = await self.get_config()
        if not config:
            return await self.create_default()
        return config
