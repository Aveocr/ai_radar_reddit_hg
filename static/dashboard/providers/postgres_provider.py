import pandas as pd
from typing import List, Dict, Any, Tuple
import psycopg
from fastapi import HTTPException
from static.dashboard.models.schemas import ModelCard, ModelDetail, SourceInfo, StatsResponse
from static.dashboard.providers.base import DataProvider

class PostgresDataProvider(DataProvider):
    def __init__(self, dsn: str):
        self.dsn = dsn
        # ВНИМАНИЕ: синхронное соединение в асинхронном приложении – упрощение для демонстрации.
        # В реальном проекте используйте asyncpg или SQLAlchemy с асинхронным драйвером.
        self.conn = None

    def _get_conn(self):
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.dsn)
        return self.conn

    async def get_models(self, filters: dict, page: int, limit: int, sort_by: str) -> Tuple[List[ModelCard], int]:
        cursor = self._get_conn().cursor()
        base_query = """
            SELECT 
                COALESCE(e.id, r.id) as id,
                r.title,
                e.category,
                r.license,
                r.popularity_metric,
                e.summary_ru,
                r.created_at_source,
                s.name as source_name
            FROM raw_items r
            LEFT JOIN enriched_items e ON r.id = e.raw_item_id
            LEFT JOIN sources s ON r.source_id = s.id
            WHERE 1=1
        """
        params = []
        if filters.get('category'):
            base_query += " AND e.category = %s"
            params.append(filters['category'])
        if filters.get('license'):
            base_query += " AND r.license = %s"
            params.append(filters['license'])
        if filters.get('date_from'):
            base_query += " AND r.created_at_source >= %s"
            params.append(filters['date_from'])
        if filters.get('date_to'):
            base_query += " AND r.created_at_source <= %s"
            params.append(filters['date_to'])
        if filters.get('min_popularity'):
            base_query += " AND r.popularity_metric >= %s"
            params.append(filters['min_popularity'])
        ids_filter = filters.get('ids')
        if ids_filter:
            placeholders = ", ".join(["%s"] * len(ids_filter))
            base_query += f" AND e.id IN ({placeholders})"
            params.extend(ids_filter)

        if sort_by == 'popularity':
            base_query += " ORDER BY r.popularity_metric DESC NULLS LAST"
        elif sort_by == 'date':
            base_query += " ORDER BY r.created_at_source DESC NULLS LAST"
        else:
            base_query += " ORDER BY id"

        count_query = f"SELECT COUNT(*) FROM ({base_query}) as sub"
        cursor.execute(count_query, params)
        total = cursor.fetchone()[0]

        base_query += " LIMIT %s OFFSET %s"
        params.extend([limit, (page-1)*limit])
        cursor.execute(base_query, params)
        rows = cursor.fetchall()

        models = []
        for row in rows:
            models.append(ModelCard(
                id=str(row[0]),
                title=row[1],
                category=row[2],
                license=row[3],
                popularity_metric=row[4],
                summary_ru=row[5],
                created_at=row[6],
                source_name=row[7]
            ))
        cursor.close()
        return models, total

    async def get_model_detail(self, model_id: str) -> ModelDetail:
        cursor = self._get_conn().cursor()
        cursor.execute("SELECT * FROM raw_items WHERE id = %s", (model_id,))
        raw_row = cursor.fetchone()
        raw_cols = [desc[0] for desc in cursor.description] if raw_row else []
        cursor.execute("SELECT * FROM enriched_items WHERE raw_item_id = %s OR id = %s", (model_id, model_id))
        enriched_row = cursor.fetchone()
        enriched_cols = [desc[0] for desc in cursor.description] if enriched_row else []
        cursor.close()

        if not raw_row and not enriched_row:
            raise HTTPException(404, "Model not found")

        raw_dict = dict(zip(raw_cols, raw_row)) if raw_row else {}
        enriched_dict = dict(zip(enriched_cols, enriched_row)) if enriched_row else {}
        return ModelDetail(raw=raw_dict, enriched=enriched_dict)

    async def get_sources(self) -> List[SourceInfo]:
        cursor = self._get_conn().cursor()
        cursor.execute("SELECT id, name, code, is_active FROM sources")
        rows = cursor.fetchall()
        cursor.close()
        return [SourceInfo(id=str(r[0]), name=r[1], code=r[2], is_active=r[3]) for r in rows]

    async def get_stats(self, period: str) -> StatsResponse:
        cursor = self._get_conn().cursor()
        date_filter = ""
        if period == 'week':
            date_filter = "AND r.created_at_source > NOW() - INTERVAL '7 days'"
        elif period == 'month':
            date_filter = "AND r.created_at_source > NOW() - INTERVAL '30 days'"
        cursor.execute(f"""
            SELECT e.category, COUNT(*)
            FROM enriched_items e
            JOIN raw_items r ON e.raw_item_id = r.id
            WHERE 1=1 {date_filter}
            GROUP BY e.category
        """)
        by_category = {row[0] or 'Unknown': row[1] for row in cursor.fetchall()}
        cursor.execute(f"""
            SELECT s.name, COUNT(*)
            FROM raw_items r
            JOIN sources s ON r.source_id = s.id
            WHERE 1=1 {date_filter}
            GROUP BY s.name
        """)
        by_source = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.close()
        return StatsResponse(by_category=by_category, by_source=by_source)

    async def get_filtered_dataframe(self, filters: dict) -> pd.DataFrame:
        models, _ = await self.get_models(filters, page=1, limit=1000000, sort_by='id')
        data = [m.dict() for m in models]
        return pd.DataFrame(data)
