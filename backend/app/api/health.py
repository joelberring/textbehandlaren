from fastapi import APIRouter
from backend.app.core.config import settings
from backend.app.core.firebase import db
from backend.app.services.embeddings import get_embeddings
import time

router = APIRouter()


@router.get("/")
async def health_check():
    status = {
        "status": "ok",
        "timestamp": int(time.time()),
        "firebase": {"ok": False},
        "anthropic": {"configured": bool(settings.ANTHROPIC_API_KEY)},
        "mistral": {"configured": bool(settings.MISTRAL_API_KEY)},
        "embeddings": {"checked": False, "ok": None},
        "local_fallback": settings.ALLOW_LOCAL_FALLBACK,
        "dev_auth_bypass": bool(settings.DEV_AUTH_BYPASS and settings.ENVIRONMENT == "development"),
    }

    # Firebase/Firestore check
    try:
        _ = list(db.collection("system_settings").limit(1).stream())
        status["firebase"]["ok"] = True
    except Exception as e:
        status["firebase"]["ok"] = False
        status["firebase"]["error"] = str(e)
        status["status"] = "degraded"

    # Embeddings check (optional to avoid heavy downloads in prod)
    if settings.HEALTH_CHECK_EMBEDDINGS:
        try:
            emb = get_embeddings()
            _ = emb.embed_query("health-check")
            status["embeddings"]["checked"] = True
            status["embeddings"]["ok"] = True
        except Exception as e:
            status["embeddings"]["checked"] = True
            status["embeddings"]["ok"] = False
            status["embeddings"]["error"] = str(e)
            status["status"] = "degraded"

    return status
