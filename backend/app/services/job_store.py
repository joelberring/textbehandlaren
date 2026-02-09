import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


MAX_PARTIAL_ANSWER_CHARS = 12000  # keep polling responses small; full answer is returned on completion
MAX_ANSWER_PREVIEW_CHARS = 8000


@dataclass
class ChatJob:
    id: str
    user_id: str
    assistant_id: str
    query: str
    conversation_id: Optional[str] = None
    project_id: Optional[str] = None

    status: str = "queued"  # queued | running | completed | failed
    stage: str = "queued"
    progress: int = 0  # 0-100
    message: str = ""

    partial_answer: str = ""
    answer: str = ""
    sources: list = field(default_factory=list)
    matched_images: list = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "assistant_id": self.assistant_id,
            "conversation_id": self.conversation_id,
            "project_id": self.project_id,
            "query": self.query,
            "status": self.status,
            "stage": self.stage,
            "progress": int(self.progress or 0),
            "message": self.message,
            "partial_answer": self.partial_answer,
            "answer": self.answer,
            "sources": self.sources,
            "matched_images": self.matched_images,
            "debug": self.debug,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, ChatJob] = {}
        self._lock = asyncio.Lock()

    async def create_chat_job(
        self,
        user_id: str,
        assistant_id: str,
        query: str,
        conversation_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ChatJob:
        job_id = str(uuid.uuid4())
        job = ChatJob(
            id=job_id,
            user_id=str(user_id),
            assistant_id=str(assistant_id),
            query=str(query or ""),
            conversation_id=str(conversation_id) if conversation_id else None,
            project_id=str(project_id) if project_id else None,
        )
        async with self._lock:
            self._jobs[job_id] = job
        return job

    async def get(self, job_id: str) -> Optional[ChatJob]:
        async with self._lock:
            return self._jobs.get(str(job_id))

    async def update(self, job_id: str, **fields) -> Optional[ChatJob]:
        async with self._lock:
            job = self._jobs.get(str(job_id))
            if not job:
                return None

            for k, v in fields.items():
                if not hasattr(job, k):
                    continue
                if k == "progress":
                    try:
                        v = max(0, min(int(v), 100))
                    except Exception:
                        v = job.progress
                if k == "partial_answer" and isinstance(v, str) and len(v) > MAX_PARTIAL_ANSWER_CHARS:
                    v = v[-MAX_PARTIAL_ANSWER_CHARS:]
                setattr(job, k, v)

            job.updated_at = time.time()
            return job


class FirestoreJobStore:
    """
    Firestore-backed job store (safe for Cloud Run multi-instance polling).

    Note: We intentionally do NOT persist full `answer`/`sources` because Firestore documents have a strict size limit.
    The frontend can read the full result from the conversation record after completion.
    """

    def __init__(self, collection_name: str = "chat_jobs"):
        from backend.app.core.firebase import db  # lazy import to avoid early init issues
        self._col = db.collection(collection_name)

    def _doc_to_job(self, doc_id: str, data: dict) -> ChatJob:
        data = data or {}
        return ChatJob(
            id=doc_id,
            user_id=str(data.get("user_id") or ""),
            assistant_id=str(data.get("assistant_id") or ""),
            query=str(data.get("query") or ""),
            conversation_id=data.get("conversation_id"),
            project_id=data.get("project_id"),
            status=str(data.get("status") or "queued"),
            stage=str(data.get("stage") or "queued"),
            progress=int(data.get("progress") or 0),
            message=str(data.get("message") or ""),
            partial_answer=str(data.get("partial_answer") or ""),
            # Persist only a short preview for safety.
            answer=str(data.get("answer_preview") or ""),
            sources=[],
            matched_images=[],
            debug={},
            error=str(data.get("error") or ""),
            created_at=float(data.get("created_at") or time.time()),
            updated_at=float(data.get("updated_at") or time.time()),
        )

    async def create_chat_job(
        self,
        user_id: str,
        assistant_id: str,
        query: str,
        conversation_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> ChatJob:
        job_id = str(uuid.uuid4())
        now = time.time()
        payload = {
            "user_id": str(user_id),
            "assistant_id": str(assistant_id),
            "query": str(query or ""),
            "conversation_id": str(conversation_id) if conversation_id else None,
            "project_id": str(project_id) if project_id else None,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "",
            "partial_answer": "",
            "answer_preview": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        self._col.document(job_id).set(payload)
        return self._doc_to_job(job_id, payload)

    async def get(self, job_id: str) -> Optional[ChatJob]:
        doc = self._col.document(str(job_id)).get()
        if not doc.exists:
            return None
        return self._doc_to_job(doc.id, doc.to_dict())

    async def update(self, job_id: str, **fields) -> Optional[ChatJob]:
        job_id = str(job_id)
        # Only persist small/progress-related fields.
        updates = {}

        if "status" in fields:
            updates["status"] = str(fields.get("status") or "")
        if "stage" in fields:
            updates["stage"] = str(fields.get("stage") or "")
        if "progress" in fields:
            try:
                updates["progress"] = max(0, min(int(fields.get("progress")), 100))
            except Exception:
                pass
        if "message" in fields:
            updates["message"] = str(fields.get("message") or "")
        if "partial_answer" in fields:
            pa = fields.get("partial_answer")
            if isinstance(pa, str):
                if len(pa) > MAX_PARTIAL_ANSWER_CHARS:
                    pa = pa[-MAX_PARTIAL_ANSWER_CHARS:]
                updates["partial_answer"] = pa
        if "answer" in fields:
            ans = fields.get("answer")
            if isinstance(ans, str):
                if len(ans) > MAX_ANSWER_PREVIEW_CHARS:
                    ans = ans[:MAX_ANSWER_PREVIEW_CHARS]
                updates["answer_preview"] = ans
        if "error" in fields:
            updates["error"] = str(fields.get("error") or "")

        if not updates:
            return await self.get(job_id)

        updates["updated_at"] = time.time()
        self._col.document(job_id).set(updates, merge=True)
        return await self.get(job_id)


def _select_job_store():
    backend = (os.getenv("JOB_STORE_BACKEND") or "").strip().lower()
    if backend in {"firestore", "fs"}:
        return FirestoreJobStore()

    try:
        from backend.app.core.config import settings
        if (settings.ENVIRONMENT or "").strip().lower() == "production":
            return FirestoreJobStore()
    except Exception:
        pass

    return JobStore()


# Default store (in-memory in dev/tests, Firestore in production).
job_store = _select_job_store()
