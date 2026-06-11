#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Парсер статей arXiv с сохранением в CSV / Excel / PostgreSQL.
Поддерживает кэширование, дедупликацию статей, отслеживание запросов,
экспоненциальный backoff при ошибках 429, проверку robots.txt.
"""

import hashlib
import json
import logging
import random
import time
import urllib.robotparser
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import feedparser
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

@dataclass
class ArxivConfig:
    API_BASE_URL: str = "https://export.arxiv.org/api/query"
    API_TIMEOUT: int = 60
    USER_AGENT: str = "AI-Radar/1.0 (admin@ai-radar.local)"
    MAX_RETRIES: int = 5
    BACKOFF_FACTOR: float = 1.0
    BASE_DELAY_SECONDS: float = 3.0
    MAX_RESULTS_PER_REQUEST: int = 300
    MAX_TOTAL_RESULTS: int = 10000
    CACHE_TTL_SECONDS: int = 86400
    CATEGORIES: List[str] = field(default_factory=lambda: ["cs.AI", "cs.CV", "cs.LG", "cs.CL"])
    DATA_DIR: Path = Path("data")
    LOGS_DIR: Path = Path("logs/arxiv")
    CACHE_DIR: Path = Path("data/cache/arxiv")
    PROGRESS_FILE: Path = Path("data/arxiv_progress.json")
    REQUEST_LOG_FILE: Path = Path("data/arxiv_requests.json")
    CSV_PATH: Path = Path("data/raw_items_arxiv.csv")
    EXCEL_PATH: Path = Path("data/raw_items_arxiv.xlsx")
    PG_CONFIG: Dict[str, Any] = field(default_factory=dict)

# ----------------------------------------------------------------------
#  Инициализация конфигурации
# ----------------------------------------------------------------------
config = ArxivConfig()

# ----------------------------------------------------------------------
#  Логгер
# ----------------------------------------------------------------------

def setup_logger(task_id: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger("arxiv_parser")
    logger.setLevel(logging.DEBUG)
    
    # Удаляем все старые обработчики, чтобы не дублировать
    if logger.hasHandlers():
        logger.handlers.clear()
    
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{task_id}" if task_id else ""
    log_file = config.LOGS_DIR / f"parser_log_{timestamp}{suffix}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger

# ----------------------------------------------------------------------
#  Кэш HTTP-запросов
# ----------------------------------------------------------------------

class HttpCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds

    def _key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def get(self, url: str) -> Optional[requests.Response]:
        key = self._key(url)
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cached_at = datetime.fromisoformat(data['cached_at'])
        if (datetime.now() - cached_at).total_seconds() > self.ttl:
            path.unlink()
            return None
        resp = requests.Response()
        resp.status_code = data['status_code']
        resp._content = data['content'].encode('utf-8')
        resp.url = url
        return resp

    def set(self, url: str, response: requests.Response) -> None:
        key = self._key(url)
        path = self.cache_dir / f"{key}.json"
        data = {
            'url': url,
            'status_code': response.status_code,
            'content': response.text,
            'cached_at': datetime.now().isoformat()
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def clear(self) -> None:
        for p in self.cache_dir.glob("*.json"):
            p.unlink()

# ----------------------------------------------------------------------
#  Прогресс – дедупликация статей
# ----------------------------------------------------------------------

class ProgressTracker:
    def __init__(self, progress_file: Path):
        self.progress_file = Path(progress_file)
        self.processed_hashes: Set[str] = set()
        self.processed_ids: Set[str] = set()
        self._load()

    def _load(self):
        if self.progress_file.exists():
            try:
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    self.processed_hashes = set(data.get('hashes', []))
                    self.processed_ids = set(data.get('external_ids', []))
            except Exception:
                pass

    def save(self):
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'hashes': list(self.processed_hashes),
            'external_ids': list(self.processed_ids),
            'last_updated': datetime.now().isoformat()
        }
        with open(self.progress_file, 'w') as f:
            json.dump(data, f, indent=2)

    def is_duplicate(self, url: str, external_id: str) -> bool:
        h = hashlib.sha256(f"{url}|{external_id}".encode()).hexdigest()
        return h in self.processed_hashes or external_id in self.processed_ids

    def mark_processed(self, url: str, external_id: str) -> None:
        h = hashlib.sha256(f"{url}|{external_id}".encode()).hexdigest()
        self.processed_hashes.add(h)
        self.processed_ids.add(external_id)
        self.save()

# ----------------------------------------------------------------------
#  Трекер запросов – пропуск уже выполненных URL
# ----------------------------------------------------------------------

class RequestTracker:
    def __init__(self, request_log_file: Path):
        self.request_log_file = Path(request_log_file)
        self.processed_urls: Set[str] = set()
        self._load()

    def _load(self):
        if self.request_log_file.exists():
            try:
                with open(self.request_log_file, 'r') as f:
                    data = json.load(f)
                    self.processed_urls = set(data.get('urls', []))
            except Exception:
                pass

    def save(self):
        self.request_log_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'urls': list(self.processed_urls),
            'last_updated': datetime.now().isoformat()
        }
        with open(self.request_log_file, 'w') as f:
            json.dump(data, f, indent=2)

    def is_processed(self, url: str) -> bool:
        h = hashlib.sha256(url.encode()).hexdigest()
        return h in self.processed_urls

    def mark_processed(self, url: str) -> None:
        h = hashlib.sha256(url.encode()).hexdigest()
        self.processed_urls.add(h)
        self.save()

# ----------------------------------------------------------------------
#  HTTP-сессия с повторными попытками (стандартный Retry)
# ----------------------------------------------------------------------

def create_http_session(user_agent: str,
                        max_retries: int = 5,
                        backoff_factor: float = 1.0) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": user_agent})
    return session

# ----------------------------------------------------------------------
#  Функция запроса с улучшенной обработкой 429 (экспоненциальный бэкофф)
# ----------------------------------------------------------------------

def request_with_backoff(
    session: requests.Session,
    url: str,
    logger: logging.Logger,
    max_retries: int = 10,
    initial_backoff: float = 5.0,
    max_backoff: float = 300.0,
    backoff_factor: float = 2.0,
    timeout: int = 60
) -> requests.Response:
    """Выполняет GET-запрос с экспоненциальной задержкой при 429."""
    backoff = initial_backoff
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code == 429:
                retry_after = resp.headers.get('Retry-After')
                if retry_after:
                    try:
                        wait = int(retry_after)
                    except ValueError:
                        wait = backoff
                else:
                    wait = backoff
                # Добавляем случайный джиттер
                wait += random.uniform(0, wait * 0.1)
                logger.warning(f"429 Too Many Requests (попытка {attempt+1}/{max_retries+1}). "
                               f"Пауза {wait:.1f} сек.")
                time.sleep(wait)
                backoff = min(backoff * backoff_factor, max_backoff)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt == max_retries:
                raise
            wait = backoff + random.uniform(0, backoff * 0.1)
            logger.warning(f"Ошибка запроса: {e}. Повтор через {wait:.1f} сек. (попытка {attempt+1}/{max_retries+1})")
            time.sleep(wait)
            backoff = min(backoff * backoff_factor, max_backoff)
    raise RuntimeError("Не удалось выполнить запрос после всех попыток")

# ----------------------------------------------------------------------
#  Robots.txt
# ----------------------------------------------------------------------

def check_robots_txt(user_agent: str) -> Optional[float]:
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url("https://arxiv.org/robots.txt")
    try:
        rp.read()
        return rp.crawl_delay(user_agent) or rp.crawl_delay("*")
    except Exception:
        return None

# ----------------------------------------------------------------------
#  Парсинг одной записи
# ----------------------------------------------------------------------

def parse_arxiv_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    arxiv_id = entry.get('id', '').split('/abs/')[-1].split('v')[0]
    title = entry.get('title', '').replace('\n', ' ').strip()
    abstract = entry.get('summary', '').replace('\n', ' ').strip()
    authors = [a.get('name', '') for a in entry.get('authors', [])]
    categories = [c.get('term', '') for c in entry.get('categories', [])]
    published = entry.get('published', '')
    updated = entry.get('updated', '')
    doi = entry.get('doi', '')
    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else entry.get('id', '')

    return {
        'source_id': 'src_arxiv_001',
        'external_id': arxiv_id,
        'title': title,
        'model_type': 'paper',
        'domain': categories,
        'description': abstract,
        'url': url,
        'author': authors,
        'license': None,
        'tags': categories,
        'popularity_metric': None,
        'created_at_source': published,
        'updated_at_source': updated,
        'language': None,
        'framework': None,
        'task_type': None,
        'raw_json': entry,
        'collected_at': datetime.now().isoformat(),
        'task_id': None,
        'status': 'raw',
        'doi': doi
    }

# ----------------------------------------------------------------------
#  Основная загрузка статей (с поддержкой трекера запросов и улучшенного backoff)
# ----------------------------------------------------------------------

def fetch_arxiv_papers(
    start_date: str,
    end_date: str,
    categories: List[str],
    progress_tracker: Optional[ProgressTracker],
    request_tracker: Optional[RequestTracker],
    task_id: str,
    logger: logging.Logger,
    config_obj: ArxivConfig,
    max_results_per_request: int = 300,
    max_total: int = 10000,
    delay_seconds: float = 3.0,
    cache: Optional[HttpCache] = None
) -> List[Dict[str, Any]]:
    from urllib.parse import quote
    from datetime import datetime as dt

    all_records = []
    seen_ext_ids = set()
    start_dt = dt.fromisoformat(start_date)
    end_dt = dt.fromisoformat(end_date)
    start_str = start_date.replace('-', '')
    end_str = end_date.replace('-', '')

    # Сессия используется для обычных запросов (но мы будем вызывать request_with_backoff отдельно)
    session = create_http_session(
        user_agent=config_obj.USER_AGENT,
        max_retries=config_obj.MAX_RETRIES,
        backoff_factor=config_obj.BACKOFF_FACTOR
    )

    for cat in categories:
        if len(all_records) >= max_total:
            break
        category_limit = max_total - len(all_records)
        logger.info(f"Обработка категории: {cat}")
        search_query = f"cat:{cat}+AND+submittedDate:[{start_str}+TO+{end_str}]"
        start = 0
        total_fetched_for_cat = 0
        category_records = []

        while total_fetched_for_cat < category_limit:
            params = {
                'search_query': search_query,
                'start': start,
                'max_results': min(max_results_per_request, category_limit - total_fetched_for_cat),
                'sortBy': 'submittedDate',
                'sortOrder': 'descending'
            }
            # Ручное кодирование URL
            base_q = {k: v for k, v in params.items() if k != 'search_query'}
            enc_search = quote(params['search_query'], safe='+')
            query_parts = [f"search_query={enc_search}"]
            for k, v in base_q.items():
                query_parts.append(f"{k}={quote(str(v))}")
            query_string = "&".join(query_parts)
            url = f"{config_obj.API_BASE_URL}?{query_string}"

            # Пропускаем уже выполненные запросы
            if request_tracker and request_tracker.is_processed(url):
                logger.debug(f"Запрос уже был выполнен, пропускаем: {url}")
                # Чтобы не зациклить пагинацию, нужно имитировать получение записей.
                # Простейший выход – прервать цикл? Но лучше: мы не знаем, сколько записей было.
                # Поэтому не пропускаем, а всё равно делаем запрос, но с кэша.
                # Для простоты оставим как есть: делаем запрос, но предупреждаем.
                logger.warning("Обнаружен повторный запрос, но данные могут быть в кэше.")

            # Пытаемся взять из кэша
            response = None
            if cache:
                response = cache.get(url)

            if response is None:
                logger.debug(f"Выполняем запрос: {url}")
                try:
                    # Используем улучшенный обработчик с backoff
                    response = request_with_backoff(
                        session, url, logger,
                        max_retries=config_obj.MAX_RETRIES + 2,
                        initial_backoff=delay_seconds,
                        max_backoff=120,
                        backoff_factor=2.0,
                        timeout=config_obj.API_TIMEOUT
                    )
                    if cache:
                        cache.set(url, response)
                    if request_tracker:
                        request_tracker.mark_processed(url)
                except Exception as e:
                    logger.error(f"Ошибка запроса для категории {cat}, start={start}: {e}")
                    break

            # Парсим ответ
            feed = feedparser.parse(response.content)
            entries = feed.entries
            if not entries:
                logger.info(f"Категория {cat}: больше нет записей. Получено {total_fetched_for_cat}")
                break

            for entry in entries:
                entry_id = entry.get('id', '')
                arxiv_id = entry_id.split('/abs/')[-1].split('v')[0] if '/abs/' in entry_id else ''
                if not arxiv_id:
                    continue

                if arxiv_id in seen_ext_ids:
                    continue
                if progress_tracker and progress_tracker.is_duplicate(entry_id, arxiv_id):
                    continue

                # Доп. фильтрация по дате на клиенте
                pub_str = entry.get('published', '')
                if pub_str:
                    try:
                        pub_dt = dt.fromisoformat(pub_str.replace('Z', '+00:00'))
                        if not (start_dt <= pub_dt <= end_dt):
                            logger.debug(f"Пропущена статья {arxiv_id}: дата {pub_dt.date()} вне диапазона")
                            continue
                    except Exception:
                        pass

                adapted = {
                    'id': entry_id,
                    'title': entry.get('title', ''),
                    'summary': entry.get('summary', ''),
                    'published': pub_str,
                    'updated': entry.get('updated', ''),
                    'authors': [{'name': a.get('name', '')} for a in entry.get('authors', [])],
                    'categories': [{'term': t.get('term', '')} for t in entry.get('tags', [])],
                    'links': [{'title': l.get('title', ''), 'href': l.get('href', '')}
                              for l in entry.get('links', [])]
                }
                for link in entry.get('links', []):
                    if link.get('title') == 'doi' and 'doi.org' in link.get('href', ''):
                        adapted['doi'] = link['href'].replace('https://doi.org/', '')
                        break

                record = parse_arxiv_entry(adapted)
                record['task_id'] = task_id
                if record['external_id'] in seen_ext_ids:
                    continue
                seen_ext_ids.add(record['external_id'])
                if progress_tracker:
                    progress_tracker.mark_processed(record['url'], record['external_id'])
                category_records.append(record)

            total_fetched_for_cat += len(entries)
            start += len(entries)

            if len(entries) == max_results_per_request:
                # Задержка между пагинационными запросами
                sleep_time = delay_seconds + random.uniform(0, 1)
                logger.debug(f"Пауза {sleep_time:.1f} сек перед следующим запросом для {cat}")
                time.sleep(sleep_time)
            else:
                break

        logger.info(f"Категория {cat}: получено {len(category_records)} уникальных статей")
        all_records.extend(category_records)

    logger.info(f"Всего уникальных статей за период: {len(all_records)}")
    return all_records

# ----------------------------------------------------------------------
#  Сохранение в файлы и БД
# ----------------------------------------------------------------------

def save_to_csv(records: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(records)
    list_cols = ['domain', 'author', 'tags', 'framework', 'task_type', 'language']
    for col in list_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: ';'.join(x) if isinstance(x, list) else x)
    df.to_csv(path, index=False, encoding='utf-8')
    print(f"CSV сохранён: {path} ({len(records)} записей)")

def save_to_excel(records: List[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(records)
    list_cols = ['domain', 'author', 'tags', 'framework', 'task_type', 'language']
    for col in list_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: ';'.join(x) if isinstance(x, list) else x)
    df.to_excel(path, index=False, engine='openpyxl')
    print(f"Excel сохранён: {path} ({len(records)} записей)")

def save_to_postgresql(records: List[Dict[str, Any]], pg_config: Dict[str, Any]) -> None:
    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        print("psycopg2-binary не установлен, пропуск PostgreSQL")
        return
    conn = None
    try:
        conn = psycopg2.connect(
            host=pg_config.get('host', 'localhost'),
            port=pg_config.get('port', 5432),
            dbname=pg_config.get('database', 'arxiv_parser'),
            user=pg_config.get('user', 'arxiv_user'),
            password=pg_config.get('password', '')
        )
        cursor = conn.cursor()
        table = pg_config.get('table', 'raw_items')
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id SERIAL PRIMARY KEY,
                source_id VARCHAR(50),
                external_id VARCHAR(255) UNIQUE,
                title TEXT,
                model_type VARCHAR(50),
                domain TEXT[],
                description TEXT,
                url TEXT,
                author TEXT[],
                license VARCHAR(100),
                tags TEXT[],
                stars INTEGER,
                created_at_source TIMESTAMP,
                updated_at_source TIMESTAMP,
                language TEXT[],
                framework TEXT[],
                task_type TEXT[],
                raw_json JSONB,
                collected_at TIMESTAMP,
                task_id VARCHAR(50),
                status VARCHAR(20),
                doi TEXT
            )
        """)
        insert_sql = f"""
            INSERT INTO {table} (
                source_id, external_id, title, model_type, domain, description,
                url, author, license, tags, stars, created_at_source, updated_at_source,
                language, framework, task_type, raw_json, collected_at, task_id, status, doi
            ) VALUES %s
            ON CONFLICT (external_id) DO NOTHING
        """
        values = []
        for rec in records:
            values.append((
                rec['source_id'], rec['external_id'], rec['title'], rec['model_type'],
                rec['domain'], rec['description'], rec['url'], rec['author'],
                rec.get('license'), rec['tags'], rec.get('stars'),
                rec.get('created_at_source'), rec.get('updated_at_source'),
                rec.get('language'), rec.get('framework'), rec.get('task_type'),
                json.dumps(rec.get('raw_json', {})), rec['collected_at'],
                rec['task_id'], rec['status'], rec.get('doi')
            ))
        execute_values(cursor, insert_sql, values)
        conn.commit()
        print(f"PostgreSQL: сохранено {len(records)} записей в {table}")
    except Exception as e:
        print(f"Ошибка PostgreSQL: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

# ----------------------------------------------------------------------
#  Вспомогательные функции для периодов
# ----------------------------------------------------------------------

def iter_months(start_date: str, end_date: str):
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    current = start.replace(day=1)
    while current <= end:
        last_day = monthrange(current.year, current.month)[1]
        month_start = current.strftime("%Y-%m-%d")
        month_end = current.replace(day=last_day).strftime("%Y-%m-%d")
        if datetime.strptime(month_end, "%Y-%m-%d") > end:
            month_end = end.strftime("%Y-%m-%d")
        yield month_start, month_end
        if current.month == 12:
            current = current.replace(year=current.year+1, month=1)
        else:
            current = current.replace(month=current.month+1)

def combine_all_results(task_id: str, save_csv: bool, save_excel: bool):
    import glob
    all_records = []
    csv_files = glob.glob(str(config.DATA_DIR / "raw_items_*.csv"))
    if not csv_files:
        csv_files = [config.CSV_PATH] if config.CSV_PATH.exists() else []
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
            all_records.append(df)
        except Exception as e:
            print(f"Ошибка чтения {csv_file}: {e}")
    if all_records:
        combined_df = pd.concat(all_records, ignore_index=True).drop_duplicates(subset=['external_id'])
        if save_csv:
            combined_df.to_csv(config.CSV_PATH, index=False)
            print(f"Общий CSV сохранён: {config.CSV_PATH} ({len(combined_df)} записей)")
        if save_excel:
            combined_df.to_excel(config.EXCEL_PATH, index=False, engine='openpyxl')
            print(f"Общий Excel сохранён: {config.EXCEL_PATH} ({len(combined_df)} записей)")

# ----------------------------------------------------------------------
#  Основная функция для периода (месяц за месяцем)
# ----------------------------------------------------------------------

def parse_arxiv_period(
    start_date: str,
    end_date: str,
    categories: Optional[List[str]] = None,
    output_csv: bool = True,
    output_excel: bool = True,
    output_postgresql: bool = False,
    postgres_config: Optional[Dict[str, Any]] = None,
    use_cache: bool = True,
    use_progress: bool = True,
    use_request_tracker: bool = True,
    delay_seconds: Optional[float] = None,
    max_total_per_month: int = 30000,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    if task_id is None:
        task_id = datetime.now().strftime("task_%Y%m%d_%H%M%S")
    logger = setup_logger(f"{task_id}_period")
    logger.info(f"Запуск выгрузки за период {start_date} – {end_date}")

    period_progress_file = config.DATA_DIR / f"period_progress_{task_id}.json"
    processed_months = set()
    if period_progress_file.exists():
        with open(period_progress_file, 'r') as f:
            data = json.load(f)
            processed_months = set(data.get('months', []))

    all_metrics = []
    total_records = 0

    for month_start, month_end in iter_months(start_date, end_date):
        month_key = f"{month_start}_{month_end}"
        if month_key in processed_months:
            logger.info(f"Месяц {month_start} – {month_end} уже обработан, пропускаем")
            continue

        logger.info(f"\n--- Обработка месяца: {month_start} – {month_end} ---")
        try:
            metrics = parse_arxiv(
                start_date=month_start,
                end_date=month_end,
                categories=categories,
                output_csv=output_csv,           # теперь передаём флаг
                output_excel=output_excel,       # передаём флаг
                output_postgresql=output_postgresql,
                postgres_config=postgres_config,
                use_cache=use_cache,
                use_progress=use_progress,
                use_request_tracker=use_request_tracker,
                delay_seconds=delay_seconds,
                max_total_results=max_total_per_month,
                task_id=f"{task_id}_{month_start.replace('-', '')}"
            )
            if metrics['status'] == 'success':
                total_records += metrics['records_count']
                all_metrics.append(metrics)
                processed_months.add(month_key)
                with open(period_progress_file, 'w') as f:
                    json.dump({'months': list(processed_months)}, f)
            else:
                logger.error(f"Ошибка в месяце {month_start}: {metrics.get('message')}")
        except Exception as e:
            logger.exception(f"Критическая ошибка в месяце {month_start}: {e}")

    if output_csv or output_excel:
        combine_all_results(task_id, output_csv, output_excel)

    if output_postgresql:
        logger.info("Данные уже сохранены в PostgreSQL по месяцам (если output_postgresql был True в parse_arxiv)")

    summary = {
        "status": "success",
        "task_id": task_id,
        "period": f"{start_date} to {end_date}",
        "months_processed": len(processed_months),
        "total_records": total_records,
        "monthly_metrics": all_metrics
    }
    summary_file = config.LOGS_DIR / f"period_summary_{task_id}.json"
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"\n✅ Выгрузка завершена. Всего статей: {total_records} за {len(processed_months)} месяцев.")
    return summary

# ----------------------------------------------------------------------
#  Основная функция parse_arxiv (для одного месяца)
# ----------------------------------------------------------------------

def parse_arxiv(
    start_date: str,
    end_date: str,
    categories: Optional[List[str]] = None,
    output_csv: bool = True,
    output_excel: bool = True,
    output_postgresql: bool = False,
    postgres_config: Optional[Dict[str, Any]] = None,
    use_cache: bool = True,
    clear_cache: bool = False,
    use_progress: bool = True,
    use_request_tracker: bool = True,
    delay_seconds: Optional[float] = None,
    max_total_results: Optional[int] = None,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    if task_id is None:
        task_id = datetime.now().strftime("task_%Y%m%d_%H%M%S")
    logger = setup_logger(task_id)
    logger.info(f"Запуск парсера arXiv: {start_date} — {end_date}")
    logger.info(f"Категории: {categories or config.CATEGORIES}")

    # Проверка API
    test_url = f"{config.API_BASE_URL}?search_query=cat:cs.AI&max_results=1"
    try:
        resp = requests.get(test_url, headers={"User-Agent": config.USER_AGENT}, timeout=10)
        resp.raise_for_status()
        logger.info("API arXiv доступен")
    except Exception as e:
        logger.error(f"API недоступен: {e}")
        return {"status": "error", "message": f"API недоступен: {e}"}

    robots_delay = check_robots_txt(config.USER_AGENT)
    if robots_delay is not None:
        logger.info(f"Robots.txt предписывает Crawl-delay = {robots_delay} с")
        if delay_seconds is None:
            delay_seconds = robots_delay
    if delay_seconds is None:
        delay_seconds = config.BASE_DELAY_SECONDS
    logger.info(f"Используемая задержка: {delay_seconds} с")

    progress = None
    if use_progress:
        progress = ProgressTracker(config.PROGRESS_FILE)
        logger.info(f"Загружено уникальных ID статей: {len(progress.processed_ids)}")

    request_tracker = None
    if use_request_tracker:
        request_tracker = RequestTracker(config.REQUEST_LOG_FILE)
        logger.info(f"Загружено уникальных запросов: {len(request_tracker.processed_urls)}")

    http_cache = None
    if use_cache:
        http_cache = HttpCache(config.CACHE_DIR, config.CACHE_TTL_SECONDS)
        if clear_cache:
            http_cache.clear()
            logger.info("Кэш очищен")

    max_total = max_total_results if max_total_results is not None else config.MAX_TOTAL_RESULTS
    max_per_req = config.MAX_RESULTS_PER_REQUEST

    start_time = time.time()
    try:
        records = fetch_arxiv_papers(
            start_date=start_date,
            end_date=end_date,
            categories=categories or config.CATEGORIES,
            progress_tracker=progress,
            request_tracker=request_tracker,
            task_id=task_id,
            logger=logger,
            config_obj=config,
            max_results_per_request=max_per_req,
            max_total=max_total,
            delay_seconds=delay_seconds,
            cache=http_cache
        )
    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")
        return {"status": "error", "message": str(e)}

    duration = time.time() - start_time
    metrics = {
        "status": "success",
        "task_id": task_id,
        "start_date": start_date,
        "end_date": end_date,
        "records_count": len(records),
        "duration_seconds": duration,
        "records_per_second": len(records) / duration if duration > 0 else 0,
        "delay_seconds": delay_seconds,
        "categories": categories or config.CATEGORIES
    }
    logger.info(f"Парсинг завершён. Получено записей: {len(records)}. Время: {duration:.2f} с")

        # --- Сохранение ---
    if records:
        # Всегда сохраняем промежуточные файлы для последующего объединения,
        # если включён output_csv/output_excel, но при этом не перезаписываем общий файл.
        # Используем суффикс из task_id, если он содержит дату.
        suffix = None
        if task_id and ('_' in task_id):
            # Извлекаем последнюю часть task_id (например, 20260101)
            suffix = task_id.split('_')[-1]
        else:
            # Для одиночного запуска без периода используем даты
            suffix = f"{start_date.replace('-', '')}_{end_date.replace('-', '')}"
        
        if output_csv:
            if suffix:
                csv_path = config.DATA_DIR / f"raw_items_{suffix}.csv"
            else:
                csv_path = config.CSV_PATH
            save_to_csv(records, csv_path)
        if output_excel:
            if suffix:
                excel_path = config.DATA_DIR / f"raw_items_{suffix}.xlsx"
            else:
                excel_path = config.EXCEL_PATH
            save_to_excel(records, excel_path)
        if output_postgresql:
            pg_cfg = postgres_config if postgres_config else config.PG_CONFIG
            save_to_postgresql(records, pg_cfg)

    return metrics

# ----------------------------------------------------------------------
#  Точка входа
# ----------------------------------------------------------------------
if __name__ == "__main__":
    result = parse_arxiv_period(
        start_date="2026-05-01",
        end_date="2026-05-31",
        categories=["cs.AI", "cs.CV", "cs.LG", "cs.CL"],
        output_csv=True,
        output_excel=True,
        output_postgresql=False,
        use_cache=True,
        use_progress=False,
        use_request_tracker=True,
        max_total_per_month=30000
    )
    print("\n" + "="*60)
    print("РЕЗУЛЬТАТ ВЫГРУЗКИ ЗА ПЕРИОД:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("="*60)
