from firebase_admin import storage
import uuid
from backend.app.core.config import settings

# Note: Firebase Storage requires a bucket name.
# This should be set in config or derived from the Firebase project.
# Format: <project-id>.appspot.com

def get_storage_bucket():
    """Get the Firebase Storage bucket."""
    bucket_name = settings.FIREBASE_STORAGE_BUCKET or f"{settings.FIREBASE_PROJECT_ID}.appspot.com"
    return storage.bucket(bucket_name)

def upload_image(image_bytes: bytes, original_filename: str, library_id: str) -> str:
    """
    Upload an image to Firebase Storage and return the public URL.
    
    Args:
        image_bytes: The raw image data.
        original_filename: The original filename for reference.
        library_id: The library this image belongs to.
    
    Returns:
        The public URL of the uploaded image.
    """
    bucket = get_storage_bucket()
    
    # Create a unique path for the image
    file_extension = original_filename.split('.')[-1] if '.' in original_filename else 'png'
    unique_id = str(uuid.uuid4())
    blob_path = f"image_assets/{library_id}/{unique_id}.{file_extension}"
    
    blob = bucket.blob(blob_path)
    blob.upload_from_string(image_bytes, content_type=f"image/{file_extension}")
    
    # Make the blob publicly accessible
    blob.make_public()
    
    return blob.public_url

def delete_image(blob_path: str):
    """Delete an image from Firebase Storage."""
    bucket = get_storage_bucket()
    blob = bucket.blob(blob_path)
    blob.delete()
