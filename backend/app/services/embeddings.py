import os
import hashlib
import math
import re
from typing import List, Optional

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


class MistralEmbeddings:
    """Remote embeddings via Mistral API (avoids heavy local ML deps in serverless)."""

    def __init__(self, api_key: str, model: str = "mistral-embed", output_dimension: Optional[int] = None):
        if not api_key:
            raise ValueError("Mistral API key is missing.")
        # Lazy import so this file can be used without mistralai installed.
        from mistralai import Mistral  # type: ignore

        self.client = Mistral(api_key=api_key)
        self.model = model
        self.output_dimension = output_dimension

    def _request(self, texts: List[str]) -> List[List[float]]:
        kwargs = {"model": self.model, "inputs": texts}
        if self.output_dimension:
            kwargs["output_dimension"] = int(self.output_dimension)

        resp = self.client.embeddings.create(**kwargs)
        data = list(getattr(resp, "data", []) or [])
        # Keep stable ordering if the SDK returns indexed items.
        data.sort(key=lambda d: int(getattr(d, "index", 0) or 0))
        vectors = []
        for item in data:
            vec = getattr(item, "embedding", None) or []
            vectors.append(list(vec))
        return vectors

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # Basic batching to keep request sizes reasonable.
        batch_size = int(os.getenv("MISTRAL_EMBED_BATCH", "32") or "32")
        batch_size = max(1, min(batch_size, 128))

        out: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            out.extend(self._request(texts[i : i + batch_size]))
        return out

    def embed_query(self, text: str) -> List[float]:
        vecs = self.embed_documents([text])
        return vecs[0] if vecs else []


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

    provider = (os.getenv("EMBEDDINGS_PROVIDER") or "").strip().upper()
    dim_env = os.getenv("EMBEDDINGS_DIM")
    dim = int(dim_env) if dim_env and dim_env.isdigit() else 384

    def _init_hash():
        return HashEmbeddings(dim=dim)

    def _init_sentence_transformers():
        model_name = os.getenv("EMBEDDINGS_MODEL", "all-MiniLM-L6-v2")
        from langchain_community.embeddings import SentenceTransformerEmbeddings  # type: ignore

        return SentenceTransformerEmbeddings(model_name=model_name)

    def _init_mistral():
        api_key = os.getenv("MISTRAL_API_KEY", "")
        model = os.getenv("MISTRAL_EMBED_MODEL", "mistral-embed")
        out_dim_env = os.getenv("MISTRAL_EMBED_DIM") or os.getenv("EMBEDDINGS_DIM")
        out_dim = int(out_dim_env) if out_dim_env and str(out_dim_env).isdigit() else None
        return MistralEmbeddings(api_key=api_key, model=model, output_dimension=out_dim)

    # Explicit selection
    if provider in {"MISTRAL", "MISTRALAI"}:
        _embeddings = _init_mistral()
        return _embeddings
    if provider in {"SENTENCE", "SENTENCE_TRANSFORMERS", "LOCAL"}:
        _embeddings = _init_sentence_transformers()
        return _embeddings
    if provider in {"HASH", "OFFLINE"}:
        _embeddings = _init_hash()
        return _embeddings

    # Auto selection: prefer Mistral if configured (keeps deploy small), else local model, else hash.
    if os.getenv("MISTRAL_API_KEY"):
        try:
            _embeddings = _init_mistral()
            return _embeddings
        except Exception as exc:
            print(f"Mistral embeddings init failed: {exc}. Falling back to local/hash.")

    try:
        _embeddings = _init_sentence_transformers()
    except Exception as exc:
        # Fall back to offline hash embeddings when local ML deps are unavailable.
        print(f"Sentence-transformers embeddings init failed: {exc}. Falling back to HashEmbeddings.")
        _embeddings = _init_hash()
    return _embeddings
