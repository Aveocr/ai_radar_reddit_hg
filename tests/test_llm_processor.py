import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from llm.processor import LLMProcessor


def scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestLlmSummaryConfig(unittest.TestCase):
    def test_disabled_config_skips_raw_summary(self):
        async def run():
            raw = MagicMock()
            raw.id = uuid4()
            raw.title = "Test model"
            raw.description = "Description"
            raw.tags = ["llm"]
            raw.status = "raw"

            db = AsyncMock(spec=AsyncSession)
            db.execute.side_effect = [
                scalar_result(raw),
                scalar_result(None),
                scalar_result(False),
            ]

            llm = MagicMock()
            llm.model = "test-model"
            llm.classify = AsyncMock(return_value={"domain": "NLP"})
            llm.summarize_raw = AsyncMock(return_value="Should not be used")

            result = await LLMProcessor(db, llm).process_item(raw.id)

            self.assertIsNotNone(result)
            llm.classify.assert_awaited_once()
            llm.summarize_raw.assert_not_awaited()
            self.assertEqual(raw.status, "enriched")

        asyncio.run(run())

    def test_config_read_error_skips_raw_summary(self):
        async def run():
            db = AsyncMock(spec=AsyncSession)
            db.execute.side_effect = RuntimeError("database unavailable")

            enabled = await LLMProcessor(db, MagicMock()).is_summary_enabled()

            self.assertFalse(enabled)
            db.rollback.assert_awaited_once()

        asyncio.run(run())

    def test_missing_config_keeps_default_enabled(self):
        async def run():
            db = AsyncMock(spec=AsyncSession)
            db.execute.return_value = scalar_result(None)

            enabled = await LLMProcessor(db, MagicMock()).is_summary_enabled()

            self.assertTrue(enabled)

        asyncio.run(run())
