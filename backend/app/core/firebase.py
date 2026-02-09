import json
import os

import firebase_admin
from firebase_admin import credentials, firestore


def initialize_firebase():
    """
    Initialize Firebase Admin SDK and return a Firestore client.

    Credential resolution order:
    1) FIREBASE_CREDENTIALS env var (JSON string)
    2) local service-account.json files (for local development)
    3) application default credentials (works on GCP environments)
    """
    try:
        if not firebase_admin._apps:
            creds_json = os.getenv("FIREBASE_CREDENTIALS", "").strip()

            if creds_json:
                try:
                    creds_attr = json.loads(creds_json)
                    cred = credentials.Certificate(creds_attr)
                    firebase_admin.initialize_app(cred)
                except Exception as e:
                    print(f"Failed to initialize Firebase from FIREBASE_CREDENTIALS env: {e}")
                    firebase_admin.initialize_app()
            else:
                possible_paths = [
                    "service-account.json",
                    "backend/service-account.json",
                    os.path.join(os.path.dirname(__file__), "..", "..", "service-account.json"),
                ]

                creds_path = next((p for p in possible_paths if os.path.exists(p)), None)
                if creds_path:
                    try:
                        cred = credentials.Certificate(creds_path)
                        firebase_admin.initialize_app(cred)
                        print(f"Firebase initialized from: {creds_path}")
                    except Exception as e:
                        print(f"Failed to initialize Firebase from file '{creds_path}': {e}")
                        firebase_admin.initialize_app()
                else:
                    print("No Firebase credentials found. Using default credentials (may fail locally).")
                    firebase_admin.initialize_app()

        return firestore.client()
    except Exception as e:
        print(f"CRITICAL: Failed to initialize Firestore client: {e}")
        return None


db = initialize_firebase()

