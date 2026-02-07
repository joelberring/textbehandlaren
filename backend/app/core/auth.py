from fastapi import Depends, HTTPException, Header
from firebase_admin import auth
from backend.app.core.firebase import db
from backend.app.core.config import settings
from backend.app.schemas.user import UserProfile, UserRole
from datetime import datetime
from typing import Optional

async def get_current_user(authorization: Optional[str] = Header(None)) -> UserProfile:
    """
    Verify Firebase Auth token and return the current user profile.
    For development, if no token is provided, returns a default superadmin user.
    In production, this raises an error.
    """
    if not authorization or not authorization.startswith("Bearer "):
        # Only allow local dev fallback if explicitly enabled
        if settings.ENVIRONMENT == "development" and settings.DEV_AUTH_BYPASS:
            return UserProfile(
                id="dev-user-1",
                email="dev@textbehandlaren.se",
                display_name="Dev User",
                role=UserRole.SUPERADMIN,
                created_at=datetime.utcnow()
            )
        else:
            raise HTTPException(status_code=401, detail="Authentication required")
    
    token = authorization.split("Bearer ")[1]
    
    try:
        # Verify Firebase ID token
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token["uid"]
        email = decoded_token.get("email", "")
        
        # Get or create user profile in Firestore
        user_ref = db.collection("users").document(uid)
        user_doc = user_ref.get()
        
        if user_doc.exists:
            user_data = user_doc.to_dict()
            return UserProfile(
                id=uid,
                email=email,
                display_name=user_data.get("display_name"),
                role=UserRole(user_data.get("role", "USER")),
                created_at=user_data.get("created_at", datetime.utcnow())
            )
        else:
            # First time user - create profile with USER role
            new_user_data = {
                "id": uid,
                "email": email,
                "display_name": decoded_token.get("name"),
                "role": UserRole.USER.value,
                "created_at": datetime.utcnow()
            }
            user_ref.set(new_user_data)
            
            # Auto-match pending invitations for this email
            await _process_pending_invitations(uid, email)
            
            return UserProfile(**new_user_data)

            
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid authentication token: {str(e)}")

async def _process_pending_invitations(user_id: str, email: str):
    """
    Check for pending invitations for this email and auto-add user to projects.
    Called on first login.
    """
    from google.cloud import firestore as fs
    
    # Find all pending invitations for this email
    pending = db.collection("invitations").where("email", "==", email.lower()).where("status", "==", "PENDING").stream()
    
    for invite_doc in pending:
        invite = invite_doc.to_dict()
        project_id = invite.get("project_id")
        role = invite.get("role", "VIEWER")
        
        # Add user to project
        proj_ref = db.collection("projects").document(project_id)
        proj_doc = proj_ref.get()
        
        if proj_doc.exists:
            new_member = {
                "user_id": user_id,
                "email": email,
                "role": role
            }
            proj_ref.update({
                "members": fs.ArrayUnion([new_member])
            })
        
        # Mark invitation as accepted
        db.collection("invitations").document(invite.get("id")).update({
            "status": "ACCEPTED",
            "accepted_at": datetime.utcnow(),
            "accepted_by": user_id
        })

def require_role(minimum_role: UserRole):
    """
    Dependency that checks if user has at least the specified role.
    Role hierarchy: SUPERADMIN > ADMIN > USER
    """
    role_hierarchy = {
        UserRole.USER: 0,
        UserRole.ADMIN: 1,
        UserRole.SUPERADMIN: 2
    }
    
    async def role_checker(current_user: UserProfile = Depends(get_current_user)):
        user_level = role_hierarchy.get(current_user.role, 0)
        required_level = role_hierarchy.get(minimum_role, 0)
        
        if user_level < required_level:
            raise HTTPException(
                status_code=403, 
                detail=f"Access denied. Required role: {minimum_role.value}"
            )
        return current_user
    
    return role_checker

# Convenience dependencies
require_admin = require_role(UserRole.ADMIN)
require_superadmin = require_role(UserRole.SUPERADMIN)
