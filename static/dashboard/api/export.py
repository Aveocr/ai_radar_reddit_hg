import io
import pandas as pd
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response, Query
from static.dashboard.dependencies import get_data_provider

router = APIRouter(prefix="/api", tags=["export"])

# Оставляем ваш POST (на всякий случай), но добавим GET
@router.get("/export_csv")
async def export_csv_get(
    category: Optional[str] = Query(None),
    license: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    min_popularity: Optional[int] = Query(None),
    provider = Depends(get_data_provider)
):
    try:
        filters = {
            "category": category,
            "license": license,
            "date_from": date_from,
            "date_to": date_to,
            "min_popularity": min_popularity,
        }
        if source:
            filters["source"] = [x.strip() for x in source.split(",") if x.strip()]
        df = await provider.get_filtered_dataframe(filters)
        if df.empty:
            csv_content = "No data"
        else:
            for col in df.select_dtypes(include=['datetime64[ns, UTC]', 'datetimetz']).columns:
                df[col] = df[col].dt.tz_localize(None).dt.strftime('%Y-%m-%d %H:%M:%S')
            output = io.StringIO()
            df.to_csv(output, index=False)
            csv_content = output.getvalue()
        content_bytes = csv_content.encode('utf-8')
        return Response(
            content=content_bytes,
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=export.csv",
                "Content-Length": str(len(content_bytes))
            }
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export_xlsx")
async def export_xlsx_get(
    category: Optional[str] = Query(None),
    license: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    min_popularity: Optional[int] = Query(None),
    provider = Depends(get_data_provider)
):
    try:
        filters = {
            "category": category,
            "license": license,
            "date_from": date_from,
            "date_to": date_to,
            "min_popularity": min_popularity,
        }
        if source:
            filters["source"] = [x.strip() for x in source.split(",") if x.strip()]
        df = await provider.get_filtered_dataframe(filters)
        if df.empty:
            return Response(content="No data", media_type="text/plain", status_code=204)
        
        # Приводим datetime к naive (без часового пояса)
        for col in df.select_dtypes(include=['datetime64[ns, UTC]', 'datetimetz']).columns:
            df[col] = df[col].dt.tz_localize(None)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Models')
        
        content_bytes = output.getvalue()
        return Response(
            content=content_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=export.xlsx"}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
