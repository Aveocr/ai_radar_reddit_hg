from typing import Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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


class AIFilterRequest(BaseModel):
    query: str
    top_k: int = 20


@router.post("/ai-filter")
async def ai_filter(req: AIFilterRequest):
    """Semantic search via FAISS. Returns matching model IDs for dashboard filtering."""
    manager = _get_index()
    if manager.is_empty:
        return {"ids": [], "results": [], "message": "FAISS index is empty. Run rebuild first."}

    try:
        provider = get_embedding_provider()
        query_vector = await provider.embed(req.query)
        results = manager.search(np.array(query_vector), k=req.top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search error: {str(exc)}")

    ids = []
    items = []
    for r in results:
        md = r.metadata
        eid = md.get("enriched_item_id", "")
        ids.append(eid)
        items.append({
            "id": eid,
            "title": md.get("title", ""),
            "category": md.get("category", ""),
            "summary_ru": md.get("summary_ru", ""),
            "score": round(r.score, 4),
        })

    return {"ids": ids, "results": items, "total": len(items)}


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
