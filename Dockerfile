FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Pre-download fastembed model at build time
RUN python -c "\
from fastembed import TextEmbedding; \
print('[BUILD] Downloading BAAI/bge-small-en-v1.5 model...'); \
TextEmbedding('BAAI/bge-small-en-v1.5'); \
print('[BUILD] Model downloaded successfully'); \
"

COPY . /app

EXPOSE 8000

VOLUME /app/.cache/huggingface

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
