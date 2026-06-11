"""
AI Radar — FAISS Index Manager
Handles creation, loading, saving, and searching of FAISS vector index.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import faiss
import numpy as np

from config import get_settings


@dataclass
class SearchResult:
    id: str
    score: float
    metadata: dict


@dataclass
class IndexMetadata:
    item_id: str
    enriched_item_id: str
    title: str
    category: str
    summary_ru: str
    tech_stack: List[str]
    use_cases: List[str]
    source_name: str = ""
    faiss_id: int = -1


class FaissIndexManager:
    """Manages a FAISS index with metadata mapping."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self.index: Optional[faiss.Index] = None
        self._metadata: Dict[int, IndexMetadata] = {}
        self._next_faiss_id: int = 0

    def build(
        self,
        vectors: np.ndarray,
        metadata_list: List[IndexMetadata],
    ) -> None:
        """Build index from numpy array and metadata list."""
        if len(vectors) == 0:
            self.index = faiss.IndexIDMap(faiss.IndexFlatIP(self.dim))
            return

        vectors = vectors.astype(np.float32)
        faiss.normalize_L2(vectors)

        index = faiss.IndexFlatIP(self.dim)
        self.index = faiss.IndexIDMap(index)

        ids = np.arange(len(vectors), dtype=np.int64)
        self.index.add_with_ids(vectors, ids)

        self._metadata = {}
        for i, md in enumerate(metadata_list):
            md.faiss_id = i
            self._metadata[i] = md
        self._next_faiss_id = len(vectors)

    def add(self, vector: np.ndarray, metadata: IndexMetadata) -> None:
        """Add a single vector to the index."""
        if self.index is None:
            raise RuntimeError("Index not built yet. Call build() first.")

        vector = vector.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(vector)

        faiss_id = self._next_faiss_id
        ids = np.array([faiss_id], dtype=np.int64)
        self.index.add_with_ids(vector, ids)

        metadata.faiss_id = faiss_id
        self._metadata[faiss_id] = metadata
        self._next_faiss_id += 1

    def search(
        self,
        query_vector: np.ndarray,
        k: int = 10,
    ) -> List[SearchResult]:
        """Search for nearest neighbors."""
        if self.index is None or self.index.ntotal == 0:
            return []

        query_vector = query_vector.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(query_vector)

        actual_k = min(k, self.index.ntotal)
        distances, indices = self.index.search(query_vector, actual_k)

        results = []
        for i in range(actual_k):
            faiss_id = int(indices[0][i])
            score = float(distances[0][i])
            md = self._metadata.get(faiss_id)
            results.append(SearchResult(
                id=md.enriched_item_id if md else str(faiss_id),
                score=score,
                metadata=asdict(md) if md else {},
            ))
        return results

    def get_metadata_by_enriched_id(self, enriched_id: str) -> Optional[IndexMetadata]:
        """Find metadata by enriched_item_id."""
        for md in self._metadata.values():
            if md.enriched_item_id == enriched_id:
                return md
        return None

    def save(self) -> None:
        """Save index and metadata to disk."""
        settings = get_settings()
        os.makedirs(os.path.dirname(settings.faiss_index_path) or ".", exist_ok=True)

        if self.index is not None:
            faiss.write_index(self.index, settings.faiss_index_path)

        meta_dict = {
            "dim": self.dim,
            "next_faiss_id": self._next_faiss_id,
            "metadata": {str(k): asdict(v) for k, v in self._metadata.items()},
        }
        with open(settings.faiss_meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_dict, f, ensure_ascii=False, default=str)

    def load(self) -> bool:
        """Load index and metadata from disk. Returns True if loaded successfully."""
        settings = get_settings()

        if not os.path.exists(settings.faiss_index_path) or not os.path.exists(settings.faiss_meta_path):
            return False

        try:
            self.index = faiss.read_index(settings.faiss_index_path)
            self.dim = self.index.d

            with open(settings.faiss_meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)

            self._next_faiss_id = meta_dict.get("next_faiss_id", 0)
            self._metadata = {}
            for str_id, md_data in meta_dict.get("metadata", {}).items():
                md = IndexMetadata(**md_data)
                self._metadata[md.faiss_id] = md

            return True
        except Exception as exc:
            print(f"[FAISS] Failed to load index: {exc}")
            return False

    @property
    def size(self) -> int:
        """Return number of vectors in index."""
        if self.index is None:
            return 0
        return self.index.ntotal

    @property
    def is_empty(self) -> bool:
        return self.size == 0

    def get_info(self) -> dict:
        return {
            "size": self.size,
            "dim": self.dim,
            "loaded": self.index is not None,
        }

    def clear(self) -> None:
        """Clear the index and metadata."""
        self.index = None
        self._metadata = {}
        self._next_faiss_id = 0
