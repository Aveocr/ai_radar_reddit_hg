"""
AI Radar — Background Scheduler.

Запускает парсеры каждые interval_hours в фоне FastAPI.
Управляется через SchedulerConfig в БД (вкл/выкл, start_date, interval).
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sqlalchemy import select

from database.models import Source
from admin.service import TaskService, SchedulerService
from admin.schemas import TaskCreate, SchedulerConfigOut
from parsers.engine import ParserEngine
from parsers.registry import get_parser_spec

logger = logging.getLogger("ai_radar.scheduler")


class BackgroundScheduler:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._spawned: set[asyncio.Task] = set()
        self._shutdown = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self):
        if self.running:
            logger.warning("[SCHEDULER] Already running, ignoring start")
            return
        self._shutdown.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[SCHEDULER] Started background loop")

    async def _track(self, coro):
        task = asyncio.create_task(coro)
        self._spawned.add(task)
        task.add_done_callback(self._spawned.discard)
        return task

    async def _run_task_with_session(self, task_id):
        async with self._session_factory() as task_db:
            eng = ParserEngine(task_db)
            await eng.run_task(task_id)

    async def stop(self):
        self._shutdown.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._spawned:
            logger.info("[SCHEDULER] Waiting for %d spawned tasks...", len(self._spawned))
            for t in list(self._spawned):
                t.cancel()
            await asyncio.gather(*self._spawned, return_exceptions=True)
            self._spawned.clear()
        logger.info("[SCHEDULER] Stopped")

    async def _run_loop(self):
        while not self._shutdown.is_set():
            try:
                async with self._session_factory() as db:
                    await self._check_and_run(db)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[SCHEDULER] Loop error")

            await asyncio.sleep(60)

    async def _check_and_run(self, db: AsyncSession):
        svc = SchedulerService(db)
        config = await svc.get_config()

        if not config or not config.enabled:
            await db.commit()
            return

        now = datetime.utcnow()
        next_run = config.next_run

        if next_run is None:
            next_run = config.start_date or now
            await svc.update_last_run(None, next_run)

        if next_run and now >= next_run:
            logger.info("[SCHEDULER] Triggering cycle...")
            await self._run_cycle(db, config)

    async def _run_cycle(self, db: AsyncSession, config: SchedulerConfigOut):
        now = datetime.utcnow()
        interval = timedelta(hours=config.interval_hours)

        result = await db.execute(select(Source).where(Source.is_active == True))
        active_sources = result.scalars().all()

        if not active_sources:
            logger.warning("[SCHEDULER] No active sources found, skipping cycle")
            next_run = now + interval
            await SchedulerService(db).update_last_run(now, next_run)
            return

        task_svc = TaskService(db)
        svc = SchedulerService(db)

        total_created = 0
        for src in active_sources:
            parser_name = src.code
            spec = get_parser_spec(parser_name)
            if not spec or not spec.implemented:
                continue

            try:
                task_data = TaskCreate(
                    parser_name=parser_name,
                    date_from=now - interval,
                    date_to=now,
                    filters={},
                    max_items=1000,
                )
                task = await task_svc.create(task_data, triggered_by="scheduler")

                await self._track(self._run_task_with_session(task.id))
                total_created += 1
                logger.info("[SCHEDULER] Created task for %s (id=%s)", parser_name, task.id)
            except Exception as e:
                logger.error("[SCHEDULER] Failed to create task for %s: %s", parser_name, e)

        next_run = now + interval
        await svc.update_last_run(now, next_run)
        logger.info(
            "[SCHEDULER] Cycle complete: %d tasks created, next run at %s",
            total_created,
            next_run.isoformat(),
        )
