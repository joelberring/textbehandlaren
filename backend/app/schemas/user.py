from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from enum import Enum

class UserRole(str, Enum):
    SUPERADMIN = "SUPERADMIN"
    ADMIN = "ADMIN"
    USER = "USER"

class UserBase(BaseModel):
    email: str
    display_name: Optional[str] = None

class UserCreate(UserBase):
    role: UserRole = UserRole.USER

class UserProfile(UserBase):
    id: str
    role: UserRole
    created_at: datetime
    
    class Config:
        from_attributes = True

class UserRoleUpdate(BaseModel):
    role: UserRole
