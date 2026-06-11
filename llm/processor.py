"""
AI Radar — LLM Batch Processor
Processes raw items from DB and creates enriched records.
"""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import RawItem, EnrichedItem, SchedulerConfig
from llm.client import LLMClient


class LLMProcessor:
    def __init__(self, db: AsyncSession, llm: Optional[LLMClient] = None):
        self.db = db
        self.llm = llm or LLMClient()

    async def is_summary_enabled(self) -> bool:
        """Return whether optional paid summary generation is enabled."""
        try:
            result = await self.db.execute(
                select(SchedulerConfig.llm_summary_enabled).where(SchedulerConfig.id == 1)
            )
            enabled = result.scalar_one_or_none()
            return True if enabled is None else bool(enabled)
        except Exception:
            # Avoid an unexpected paid LLM call when config cannot be read.
            await self.db.rollback()
            return False

    async def process_item(self, raw_item_id: UUID) -> Optional[EnrichedItem]:
        """Process single raw item through LLM pipeline."""
        result = await self.db.execute(select(RawItem).where(RawItem.id == raw_item_id))
        raw = result.scalar_one_or_none()
        if not raw:
            return None

        # Skip if already enriched
        existing = await self.db.execute(
            select(EnrichedItem).where(EnrichedItem.raw_item_id == raw_item_id)
        )
        if existing.scalar_one_or_none():
            return None

        try:
            summary_enabled = await self.is_summary_enabled()

            # Call LLM for enrichment
            enrichment = await self.llm.classify(
                title=raw.title,
                description=raw.description or "",
                tags=raw.tags or [],
            )

            enriched = EnrichedItem(
                raw_item_id=raw_item_id,
                summary_en=enrichment.get("summary_en", ""),
                summary_ru=enrichment.get("summary_ru", ""),
                category=enrichment.get("domain", "Other"),
                subcategories=enrichment.get("subcategories", []),
                tech_stack=enrichment.get("tech_stack", []),
                use_cases=enrichment.get("use_cases", []),
                relevance_score=enrichment.get("relevance_score", 0.5),
                language_confirmed=enrichment.get("language_confirmed", ["en"]),
                model_size=enrichment.get("model_size", "unknown"),
                benchmarks=enrichment.get("benchmarks", {}),
                llm_model=self.llm.model,
                processing_status="completed",
            )

            self.db.add(enriched)

            if summary_enabled:
                raw_summary = await self.llm.summarize_raw(
                    title=raw.title,
                    description=raw.description or "",
                    tags=raw.tags or [],
                )
                if raw_summary:
                    raw.summary = raw_summary

            raw.status = "enriched"
            await self.db.commit()
            await self.db.refresh(enriched)
            return enriched

        except Exception as e:
            await self.db.rollback()
            # Create failed record
            enriched = EnrichedItem(
                raw_item_id=raw_item_id,
                processing_status="failed",
                error_message=str(e)[:500],
            )
            self.db.add(enriched)
            await self.db.commit()
            return None

    async def process_pending(self, limit: int = 10) -> int:
        """Process batch of pending raw items."""
        result = await self.db.execute(
            select(RawItem)
            .where(RawItem.status == "raw")
            .limit(limit)
        )
        items = result.scalars().all()
        count = 0
        for item in items:
            enriched = await self.process_item(item.id)
            if enriched:
                count += 1
        return count
