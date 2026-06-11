import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from parsers.base import BaseParser
from parsers.arxiv_parser import ArxivConfig, fetch_arxiv_papers, setup_logger
from parsers.github_parser import GitHubParser as GitHubParserImpl
from parsers.parse import HuggingFaceModelsParser
from parsers.reddit_parser import RedditParser
from parsers.task import ParserRunTask


def _as_utc_naive(value: Any) -> Any:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _as_utc_aware(value: Any) -> Any:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class GitHubAdapter(BaseParser):
    def __init__(self):
        super().__init__("github", "GitHub", "https://api.github.com")
        self._parser: Optional[GitHubParserImpl] = None

    async def fetch(self, date_from, date_to, filters=None, max_items=1000, task_id: Optional[uuid.UUID] = None):
        if self._parser is None:
            self._parser = GitHubParserImpl()
        return await asyncio.to_thread(
            self._parser.fetch,
            _as_utc_aware(date_from),
            _as_utc_aware(date_to),
            filters,
            max_items,
        )

    @staticmethod
    def _ensure_standard_keys(item: dict[str, Any]) -> dict[str, Any]:
        item["domain"] = item.get("domain") or []
        item["tags"] = item.get("tags") or []
        item["language"] = item.get("language") or []
        item["framework"] = item.get("framework") or []
        item["task_type"] = item.get("task_type") or []
        item["model_type"] = item.get("model_type")
        item["popularity_metric"] = item.get("popularity_metric") or item.get("stars") or item.get("likes")
        item["license"] = item.get("license")
        item["author"] = item.get("author")
        item["description"] = item.get("description")
        item["created_at_source"] = _as_utc_naive(item.get("created_at_source"))
        item["updated_at_source"] = _as_utc_naive(item.get("updated_at_source"))
        item["raw_json"] = item.get("raw_json")
        item["status"] = item.get("status") or "raw"
        return item

    def normalize(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw_item)
        return self._ensure_standard_keys(item)


class HuggingFaceAdapter(BaseParser):
    def __init__(self):
        super().__init__("huggingface", "HuggingFace", "https://huggingface.co/api")
        self._parser: Optional[HuggingFaceModelsParser] = None

    async def fetch(self, date_from, date_to, filters=None, max_items=1000, task_id: Optional[uuid.UUID] = None):
        if self._parser is None:
            self._parser = HuggingFaceModelsParser()

        task = ParserRunTask(
            task_id=task_id or uuid.uuid4(),
            parser_name="huggingface",
            date_from=_as_utc_aware(date_from),
            date_to=_as_utc_aware(date_to),
            source_type="huggingface",
            filters=filters or {},
            max_items=max_items,
            parse_all_categories=bool((filters or {}).get("parse_all_categories", False)),
            delay_seconds=float((filters or {}).get("delay_seconds", 0) or 0),
            delay_between_items=float((filters or {}).get("delay_between_items", 0) or 0),
        )

        return await asyncio.to_thread(self._parser.run, task)

    def normalize(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw_item)
        item["popularity_metric"] = item.get("popularity_metric") or item.get("likes") or item.get("downloads")
        return self._ensure_standard_keys(item)


class RedditAdapter(BaseParser):
    def __init__(self):
        super().__init__("reddit", "Reddit", "https://oauth.reddit.com")
        self._parser: Optional[RedditParser] = None

    async def fetch(self, date_from, date_to, filters=None, max_items=1000, task_id: Optional[uuid.UUID] = None):
        if self._parser is None:
            self._parser = RedditParser()

        task = ParserRunTask(
            task_id=task_id or uuid.uuid4(),
            parser_name="reddit",
            date_from=_as_utc_aware(date_from),
            date_to=_as_utc_aware(date_to),
            source_type="reddit",
            filters=filters or {},
            max_items=max_items,
        )

        return await asyncio.to_thread(self._parser.run, task)

    def normalize(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw_item)
        item["popularity_metric"] = item.get("popularity_metric") or item.get("likes")
        if not item.get("domain"):
            item["domain"] = ["Reddit"]
        return self._ensure_standard_keys(item)


class ArxivAdapter(BaseParser):
    def __init__(self):
        super().__init__("arxiv", "arXiv", "https://export.arxiv.org/api/query")

    async def fetch(self, date_from, date_to, filters=None, max_items=1000, task_id: Optional[uuid.UUID] = None):
        filters = filters or {}
        config = ArxivConfig()
        categories = filters.get("categories") or config.CATEGORIES
        if isinstance(categories, str):
            categories = [categories]
        run_id = str(task_id or uuid.uuid4())

        return await asyncio.to_thread(
            fetch_arxiv_papers,
            start_date=_as_utc_aware(date_from).date().isoformat(),
            end_date=_as_utc_aware(date_to).date().isoformat(),
            categories=categories,
            progress_tracker=None,
            request_tracker=None,
            task_id=run_id,
            logger=setup_logger(run_id),
            config_obj=config,
            max_results_per_request=min(int(filters.get("page_size", 100)), 300),
            max_total=max_items,
            delay_seconds=float(filters.get("delay_seconds", 3) or 0),
            cache=None,
        )

    def normalize(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw_item)
        if isinstance(item.get("author"), list):
            item["author"] = ", ".join(str(author) for author in item["author"] if author)
        item["popularity_metric"] = item.get("popularity_metric") or item.get("citations")
        return self._ensure_standard_keys(item)
