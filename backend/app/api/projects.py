from fastapi import APIRouter, HTTPException, Depends
from typing import List
from backend.app.core.firebase import db
from backend.app.core.auth import get_current_user
from backend.app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectResponse, ProjectRole,
    ProjectMember, AddMemberRequest, AddResourceRequest, UpdateMemberRoleRequest
)
from backend.app.schemas.user import UserProfile
import uuid
from datetime import datetime
from google.cloud import firestore

router = APIRouter()

def check_project_access(project_data: dict, user_id: str, min_role: ProjectRole = ProjectRole.VIEWER) -> bool:
    """Check if user has at least the minimum required role in the project."""
    if project_data.get("owner_id") == user_id:
        return True
    
    role_hierarchy = {ProjectRole.VIEWER: 0, ProjectRole.EDITOR: 1, ProjectRole.OWNER: 2}
    required_level = role_hierarchy.get(min_role, 0)
    
    for member in project_data.get("members", []):
        if member.get("user_id") == user_id:
            member_level = role_hierarchy.get(ProjectRole(member.get("role", "VIEWER")), 0)
            return member_level >= required_level
    
    return False

@router.post("/", response_model=ProjectResponse)
async def create_project(
    request: ProjectCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    """Create a new project. The creator becomes the owner."""
    project_id = str(uuid.uuid4())
    
    project_data = {
        "id": project_id,
        "name": request.name,
        "description": request.description,
        "owner_id": current_user.id,
        "members": [{
            "user_id": current_user.id,
            "email": current_user.email,
            "role": ProjectRole.OWNER.value
        }],
        "library_ids": [],
        "assistant_ids": [],
        "created_at": datetime.utcnow()
    }
    
    db.collection("projects").document(project_id).set(project_data)
    return project_data

@router.get("/", response_model=List[ProjectResponse])
async def list_projects(current_user: UserProfile = Depends(get_current_user)):
    """List all projects the user owns or is a member of."""
    projects = []
    
    # Projects owned by user
    owned = db.collection("projects").where("owner_id", "==", current_user.id).stream()
    for doc in owned:
        projects.append(doc.to_dict())
    
    # Projects where user is a member (but not owner - already added)
    all_projects = db.collection("projects").stream()
    owned_ids = {p["id"] for p in projects}
    
    for doc in all_projects:
        data = doc.to_dict()
        if data["id"] not in owned_ids:
            for member in data.get("members", []):
                if member.get("user_id") == current_user.id:
                    projects.append(data)
                    break
    
    return projects

@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    doc = db.collection("projects").document(project_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id):
        raise HTTPException(status_code=403, detail="Not a member of this project")
    
    return data

@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    request: ProjectUpdate,
    current_user: UserProfile = Depends(get_current_user)
):
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to edit this project")
    
    updates = {"updated_at": datetime.utcnow()}
    if request.name:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    
    doc_ref.update(updates)
    data.update(updates)
    return data

@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if data.get("owner_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can delete a project")
    
    doc_ref.delete()
    return {"message": "Project deleted successfully"}

# Member Management
@router.post("/{project_id}/members")
async def add_member(
    project_id: str,
    request: AddMemberRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """Add a member to the project. Only Owner/Editor can invite."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to add members")
    
    # Check if already a member
    for member in data.get("members", []):
        if member.get("user_id") == request.user_id:
            raise HTTPException(status_code=400, detail="User is already a member")
    
    new_member = {
        "user_id": request.user_id,
        "email": request.email,
        "role": request.role.value
    }
    
    doc_ref.update({
        "members": firestore.ArrayUnion([new_member])
    })
    
    return {"message": f"Member added with role {request.role.value}"}

@router.delete("/{project_id}/members/{user_id}")
async def remove_member(
    project_id: str,
    user_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Remove a member from the project. Only Owner can remove."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if data.get("owner_id") != current_user.id:
        raise HTTPException(status_code=403, detail="Only the owner can remove members")
    
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself as owner")
    
    # Find and remove the member
    updated_members = [m for m in data.get("members", []) if m.get("user_id") != user_id]
    doc_ref.update({"members": updated_members})
    
    return {"message": "Member removed"}


@router.put("/{project_id}/members/{user_id}/role")
async def update_member_role(
    project_id: str,
    user_id: str,
    request: UpdateMemberRoleRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """Update role for an existing member. Owner/Editor can update, owner role is protected."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")

    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to update member roles")

    if user_id == data.get("owner_id"):
        raise HTTPException(status_code=400, detail="Project owner role cannot be changed")
    if request.role == ProjectRole.OWNER:
        raise HTTPException(status_code=400, detail="Use dedicated ownership transfer flow for OWNER role")

    members = data.get("members", [])
    updated = False
    for m in members:
        if m.get("user_id") == user_id:
            m["role"] = request.role.value
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Member not found")

    doc_ref.update({
        "members": members,
        "updated_at": datetime.utcnow()
    })
    return {"message": f"Member role updated to {request.role.value}"}

# Resource linking
@router.post("/{project_id}/libraries")
async def add_library_to_project(
    project_id: str,
    request: AddResourceRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """Link a library to the project."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to modify project resources")
    
    doc_ref.update({
        "library_ids": firestore.ArrayUnion([request.resource_id])
    })
    
    return {"message": "Library added to project"}


@router.delete("/{project_id}/libraries/{library_id}")
async def remove_library_from_project(
    project_id: str,
    library_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Unlink a library from the project."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")

    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to modify project resources")

    doc_ref.update({
        "library_ids": firestore.ArrayRemove([library_id]),
        "updated_at": datetime.utcnow()
    })
    return {"message": "Library removed from project"}

@router.post("/{project_id}/assistants")
async def add_assistant_to_project(
    project_id: str,
    request: AddResourceRequest,
    current_user: UserProfile = Depends(get_current_user)
):
    """Link an assistant to the project."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to modify project resources")
    
    doc_ref.update({
        "assistant_ids": firestore.ArrayUnion([request.resource_id])
    })
    
    return {"message": "Assistant added to project"}


@router.delete("/{project_id}/assistants/{assistant_id}")
async def remove_assistant_from_project(
    project_id: str,
    assistant_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Unlink an assistant from the project."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")

    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to modify project resources")

    doc_ref.update({
        "assistant_ids": firestore.ArrayRemove([assistant_id]),
        "updated_at": datetime.utcnow()
    })
    return {"message": "Assistant removed from project"}

# Email-based invitations
from backend.app.schemas.invitation import InvitationCreate, InvitationResponse, InvitationStatus

@router.post("/{project_id}/invite")
async def invite_by_email(
    project_id: str,
    request: InvitationCreate,
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Invite a user by email. Creates a pending invitation that is automatically
    applied when the user signs in.
    Project owner/editor can invite users.
    """
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to invite users to this project")

    # Check if already invited
    existing = db.collection("invitations").where("project_id", "==", project_id).where("email", "==", request.email.lower()).where("status", "==", "PENDING").get()
    if len(existing) > 0:
        raise HTTPException(status_code=400, detail="User already has a pending invitation")
    
    # Check if already a member (by email)
    for member in data.get("members", []):
        if member.get("email", "").lower() == request.email.lower():
            raise HTTPException(status_code=400, detail="User is already a member of this project")
    
    invitation_id = str(uuid.uuid4())
    invitation_data = {
        "id": invitation_id,
        "project_id": project_id,
        "project_name": data.get("name", ""),
        "email": request.email.lower(),
        "role": request.role.value,
        "status": InvitationStatus.PENDING.value,
        "invited_by": current_user.id,
        "invited_by_email": current_user.email,
        "created_at": datetime.utcnow()
    }
    
    db.collection("invitations").document(invitation_id).set(invitation_data)
    
    return {"message": f"Invitation sent to {request.email}", "invitation_id": invitation_id}

@router.get("/{project_id}/invitations")
async def list_project_invitations(
    project_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """List all pending invitations for a project."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.VIEWER):
        raise HTTPException(status_code=403, detail="Not authorized to view invitations")
    
    invites = db.collection("invitations").where("project_id", "==", project_id).stream()
    return [inv.to_dict() for inv in invites]

@router.delete("/{project_id}/invitations/{invitation_id}")
async def cancel_invitation(
    project_id: str,
    invitation_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """Cancel a pending invitation."""
    doc_ref = db.collection("projects").document(project_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Project not found")
    
    data = doc.to_dict()
    if not check_project_access(data, current_user.id, ProjectRole.EDITOR):
        raise HTTPException(status_code=403, detail="Not authorized to cancel invitations")
    
    db.collection("invitations").document(invitation_id).delete()
    return {"message": "Invitation cancelled"}
