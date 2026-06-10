"""
AI Radar — Embedding Providers
Supports local (fastembed / ONNX) and GigaChat API embeddings.
"""

import asyncio
import base64
import json
import time
from abc import ABC, abstractmethod
from typing import List, Optional

import aiohttp
import numpy as np

from config import get_settings


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Return embedding dimension."""
        pass

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """Embed a single text."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> np.ndarray:
        """Embed multiple texts, return numpy array of shape (n, dim)."""
        pass


class LocalEmbeddings(EmbeddingProvider):
    """Local embeddings using fastembed (ONNX runtime, no torch)."""

    MODEL_CACHE_DIR = "models/fastembed_cache"

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._cache_dir = self._resolve_cache_dir()
        self._model = None
        self._dim = 384

    @staticmethod
    def _resolve_cache_dir() -> str:
        import os
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "models", "fastembed_cache",
        )

    @property
    def dim(self) -> int:
        return self._dim

    def _load_model(self):
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(self._model_name, cache_dir=self._cache_dir)
            try:
                self._dim = self._model.model_dim
            except AttributeError:
                sample = list(self._model.embed(["test"]))
                if sample:
                    self._dim = len(sample[0])

    async def embed(self, text: str) -> List[float]:
        self._load_model()
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None, lambda: list(self._model.embed([text]))
        )
        return embeddings[0].tolist()

    async def embed_batch(self, texts: List[str]) -> np.ndarray:
        self._load_model()
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(
            None, lambda: np.array(list(self._model.embed(texts)), dtype=np.float32)
        )
        return embeddings


class GigaChatEmbeddings(EmbeddingProvider):
    """GigaChat API embeddings."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        scope: str = "GIGACHAT_API_PERS",
        base_url: str = "https://gigachat.devices.sberbank.ru/api/v1",
        verify_ssl: bool = False,
        model: str = "EmbeddingsGigaR",
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._base_url = base_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._model = model
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._dim = 1024

    @property
    def dim(self) -> int:
        return self._dim

    def _get_auth_header(self) -> str:
        credentials = f"{self._client_id}:{self._client_secret}"
        return base64.b64encode(credentials.encode()).decode()

    async def _get_access_token(self, session: aiohttp.ClientSession) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        auth_header = self._get_auth_header()
        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "RqUID": "ai-radar-embeddings",
        }
        data = {"scope": self._scope}

        async with session.post(
            f"{self._base_url}/oauth2/token",
            headers=headers,
            data=data,
            ssl=self._verify_ssl,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"GigaChat auth failed {resp.status}: {text}")
            data = await resp.json()
            self._access_token = data["access_token"]
            self._token_expires_at = now + data.get("expires_in", 1800)
            return self._access_token

    async def _embed_request(self, session: aiohttp.ClientSession, texts: List[str]) -> List[List[float]]:
        token = await self._get_access_token(session)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {"model": self._model, "input": texts}

        async with session.post(
            f"{self._base_url}/embeddings",
            headers=headers,
            json=payload,
            ssl=self._verify_ssl,
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                if resp.status == 401:
                    self._access_token = None
                    return await self._embed_request(session, texts)
                raise RuntimeError(f"GigaChat embeddings failed {resp.status}: {text}")
            data = await resp.json()
            return [item["embedding"] for item in data["data"]]

    async def embed(self, text: str) -> List[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: List[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)

        connector = aiohttp.TCPConnector(limit=10)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            embeddings = await self._embed_request(session, texts)

        return np.array(embeddings, dtype=np.float32)


def get_embedding_provider() -> EmbeddingProvider:
    """Factory function to get the configured embedding provider."""
    settings = get_settings()

    if settings.embedding_provider == "gigachat":
        if not settings.gigachat_client_id or not settings.gigachat_client_secret:
            raise RuntimeError(
                "GigaChat embedding provider selected but credentials not configured. "
                "Set GIGACHAT_CLIENT_ID and GIGACHAT_CLIENT_SECRET in .env"
            )
        return GigaChatEmbeddings(
            client_id=settings.gigachat_client_id,
            client_secret=settings.gigachat_client_secret,
            scope=settings.gigachat_scope,
            base_url=settings.gigachat_base_url,
            verify_ssl=settings.gigachat_verify_ssl,
        )

    return LocalEmbeddings(model_name=settings.embedding_model)