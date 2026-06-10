"""
Download the embedding model for offline use.
Saves to models/fastembed_cache/ so Docker can COPY it.

Usage:
    python scripts/download_model.py

    Set EMBEDDING_MODEL env var to change model:
        EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 python scripts/download_model.py

Manual download (if script times out):
    1. Go to https://huggingface.co/qdrant/all-MiniLM-L6-v2-onnx/tree/main
    2. Download: model.onnx, config.json, tokenizer.json, tokenizer_config.json, special_tokens_map.json
    3. Get commit hash from the page URL or create one
    4. Place files in:
         models/fastembed_cache/models--qdrant--all-MiniLM-L6-v2-onnx/snapshots/<hash>/
    5. Create: models/fastembed_cache/models--qdrant--all-MiniLM-L6-v2-onnx/refs/main
       with content: <hash>
"""

import os
import sys

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(PROJECT_DIR, "models", "fastembed_cache")

MODEL_NAME = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    print(f"[DOWNLOAD] Model: {MODEL_NAME}")
    print(f"[DOWNLOAD] Cache:  {CACHE_DIR}")
    print()

    from fastembed import TextEmbedding

    model = TextEmbedding(MODEL_NAME, cache_dir=CACHE_DIR)
    _ = list(model.embed(["test query"]))
    print(f"[DOWNLOAD] OK! dim={model.embedding_size}")

    cache_path = os.path.join(CACHE_DIR, f"models--{MODEL_NAME.replace('/', '--')}")
    print(f"[DOWNLOAD] Files in: {cache_path}")
    for dirpath, dirnames, filenames in os.walk(cache_path):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            size_mb = os.path.getsize(fp) / 1024 / 1024
            print(f"  {fp}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
