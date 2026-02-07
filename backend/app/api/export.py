from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
from backend.app.services.exporter import exporter_service
from backend.app.core.firebase import db
import os

router = APIRouter()

class ExportRequest(BaseModel):
    query: str
    answer: str
    sources: List[dict]
    matched_images: Optional[List[dict]] = None
    assistant_id: Optional[str] = None

@router.post("/word")
async def export_to_word(request: ExportRequest):
    template_path = None
    
    if request.assistant_id:
        asst_ref = db.collection("assistants").document(request.assistant_id).get()
        if asst_ref.exists:
            template_id = asst_ref.to_dict().get("template_id")
            if template_id:
                temp_ref = db.collection("templates").document(template_id).get()
                if temp_ref.exists:
                    template_path = temp_ref.to_dict().get("path")

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

