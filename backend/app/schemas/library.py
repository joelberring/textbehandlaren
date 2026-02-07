from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class LibraryBase(BaseModel):
    name: str
    description: Optional[str] = None
    library_type: str = "BACKGROUND"  # "INPUT" or "BACKGROUND"
    scrub_enabled: bool = False
    gdpr_name_scrub_default: bool = False
    priority: int = 50  # 0-100, higher means stronger weighting in retrieval

class LibraryCreate(LibraryBase):
    pass

class LibraryResponse(LibraryBase):
    id: str
    owner_id: str  # Firebase Auth UID
    shared_with: List[str] = []
    created_at: datetime

    class Config:
        from_attributes = True

class LibraryDocumentResponse(BaseModel):
    id: str
    filename: str
    uploaded_at: datetime
    interpret_images: bool
    images_indexed: int
    extension: str
    status: Optional[str] = None
    progress: Optional[int] = None
    total_chunks: Optional[int] = None
    processed_chunks: Optional[int] = None
    error: Optional[str] = None
    gdpr_name_scrub: Optional[bool] = None
    gdpr_scrub_mode: Optional[str] = None
    gdpr_scrub_status: Optional[str] = None
    gdpr_scrub_findings: Optional[int] = None
    gdpr_scrub_replacements: Optional[int] = None
    gdpr_scrub_cards_created: Optional[int] = None
    gdpr_scrub_provider: Optional[str] = None
    gdpr_scrub_model: Optional[str] = None
    gdpr_scrub_at: Optional[datetime] = None
    gdpr_scrub_initiated_by: Optional[str] = None

    class Config:
        from_attributes = True
