from typing import Optional

import numpy as np
from fastapi import APIRouter, HTTPException

from config import get_settings
from vector.embeddings import get_embedding_provider
from vector.faiss_index import FaissIndexManager

router = APIRouter(prefix="/api", tags=["ai"])

_index_manager: Optional[FaissIndexManager] = None


def _get_index():
    global _index_manager
    if _index_manager is None:
        settings = get_settings()
        _index_manager = FaissIndexManager(dim=settings.embedding_dim)
        _index_manager.load()
    return _index_manager


@router.post("/ai-filter")
async def ai_filter(query: str):
    return {"message": f"AI filter would process: {query}", "sql_condition": "1=1"}


@router.post("/vector-search")
async def vector_search(model_id: str, k: int = 10):
    """Find similar items by model ID using FAISS vector search."""
    manager = _get_index()
    if manager.is_empty:
        return {"message": "FAISS index is empty. Run rebuild first.", "similar_ids": []}

    from sqlalchemy import create_engine, text

    settings = get_settings()
    try:
        db_url = settings.database_url
        for prefix in ["+asyncpg", "+aiosqlite"]:
            db_url = db_url.replace(prefix, "")
        engine = create_engine(db_url)
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT id FROM enriched_items WHERE raw_item_id = :rid OR id = :rid"),
                {"rid": model_id},
            )
            row = result.fetchone()
            engine.dispose()

            if not row:
                return {"message": "Item not found in enriched data", "similar_ids": []}

            enriched_id = str(row[0])
    except Exception as exc:
        return {"message": f"Database lookup error: {str(exc)}", "similar_ids": []}

    md = manager.get_metadata_by_enriched_id(enriched_id)
    if md is None:
        return {"message": "Item not found in FAISS index", "similar_ids": []}

    if manager.index and manager.index.ntotal > 0 and md.faiss_id < manager.index.ntotal:
        query_vector = manager.index.reconstruct(md.faiss_id).reshape(1, -1).copy()
    else:
        return {"message": "Item vector not found in index", "similar_ids": []}

    results = manager.search(query_vector, k=k + 1)

    similar_ids = []
    for r in results:
        if r.id == model_id or r.id == enriched_id:
            continue
        similar_ids.append(r.id)
        if len(similar_ids) >= k:
            break

    return {"similar_ids": similar_ids, "message": f"Found {len(similar_ids)} similar items"}
