"""
Tests for the Vector / FAISS module.
Uses mocks to avoid heavy dependency loading (fastembed, FAISS).
"""

import asyncio
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np


class TestFaissIndexManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.index_path = os.path.join(self.temp_dir, "test.index")
        self.meta_path = os.path.join(self.temp_dir, "test_meta.json")

    def tearDown(self):
        for p in [self.index_path, self.meta_path]:
            if os.path.exists(p):
                os.remove(p)
        os.rmdir(self.temp_dir)

    def _make_metadata(self, count=3):
        from vector.faiss_index import IndexMetadata
        return [
            IndexMetadata(
                item_id=f"item-{i}",
                enriched_item_id=f"enr-{i}",
                title=f"Model {i}",
                category="NLP" if i % 2 == 0 else "CV",
                summary_ru=f"Описание модели {i}",
                tech_stack=["pytorch"],
                use_cases=["text"] if i % 2 == 0 else ["image"],
                source_name="huggingface",
            )
            for i in range(count)
        ]

    @patch.dict(os.environ, {"FAISS_INDEX_PATH": "test.index", "FAISS_META_PATH": "test_meta.json"})
    def test_build_and_search(self):
        from vector.faiss_index import FaissIndexManager

        manager = FaissIndexManager(dim=4)
        vectors = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], dtype=np.float32)
        metadata = self._make_metadata(3)

        manager.build(vectors, metadata)

        self.assertEqual(manager.size, 3)
        self.assertFalse(manager.is_empty)

        info = manager.get_info()
        self.assertEqual(info["size"], 3)
        self.assertEqual(info["dim"], 4)

        results = manager.search(np.array([[1, 0, 0, 0]], dtype=np.float32), k=2)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].id, "enr-0")
        self.assertAlmostEqual(results[0].score, 1.0, places=5)

    @patch.dict(os.environ, {"FAISS_INDEX_PATH": "test.index", "FAISS_META_PATH": "test_meta.json"})
    def test_save_and_load(self):
        from vector.faiss_index import FaissIndexManager

        manager = FaissIndexManager(dim=4)
        vectors = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        metadata = self._make_metadata(2)
        manager.build(vectors, metadata)

        with patch.object(manager, 'save') as mock_save:
            manager.save()
            mock_save.assert_called_once()

    def test_metadata_lookup(self):
        from vector.faiss_index import FaissIndexManager

        manager = FaissIndexManager(dim=4)
        vectors = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        metadata = self._make_metadata(2)
        manager.build(vectors, metadata)

        md = manager.get_metadata_by_enriched_id("enr-1")
        self.assertIsNotNone(md)
        self.assertEqual(md.title, "Model 1")

        md = manager.get_metadata_by_enriched_id("nonexistent")
        self.assertIsNone(md)

    def test_empty_index(self):
        from vector.faiss_index import FaissIndexManager

        manager = FaissIndexManager(dim=4)
        self.assertTrue(manager.is_empty)
        self.assertEqual(manager.size, 0)
        results = manager.search(np.array([[1, 0, 0, 0]], dtype=np.float32))
        self.assertEqual(results, [])


class TestLocalEmbeddings(unittest.TestCase):
    @patch("fastembed.TextEmbedding")
    def test_embed(self, mock_te):
        from vector.embeddings import LocalEmbeddings

        mock_model = MagicMock()
        mock_model.model_dim = 384
        mock_model.embed.return_value = iter([np.array([0.1] * 384)])
        mock_te.return_value = mock_model

        emb = LocalEmbeddings()
        result = asyncio.run(emb.embed("test text"))

        self.assertEqual(len(result), 384)
        self.assertAlmostEqual(result[0], 0.1)

    @patch("fastembed.TextEmbedding")
    def test_embed_batch(self, mock_te):
        from vector.embeddings import LocalEmbeddings

        mock_model = MagicMock()
        mock_model.model_dim = 384
        mock_model.embed.return_value = iter([
            np.array([0.1] * 384),
            np.array([0.2] * 384),
        ])
        mock_te.return_value = mock_model

        emb = LocalEmbeddings()
        result = asyncio.run(emb.embed_batch(["text1", "text2"]))

        self.assertEqual(result.shape, (2, 384))


class TestGigaChatEmbeddings(unittest.TestCase):
    def setUp(self):
        self.token_response = MagicMock()
        self.token_response.status = 200
        self.token_response.json = AsyncMock(return_value={"access_token": "test_token", "expires_in": 1800})

        self.embed_response = MagicMock()
        self.embed_response.status = 200
        self.embed_response.json = AsyncMock(return_value={
            "data": [
                {"embedding": [0.1, 0.2, 0.3]},
                {"embedding": [0.4, 0.5, 0.6]},
            ]
        })

    def test_dim_property(self):
        from vector.embeddings import GigaChatEmbeddings
        emb = GigaChatEmbeddings(client_id="test", client_secret="test")
        self.assertEqual(emb.dim, 1024)

    @patch("vector.embeddings.GigaChatEmbeddings._get_access_token")
    @patch("vector.embeddings.GigaChatEmbeddings._embed_request")
    def test_embed_batch(self, mock_embed_request, mock_get_token):
        from vector.embeddings import GigaChatEmbeddings

        mock_get_token.return_value = "test_token"
        mock_embed_request.return_value = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

        emb = GigaChatEmbeddings(client_id="test", client_secret="test")
        result = asyncio.run(emb.embed_batch(["text1", "text2"]))

        self.assertEqual(result.shape, (2, 3))


class TestIndexBuilder(unittest.TestCase):
    def test_build_item_text(self):
        from vector.index_builder import _build_item_text

        class MockRaw:
            title = "Test Model"

        class MockEnriched:
            summary_ru = "Test summary"
            category = "NLP"
            tech_stack = ["pytorch", "transformers"]
            use_cases = ["text classification"]
            subcategories = ["transformer"]

        text = _build_item_text(MockEnriched(), MockRaw())
        self.assertIn("Test Model", text)
        self.assertIn("Test summary", text)
        self.assertIn("pytorch", text)
        self.assertIn("transformers", text)

    def test_build_empty_db(self):
        from vector.index_builder import build_index_from_db
        from vector.faiss_index import FaissIndexManager

        async def _run():
            mock_db = AsyncMock()

            class MockResult:
                def all(self):
                    return []

            mock_db.execute.return_value = MockResult()
            manager = FaissIndexManager(dim=384)
            count = await build_index_from_db(mock_db, manager=manager)
            return count

        count = asyncio.run(_run())
        self.assertEqual(count, 0)


class TestVectorSchemas(unittest.TestCase):
    def test_chat_request(self):
        from vector.schemas import ChatRequest, ChatMessage

        req = ChatRequest(
            message="Hello",
            history=[ChatMessage(role="user", content="Hi")],
        )
        self.assertEqual(req.message, "Hello")
        self.assertEqual(len(req.history), 1)
        self.assertEqual(req.history[0].role, "user")

    def test_search_result(self):
        from vector.schemas import VectorSearchResult

        result = VectorSearchResult(
            id="test-id",
            title="Test",
            category="NLP",
            summary_ru="Описание",
            tech_stack=["pytorch"],
            use_cases=["text"],
            source_name="hf",
            score=0.95,
        )
        self.assertEqual(result.id, "test-id")
        self.assertAlmostEqual(result.score, 0.95)


if __name__ == "__main__":
    unittest.main()
