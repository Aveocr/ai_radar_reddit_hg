FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libomp5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Pre-download sentence-transformers model at build time
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
print('[BUILD] Downloading all-MiniLM-L6-v2 model...'); \
SentenceTransformer('all-MiniLM-L6-v2'); \
print('[BUILD] Model downloaded successfully'); \
"

COPY . /app

EXPOSE 8000

VOLUME /app/.cache/huggingface

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
