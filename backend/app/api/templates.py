from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
import shutil
import os
import re
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user, require_role
from backend.app.schemas.user import UserProfile, UserRole
import uuid
from datetime import datetime

router = APIRouter()

TEMPLATE_DIR = "backend/app/templates"
os.makedirs(TEMPLATE_DIR, exist_ok=True)

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._() -]+")

def _sanitize_filename(name: str) -> str:
    base = os.path.basename(name or "").strip()
    base = _FILENAME_SAFE_RE.sub("_", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or "template.docx"


def _is_safe_template_path(path: str) -> bool:
    try:
        base = os.path.abspath(TEMPLATE_DIR) + os.sep
        target = os.path.abspath(path)
        return target.startswith(base) and target.lower().endswith(".docx")
    except Exception:
        return False

@router.post("/upload")
async def upload_template(
    file: UploadFile = File(...),
    current_user: UserProfile = Depends(require_role(UserRole.ADMIN))
):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx templates are supported.")
    
    template_id = str(uuid.uuid4())
    safe_name = _sanitize_filename(file.filename)
    filename = f"{template_id}_{safe_name}"
    file_path = os.path.join(TEMPLATE_DIR, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        template_data = {
            "id": template_id,
            "name": safe_name,
            "path": file_path,
            "uploaded_by": current_user.id,
            "created_at": datetime.utcnow()
        }
        db.collection("templates").document(template_id).set(template_data)
        
        # Do not expose server file paths to the client.
        return {
            "id": template_id,
            "name": safe_name,
            "created_at": template_data["created_at"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/")
async def list_templates(current_user: UserProfile = Depends(get_current_user)):
    docs = db.collection("templates").stream()
    out = []
    for doc in docs:
        data = doc.to_dict() or {}
        out.append({
            "id": data.get("id") or doc.id,
            "name": data.get("name") or "Mall",
            "created_at": data.get("created_at"),
        })
    return out

@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: UserProfile = Depends(require_role(UserRole.ADMIN))
):
    doc_ref = db.collection("templates").document(template_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Template not found")
    
    data = doc.to_dict()
    file_path = data.get("path")
    
    try:
        # 1. Remove from filesystem
        if file_path and os.path.exists(file_path):
            if not _is_safe_template_path(file_path):
                raise HTTPException(status_code=400, detail="Unsafe template path.")
            os.remove(file_path)
            
        # 2. Remove from Firestore
        doc_ref.delete()
        
        return {"message": "Template deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
