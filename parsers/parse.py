import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, List

from huggingface_hub import HfApi

from parsers.task import ParserRunTask

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# Default pipeline tags to iterate when parsing all categories
DEFAULT_PIPELINE_TAGS: List[str] = [
    "text-generation",
    "text-classification",
    "token-classification",
    "question-answering",
    "summarization",
    "translation",
    "sentence-similarity",
    "image-classification",
    "object-detection",
    "image-segmentation",
    "text-to-image",
    "image-to-text",
    "automatic-speech-recognition",
    "text-to-speech",
    "audio-classification",
]


def _clean_token(value: Optional[str]) -> Optional[str]:
    if not value or value.strip() in {"", "your_huggingface_token_here"}:
        return None
    return value.strip()


class HuggingFaceModelsParser:
    source_type = "huggingface"

    def __init__(
        self,
        token: Optional[str] = None,
        endpoint: str = "https://huggingface.co",
        user_agent: Optional[str] = None,
    ):
        self.token = _clean_token(token or os.environ.get("HUGGINGFACE_TOKEN"))
        if self.token is None and load_dotenv is not None:
            env_path = Path(".env")
            if env_path.exists():
                load_dotenv(dotenv_path=env_path)
                self.token = self.token or _clean_token(os.environ.get("HUGGINGFACE_TOKEN"))

        self.endpoint = os.environ.get("HUGGINGFACE_ENDPOINT", endpoint)

        self.api = HfApi(
            endpoint=self.endpoint,
            token=self.token,
            user_agent=user_agent or "ai-models-parser/1.0",
        )

    def run(self, task: ParserRunTask) -> list[dict[str, Any]]:
        filters = task.filters or {}

        raw_items: List[dict[str, Any]] = []
        seen = set()

        def process_model_iter(models_iter):
            for model in models_iter:
                if task.max_items and len(raw_items) >= task.max_items:
                    break
                item = self._map_model_to_raw_item(model, task)
                ext = item.get("external_id")
                if ext and ext in seen:
                    continue
                if ext:
                    seen.add(ext)

                if not self._pass_local_filters(item, task):
                    item["status"] = "skipped"
                else:
                    item["status"] = "raw"

                raw_items.append(item)

                if getattr(task, "delay_between_items", None):
                    time.sleep(task.delay_between_items)

        def build_filter(pipeline_tag: Optional[str] = None) -> Optional[list[str]]:
            values = []
            tags_filter = filters.get("tags")
            if isinstance(tags_filter, str):
                values.append(tags_filter)
            elif tags_filter:
                values.extend(tags_filter)
            if pipeline_tag:
                values.append(pipeline_tag)
            return values or None

        sort = filters.get("sort", "lastModified")
        if sort == "last_modified":
            sort = "lastModified"

        if getattr(task, "parse_all_categories", False):
            # allow overriding default list via filters['pipeline_tags']
            pipeline_tags = filters.get("pipeline_tags") or DEFAULT_PIPELINE_TAGS
            # normalize single string to list
            if isinstance(pipeline_tags, str):
                pipeline_tags = [pipeline_tags]

            for pt in pipeline_tags:
                models_iter = self.api.list_models(
                    search=filters.get("search"),
                    author=filters.get("author"),
                    filter=build_filter(pt),
                    sort=sort,
                    limit=task.max_items,
                    full=True,
                    cardData=True,
                    fetch_config=True,
                )
                process_model_iter(models_iter)
                if task.max_items and len(raw_items) >= task.max_items:
                    break
                if getattr(task, "delay_seconds", 0):
                    time.sleep(task.delay_seconds)
        else:
            models_iter = self.api.list_models(
                search=filters.get("search"),
                author=filters.get("author"),
                filter=build_filter(filters.get("pipeline_tag")),
                sort=sort,
                limit=task.max_items,
                full=True,
                cardData=True,
                fetch_config=True,
            )
            process_model_iter(models_iter)

        return raw_items

    def _map_model_to_raw_item(self, model: Any, task: ParserRunTask) -> dict[str, Any]:
        repo_id = getattr(model, "id", None) or getattr(model, "modelId", None)
        tags = getattr(model, "tags", None) or []
        card_data = getattr(model, "card_data", None) or getattr(model, "cardData", None) or {}

        created_at = getattr(model, "created_at", None) or getattr(model, "createdAt", None)
        updated_at = getattr(model, "last_modified", None) or getattr(model, "lastModified", None)

        return {
            "source_id": None,  # заполняется из таблицы sources
            "external_id": repo_id,
            "title": repo_id.split("/")[-1] if repo_id else None,
            "model_type": self._detect_model_type(tags),
            "domain": self._detect_domain(tags, getattr(model, "pipeline_tag", None)),
            "description": self._extract_description(card_data),
            "url": f"https://huggingface.co/{repo_id}" if repo_id else None,
            "author": getattr(model, "author", None) or self._extract_author(repo_id),
            "license": self._extract_license(card_data, tags),
            "tags": tags,
            "likes": getattr(model, "likes", None),
            "downloads": getattr(model, "downloads", None),
            "downloads_all_time": getattr(model, "downloads_all_time", None),
            "citations": None,
            "created_at_source": created_at,
            "updated_at_source": updated_at,
            "language": self._extract_languages(tags, card_data),
            "framework": self._extract_frameworks(tags, getattr(model, "library_name", None)),
            "task_type": self._extract_task_types(tags, getattr(model, "pipeline_tag", None)),
            "sha": getattr(model, "sha", None),
            "raw_json": self._to_serializable_dict(model),
            "collected_at": datetime.now(timezone.utc),
            "task_id": str(task.task_id),
            "status": "raw",
        }

    def _pass_local_filters(self, item: dict[str, Any], task: ParserRunTask) -> bool:
        filters = task.filters or {}

        if task.date_from and item["updated_at_source"]:
            if item["updated_at_source"] < task.date_from:
                return False

        if task.date_to and item["updated_at_source"]:
            if item["updated_at_source"] >= task.date_to:
                return False

        min_likes = filters.get("min_likes")
        if min_likes is not None and (item.get("likes") or 0) < min_likes:
            return False

        min_downloads = filters.get("min_downloads")
        if min_downloads is not None and (item.get("downloads") or 0) < min_downloads:
            return False

        return True

    @staticmethod
    def _extract_author(repo_id: Optional[str]) -> Optional[str]:
        if repo_id and "/" in repo_id:
            return repo_id.split("/")[0]
        return None

    @staticmethod
    def _extract_description(card_data: Any) -> Optional[str]:
        if isinstance(card_data, dict):
            return card_data.get("description") or card_data.get("summary")
        return None

    @staticmethod
    def _extract_license(card_data: Any, tags: list[str]) -> Optional[str]:
        if isinstance(card_data, dict) and card_data.get("license"):
            return card_data["license"]

        for tag in tags:
            if tag.startswith("license:"):
                return tag.replace("license:", "")

        return None

    @staticmethod
    def _extract_languages(tags: list[str], card_data: Any) -> list[str]:
        languages = []

        if isinstance(card_data, dict):
            lang = card_data.get("language")
            if isinstance(lang, str):
                languages.append(lang)
            elif isinstance(lang, list):
                languages.extend(lang)

        for tag in tags:
            if tag.startswith("language:"):
                languages.append(tag.replace("language:", ""))

        return sorted(set(languages))

    @staticmethod
    def _extract_frameworks(tags: list[str], library_name: Optional[str]) -> list[str]:
        frameworks = set()

        if library_name:
            frameworks.add(library_name)

        known = {
            "transformers",
            "diffusers",
            "pytorch",
            "tensorflow",
            "keras",
            "sentence-transformers",
            "onnx",
            "gguf",
            "safetensors",
        }

        for tag in tags:
            if tag in known:
                frameworks.add(tag)

        return sorted(frameworks)

    @staticmethod
    def _extract_task_types(tags: list[str], pipeline_tag: Optional[str]) -> list[str]:
        result = set()

        if pipeline_tag:
            result.add(pipeline_tag)

        for tag in tags:
            if tag.startswith("task:"):
                result.add(tag.replace("task:", ""))

        return sorted(result)

    @staticmethod
    def _detect_model_type(tags: list[str]) -> Optional[str]:
        if "transformers" in tags:
            return "transformer"
        if "diffusers" in tags:
            return "diffusion"
        if "gguf" in tags:
            return "gguf"
        return None

    @staticmethod
    def _detect_domain(tags: list[str], pipeline_tag: Optional[str]) -> list[str]:
        domains = set()

        nlp_tasks = {
            "text-generation",
            "text-classification",
            "token-classification",
            "question-answering",
            "summarization",
            "translation",
            "sentence-similarity",
        }

        cv_tasks = {
            "image-classification",
            "object-detection",
            "image-segmentation",
            "text-to-image",
            "image-to-text",
        }

        audio_tasks = {
            "automatic-speech-recognition",
            "text-to-speech",
            "audio-classification",
        }

        if pipeline_tag in nlp_tasks:
            domains.add("NLP")
        if pipeline_tag in cv_tasks:
            domains.add("CV")
        if pipeline_tag in audio_tasks:
            domains.add("Audio")

        if "llm" in tags:
            domains.add("LLM")

        return sorted(domains)

    @staticmethod
    def _to_serializable_dict(model: Any) -> dict[str, Any]:
        essential = {}
        for attr in ("id", "modelId", "pipeline_tag", "library_name", "author",
                     "likes", "downloads", "downloads_all_time", "created_at",
                     "createdAt", "last_modified", "lastModified", "sha",
                     "private", "disabled", "gated", "safetensors"):
            val = getattr(model, attr, None)
            if val is not None:
                essential[attr] = val
        tags = getattr(model, "tags", None) or []
        essential["tags"] = tags[:100]
        card_data = getattr(model, "card_data", None) or getattr(model, "cardData", None)
        if card_data:
            if isinstance(card_data, dict):
                essential["card_data"] = {
                    k: v for k, v in card_data.items()
                    if k in ("description", "license", "language", "metrics",
                             "model-index", "datasets", "library_name",
                             "pipeline_tag", "tags", "base_model")
                }
            elif hasattr(card_data, "__dict__"):
                essential["card_data"] = {
                    k: v for k, v in card_data.__dict__.items()
                    if not k.startswith("_") and k in ("description", "license", "language",
                                                        "metrics", "model-index", "datasets",
                                                        "library_name", "pipeline_tag",
                                                        "tags", "base_model")
                }
        return essential
