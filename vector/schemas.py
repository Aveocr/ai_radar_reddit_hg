from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    chat_id: Optional[str] = None
    message: str
    history: List[ChatMessageIn] = []


class ChatResponse(BaseModel):
    reply: str
    chat_id: str


class ChatCreate(BaseModel):
    title: str = "Новый чат"


class ChatOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ChatMessageOut(BaseModel):
    id: str
    chat_id: str
    role: str
    content: str
    created_at: datetime


class VectorSearchRequest(BaseModel):
    query: str
    k: int = 10


class VectorSearchResult(BaseModel):
    id: str
    title: str
    category: str
    summary_ru: str
    tech_stack: List[str]
    use_cases: List[str]
    source_name: str
    score: float


class VectorSearchResponse(BaseModel):
    results: List[VectorSearchResult]


class SimilarRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_id: str
    k: int = 10


class IndexInfo(BaseModel):
    size: int
    dim: int
    loaded: bool
    db_count: int = 0


class RebuildResponse(BaseModel):
    indexed_count: int


class VectorConfigUpdate(BaseModel):
    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    gigachat_client_id: Optional[str] = None
    gigachat_client_secret: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"


class VectorConfigInfo(BaseModel):
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    gigachat_configured: bool
    llm_configured: bool
    llm_model: str
    faiss_index_size: int
