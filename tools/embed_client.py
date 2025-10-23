import os
from typing import List, Optional, Any, cast
from google import genai
from google.genai import types
from dotenv import load_dotenv, find_dotenv

# Carga .env antes de construir el cliente (robustece ejecución bajo pm2)
load_dotenv(find_dotenv())

# Usa GOOGLE_API_KEY del entorno (evita hardcodear claves)
client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY", ""))
_MAX_BATCH = 100

def _batched(xs: List[str], n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

def embed_texts(
    texts: List[str],
    dim: int = 768,                               # debe coincidir con tu columna vector(768)
    task_type: Optional[str] = None               # "RETRIEVAL_DOCUMENT", etc.
) -> List[List[float]]:
    cfg = types.EmbedContentConfig(output_dimensionality=dim, task_type=task_type) if task_type \
          else types.EmbedContentConfig(output_dimensionality=dim)

    out: List[List[float]] = []
    for chunk in _batched(texts, _MAX_BATCH):
        res = client.models.embed_content(
            model="text-embedding-004",           # sustituye gemini-embedding-001
            contents=chunk,
            config=cfg,
        )
        embs = cast(List[Any], getattr(res, "embeddings", []) or [])
        for e in embs:
            vals = cast(List[float], getattr(e, "values", None) or [])
            if vals:
                out.append(vals)
    return out
