from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class ProjectRole(str, Enum):
    OWNER = "OWNER"
    EDITOR = "EDITOR"
    VIEWER = "VIEWER"

class ProjectMember(BaseModel):
    user_id: str
    email: Optional[str] = None
    role: ProjectRole

class ProjectBase(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectCreate(ProjectBase):
    pass

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class ProjectResponse(ProjectBase):
    id: str
    owner_id: str
    members: List[ProjectMember] = []
    library_ids: List[str] = []
    assistant_ids: List[str] = []
    created_at: datetime

    class Config:
        from_attributes = True

class AddMemberRequest(BaseModel):
    user_id: str
    email: Optional[str] = None
    role: ProjectRole = ProjectRole.EDITOR

class AddResourceRequest(BaseModel):
    resource_id: str


class UpdateMemberRoleRequest(BaseModel):
    role: ProjectRole
