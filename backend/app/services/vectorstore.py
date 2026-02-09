import re
from typing import List, TYPE_CHECKING
from langchain_core.documents import Document
from backend.app.services.embeddings import get_embeddings
from backend.app.core.config import settings

if TYPE_CHECKING:
    from langchain_community.vectorstores import Chroma


def _sanitize_collection_name(library_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", library_id)
    return f"library_{safe}"


def get_store(library_id: str):
    if not settings.ALLOW_LOCAL_FALLBACK:
        raise RuntimeError("Local vectorstore fallback is disabled.")
    try:
        from langchain_community.vectorstores import Chroma  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "Local vectorstore fallback requires optional Chroma deps. "
            "Install 'chromadb' (and langchain-community extras) to enable it."
        ) from exc
    return Chroma(
        collection_name=_sanitize_collection_name(library_id),
        embedding_function=get_embeddings(),
        persist_directory=settings.CHROMA_DB_PATH,
    )


def add_documents(library_id: str, documents: List[Document]):
    if not settings.ALLOW_LOCAL_FALLBACK:
        return
    store = get_store(library_id)
    store.add_documents(documents)
    store.persist()


def search(library_id: str, query: str, k: int = 5) -> List[Document]:
    if not settings.ALLOW_LOCAL_FALLBACK:
        return []
    store = get_store(library_id)
    return store.similarity_search(query, k=k)
