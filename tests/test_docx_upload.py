from fastapi.testclient import TestClient
from backend.app.main import app
import os
import pytest

client = TestClient(app)

def test_docx_upload():
    # 1. Create a library
    # Note: We don't send Authorization header to trigger dev user fallback in auth.py
    lib_response = client.post("/api/libraries", json={
        "name": "Test Library",
        "description": "Test library for DOCX upload",
        "library_type": "BACKGROUND",
        "scrub_enabled": False
    })
    
    if lib_response.status_code != 200:
        print(f"Failed to create library: {lib_response.text}")
    
    assert lib_response.status_code == 200
    lib_id = lib_response.json()["id"]

    # 2. Upload the DOCX file
    test_file_path = "/Users/joelberring/Desktop/vibe_old/textbehandlaren/Sammanställning granskning Örnsberg_251015_inkl da gvattenutredning.docx"
    
    if not os.path.exists(test_file_path):
        pytest.fail(f"Test file not found at {test_file_path}")

    with open(test_file_path, "rb") as f:
        upload_response = client.post(
            f"/api/documents/library/{lib_id}/upload",
            files={"file": (os.path.basename(test_file_path), f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        )
    
    if upload_response.status_code != 200:
        print(f"Failed to upload document: {upload_response.text}")
        
    assert upload_response.status_code == 200
    data = upload_response.json()
    assert data["status"] == "accepted"
    assert "bearbetas nu i bakgrunden" in data["message"]

    
    # 3. Clean up (optional)
    client.delete(f"/api/libraries/{lib_id}")

if __name__ == "__main__":
    test_docx_upload()
    print("Test passed!")
