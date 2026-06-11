import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Source, ParserTask, ParserLog, RawItem
from database.session import is_postgres
from admin.service import TaskService, LogService
from parsers.registry import get_parser


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    if hasattr(value, "__dict__"):
        return _json_safe(
            {
                key: item
                for key, item in value.__dict__.items()
                if not key.startswith("_")
            }
        )
    return str(value)


def _datetime_safe(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def _array_safe(value: Any) -> Any:
    if value is None:
        return None
    if is_postgres():
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


class ParserEngine:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.task_svc = TaskService(db)
        self.log_svc = LogService(db)

    async def run_task(self, task_id: UUID) -> None:
        result = await self.db.execute(select(ParserTask).where(ParserTask.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return

        parser = get_parser(task.parser_name)
        if not parser:
            await self.task_svc.update_status(task_id, "failed", error=f"Unknown parser: {task.parser_name}")
            return

        source_result = await self.db.execute(select(Source).where(Source.code == task.parser_name))
        source = source_result.scalar_one_or_none()
        if not source:
            await self.task_svc.update_status(task_id, "failed", error=f"Source not found: {task.parser_name}")
            return

        if not source.is_active:
            await self.task_svc.update_status(task_id, "failed", error=f"Source '{task.parser_name}' is disabled")
            return

        log = ParserLog(
            parser_name=task.parser_name,
            task_id=task_id,
            status="running",
        )
        self.db.add(log)
        await self.db.commit()
        await self.db.refresh(log)

        await self.task_svc.update_status(task_id, "running")

        try:
            raw_data = await parser.fetch(
                date_from=task.date_from,
                date_to=task.date_to,
                filters=task.filters,
                max_items=task.max_items or 1000,
                task_id=task.id,
            )

            items_collected = len(raw_data)
            items_new = 0

            for item in raw_data:
                normalized = parser.normalize(item)
                if not all(normalized.get(key) for key in ("external_id", "title", "url")):
                    continue
                item_hash = hashlib.sha256(
                    f"{normalized['url']}:{normalized['external_id']}".encode()
                ).hexdigest()

                dup_check = await self.db.execute(
                    select(RawItem).where(RawItem.hash == item_hash)
                )
                if dup_check.scalar_one_or_none():
                    continue

                raw_item = RawItem(
                    source_id=source.id,
                    task_id=task_id,
                    external_id=normalized["external_id"],
                    title=normalized["title"],
                    model_type=normalized.get("model_type"),
                    domain=_array_safe(normalized.get("domain")),
                    description=normalized.get("description"),
                    url=normalized["url"],
                    author=normalized.get("author"),
                    license=normalized.get("license"),
                    tags=_array_safe(normalized.get("tags")),
                    popularity_metric=normalized.get("popularity_metric"),
                    created_at_source=_datetime_safe(normalized.get("created_at_source")),
                    updated_at_source=_datetime_safe(normalized.get("updated_at_source")),
                    language=_array_safe(normalized.get("language")),
                    framework=_array_safe(normalized.get("framework")),
                    task_type=_array_safe(normalized.get("task_type")),
                    raw_json=_json_safe(normalized),
                    hash=item_hash,
                    status=normalized.get("status") or "raw",
                )
                self.db.add(raw_item)
                items_new += 1

            await self.db.commit()

            log.status = "completed"
            log.items_count = items_collected
            log.duration_sec = int((datetime.utcnow() - log.run_at).total_seconds())
            await self.db.commit()

            await self.task_svc.update_status(
                task_id, "completed", items_collected=items_collected, items_new=items_new
            )

            if items_new > 0:
                from vector.scheduler import flag_rebuild_needed
                flag_rebuild_needed()

        except Exception as e:
            await self.db.rollback()
            log.status = "failed"
            log.errors_count = 1
            log.details = {"error": str(e)}
            await self.db.commit()
            await self.task_svc.update_status(task_id, "failed", error=str(e))
