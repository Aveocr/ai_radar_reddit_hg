from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Source
from database.session import get_db
from admin.schemas import (
    SourceCreate, SourceOut, TaskCreate, HuggingFaceTaskCreate, TaskOut, TaskRetry,
    LogOut, LogFilter, StatsOut, PipelineStatus, RedditTaskCreate, ParserRunCreate,
    SchedulerConfigOut, SchedulerConfigUpdate
)
from admin.service import SourceService, TaskService, LogService, StatsService, PipelineService, SchedulerService
from parsers.engine import ParserEngine
from parsers.registry import get_parser_spec
from admin.auth import get_current_admin, create_session, destroy_session, _verify, _hash, ADMIN_COOKIE, SESSION_TTL

from llm.processor import LLMProcessor
from llm.client import LLMClient

from config import get_settings


settings = get_settings()
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ═══════════════════════════════════════════════════════
#  Auth endpoints (публичные)
# ═══════════════════════════════════════════════════════
@router.post("/login")
async def admin_login(request: Request):
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")

    if username != settings.admin_username:
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)
    if not _verify(password, _hash(settings.admin_password)):
        return JSONResponse({"error": "Invalid credentials"}, status_code=401)

    token = create_session()
    response = JSONResponse({"success": True})
    response.set_cookie(
        key=ADMIN_COOKIE,
        value=token,
        httponly=True,
        max_age=SESSION_TTL,
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request):
    destroy_session(request)
    response = JSONResponse({"success": True})
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


# ═══════════════════════════════════════════════════════
#  Protected endpoints
# ═══════════════════════════════════════════════════════

# ─── Sources ───
@router.get("/sources", response_model=List[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SourceService(db)
    return await svc.list()


@router.get("/sources/{code}", response_model=SourceOut)
async def get_source(code: str, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SourceService(db)
    source = await svc.get(code)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post("/sources", response_model=SourceOut)
async def create_source(data: SourceCreate, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SourceService(db)
    return await svc.create(data)


@router.patch("/sources/{code}", response_model=SourceOut)
async def update_source(code: str, data: Dict[str, Any], db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SourceService(db)
    source = await svc.update(code, data)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.post("/sources/{code}/toggle", response_model=SourceOut)
async def toggle_source(code: str, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SourceService(db)
    source = await svc.toggle(code)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


# ─── Tasks ───
@router.get("/tasks", response_model=List[TaskOut])
async def list_tasks(limit: int = 100, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    return await svc.list(limit)


@router.post("/tasks", response_model=TaskOut)
async def create_task(data: TaskCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    spec = get_parser_spec(data.parser_name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown parser: {data.parser_name}")
    if not spec.implemented:
        raise HTTPException(status_code=501, detail=f"Parser is registered but not implemented yet: {data.parser_name}")

    svc = TaskService(db)
    task = await svc.create(data, triggered_by="admin")
    engine = ParserEngine(db)
    background_tasks.add_task(engine.run_task, task.id)
    return task


@router.post("/tasks/huggingface", response_model=TaskOut)
async def create_huggingface_task(data: HuggingFaceTaskCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    task_data = TaskCreate(
        parser_name="huggingface",
        date_from=data.date_from,
        date_to=data.date_to,
        filters=data.filters,
        max_items=data.max_items,
    )
    task = await svc.create(task_data, triggered_by="admin")
    engine = ParserEngine(db)
    background_tasks.add_task(engine.run_task, task.id)
    return task


@router.post("/tasks/reddit", response_model=TaskOut)
async def create_reddit_task(data: RedditTaskCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    task_data = TaskCreate(
        parser_name="reddit",
        date_from=data.date_from,
        date_to=data.date_to,
        filters=data.filters,
        max_items=data.max_items,
    )
    task = await svc.create(task_data, triggered_by="admin")
    engine = ParserEngine(db)
    background_tasks.add_task(engine.run_task, task.id)
    return task


@router.post("/tasks/{parser_name}/run", response_model=TaskOut)
async def create_parser_task(parser_name: str, data: ParserRunCreate, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    spec = get_parser_spec(parser_name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown parser: {parser_name}")
    if not spec.implemented:
        raise HTTPException(status_code=501, detail=f"Parser is registered but not implemented yet: {parser_name}")

    svc = TaskService(db)
    task_data = TaskCreate(
        parser_name=parser_name,
        date_from=data.date_from,
        date_to=data.date_to,
        filters=data.filters,
        max_items=data.max_items,
    )
    task = await svc.create(task_data, triggered_by="admin")
    engine = ParserEngine(db)
    background_tasks.add_task(engine.run_task, task.id)
    return task


@router.get("/tasks/huggingface/active", response_model=List[TaskOut])
async def list_running_huggingface_tasks(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    tasks = await svc.list_by_parser("huggingface", limit=50)
    return [task for task in tasks if task.status == "running"]


@router.get("/tasks/{parser_name}/active", response_model=List[TaskOut])
async def list_running_parser_tasks(parser_name: str, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    spec = get_parser_spec(parser_name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown parser: {parser_name}")

    svc = TaskService(db)
    tasks = await svc.list_by_parser(parser_name, limit=50)
    return [task for task in tasks if task.status == "running"]


@router.get("/tasks/active", response_model=List[TaskOut])
async def list_running_tasks(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    return await svc.list_running()


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: UUID, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    task = await svc.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
async def retry_task(task_id: UUID, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = TaskService(db)
    task = await svc.retry(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    engine = ParserEngine(db)
    background_tasks.add_task(engine.run_task, task.id)
    return task


# ─── Logs ───
@router.get("/logs", response_model=List[LogOut])
async def list_logs(
    parser_name: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    svc = LogService(db)
    return await svc.list(parser_name, status, date_from, date_to, limit)


# ─── Stats ───
@router.get("/stats", response_model=StatsOut)
async def get_stats(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = StatsService(db)
    return await svc.get()


# ─── Pipeline / DAG ───
@router.get("/pipeline", response_model=PipelineStatus)
async def get_pipeline(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = PipelineService(db)
    return await svc.get_status()


# ─── LLM Processing ───
@router.post("/llm/enrich/{raw_item_id}")
async def enrich_item(
    raw_item_id: UUID,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    processor = LLMProcessor(db)
    result = await processor.process_item(raw_item_id)
    if not result:
        raise HTTPException(status_code=404, detail="Item not found or already processed")
    return {"status": "completed", "enriched_id": str(result.id)}


@router.post("/llm/enrich-batch")
async def enrich_batch(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    processor = LLMProcessor(db)
    count = await processor.process_pending(limit)
    return {"processed": count}


@router.get("/llm/status")
async def llm_status(admin: str = Depends(get_current_admin)):
    client = LLMClient()
    return {
        "model": client.model,
        "base_url": client.base_url,
        "api_key_configured": bool(client.api_key),
    }


# ─── LLM Summary Config ───
@router.get("/llm/summary-config")
async def get_llm_summary_config(
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    svc = SchedulerService(db)
    config = await svc.ensure_config_exists()
    return {"enabled": config.llm_summary_enabled}


@router.post("/llm/summary-config")
async def toggle_llm_summary_config(
    body: dict,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    svc = SchedulerService(db)
    config = await svc.update_config(SchedulerConfigUpdate(
        llm_summary_enabled=body.get("enabled", True),
    ))
    return {"enabled": config.llm_summary_enabled}


# ─── Scheduler ───
@router.get("/scheduler", response_model=SchedulerConfigOut)
async def get_scheduler_config(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SchedulerService(db)
    config = await svc.ensure_config_exists()
    return config


@router.patch("/scheduler", response_model=SchedulerConfigOut)
async def update_scheduler_config(
    data: SchedulerConfigUpdate,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    svc = SchedulerService(db)
    config = await svc.update_config(data)
    return config


@router.post("/scheduler/enable", response_model=SchedulerConfigOut)
async def enable_scheduler(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SchedulerService(db)
    config = await svc.update_config(SchedulerConfigUpdate(enabled=True))
    return config


@router.post("/scheduler/disable", response_model=SchedulerConfigOut)
async def disable_scheduler(db: AsyncSession = Depends(get_db), admin: str = Depends(get_current_admin)):
    svc = SchedulerService(db)
    config = await svc.update_config(SchedulerConfigUpdate(enabled=False))
    return config


@router.post("/scheduler/trigger", response_model=dict)
async def trigger_scheduler_run(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    svc = SchedulerService(db)
    config = await svc.ensure_config_exists()

    if not config.enabled:
        raise HTTPException(status_code=400, detail="Scheduler is disabled. Enable it first.")

    result = await db.execute(select(Source).where(Source.is_active == True))
    active_sources = result.scalars().all()

    if not active_sources:
        raise HTTPException(status_code=400, detail="No active sources found. Enable sources in the Sources tab first.")

    engine = ParserEngine(db)
    tasks_created = []

    for src in active_sources:
        parser_name = src.code
        spec = get_parser_spec(parser_name)
        if not spec or not spec.implemented:
            continue

        now = datetime.utcnow()
        task_data = TaskCreate(
            parser_name=parser_name,
            date_from=now - timedelta(hours=config.interval_hours),
            date_to=now,
            filters={},
            max_items=1000,
        )
        svc_task = TaskService(db)
        task = await svc_task.create(task_data, triggered_by="scheduler")
        background_tasks.add_task(engine.run_task, task.id)
        tasks_created.append({"parser": parser_name, "task_id": str(task.id)})

    return {"status": "triggered", "tasks": tasks_created}
