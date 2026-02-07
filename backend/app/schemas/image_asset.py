from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class ImageAssetBase(BaseModel):
    description: str
    source_document: str  # Original filename of the document it was extracted from
    page: int
    tags: List[str] = Field(default_factory=list)
    section_hints: List[str] = Field(default_factory=list)
    context_excerpt: Optional[str] = None
    source_doc_id: Optional[str] = None

class ImageAssetResponse(ImageAssetBase):
    id: str
    library_id: str
    url: str  # Public URL in Firebase Storage
    created_at: datetime

    class Config:
        from_attributes = True
