import firebase_admin
from firebase_admin import credentials, firestore
import os
import json

def initialize_firebase():
    # Try to load from environment variable first (JSON string)
    creds_json = os.getenv("FIREBASE_CREDENTIALS")
    
    if creds_json:
        try:
            creds_attr = json.loads(creds_json)
            cred = credentials.Certificate(creds_attr)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"Failed to initialize Firebase from env: {e}")
            # Fallback to default credentials (works on GCP environments)
            if not firebase_admin._apps:
                firebase_admin.initialize_app()
    else:
        # Fallback for local development: look for service-account.json
        possible_paths = [
            "service-account.json",
            "backend/service-account.json",
            os.path.join(os.path.dirname(__file__), "..", "..", "service-account.json"),
        ]
        
        creds_path = None
        for path in possible_paths:
            if os.path.exists(path):
                creds_path = path
                break
        
        if creds_path:
            cred = credentials.Certificate(creds_path)
            firebase_admin.initialize_app(cred)
            print(f"Firebase initialized from: {creds_path}")
        else:
            print("No Firebase credentials found. Proceeding with default (may fail locally).")
            if not firebase_admin._apps:
                firebase_admin.initialize_app()

    except Exception as e:
        print(f"CRITICAL: Failed to initialize Firestore client: {e}")
        # Don't raise here, let the app start so we can see the logs
        return None

try:
    db = initialize_firebase()
except Exception as e:
    print(f"CRITICAL: Firebase initialization failed: {e}")
    db = None
