"""
AI Radar — Vector & Chat Router
Endpoints for semantic search, similar items, LLM chat, and index management.
"""

import asyncio
import os
from typing import Optional

import faiss
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import get_current_admin
from config import get_settings
from database.models import EnrichedItem, RawItem
from database.session import get_db
from llm.client import LLMClient
from vector.embeddings import get_embedding_provider
from vector.faiss_index import FaissIndexManager
from vector.index_builder import rebuild_index
from vector.schemas import (
    ChatRequest,
    ChatResponse,
    IndexInfo,
    RebuildResponse,
    SimilarRequest,
    VectorConfigInfo,
    VectorConfigUpdate,
    VectorSearchRequest,
    VectorSearchResponse,
    VectorSearchResult,
)

router = APIRouter(prefix="/api/v1/vector", tags=["vector"])

# Lock for rebuild to prevent concurrent execution
_rebuild_lock = asyncio.Lock()

# Singleton index manager — lazy loaded
_index_manager: Optional[FaissIndexManager] = None


def get_index_manager() -> FaissIndexManager:
    global _index_manager
    if _index_manager is None:
        settings = get_settings()
        _index_manager = FaissIndexManager(dim=settings.embedding_dim)
        _index_manager.load()
    return _index_manager


VECTOR_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "vector_search",
        "description": "Поиск AI-моделей в базе знаний AI Radar по семантическому сходству. Возвращает описание, категорию, технологии и сценарии использования.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос на естественном языке (например: 'модели для сегментации изображений на мобильных устройствах')",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Количество результатов (максимум 20, по умолчанию 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


async def _execute_vector_search(query: str = "", top_k: int = 5):
    manager = get_index_manager()
    if manager.is_empty:
        return {"results": [], "message": "FAISS index is empty"}
    try:
        provider = get_embedding_provider()
        query_vector = await provider.embed(query)
        results = manager.search(np.array(query_vector), k=min(top_k, 20))
        items = []
        for r in results:
            md = r.metadata
            items.append({
                "id": md.get("enriched_item_id", ""),
                "title": md.get("title", ""),
                "category": md.get("category", ""),
                "summary_ru": md.get("summary_ru", ""),
                "tech_stack": md.get("tech_stack", []),
                "use_cases": md.get("use_cases", []),
                "source_name": md.get("source_name", ""),
                "score": round(r.score, 4),
            })
        return {"results": items, "total": len(items)}
    except Exception as exc:
        return {"results": [], "error": str(exc)}


async def _tool_executor(name: str, args: dict):
    if name == "vector_search":
        return await _execute_vector_search(**args)
    return {"error": f"Unknown tool: {name}"}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Chat with LLM, optionally using FAISS context via function calling."""
    settings = get_settings()
    llm = LLMClient(
        api_key=settings.llm_api_key or None,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
    )

    system_prompt = (
        "Ты — AI-ассистент системы мониторинга AI-инноваций AI Radar. "
        "Отвечай на русском языке. "
        "У тебя есть функция vector_search — используй её когда пользователь спрашивает про AI-модели, "
        "просит найти что-то, сравнить, или получить информацию из базы знаний. "
        "После получения результатов дай краткую сводку: что найдено, какие категории, "
        "выдели топ-3 наиболее релевантные модели."
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in request.history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": request.message})

    try:
        reply = await llm.chat_with_tools(
            messages,
            tools=[VECTOR_SEARCH_TOOL],
            tool_executor=_tool_executor,
            temperature=0.7,
            max_tokens=4000,
        )
        return ChatResponse(reply=reply)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM API error: {str(exc)}")


@router.post("/search", response_model=VectorSearchResponse)
async def search(
    request: VectorSearchRequest,
):
    """Semantic search by query text."""
    manager = get_index_manager()
    if manager.is_empty:
        return VectorSearchResponse(results=[])

    try:
        provider = get_embedding_provider()
        query_vector = await provider.embed(request.query)
        results = manager.search(np.array(query_vector), k=request.k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search error: {str(exc)}")

    items = []
    for r in results:
        md = r.metadata
        items.append(VectorSearchResult(
            id=md.get("item_id", ""),
            title=md.get("title", ""),
            category=md.get("category", ""),
            summary_ru=md.get("summary_ru", ""),
            tech_stack=md.get("tech_stack", []),
            use_cases=md.get("use_cases", []),
            source_name=md.get("source_name", ""),
            score=r.score,
        ))

    return VectorSearchResponse(results=items)


@router.post("/similar", response_model=VectorSearchResponse)
async def similar(
    request: SimilarRequest,
    db: AsyncSession = Depends(get_db),
):
    """Find similar items by model ID."""
    manager = get_index_manager()
    if manager.is_empty:
        return VectorSearchResponse(results=[])

    # First, try to find by enriched_item_id
    md = manager.get_metadata_by_enriched_id(request.model_id)

    # If not found, try to find by raw item ID — lookup enriched ID from DB
    if md is None:
        result = await db.execute(
            select(EnrichedItem).where(EnrichedItem.raw_item_id == request.model_id)
        )
        enriched = result.scalar_one_or_none()
        if enriched:
            md = manager.get_metadata_by_enriched_id(str(enriched.id))

    if md is None:
        raise HTTPException(status_code=404, detail="Item not found in index")

    # Get the vector from the index using reconstruct for single vector (O(1) instead of O(n))
    if not (manager.index and manager.index.ntotal > 0 and md.faiss_id < manager.index.ntotal):
        raise HTTPException(status_code=404, detail="Item vector not found in index")

    query_vector = manager.index.reconstruct(md.faiss_id).reshape(1, -1).copy()

    # Search
    results = manager.search(query_vector, k=request.k + 1)

    # Filter out the query item itself
    items = []
    for r in results:
        if r.id == request.model_id or r.id == str(md.enriched_item_id):
            continue
        md_data = r.metadata
        items.append(VectorSearchResult(
            id=md_data.get("item_id", ""),
            title=md_data.get("title", ""),
            category=md_data.get("category", ""),
            summary_ru=md_data.get("summary_ru", ""),
            tech_stack=md_data.get("tech_stack", []),
            use_cases=md_data.get("use_cases", []),
            source_name=md_data.get("source_name", ""),
            score=r.score,
        ))
        if len(items) >= request.k:
            break

    return VectorSearchResponse(results=items)


@router.post("/rebuild", response_model=RebuildResponse)
async def rebuild(
    db: AsyncSession = Depends(get_db),
    admin: str = Depends(get_current_admin),
):
    """Rebuild the FAISS index from database. Requires admin."""
    async with _rebuild_lock:
        try:
            count = await rebuild_index(db)
            global _index_manager
            settings = get_settings()
            _index_manager = FaissIndexManager(dim=settings.embedding_dim)
            _index_manager.load()
            return RebuildResponse(indexed_count=count)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Rebuild failed: {str(exc)}")


@router.get("/index-info", response_model=IndexInfo)
async def index_info(
    db: AsyncSession = Depends(get_db),
):
    """Get FAISS index status with DB item count."""
    manager = get_index_manager()
    from sqlalchemy import select, func
    from database.models import EnrichedItem
    result = await db.execute(
        select(func.count()).select_from(EnrichedItem).where(
            EnrichedItem.processing_status == "completed"
        )
    )
    db_count = result.scalar() or 0
    return IndexInfo(
        size=manager.size,
        dim=manager.dim,
        loaded=not manager.is_empty,
        db_count=db_count,
    )


@router.get("/config", response_model=VectorConfigInfo)
async def get_config():
    """Get current vector/LLM configuration."""
    settings = get_settings()
    manager = get_index_manager()
    return VectorConfigInfo(
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        gigachat_configured=bool(settings.gigachat_client_id and settings.gigachat_client_secret),
        llm_configured=bool(settings.llm_api_key),
        llm_model=settings.llm_model,
        faiss_index_size=manager.size,
    )


@router.post("/config", response_model=VectorConfigInfo)
async def update_config(config: VectorConfigUpdate):
    """Update vector/LLM configuration in .env file."""
    settings = get_settings()
    env_path = ".env"

    updates = {
        "EMBEDDING_PROVIDER": config.embedding_provider,
        "EMBEDDING_MODEL": config.embedding_model,
        "LLM_API_KEY": config.llm_api_key or settings.llm_api_key,
        "LLM_MODEL": config.llm_model,
        "LLM_BASE_URL": config.llm_base_url,
    }
    if config.gigachat_client_id is not None:
        updates["GIGACHAT_CLIENT_ID"] = config.gigachat_client_id
    if config.gigachat_client_secret is not None:
        updates["GIGACHAT_CLIENT_SECRET"] = config.gigachat_client_secret

    try:
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            lines = []

        existing_keys = set()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            existing_keys.add(key)
            if key in updates:
                lines[i] = f"{key}={updates[key]}\n"

        for key, value in updates.items():
            if key not in existing_keys:
                lines.append(f"{key}={value}\n")

        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        # Reload settings
        get_settings.cache_clear()

        # Recreate index manager with new dim if needed
        global _index_manager
        _index_manager = None

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Config update failed: {str(exc)}")

    return await get_config()
