import os
import sys
from datetime import datetime

# Add the project root to sys.path
sys.path.append(os.getcwd())

# Force environment to development to allow local firestore access if needed, 
# but we want to use the actual credentials if available.
os.environ["ENVIRONMENT"] = "development" 

try:
    from backend.app.core.firebase import db
    from google.cloud import firestore

    print("Checking Firestore 'conversations' collection...")
    
    # Get last 5 conversations regardless of user
    docs = db.collection("conversations").order_by("updated_at", direction=firestore.Query.DESCENDING).limit(5).get()
    
    if not docs:
        print("No conversations found in the collection.")
    else:
        for d in docs:
            data = d.to_dict()
            print(f"ID: {d.id}")
            print(f"  User ID: {data.get('user_id')}")
            print(f"  Title: {data.get('title')}")
            print(f"  Updated At: {data.get('updated_at')}")
            print(f"  Message Count: {len(data.get('messages', []))}")
            print("-" * 20)

    # Also check 'users' collection
    print("\nChecking last 5 users...")
    users = db.collection("users").limit(5).get()
    for u in users:
        print(f"UID: {u.id}, Email: {u.to_dict().get('email')}")

except Exception as e:
    print(f"Error: {e}")
