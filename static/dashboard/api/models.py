from typing import Optional, Dict, Any, List
from datetime import datetime
from fastapi import APIRouter, Query, Depends
from static.dashboard.models.schemas import ModelCard, ModelDetail
from static.dashboard.dependencies import get_data_provider

router = APIRouter(prefix="/api", tags=["models"])

@router.get("/models", response_model=Dict[str, Any])
async def list_models(
    category: Optional[str] = Query(None),
    license: Optional[str] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    min_popularity: Optional[int] = Query(None),
    ids: Optional[str] = Query(None, description="Comma-separated enriched_item_ids to filter by"),
    sort_by: str = Query("date", regex="^(date|popularity)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    provider = Depends(get_data_provider)
):
    filters = {
        "category": category,
        "license": license,
        "date_from": date_from,
        "date_to": date_to,
        "min_popularity": min_popularity,
    }
    if ids:
        filters["ids"] = [x.strip() for x in ids.split(",") if x.strip()]
    models, total = await provider.get_models(filters, page, limit, sort_by)
    return {"items": models, "total": total, "page": page, "limit": limit}

@router.get("/models/{model_id}", response_model=ModelDetail)
async def get_model(model_id: str, provider = Depends(get_data_provider)):
    return await provider.get_model_detail(model_id)
