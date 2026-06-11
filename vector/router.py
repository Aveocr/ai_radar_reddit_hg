"""
AI Radar — Vector & Chat Router
Endpoints for semantic search, similar items, LLM chat, and index management.
"""

import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import faiss
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from admin.auth import get_current_admin
from config import get_settings
from database.models import EnrichedItem, RawItem, Chat, ChatMessage
from database.session import get_db
from llm.client import LLMClient
from user.auth import verify_user_session
from vector.embeddings import get_embedding_provider
from vector.faiss_index import FaissIndexManager
from vector.index_builder import rebuild_index
from vector.schemas import (
    ChatCreate,
    ChatMessageOut,
    ChatOut,
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


CHAT_CLEANUP_DAYS = 30


async def _cleanup_stale_chats(db: AsyncSession):
    """Delete chats with no activity for 30+ days."""
    cutoff = datetime.utcnow() - timedelta(days=CHAT_CLEANUP_DAYS)
    await db.execute(delete(Chat).where(Chat.updated_at < cutoff))
    await db.commit()


async def _chat_title_from_message(message: str) -> str:
    """Generate a short chat title from the first user message."""
    title = message.strip()[:80]
    if len(message.strip()) > 80:
        title += "..."
    return title or "Новый чат"


# ─── Chat CRUD ───


@router.get("/chats", response_model=list[ChatOut])
async def list_chats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """List all chats for the current user, ordered by last activity."""
    user_id = verify_user_session(request)
    await _cleanup_stale_chats(db)

    result = await db.execute(
        select(
            Chat.id,
            Chat.title,
            Chat.created_at,
            Chat.updated_at,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage, ChatMessage.chat_id == Chat.id)
        .where(Chat.user_id == user_id)
        .group_by(Chat.id)
        .order_by(Chat.updated_at.desc())
    )
    rows = result.all()
    return [
        ChatOut(
            id=str(row.id),
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
            message_count=row.message_count,
        )
        for row in rows
    ]


@router.post("/chats", response_model=ChatOut, status_code=201)
async def create_chat(
    payload: ChatCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat for the current user."""
    user_id = verify_user_session(request)
    chat = Chat(user_id=user_id, title=payload.title)
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    await _cleanup_stale_chats(db)
    return ChatOut(
        id=str(chat.id),
        title=chat.title,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(
    chat_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat and all its messages."""
    user_id = verify_user_session(request)
    result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    chat = result.scalar_one_or_none()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    await db.execute(delete(ChatMessage).where(ChatMessage.chat_id == chat_id))
    await db.delete(chat)
    await db.commit()


@router.get("/chats/{chat_id}/messages", response_model=list[ChatMessageOut])
async def get_chat_messages(
    chat_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get all messages for a chat."""
    user_id = verify_user_session(request)
    result = await db.execute(
        select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Chat not found")
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return [
        ChatMessageOut(
            id=str(msg.id),
            chat_id=str(msg.chat_id),
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
        )
        for msg in result.scalars().all()
    ]


async def _tool_executor(name: str, args: dict):
    if name == "vector_search":
        return await _execute_vector_search(**args)
    return {"error": f"Unknown tool: {name}"}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    request_obj: Request,
    db: AsyncSession = Depends(get_db),
):
    """Chat with LLM, optionally using FAISS context via function calling.
    Saves messages to DB if chat_id is provided."""
    user_id = verify_user_session(request_obj)

    chat_id: Optional[UUID] = None
    if request.chat_id:
        try:
            chat_id = UUID(request.chat_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid chat_id")

    # Create or find chat
    if chat_id is None:
        title = await _chat_title_from_message(request.message)
        chat = Chat(
            user_id=user_id,
            title=title,
        )
        db.add(chat)
        await db.flush()
        chat_id = chat.id
    else:
        result = await db.execute(
            select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id)
        )
        chat = result.scalar_one_or_none()
        if not chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        # Auto-title from first message if still default
        if chat.title == "Новый чат":
            result = await db.execute(
                select(func.count()).select_from(ChatMessage).where(ChatMessage.chat_id == chat_id)
            )
            if result.scalar() == 0:
                chat.title = await _chat_title_from_message(request.message)

    # Save user message
    user_msg = ChatMessage(chat_id=chat_id, role="user", content=request.message)
    db.add(user_msg)

    # Update chat timestamp
    chat.updated_at = datetime.utcnow()

    # Commit user message before LLM call so it persists even if LLM fails
    await db.commit()

    # Build LLM context
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

    reply = ""
    try:
        reply = await llm.chat_with_tools(
            messages,
            tools=[VECTOR_SEARCH_TOOL],
            tool_executor=_tool_executor,
            temperature=0.7,
            max_tokens=4000,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM API error: {str(exc)}")

    # Save assistant reply
    if reply:
        assistant_msg = ChatMessage(chat_id=chat_id, role="assistant", content=reply)
        db.add(assistant_msg)
        await db.commit()

    return ChatResponse(reply=reply, chat_id=str(chat_id))


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
