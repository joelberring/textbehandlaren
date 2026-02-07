from fastapi import APIRouter, Depends, HTTPException
from typing import List
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user, require_superadmin
from backend.app.schemas.user import UserProfile, UserRole, UserRoleUpdate
from datetime import datetime

router = APIRouter()

@router.get("/me", response_model=UserProfile)
async def get_my_profile(current_user: UserProfile = Depends(get_current_user)):
    """Get the current user's profile."""
    return current_user

@router.get("/", response_model=List[UserProfile])
async def list_all_users(current_user: UserProfile = Depends(require_superadmin)):
    """List all users. Superadmin only."""
    docs = db.collection("users").stream()
    users = []
    for doc in docs:
        data = doc.to_dict()
        users.append(UserProfile(
            id=data.get("id", doc.id),
            email=data.get("email", ""),
            display_name=data.get("display_name"),
            role=UserRole(data.get("role", "USER")),
            created_at=data.get("created_at", datetime.utcnow())
        ))
    return users

@router.put("/{user_id}/role")
async def update_user_role(
    user_id: str, 
    role_update: UserRoleUpdate,
    current_user: UserProfile = Depends(require_superadmin)
):
    """Update a user's role. Superadmin only."""
    user_ref = db.collection("users").document(user_id)
    doc = user_ref.get()
    
    if not doc.exists:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Prevent demoting yourself
    if user_id == current_user.id and role_update.role != UserRole.SUPERADMIN:
        raise HTTPException(status_code=400, detail="Cannot demote yourself")
    
    user_ref.update({
        "role": role_update.role.value,
        "updated_at": datetime.utcnow()
    })
    
    return {"message": f"User role updated to {role_update.role.value}"}
