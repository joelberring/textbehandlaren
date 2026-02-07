from fastapi import APIRouter, HTTPException, Query, Depends
from typing import List, Optional
from backend.app.core.firebase import db
from backend.app.schemas.image_asset import ImageAssetResponse
from backend.app.schemas.user import UserProfile, UserRole
from backend.app.core.auth import get_current_user
from google.cloud.firestore_v1.vector import Vector
from backend.app.services.embeddings import get_embeddings

router = APIRouter()


def _get_accessible_library_ids(current_user: UserProfile) -> set:
    if current_user.role in [UserRole.ADMIN, UserRole.SUPERADMIN]:
        return {doc.id for doc in db.collection("libraries").stream()}

    owned = db.collection("libraries").where("owner_id", "==", current_user.id).stream()
    shared = db.collection("libraries").where("shared_with", "array_contains", current_user.id).stream()
    return {doc.id for doc in owned}.union({doc.id for doc in shared})


@router.get("/library/{library_id}", response_model=List[ImageAssetResponse])
async def list_images_in_library(
    library_id: str,
    current_user: UserProfile = Depends(get_current_user)
):
    """List all images indexed within a specific library."""
    accessible_ids = _get_accessible_library_ids(current_user)
    if library_id not in accessible_ids:
        raise HTTPException(status_code=403, detail="Not authorized to view images in this library")

    docs = db.collection("image_assets").where("library_id", "==", library_id).stream()
    
    images = []
    for doc in docs:
        images.append(doc.to_dict())
    return images

@router.get("/search/", response_model=List[ImageAssetResponse])
async def search_images(
    query: str = Query(..., description="Search query for semantic image matching"),
    library_ids: Optional[str] = Query(None, description="Comma-separated library IDs to search within"),
    current_user: UserProfile = Depends(get_current_user)
):
    """
    Semantic search for images based on their descriptions.
    Returns images whose descriptions best match the query.
    """
    embeddings = get_embeddings()
    query_vector = embeddings.embed_query(query)
    
    accessible_ids = _get_accessible_library_ids(current_user)
    libs = [l.strip() for l in library_ids.split(",")] if library_ids else list(accessible_ids)
    libs = [lib_id for lib_id in libs if lib_id in accessible_ids]
    if library_ids and not libs:
        raise HTTPException(status_code=403, detail="No access to the requested library/libraries")
    
    found_images = []
    
    # Search within allowed libraries only
    for lib_id in libs:
        collection_ref = db.collection("image_assets").where("library_id", "==", lib_id)
        
        try:
            results = collection_ref.find_nearest(
                vector_field="embedding",
                query_vector=Vector(query_vector),
                distance_measure="COSINE",
                limit=5
            ).get()
            
            for doc in results:
                found_images.append(doc.to_dict())
        except Exception as e:
            print(f"Image search failed for library {lib_id}: {e}")
            continue
    
    return found_images[:10]  # Limit to top 10
