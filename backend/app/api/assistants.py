from fastapi import APIRouter, HTTPException, Depends
from typing import List
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user
from backend.app.core.config import settings
from backend.app.schemas import assistant
from backend.app.schemas.user import UserProfile, UserRole
import uuid
from datetime import datetime

router = APIRouter()


def _sanitize_library_priority_profile(raw_profile, allowed_library_ids):
    allowed = set(allowed_library_ids or [])
    if not isinstance(raw_profile, list):
        return []

    cleaned = []
    seen = set()
    for item in raw_profile:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        elif hasattr(item, "dict"):
            item = item.dict()
        if not isinstance(item, dict):
            continue
        lib_id = item.get("library_id")
        if not lib_id or lib_id not in allowed or lib_id in seen:
            continue
        try:
            prio = max(0, min(int(item.get("priority", 50)), 100))
        except Exception:
            prio = 50
        cleaned.append({"library_id": lib_id, "priority": prio})
        seen.add(lib_id)
    return cleaned


def _sanitize_model_preference(raw_model):
    model = (raw_model or "").strip()
    return model or None

@router.post("/", response_model=assistant.AssistantResponse)
async def create_assistant(
    request: assistant.AssistantCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    assistant_id = str(uuid.uuid4())
    library_ids = request.library_ids or []
    library_priority_profile = _sanitize_library_priority_profile(
        request.library_priority_profile,
        library_ids
    )
    
    assistant_data = {
        "id": assistant_id,
        "name": request.name,
        "system_prompt": request.system_prompt,
        "user_id": current_user.id,
        "library_ids": library_ids,
        "library_priority_profile": library_priority_profile,
        "template_id": request.template_id,
        "model_preference": _sanitize_model_preference(request.model_preference),
        "interpret_images": request.interpret_images,
        "created_at": datetime.utcnow()
    }
    
    db.collection("assistants").document(assistant_id).set(assistant_data)
    return assistant_data

@router.get("/", response_model=List[assistant.AssistantResponse])
async def list_assistants(current_user: UserProfile = Depends(get_current_user)):
    assts = []
    
    # Assistants owned by user
    owned_docs = db.collection("assistants").where("user_id", "==", current_user.id).stream()
    for doc in owned_docs:
        assts.append(doc.to_dict())
    
    # Admin/Superadmin can see all assistants
    if current_user.role in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        all_docs = db.collection("assistants").stream()
        existing_ids = {a["id"] for a in assts}
        for doc in all_docs:
            data = doc.to_dict()
            if data["id"] not in existing_ids:
                assts.append(data)
    
    return assts

@router.get("/{assistant_id}", response_model=assistant.AssistantResponse)
async def get_assistant(assistant_id: str, current_user: UserProfile = Depends(get_current_user)):
    doc = db.collection("assistants").document(assistant_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Assistant not found")
    
    data = doc.to_dict()
    # User can only view their own assistants unless admin
    if data.get("user_id") != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to view this assistant")
    
    return data

@router.put("/{assistant_id}", response_model=assistant.AssistantResponse)
async def update_assistant(
    assistant_id: str, 
    request: assistant.AssistantCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    asst_ref = db.collection("assistants").document(assistant_id)
    doc = asst_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Assistant not found")
    
    asst_data = doc.to_dict()
    # Owner or Admin can update
    if asst_data.get("user_id") != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to update this assistant")
    
    library_ids = request.library_ids or []
    library_priority_profile = _sanitize_library_priority_profile(
        request.library_priority_profile,
        library_ids
    )
    updated_data = {
        "name": request.name,
        "system_prompt": request.system_prompt,
        "library_ids": library_ids,
        "library_priority_profile": library_priority_profile,
        "template_id": request.template_id,
        "model_preference": _sanitize_model_preference(request.model_preference),
        "interpret_images": request.interpret_images,
        "updated_at": datetime.utcnow()
    }
    
    asst_ref.update(updated_data)
    
    full_data = asst_data.copy()
    full_data.update(updated_data)
    return full_data

@router.delete("/{assistant_id}")
async def delete_assistant(
    assistant_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    asst_ref = db.collection("assistants").document(assistant_id)
    doc = asst_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Assistant not found")
    
    asst_data = doc.to_dict()
    # Owner or Admin can delete. In development mode, we allow all for ease of use.
    is_owner = asst_data.get("user_id") == current_user.id
    is_admin = current_user.role in [UserRole.ADMIN, UserRole.SUPERADMIN]
    is_dev = settings.ENVIRONMENT == "development"

    if not (is_owner or is_admin or is_dev):
        raise HTTPException(status_code=403, detail="Not authorized to delete this assistant")
    
    asst_ref.delete()
    return {"message": "Assistant deleted successfully"}
