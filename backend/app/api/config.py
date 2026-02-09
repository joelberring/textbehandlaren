from fastapi import APIRouter
from backend.app.core.config import settings

router = APIRouter()

@router.get("/firebase")
async def get_firebase_config():
    """
    Expose non-secret Firebase web configuration to the frontend.
    These values are required by the Firebase JS SDK and are safe to expose
    as they are intended for public client-side use.
    """
    return {
        "apiKey": settings.FIREBASE_API_KEY,
        "authDomain": settings.FIREBASE_AUTH_DOMAIN,
        "projectId": settings.FIREBASE_PROJECT_ID,
        "storageBucket": settings.FIREBASE_STORAGE_BUCKET,
        "messagingSenderId": settings.FIREBASE_MESSAGING_SENDER_ID,
        "appId": settings.FIREBASE_APP_ID,
        "measurementId": settings.FIREBASE_MEASUREMENT_ID
    }
