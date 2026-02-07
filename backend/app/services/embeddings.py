import os
import hashlib
import math
import re
from typing import List
from langchain_community.embeddings import SentenceTransformerEmbeddings

_embeddings = None


class HashEmbeddings:
    """Deterministic, offline-friendly embeddings fallback."""

    def __init__(self, dim: int = 384):
        self.dim = dim
        self._token_re = re.compile(r"\w+", re.UNICODE)

    def _embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = self._token_re.findall(text.lower())
        for tok in tokens:
            h = hashlib.sha256(tok.encode("utf-8")).hexdigest()
            idx = int(h[:8], 16) % self.dim
            vec[idx] += 1.0
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._embed(text)


def get_embeddings():
    """
    Lazily initialize embeddings to avoid network calls at import time.
    Set EMBEDDINGS_DISABLED=1 to hard-disable (raises RuntimeError on use).
    """
    global _embeddings
    if _embeddings is not None:
        return _embeddings

    if os.getenv("EMBEDDINGS_DISABLED") == "1":
        raise RuntimeError("Embeddings are disabled via EMBEDDINGS_DISABLED=1")

    model_name = os.getenv("EMBEDDINGS_MODEL", "all-MiniLM-L6-v2")
    try:
        _embeddings = SentenceTransformerEmbeddings(model_name=model_name)
    except Exception as exc:
        # Fall back to offline hash embeddings when model download is unavailable
        print(f"Embeddings init failed for '{model_name}': {exc}. Falling back to HashEmbeddings.")
        _embeddings = HashEmbeddings()
    return _embeddings
