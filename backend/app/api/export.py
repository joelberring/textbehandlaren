from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from backend.app.services.exporter import exporter_service
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user
from backend.app.schemas.user import UserProfile
import os

router = APIRouter()

TEMPLATE_DIR = "backend/app/templates"

def _is_safe_template_path(path: str) -> bool:
    try:
        base = os.path.abspath(TEMPLATE_DIR) + os.sep
        target = os.path.abspath(path)
        return target.startswith(base) and target.lower().endswith(".docx")
    except Exception:
        return False

class ExportRequest(BaseModel):
    query: str
    answer: str
    sources: List[dict]
    matched_images: Optional[List[dict]] = None
    assistant_id: Optional[str] = None

@router.post("/word")
async def export_to_word(
    request: ExportRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    template_path = None
    
    if request.assistant_id:
        asst_ref = db.collection("assistants").document(request.assistant_id).get()
        if asst_ref.exists:
            template_id = asst_ref.to_dict().get("template_id")
            if template_id:
                temp_ref = db.collection("templates").document(template_id).get()
                if temp_ref.exists:
                    candidate = temp_ref.to_dict().get("path")
                    if candidate and _is_safe_template_path(candidate) and os.path.exists(candidate):
                        template_path = candidate

    try:
        file_path = await exporter_service.generate_word_response(
            request.query, 
            request.answer, 
            request.sources,
            template_path=template_path,
            matched_images=request.matched_images
        )
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=500, detail="Failed to create export file.")
            
        return FileResponse(
            path=file_path, 
            filename=os.path.basename(file_path),
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
