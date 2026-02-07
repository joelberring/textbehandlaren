from fastapi import APIRouter, HTTPException, Depends
from typing import List
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user, require_role
from backend.app.core.config import settings
from backend.app.schemas import library
from backend.app.schemas.user import UserProfile, UserRole
import uuid
from datetime import datetime
from google.cloud import firestore

router = APIRouter()

@router.post("/", response_model=library.LibraryResponse)
async def create_library(
    request: library.LibraryCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    library_id = str(uuid.uuid4())
    priority = max(0, min(int(request.priority), 100))
    
    library_data = {
        "id": library_id,
        "name": request.name,
        "description": request.description,
        "library_type": request.library_type,
        "scrub_enabled": request.scrub_enabled,
        "gdpr_name_scrub_default": request.gdpr_name_scrub_default,
        "priority": priority,
        "owner_id": current_user.id,
        "shared_with": [],
        "created_at": datetime.utcnow()
    }
    
    db.collection("libraries").document(library_id).set(library_data)
    return library_data

@router.get("/", response_model=List[library.LibraryResponse])
async def list_libraries(current_user: UserProfile = Depends(get_current_user)):
    libs = []
    
    # Libraries owned by user
    owned_docs = db.collection("libraries").where("owner_id", "==", current_user.id).stream()
    for doc in owned_docs:
        libs.append(doc.to_dict())
    
    # Libraries shared with user
    shared_docs = db.collection("libraries").where("shared_with", "array_contains", current_user.id).stream()
    for doc in shared_docs:
        libs.append(doc.to_dict())
    
    # Admin/Superadmin can see all shared libraries marked as shared
    if current_user.role in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        all_docs = db.collection("libraries").stream()
        existing_ids = {lib["id"] for lib in libs}
        for doc in all_docs:
            data = doc.to_dict()
            if data["id"] not in existing_ids:
                libs.append(data)
    
    return libs

@router.post("/{library_id}/share")
async def share_library(
    library_id: str, 
    target_user_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    lib_ref = db.collection("libraries").document(library_id)
    doc = lib_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Library not found")
    
    lib_data = doc.to_dict()
    # Only owner or admin can share
    if lib_data.get("owner_id") != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to share this library")
    
    lib_ref.update({
        "shared_with": firestore.ArrayUnion([target_user_id])
    })
    return {"message": "Library shared successfully"}

@router.put("/{library_id}", response_model=library.LibraryResponse)
async def update_library(
    library_id: str, 
    request: library.LibraryCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    lib_ref = db.collection("libraries").document(library_id)
    doc = lib_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Library not found")
    
    lib_data = doc.to_dict()
    # Owner or Admin can update
    if lib_data.get("owner_id") != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        raise HTTPException(status_code=403, detail="Not authorized to update this library")
    
    priority = max(0, min(int(request.priority), 100))
    updated_data = {
        "name": request.name,
        "description": request.description,
        "library_type": request.library_type,
        "scrub_enabled": request.scrub_enabled,
        "gdpr_name_scrub_default": request.gdpr_name_scrub_default,
        "priority": priority,
        "updated_at": datetime.utcnow()
    }
    
    lib_ref.update(updated_data)
    
    full_data = lib_data.copy()
    full_data.update(updated_data)
    return full_data

@router.delete("/{library_id}")
async def delete_library(
    library_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    lib_ref = db.collection("libraries").document(library_id)
    doc = lib_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Library not found")
    
    lib_data = doc.to_dict()
    # Owner or Admin can delete. In development mode, we allow all for ease of use.
    is_owner = lib_data.get("owner_id") == current_user.id
    is_admin = current_user.role in [UserRole.ADMIN, UserRole.SUPERADMIN]
    is_dev = settings.ENVIRONMENT == "development"

    if not (is_owner or is_admin or is_dev):
        raise HTTPException(status_code=403, detail="Not authorized to delete this library")
    
    # Delete knowledge_base subcollection
    kb_docs = lib_ref.collection("knowledge_base").stream()
    for kb_doc in kb_docs:
        kb_doc.reference.delete()
    
    # Delete related image_assets
    image_docs = db.collection("image_assets").where("library_id", "==", library_id).stream()
    for img_doc in image_docs:
        img_doc.reference.delete()
    
    # Finally, delete the library document itself
    lib_ref.delete()
    
    return {"message": "Library and all related data deleted successfully"}

@router.get("/{library_id}/documents", response_model=List[library.LibraryDocumentResponse])
async def list_documents(
    library_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    lib_ref = db.collection("libraries").document(library_id)
    doc = lib_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Library not found")
    
    lib_data = doc.to_dict()
    # Check ownership or admin status or shared_with
    is_admin = current_user.role in [UserRole.ADMIN, UserRole.SUPERADMIN]
    is_owner = lib_data.get("owner_id") == current_user.id
    is_shared = current_user.id in lib_data.get("shared_with", [])
    
    if not (is_admin or is_owner or is_shared):
        raise HTTPException(status_code=403, detail="Not authorized to view documents in this library")
        
    docs = []
    doc_stream = lib_ref.collection("documents").order_by("uploaded_at", direction=firestore.Query.DESCENDING).stream()
    for d in doc_stream:
        docs.append(d.to_dict())
    
    return docs
