from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class LibraryPriorityProfileItem(BaseModel):
    library_id: str
    priority: int  # 0-100

class AssistantBase(BaseModel):
    name: str
    system_prompt: str
    template_id: Optional[str] = None
    model_preference: Optional[str] = None
    interpret_images: bool = False
    library_ids: List[str] = Field(default_factory=list)
    library_priority_profile: List[LibraryPriorityProfileItem] = Field(default_factory=list)

class AssistantCreate(AssistantBase):
    pass

class AssistantUpdate(AssistantBase):
    name: Optional[str] = None
    system_prompt: Optional[str] = None

class AssistantResponse(AssistantBase):
    id: str
    user_id: str  # Firebase Auth UID
    created_at: datetime

    class Config:
        from_attributes = True
