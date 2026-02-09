from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks
import shutil
import os
import uuid
import re
from datetime import datetime
from typing import Optional
from backend.app.services.ingestion import ingestion_service
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user
from backend.app.schemas.user import UserProfile
from backend.app.core.config import settings
from backend.app.services.scrubber import scrubber_service
from google.cloud import firestore
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader

router = APIRouter()

UPLOAD_DIR = "/tmp/temp_uploads"
try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except Exception as e:
    print(f"Warning: Could not create UPLOAD_DIR {UPLOAD_DIR}: {e}")
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt"}
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._() -]+")


def _validate_upload_filename(filename: str):
    if not filename:
        raise HTTPException(status_code=400, detail="Filen saknar namn.")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(
            status_code=400,
            detail=f"Filtypen stöds inte ({extension or 'okänd'}). Tillåtna format: {allowed}."
        )


def _sanitize_upload_filename(filename: str) -> str:
    base = os.path.basename(filename or "").strip()
    base = _FILENAME_SAFE_RE.sub("_", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base or "upload"


def _get_or_create_attachment_library(conversation_id: str, current_user: UserProfile) -> str:
    attach_ref = db.collection("conversation_attachments").document(conversation_id)
    attach_doc = attach_ref.get()
    if attach_doc.exists:
        data = attach_doc.to_dict()
        if data.get("user_id") != current_user.id:
            raise HTTPException(status_code=403, detail="Not authorized to upload to this conversation")
        return data.get("library_id")

    library_id = str(uuid.uuid4())
    library_data = {
        "id": library_id,
        "name": f"Bilagor - {conversation_id[:8]}",
        "description": "Filer bifogade i konversationen",
        "library_type": "INPUT",
        "scrub_enabled": True,
        "owner_id": current_user.id,
        "shared_with": [],
        "created_at": datetime.utcnow(),
        "is_attachment_library": True
    }
    db.collection("libraries").document(library_id).set(library_data)
    attach_ref.set({
        "conversation_id": conversation_id,
        "library_id": library_id,
        "user_id": current_user.id,
        "created_at": datetime.utcnow()
    })
    # Attach to conversation doc if it exists (merge-safe)
    db.collection("conversations").document(conversation_id).set({
        "attachment_library_id": library_id,
        "user_id": current_user.id,
        "updated_at": datetime.utcnow()
    }, merge=True)

    return library_id


def _extract_inline_text_if_small(file_path: str) -> str:
    extension = os.path.splitext(file_path)[1].lower()
    text = ""
    if extension == ".pdf":
        page_count = None
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(file_path)
            page_count = len(reader.pages)
        except Exception:
            # Dev fallback: allow PyMuPDF if available locally.
            try:
                import fitz  # type: ignore

                doc = fitz.open(file_path)
                page_count = int(getattr(doc, "page_count", 0) or 0)
            except Exception:
                return ""

        if page_count and page_count > settings.DIRECT_ATTACHMENT_MAX_PAGES:
            return ""
        loader = PyPDFLoader(file_path)
        docs = loader.load()
        text = "\n".join([d.page_content for d in docs])
    elif extension == ".docx":
        loader = Docx2txtLoader(file_path)
        docs = loader.load()
        text = "\n".join([d.page_content for d in docs])
    elif extension == ".txt":
        loader = TextLoader(file_path)
        docs = loader.load()
        text = "\n".join([d.page_content for d in docs])
    else:
        return ""

    if len(text) > settings.DIRECT_ATTACHMENT_MAX_CHARS:
        return ""
    return text.strip()

@router.post("/library/{library_id}/upload")
async def upload_document(
    library_id: str, 
    background_tasks: BackgroundTasks,
    interpret_images: bool = False,
    gdpr_name_scrub: Optional[bool] = None,
    file: UploadFile = File(...),
    current_user: UserProfile = Depends(get_current_user)
):
    _validate_upload_filename(file.filename)
    safe_name = _sanitize_upload_filename(file.filename)
    lib_ref = db.collection("libraries").document(library_id)
    doc_snap = lib_ref.get()
    
    if not doc_snap.exists:
        raise HTTPException(status_code=404, detail="Library not found")
    
    lib_data = doc_snap.to_dict()
    effective_gdpr_name_scrub = gdpr_name_scrub
    if effective_gdpr_name_scrub is None:
        effective_gdpr_name_scrub = bool(lib_data.get("gdpr_name_scrub_default", False))

    if effective_gdpr_name_scrub and not scrubber_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="GDPR-namntvätt kräver Mistral API-nyckel (EU-scrubber)."
        )
    # Check ownership or admin status
    if lib_data.get("owner_id") != current_user.id and current_user.role not in ["ADMIN", "SUPERADMIN"]:
        raise HTTPException(status_code=403, detail="Not authorized to upload to this library")
    
    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{safe_name}")
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Start ingestion in the background
        background_tasks.add_task(
            ingestion_service.process_document,
            file_path, 
            safe_name, 
            library_id,
            interpret_images=interpret_images,
            gdpr_name_scrub=effective_gdpr_name_scrub,
            gdpr_scrub_initiated_by=current_user.email or current_user.id
        )
        
        return {
            "status": "accepted",
            "message": f"Filen {safe_name} har tagits emot och bearbetas nu i bakgrunden.",
            "filename": safe_name,
            "gdpr_name_scrub": effective_gdpr_name_scrub
        }
    except Exception as e:
        print(f"Error saving uploaded document: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversation/{conversation_id}/upload")
async def upload_document_to_conversation(
    conversation_id: str,
    background_tasks: BackgroundTasks,
    interpret_images: bool = False,
    gdpr_name_scrub: Optional[bool] = None,
    file: UploadFile = File(...),
    current_user: UserProfile = Depends(get_current_user)
):
    _validate_upload_filename(file.filename)
    safe_name = _sanitize_upload_filename(file.filename)
    effective_gdpr_name_scrub = bool(gdpr_name_scrub)
    if effective_gdpr_name_scrub and not scrubber_service.is_configured():
        raise HTTPException(
            status_code=400,
            detail="GDPR-namntvätt kräver Mistral API-nyckel (EU-scrubber)."
        )
    library_id = _get_or_create_attachment_library(conversation_id, current_user)

    file_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{safe_name}")
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Optional: direct inline text for small attachments
        inline_text = ""
        inline_name_map = None
        inline_findings = []
        try:
            inline_text = _extract_inline_text_if_small(file_path)
        except Exception as e:
            print(f"Inline extraction failed: {e}")

        if inline_text and effective_gdpr_name_scrub:
            try:
                inline_text, inline_findings, inline_name_map = await scrubber_service.scrub_person_names_with_cards(inline_text)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"GDPR-namntvätt misslyckades: {e}")

        if inline_text:
            conv_ref = db.collection("conversations").document(conversation_id)
            conv_doc = conv_ref.get()
            if conv_doc.exists and conv_doc.to_dict().get("user_id") != current_user.id:
                raise HTTPException(status_code=403, detail="Not authorized to update this conversation")
            payload = {
                "attachment_inline_texts": firestore.ArrayUnion([{
                    "filename": safe_name,
                    "text": inline_text,
                    "chars": len(inline_text),
                    "gdpr_name_scrub": effective_gdpr_name_scrub,
                    "gdpr_scrub_findings": len(inline_findings)
                }]),
                "updated_at": datetime.utcnow(),
                "user_id": current_user.id
            }
            conv_ref.set(payload, merge=True)

        background_tasks.add_task(
            ingestion_service.process_document,
            file_path,
            safe_name,
            library_id,
            interpret_images=interpret_images,
            gdpr_name_scrub=effective_gdpr_name_scrub,
            initial_name_map=inline_name_map or {},
            gdpr_scrub_initiated_by=current_user.email or current_user.id
        )

        return {
            "status": "accepted",
            "message": f"Filen {safe_name} har bifogats till konversationen och bearbetas nu.",
            "filename": safe_name,
            "gdpr_name_scrub": effective_gdpr_name_scrub
        }
    except Exception as e:
        print(f"Error saving uploaded document: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
