"""
AI Radar — FAISS Index Builder
Builds vector index from enriched database items.
"""

import asyncio
from typing import List, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import EnrichedItem, RawItem, Source
from vector.embeddings import EmbeddingProvider, get_embedding_provider
from vector.faiss_index import FaissIndexManager, IndexMetadata


def _build_item_text(item: EnrichedItem, raw: Optional[RawItem] = None) -> str:
    """Build a searchable text from an enriched item."""
    parts = []

    if raw and raw.title:
        parts.append(raw.title)
    if raw and raw.summary:
        parts.append(raw.summary)
    if item.summary_ru:
        parts.append(item.summary_ru)
    if item.category:
        parts.append(item.category)
    if item.tech_stack:
        parts.append(" ".join(item.tech_stack))
    if item.use_cases:
        parts.append(" ".join(item.use_cases))
    if item.subcategories:
        parts.append(" ".join(item.subcategories))

    return ". ".join(parts)


async def build_index_from_db(
    db: AsyncSession,
    embedding_provider: Optional[EmbeddingProvider] = None,
    manager: Optional[FaissIndexManager] = None,
    batch_size: int = 32,
) -> int:
    """Build FAISS index from all enriched items in the database.

    Returns the number of items indexed.
    """
    if embedding_provider is None:
        embedding_provider = get_embedding_provider()
    if manager is None:
        manager = FaissIndexManager(dim=embedding_provider.dim)

    # Load all completed enriched items with raw data and source
    result = await db.execute(
        select(EnrichedItem, RawItem, Source)
        .join(RawItem, EnrichedItem.raw_item_id == RawItem.id)
        .join(Source, RawItem.source_id == Source.id)
        .where(EnrichedItem.processing_status == "completed")
        .order_by(EnrichedItem.processed_at.desc())
    )
    rows = result.all()

    if not rows:
        manager.build(
            np.empty((0, embedding_provider.dim), dtype=np.float32),
            [],
        )
        manager.save()
        return 0

    texts = []
    metadata_list = []
    for enriched, raw, source in rows:
        text = _build_item_text(enriched, raw)
        texts.append(text)
        metadata_list.append(IndexMetadata(
            item_id=str(raw.id),
            enriched_item_id=str(enriched.id),
            title=raw.title or "",
            category=enriched.category or "",
            summary_ru=enriched.summary_ru or "",
            tech_stack=enriched.tech_stack or [],
            use_cases=enriched.use_cases or [],
            source_name=source.name or "",
        ))

    # Generate embeddings in batches
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_embeddings = await embedding_provider.embed_batch(batch_texts)
        all_embeddings.append(batch_embeddings)

    if all_embeddings:
        vectors = np.vstack(all_embeddings)
    else:
        vectors = np.empty((0, embedding_provider.dim), dtype=np.float32)

    manager.build(vectors, metadata_list)
    manager.save()

    return len(metadata_list)


async def rebuild_index(db: AsyncSession) -> int:
    """Rebuild the FAISS index from scratch."""
    count = await build_index_from_db(db)
    return count
