from fastapi import APIRouter, UploadFile, File, HTTPException
import shutil
import os
from backend.app.core.firebase import db
import uuid
from datetime import datetime

router = APIRouter()

TEMPLATE_DIR = "backend/app/templates"
os.makedirs(TEMPLATE_DIR, exist_ok=True)

@router.post("/upload")
async def upload_template(file: UploadFile = File(...)):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx templates are supported.")
    
    template_id = str(uuid.uuid4())
    filename = f"{template_id}_{file.filename}"
    file_path = os.path.join(TEMPLATE_DIR, filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        template_data = {
            "id": template_id,
            "name": file.filename,
            "path": file_path,
            "created_at": datetime.utcnow()
        }
        db.collection("templates").document(template_id).set(template_data)
        
        return template_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/")
async def list_templates():
    docs = db.collection("templates").stream()
    return [doc.to_dict() for doc in docs]

@router.delete("/{template_id}")
async def delete_template(template_id: str):
    doc_ref = db.collection("templates").document(template_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Template not found")
    
    data = doc.to_dict()
    file_path = data.get("path")
    
    try:
        # 1. Remove from filesystem
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            
        # 2. Remove from Firestore
        doc_ref.delete()
        
        return {"message": "Template deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
