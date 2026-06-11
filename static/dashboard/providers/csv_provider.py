import os
import pandas as pd
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from fastapi import HTTPException
from static.dashboard.config import DATA_DIR, DATA_SOURCE
from static.dashboard.models.schemas import ModelCard, ModelDetail, SourceInfo, StatsResponse
from static.dashboard.providers.base import DataProvider

class CSVDataProvider(DataProvider):
    def __init__(self):
        self.enriched_file = os.path.join(DATA_DIR, "enriched_items.csv")
        self.raw_file = os.path.join(DATA_DIR, "raw_items.csv")
        self.sources_file = os.path.join(DATA_DIR, "sources.csv")
        self._enriched_df = None
        self._raw_df = None
        self._sources_df = None
        self._load_data()

    def _load_data(self):
        # Читаем CSV, если файл есть, иначе пустой DataFrame
        if os.path.exists(self.raw_file):
            self._raw_df = pd.read_csv(self.raw_file)
            # Приводим типы
            if 'created_at_source' in self._raw_df.columns:
                self._raw_df['created_at_source'] = pd.to_datetime(self._raw_df['created_at_source'], errors='coerce')
                # Если данные наивные, переведём их в UTC (без сдвига, просто добавим tz)
                if self._raw_df['created_at_source'].dt.tz is None:
                    self._raw_df['created_at_source'] = self._raw_df['created_at_source'].dt.tz_localize('UTC')
            # Убедимся, что external_id используется как строковый идентификатор
            if 'external_id' in self._raw_df.columns:
                self._raw_df['external_id'] = self._raw_df['external_id'].astype(str)
            # Добавим виртуальную колонку 'id' для совместимости с ожиданиями кода
            self._raw_df['id'] = self._raw_df['external_id']
        else:
            self._raw_df = pd.DataFrame(columns=[
                'source_id', 'external_id', 'title', 'license', 'stars', 'created_at_source'
            ])

        # enriched и sources аналогично
        if os.path.exists(self.enriched_file):
            self._enriched_df = pd.read_csv(self.enriched_file)
            if 'id' in self._enriched_df.columns:
                self._enriched_df['id'] = self._enriched_df['id'].astype(str)
            if 'raw_item_id' in self._enriched_df.columns:
                self._enriched_df['raw_item_id'] = self._enriched_df['raw_item_id'].astype(str)
        else:
            self._enriched_df = pd.DataFrame(columns=['id', 'raw_item_id', 'category', 'summary_ru', 'relevance_score'])

        if os.path.exists(self.sources_file):
            self._sources_df = pd.read_csv(self.sources_file)
            if 'id' in self._sources_df.columns:
                self._sources_df['id'] = self._sources_df['id'].astype(str)
        else:
            self._sources_df = pd.DataFrame(columns=['id', 'name', 'code', 'is_active'])

    def _apply_filters(self, df: pd.DataFrame, filters: dict) -> pd.DataFrame:
        # Сделаем копию, чтобы не менять оригинал, если нужно
        df = df.copy()
        
        # Приводим created_at_source к naive (без часового пояса) для корректного сравнения
        if 'created_at_source' in df.columns and df['created_at_source'].dtype == 'datetime64[ns, UTC]':
            df['created_at_source'] = df['created_at_source'].dt.tz_localize(None)
        
        if filters.get('category') and 'category' in df.columns:
            df = df[df['category'].str.contains(filters['category'], na=False, case=False)]
        if filters.get('license') and 'license' in df.columns:
            df = df[df['license'] == filters['license']]
        if filters.get('date_from') and 'created_at_source' in df.columns:
            date_from = pd.Timestamp(filters['date_from'])
            df = df[df['created_at_source'] >= date_from]
        if filters.get('date_to') and 'created_at_source' in df.columns:
            date_to = pd.Timestamp(filters['date_to'])
            df = df[df['created_at_source'] <= date_to]
        if filters.get('min_popularity') and 'stars' in df.columns:
            stars_numeric = pd.to_numeric(df['stars'], errors='coerce')
            df = df[stars_numeric >= filters['min_popularity']]
        if filters.get('source') and 'source_id' in df.columns:
            df = df[df['source_id'].isin(filters['source'])]
        return df

    def _clean_nan(self, value):
        """Преобразует NaN в None, оставляя другие значения без изменений"""
        return None if pd.isna(value) else value

    async def get_models(self, filters: dict, page: int, limit: int, sort_by: str) -> Tuple[List[ModelCard], int]:
        if DATA_SOURCE == "enriched":
            if self._enriched_df.empty or self._raw_df.empty:
                return [], 0
            merged = pd.merge(
                self._enriched_df,
                self._raw_df[['id', 'title', 'license', 'stars', 'created_at_source', 'source_id']],
                left_on='raw_item_id', right_on='id', how='inner', suffixes=('_enr', '_raw')
            )
        else:
            merged = self._raw_df.copy()
            # Используем колонку 'domain' как 'category' (если её нет, то None)
            if 'domain' in merged.columns:
                merged['category'] = merged['domain']
            else:
                merged['category'] = None
            if 'summary_ru' not in merged.columns:
                merged['summary_ru'] = merged.get('description', None)

        if merged.empty:
            return [], 0

        filtered = self._apply_filters(merged, filters)

        if sort_by == 'stars' and 'stars' in filtered.columns:
            filtered = filtered.sort_values('stars', ascending=False)
        elif sort_by == 'date' and 'created_at_source' in filtered.columns:
            filtered = filtered.sort_values('created_at_source', ascending=False)
        else:
            filtered = filtered.sort_values('id')

        total = len(filtered)
        start = (page - 1) * limit
        end = start + limit
        page_data = filtered.iloc[start:end]

        sources_dict = {}
        if not self._sources_df.empty:
            sources_dict = dict(zip(self._sources_df['id'].astype(str), self._sources_df['name']))

        models = []
        for _, row in page_data.iterrows():
            # Приводим NaN значения к None
            license_val = row.get('license')
            if pd.isna(license_val):
                license_val = None
            
            popularity_val = row.get('stars')
            if pd.isna(popularity_val):
                popularity_val = None
            elif popularity_val is not None:
                popularity_val = int(popularity_val)
            
            summary_val = row.get('description', row.get('summary_ru'))
            if pd.isna(summary_val):
                summary_val = None
            
            created_val = row.get('created_at_source')
            if pd.isna(created_val):
                created_val = None
            
            models.append(ModelCard(
                id=str(row.get('id', row.get('external_id', ''))),
                title=row.get('title', 'Untitled'),
                category=row.get('category') if not pd.isna(row.get('category')) else None,
                license=license_val,
                popularity_metric=popularity_val,
                summary_ru=summary_val,
                created_at=created_val,
                source_name=sources_dict.get(str(row.get('source_id', '')), 'Unknown'),
                model_type=row.get('model_type') if not pd.isna(row.get('model_type')) else None,
                framework=row.get('framework') if not pd.isna(row.get('framework')) else None,
                task_type=row.get('task_type') if not pd.isna(row.get('task_type')) else None,
                doi=row.get('doi') if not pd.isna(row.get('doi')) else None,
            ))
        return models, total

    async def get_model_detail(self, model_id: str) -> ModelDetail:
        raw_row = None
        enriched_row = None
        if not self._raw_df.empty:
            # Ищем по внешнему ID (external_id) или по нашему внутреннему id
            raw_mask = (self._raw_df['external_id'] == model_id) | (self._raw_df['id'] == model_id)
            if raw_mask.any():
                raw_row = self._raw_df[raw_mask].iloc[0].to_dict()
        if not self._enriched_df.empty:
            enriched_mask = (self._enriched_df['id'] == model_id) | (self._enriched_df['raw_item_id'] == model_id)
            if enriched_mask.any():
                enriched_row = self._enriched_df[enriched_mask].iloc[0].to_dict()

        if raw_row is None and enriched_row is None:
            raise HTTPException(status_code=404, detail="Model not found")

        def clean_dict(d):
            if d is None:
                return {}
            return {k: (None if pd.isna(v) else v) for k, v in d.items()}

        return ModelDetail(raw=clean_dict(raw_row), enriched=clean_dict(enriched_row))

    async def get_sources(self) -> List[SourceInfo]:
        if self._sources_df.empty:
            return []
        sources = []
        for _, row in self._sources_df.iterrows():
            sources.append(SourceInfo(
                id=str(row['id']),
                name=row['name'],
                code=row['code'],
                is_active=bool(row.get('is_active', True))
            ))
        return sources

    async def get_stats(self, period: str) -> StatsResponse:
        by_category = {}
        by_source = {}

        if not self._enriched_df.empty and not self._raw_df.empty:
            # Соединяем по external_id (или по id)
            merged = pd.merge(
                self._enriched_df,
                self._raw_df[['id', 'source_id', 'created_at_source']],
                left_on='raw_item_id', right_on='id', how='inner'
            )
            # Фильтр по периоду
            if 'created_at_source' in self._raw_df.columns and period != 'all_time':
                now = datetime.utcnow()
                cutoff = None
                if period == 'week':
                    cutoff = now - timedelta(days=7)
                elif period == 'month':
                    cutoff = now - timedelta(days=30)
                if cutoff:
                    merged = merged[merged['created_at_source'] >= cutoff]

            if 'category' in merged.columns:
                by_category = merged['category'].value_counts().to_dict()
            if 'source_id' in merged.columns:
                by_source = merged['source_id'].value_counts().to_dict()
                sources_dict = {str(s.id): s.name for s in await self.get_sources()}
                by_source = {sources_dict.get(str(k), str(k)): v for k, v in by_source.items()}
        else:
            if not self._raw_df.empty:
                by_source = self._raw_df['source_id'].value_counts().to_dict()
                sources_dict = {str(s.id): s.name for s in await self.get_sources()}
                by_source = {sources_dict.get(str(k), str(k)): v for k, v in by_source.items()}
        return StatsResponse(by_category=by_category, by_source=by_source)

    async def get_filtered_dataframe(self, filters: dict) -> pd.DataFrame:
        if DATA_SOURCE == "enriched":
            if self._enriched_df.empty or self._raw_df.empty:
                return pd.DataFrame()
            merged = pd.merge(
                self._enriched_df,
                self._raw_df,
                left_on='raw_item_id', right_on='id', how='inner', suffixes=('_enr', '_raw')
            )
        else:
            merged = self._raw_df.copy()
            # Добавляем колонку 'category' из 'domain' (если есть)
            if 'domain' in merged.columns:
                merged['category'] = merged['domain']
            else:
                merged['category'] = None
            # Добавляем summary_ru из description
            if 'summary_ru' not in merged.columns:
                merged['summary_ru'] = merged.get('description', None)
            for col in ['relevance_score']:
                if col not in merged.columns:
                    merged[col] = None

        # Приводим даты к naive (без часового пояса) для корректного сравнения с фильтрами
        if 'created_at_source' in merged.columns and hasattr(merged['created_at_source'].dtype, 'tz') and merged['created_at_source'].dtype.tz is not None:
            merged['created_at_source'] = merged['created_at_source'].dt.tz_localize(None)

        filtered = self._apply_filters(merged, filters)
        return filtered
