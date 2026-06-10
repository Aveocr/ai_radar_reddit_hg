from typing import List, Optional
from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []


class ChatResponse(BaseModel):
    reply: str


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


class RebuildResponse(BaseModel):
    indexed_count: int


class VectorConfigUpdate(BaseModel):
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
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
