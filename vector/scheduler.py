"""
AI Radar — FAISS Auto-Rebuild Scheduler
Runs in background, rebuilds FAISS index when new data is available
during low-traffic window (19:00-04:00 MSK).
"""

import asyncio
import os
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from database.models import EnrichedItem
from config import get_settings
from vector.index_builder import rebuild_index

REBUILD_FLAG = "data/.rebuild_needed"
REBUILD_LOCK = asyncio.Lock()
_MSK = timezone(timedelta(hours=3))


def _in_rebuild_window() -> bool:
    now_msk = datetime.now(_MSK)
    hour = now_msk.hour
    return hour >= 19 or hour < 4


def flag_rebuild_needed():
    """Mark that rebuild is needed (called after parser completes)."""
    os.makedirs(os.path.dirname(REBUILD_FLAG) or ".", exist_ok=True)
    with open(REBUILD_FLAG, "w") as f:
        f.write(str(time.time()))


def _is_rebuild_needed() -> bool:
    return os.path.exists(REBUILD_FLAG)


def _clear_rebuild_flag():
    if os.path.exists(REBUILD_FLAG):
        os.remove(REBUILD_FLAG)


async def _count_enriched_items(db_session) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(EnrichedItem).where(
            EnrichedItem.processing_status == "completed"
        )
    )
    return result.scalar() or 0


async def check_and_rebuild(db_session) -> bool:
    """Check if rebuild is needed and run it. Returns True if rebuild ran."""
    from vector.router import get_index_manager

    manager = get_index_manager()
    db_count = await _count_enriched_items(db_session)
    if db_count == 0:
        return False

    faiss_size = manager.size if not manager.is_empty else 0
    has_new_data = db_count > faiss_size
    flag_present = _is_rebuild_needed()

    if not has_new_data and not flag_present:
        return False

    if not _in_rebuild_window():
        return False

    async with REBUILD_LOCK:
        print(f"[FAISS-SCHED] Rebuilding: {db_count} items in DB, {faiss_size} in FAISS")
        try:
            count = await rebuild_index(db_session)
            _clear_rebuild_flag()
            from vector.router import _index_manager
            settings = get_settings()
            from vector.faiss_index import FaissIndexManager
            _index_manager = FaissIndexManager(dim=settings.embedding_dim)
            _index_manager.load()
            print(f"[FAISS-SCHED] Rebuild complete: {count} items indexed")
            return True
        except Exception as exc:
            print(f"[FAISS-SCHED] Rebuild failed: {exc}")
            return False


async def scheduler_loop(interval_seconds: int = 3600):
    """Background loop: periodically checks and rebuilds FAISS index."""
    from database.session import _ensure_session_factory

    print("[FAISS-SCHED] Background scheduler started")
    while True:
        try:
            factory = _ensure_session_factory()
            async with factory() as session:
                await check_and_rebuild(session)
        except Exception as exc:
            print(f"[FAISS-SCHED] Error: {exc}")

        await asyncio.sleep(interval_seconds)
