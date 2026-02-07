from fastapi import APIRouter, Depends, Query
from backend.app.core.firebase import db
from backend.app.core.auth import require_superadmin
from backend.app.core.config import settings
from backend.app.schemas.user import UserProfile
from typing import List, Dict
from datetime import datetime, timedelta
from pydantic import BaseModel

router = APIRouter()


def _allowed_models() -> List[str]:
    return [m.strip() for m in (settings.LLM_ALLOWED_MODELS or "").split(",") if m.strip()]


def _sanitize_model(model: str, allowed: List[str], default_model: str):
    m = (model or "").strip()
    if m in allowed:
        return m
    return default_model


class LLMRoutingUpdate(BaseModel):
    global_model: str
    fallback_model: str
    allow_assistant_override: bool = True

@router.get("/stats")
async def get_system_stats(current_user: UserProfile = Depends(require_superadmin)):
    """Get global statistics for the system. Superadmin only."""
    assts = db.collection("assistants").get()
    libs = db.collection("libraries").get()
    users = db.collection("users").get()
    images = db.collection("image_assets").get()
    
    return {
        "total_assistants": len(assts),
        "total_libraries": len(libs),
        "total_users": len(users),
        "total_images": len(images),
        "system_version": "V9.0"
    }

@router.get("/all-resources")
async def get_all_resources(current_user: UserProfile = Depends(require_superadmin)):
    """Admin view: List all assistants and libraries regardless of owner. Superadmin only."""
    assts = [doc.to_dict() for doc in db.collection("assistants").stream()]
    libs = [doc.to_dict() for doc in db.collection("libraries").stream()]
    users = [doc.to_dict() for doc in db.collection("users").stream()]
    projects = [doc.to_dict() for doc in db.collection("projects").stream()]
    
    return {
        "assistants": assts,
        "libraries": libs,
        "users": users,
        "projects": projects
    }


@router.get("/llm-routing")
async def get_llm_routing(current_user: UserProfile = Depends(require_superadmin)):
    allowed = _allowed_models()
    defaults = {
        "global_model": settings.LLM_DEFAULT_MODEL,
        "fallback_model": settings.LLM_FALLBACK_MODEL,
        "allow_assistant_override": True,
    }
    doc = db.collection("system_settings").document("llm_routing").get()
    data = doc.to_dict() if doc.exists else {}

    global_model = _sanitize_model(data.get("global_model"), allowed, defaults["global_model"])
    fallback_model = _sanitize_model(data.get("fallback_model"), allowed, defaults["fallback_model"])
    allow_override = bool(data.get("allow_assistant_override", defaults["allow_assistant_override"]))

    return {
        "allowed_models": allowed,
        "global_model": global_model,
        "fallback_model": fallback_model,
        "allow_assistant_override": allow_override
    }


@router.put("/llm-routing")
async def update_llm_routing(
    request: LLMRoutingUpdate,
    current_user: UserProfile = Depends(require_superadmin)
):
    allowed = _allowed_models()
    if not allowed:
        return {"message": "No allowed models configured.", "allowed_models": []}

    global_model = _sanitize_model(request.global_model, allowed, settings.LLM_DEFAULT_MODEL)
    fallback_model = _sanitize_model(request.fallback_model, allowed, settings.LLM_FALLBACK_MODEL)

    payload = {
        "global_model": global_model,
        "fallback_model": fallback_model,
        "allow_assistant_override": bool(request.allow_assistant_override),
        "updated_at": datetime.utcnow(),
        "updated_by": current_user.id
    }
    db.collection("system_settings").document("llm_routing").set(payload, merge=True)
    payload["allowed_models"] = allowed
    return payload


@router.get("/gdpr-audit")
async def get_gdpr_audit(
    project_id: str = Query(None),
    library_id: str = Query(None),
    status: str = Query("all"),
    days: int = Query(90, ge=1, le=3650),
    limit: int = Query(200, ge=1, le=1000),
    current_user: UserProfile = Depends(require_superadmin)
):
    """
    GDPR audit log across library documents with optional filtering by project/library.
    Superadmin only.
    """
    libs = [doc.to_dict() for doc in db.collection("libraries").stream()]

    if project_id:
        proj_ref = db.collection("projects").document(project_id).get()
        if not proj_ref.exists:
            return {"rows": [], "total": 0}
        project_lib_ids = set(proj_ref.to_dict().get("library_ids", []))
        libs = [l for l in libs if l.get("id") in project_lib_ids]

    if library_id:
        libs = [l for l in libs if l.get("id") == library_id]

    since = datetime.utcnow() - timedelta(days=days)
    rows = []

    for lib in libs:
        lib_id = lib.get("id")
        lib_name = lib.get("name", "Okänt bibliotek")
        try:
            docs = db.collection("libraries").document(lib_id).collection("documents").stream()
            for d in docs:
                data = d.to_dict()
                if not data.get("gdpr_name_scrub"):
                    continue
                gdpr_status = (data.get("gdpr_scrub_status") or "unknown").lower()
                if status and status.lower() != "all" and gdpr_status != status.lower():
                    continue

                ts = data.get("gdpr_scrub_at") or data.get("uploaded_at")
                if ts and hasattr(ts, "replace"):
                    ts_val = ts.replace(tzinfo=None)
                else:
                    ts_val = None
                if ts_val and ts_val < since:
                    continue

                rows.append({
                    "library_id": lib_id,
                    "library_name": lib_name,
                    "doc_id": data.get("id"),
                    "filename": data.get("filename"),
                    "uploaded_at": data.get("uploaded_at"),
                    "gdpr_scrub_at": data.get("gdpr_scrub_at"),
                    "gdpr_scrub_status": data.get("gdpr_scrub_status"),
                    "gdpr_scrub_mode": data.get("gdpr_scrub_mode"),
                    "gdpr_scrub_provider": data.get("gdpr_scrub_provider"),
                    "gdpr_scrub_model": data.get("gdpr_scrub_model"),
                    "gdpr_scrub_initiated_by": data.get("gdpr_scrub_initiated_by"),
                    "gdpr_scrub_findings": data.get("gdpr_scrub_findings"),
                    "gdpr_scrub_replacements": data.get("gdpr_scrub_replacements"),
                    "gdpr_scrub_cards_created": data.get("gdpr_scrub_cards_created"),
                    "status": data.get("status")
                })
        except Exception as e:
            print(f"GDPR audit read failed for library {lib_id}: {e}")
            continue

    def _sort_key(row):
        return row.get("gdpr_scrub_at") or row.get("uploaded_at") or datetime.min

    rows.sort(key=_sort_key, reverse=True)
    limited = rows[:limit]
    return {"rows": limited, "total": len(rows)}
