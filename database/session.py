"""
AI Radar — Database Session Manager
Supports PostgreSQL (production) and SQLite (demo/fallback).
NO engine creation at import time — only on first HTTP request.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from config import get_settings

settings = get_settings()

_engine = None
_session_factory = None
_db_type = None


def _init_postgres():
    return create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
    )


def _init_sqlite():
    from database.fallback import init_sqlite
    sqlite_path = init_sqlite()
    # Создаём движок для aiosqlite
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{sqlite_path}",
        echo=settings.debug,
    )
    return engine


def _ensure_engine():
    """Create engine if not exists. Called ONLY from get_db() or lifespan."""
    global _engine, _db_type
    if _engine is not None:
        return _engine

    if not settings.database_url.startswith("postgresql"):
        _engine = _init_sqlite()
        _db_type = "sqlite"
        print(f"[DB] SQLite engine created ({settings.database_url})")
        return _engine

    try:
        _engine = _init_postgres()
        _db_type = "postgres"
        print("[DB] PostgreSQL engine created")
    except Exception as e:
        print(f"[DB] PostgreSQL failed: {type(e).__name__}")
        print("[DB] Switching to SQLite (demo mode)")
        _engine = _init_sqlite()
        _db_type = "sqlite"

    return _engine


def _ensure_session_factory():
    """Create session factory if not exists."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    engine = _ensure_engine()
    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _session_factory


async def init_engine_for_app():
    """Initialize the best available database engine for FastAPI startup."""
    global _engine, _session_factory, _db_type

    # If URL is not PostgreSQL, use SQLite directly
    if not settings.database_url.startswith("postgresql"):
        _engine = _init_sqlite()
        _db_type = "sqlite"
        print(f"[DB] SQLite engine created ({settings.database_url})")
    else:
        try:
            if _engine is None or _db_type != "postgres":
                _engine = _init_postgres()
                _db_type = "postgres"
            async with _engine.connect():
                pass
            print("[DB] PostgreSQL connection verified")
        except Exception as exc:
            print(f"[DB] PostgreSQL startup failed: {type(exc).__name__}")
            print("[DB] Switching to SQLite (demo mode)")
            if _engine is not None:
                await _engine.dispose()
            _engine = _init_sqlite()
            _db_type = "sqlite"

    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields async DB session.
    Engine is created on first HTTP request, NOT at import."""
    global _engine, _session_factory, _db_type

    factory = _ensure_session_factory()

    if _db_type == "postgres" and _engine is not None:
        try:
            async with _engine.connect() as conn:
                pass
        except Exception as exc:
            print(f"[DB] PostgreSQL connect failed: {type(exc).__name__}")
            print("[DB] Switching to SQLite (demo mode)")
            _engine = _init_sqlite()
            _db_type = "sqlite"
            _session_factory = async_sessionmaker(
                _engine,
                class_=AsyncSession,
                expire_on_commit=False,
                autoflush=False,
            )
            factory = _session_factory

    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()


def get_engine_for_lifespan():
    """Called from lifespan context manager only."""
    return _ensure_engine()


def is_postgres() -> bool:
    return _db_type == "postgres"
