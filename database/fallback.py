"""
AI Radar — Database Fallback (SQLite for demo mode)
Used when PostgreSQL is not available.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "ai_radar.db"
# ASYNC_DB_PATH = Path(__file__).parent.parent / "data" / "ai_radar_async.db"


def init_sqlite():
    """Initialize SQLite with same schema structure."""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Create tables matching PostgreSQL schema
    tables = [
        """
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            api_base_url TEXT,
            api_doc_url TEXT,
            auth_type TEXT,
            rate_limit TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_items (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            task_id TEXT,
            external_id TEXT NOT NULL,
            title TEXT NOT NULL,
            model_type TEXT,
            domain TEXT,
            description TEXT,
            url TEXT NOT NULL,
            author TEXT,
            license TEXT,
            tags TEXT,
            popularity_metric INTEGER,
            created_at_source TEXT,
            updated_at_source TEXT,
            language TEXT,
            framework TEXT,
            task_type TEXT,
            raw_json TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'raw',
            hash TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parser_tasks (
            id TEXT PRIMARY KEY,
            parser_name TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            date_from TEXT NOT NULL,
            date_to TEXT NOT NULL,
            items_collected INTEGER DEFAULT 0,
            items_new INTEGER DEFAULT 0,
            started_at TEXT,
            finished_at TEXT,
            error_log TEXT,
            retry_count INTEGER DEFAULT 0,
            triggered_by TEXT DEFAULT 'admin',
            filters TEXT,
            max_items INTEGER DEFAULT 1000,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS parser_logs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            parser_name TEXT NOT NULL,
            run_at TEXT DEFAULT CURRENT_TIMESTAMP,
            duration_sec INTEGER,
            items_count INTEGER DEFAULT 0,
            errors_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running',
            details TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS enriched_items (
            id TEXT PRIMARY KEY,
            raw_item_id TEXT NOT NULL,
            summary_en TEXT,
            summary_ru TEXT,
            category TEXT,
            subcategories TEXT,
            tech_stack TEXT,
            use_cases TEXT,
            relevance_score REAL,
            language_confirmed TEXT,
            model_size TEXT,
            benchmarks TEXT,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            llm_model TEXT,
            processing_status TEXT DEFAULT 'pending',
            error_message TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS vectors (
            id TEXT PRIMARY KEY,
            enriched_item_id TEXT NOT NULL,
            faiss_index_id INTEGER NOT NULL,
            embedding_model TEXT NOT NULL,
            vector_dim INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            display_name TEXT,
            email TEXT,
            email_notifications INTEGER DEFAULT 1,
            digest_frequency TEXT DEFAULT 'daily',
            onboarding_completed INTEGER DEFAULT 0,
            settings TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_interests (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, category)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_activity_logs (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            action TEXT NOT NULL,
            session_duration_sec INTEGER,
            meta TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS scheduler_config (
            id INTEGER PRIMARY KEY DEFAULT 1,
            enabled INTEGER DEFAULT 0,
            interval_hours INTEGER DEFAULT 48,
            start_date TEXT,
            last_run TEXT,
            next_run TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]

    for sql in tables:
        conn.execute(sql)

    _ensure_sqlite_schema(conn)

    # Insert default sources if empty
    cursor = conn.execute("SELECT COUNT(*) FROM sources")
    if cursor.fetchone()[0] == 0:
        default_sources = [
            ("huggingface", "HuggingFace", "huggingface", "https://huggingface.co/api", "https://huggingface.co/docs/api", "token_header", '{"rpm": 100}', 1),
            ("reddit", "Reddit", "reddit", "https://oauth.reddit.com", "https://www.reddit.com/dev/api", "oauth2", '{"rpm": 60}', 1),
            ("src_gh", "GitHub", "github", "https://api.github.com", "https://docs.github.com/en/rest", "token_header", '{"rpm": 30}', 1),
            ("src_arx", "arXiv", "arxiv", "http://export.arxiv.org/api", "https://arxiv.org/help/api", "none", '{"rpm": 20}', 1),
            ("src_pypi", "PyPI", "pypi", "https://pypi.org/pypi", "https://docs.pypi.org/api", "none", '{"rpm": 60}', 1),
            ("src_web", "Web/Reddit", "web", "", "", "none", '{"rpm": 30}', 1),
        ]
        conn.executemany(
            "INSERT INTO sources (id, name, code, api_base_url, api_doc_url, auth_type, rate_limit, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            default_sources
        )

    conn.commit()
    conn.close()
    return str(DB_PATH)


def _ensure_sqlite_schema(conn):
    """Ensure compatibility for older SQLite DB files."""
    existing = [row[1] for row in conn.execute("PRAGMA table_info(parser_tasks)")]
    if "created_at" not in existing:
        conn.execute(
            "ALTER TABLE parser_tasks ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP"
        )
    if "max_items" not in existing:
        conn.execute(
            "ALTER TABLE parser_tasks ADD COLUMN max_items INTEGER DEFAULT 1000"
        )


@contextmanager
def get_sqlite_conn():
    """Get SQLite connection context manager."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
