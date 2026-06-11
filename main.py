#!/usr/bin/env python3
"""
AI Radar — Main Application Entry Point
"""

import asyncio
import uvicorn
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from starlette.middleware.base import BaseHTTPMiddleware

from admin.router import router as admin_router
from user.router import router as user_router
from admin.auth import verify_session
from static.dashboard.main import app as dashboard_app
from database.base import Base
from database import models as db_models
from database.session import init_engine_for_app, is_postgres
from database.bootstrap import seed_default_sources
from admin.service import SchedulerService
from parsers.scheduler import BackgroundScheduler
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from vector.router import router as vector_router, get_index_manager


_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    engine = await init_engine_for_app()
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create tables for both PostgreSQL and SQLite fallback
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migration: add llm_summary_enabled to existing scheduler_config tables
        try:
            await conn.execute(
                sa.text("ALTER TABLE scheduler_config ADD COLUMN llm_summary_enabled BOOLEAN DEFAULT TRUE")
            )
        except Exception:
            pass  # column already exists

    async with session_factory() as session:
        await seed_default_sources(session)

    app.state.http_client = httpx.AsyncClient(timeout=10.0)

    _scheduler = BackgroundScheduler(session_factory)
    app.state.scheduler = _scheduler

    async with session_factory() as session:
        svc = SchedulerService(session)
        await svc.ensure_config_exists()

    if _scheduler:
        asyncio.create_task(_scheduler.start())

    get_index_manager()

    from vector.embeddings import get_embedding_provider
    embed_provider = get_embedding_provider()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, embed_provider._load_model)
    print("[Model] Embedding model loaded")

    from vector.scheduler import scheduler_loop
    scheduler_task = asyncio.create_task(scheduler_loop(), name="faiss-scheduler")

    yield

    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass

    from vector.router import _index_manager
    if _index_manager is not None:
        try:
            _index_manager.save()
        except Exception:
            pass

    if _scheduler:
        await _scheduler.stop()

    await app.state.http_client.aclose()
    await engine.dispose()


app = FastAPI(
    title="AI Radar",
    description="Automated AI innovation monitoring system",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════
#  Admin Auth Middleware — перехватывает ВСЕ /admin запросы
#  до попадания в роуты. Работает для static тоже.
# ═══════════════════════════════════════════════════════
class AdminAuthMiddleware(BaseHTTPMiddleware):
    PUBLIC_PATHS = {"/admin/login", "/admin/login.html", "/admin/static/"}
    PROTECTED_PREFIXES = ("/admin",)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Не /admin — пропускаем
        if not path.startswith(self.PROTECTED_PREFIXES):
            return await call_next(request)

        # Публичные пути — пропускаем
        if any(path.startswith(p) for p in self.PUBLIC_PATHS):
            return await call_next(request)

        # Проверяем сессию
        try:
            verify_session(request)
        except Exception:
            # API запросы — 401, HTML — редирект
            is_api = (
                request.headers.get("accept", "").startswith("application/json")
                or request.headers.get("x-requested-with") == "XMLHttpRequest"
            )
            if is_api:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url="/admin/login", status_code=303)

        return await call_next(request)


# Middleware ДО static mounts и роутов
app.add_middleware(AdminAuthMiddleware)


# ═══════════════════════════════════════════════════════
#  Static files — после middleware!
# ═══════════════════════════════════════════════════════
app.mount("/app/static", StaticFiles(directory="static/app/static"), name="app_static")
app.mount("/dashboard", dashboard_app, name="dashboard")

# Admin static — через кастомный handler с проверкой
class ProtectedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        request = Request(scope)
        # Проверяем сессию для всего кроме login страницы
        # Нормализуем путь для кроссплатформенности (Windows использует \)
        norm_path = path.replace("\\", "/")
        if not norm_path.startswith("login") and not norm_path.startswith("js/login"):
            try:
                verify_session(request)
            except Exception:
                return RedirectResponse(url="/admin/login", status_code=303)
        return await super().get_response(path, scope)

app.mount("/admin/static", ProtectedStaticFiles(directory="static/admin/static"), name="admin_static")


# ═══════════════════════════════════════════════════════
#  Favicon
# ═══════════════════════════════════════════════════════
@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")


# ═══════════════════════════════════════════════════════
#  App pages (public)
# ═══════════════════════════════════════════════════════
@app.get("/app", response_class=FileResponse)
async def app_page():
    return FileResponse("static/app/index.html")

@app.get("/app/{path:path}")
async def app_spa(path: str):
    return FileResponse("static/app/index.html")


# ═══════════════════════════════════════════════════════
#  Admin login page (public)
# ═══════════════════════════════════════════════════════
@app.get("/admin/login")
async def admin_login_page():
    return FileResponse("static/admin/login.html")


# ═══════════════════════════════════════════════════════
#  Admin SPA — middleware уже проверил сессию,
#  здесь просто отдаём HTML
# ═══════════════════════════════════════════════════════
@app.get("/admin")
@app.get("/admin/{path:path}")
async def admin_spa(request: Request, path: str = ""):
    # Middleware уже проверил сессию, но на всякий случай
    try:
        verify_session(request)
    except Exception:
        return RedirectResponse(url="/admin/login", status_code=303)
    return FileResponse("static/admin/index.html")


# ═══════════════════════════════════════════════════════
#  API routes
# ═══════════════════════════════════════════════════════
app.include_router(admin_router)
app.include_router(user_router)
app.include_router(vector_router)


@app.get("/")
async def root():
    return RedirectResponse(url="/app", status_code=302)


@app.get("/docs", include_in_schema=False)
async def get_documentation(request: Request, _=Depends(verify_session)):
    """Защищенный доступ к документации Swagger UI."""
    return get_swagger_ui_html(openapi_url="/openapi.json", title="AI Radar API")


@app.get("/openapi.json", include_in_schema=False)
async def openapi(request: Request, _=Depends(verify_session)):
    """Защищенный доступ к OpenAPI спецификации."""
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "ai-radar",
        "version": "1.0.0",
        "database": "postgresql" if is_postgres() else "Error db server",
    }


if __name__ == "__main__":
    print("=" * 55)
    print("  AI RADAR — Starting Server")
    print("=" * 55)
    print("URLs:")
    print("Main app:   http://localhost:8000/app")
    print("Admin:      http://localhost:8000/admin")
    print("API Docs:   http://localhost:8000/docs")
    print("Health:     http://localhost:8000/health")
    print("Press Ctrl+C to stop")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
