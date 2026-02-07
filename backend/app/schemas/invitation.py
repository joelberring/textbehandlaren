from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from enum import Enum

class InvitationStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"

class InvitationCreate(BaseModel):
    email: str
    role: str = "EDITOR"  # ProjectRole value

class InvitationResponse(BaseModel):
    id: str
    project_id: str
    project_name: str
    email: str
    role: str
    status: InvitationStatus
    invited_by: str
    created_at: datetime

    class Config:
        from_attributes = True
