from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def test_get_firebase_config():
    """Verify that the /api/config/firebase endpoint returns expected keys."""
    response = client.get("/api/config/firebase")
    assert response.status_code == 200
    data = response.json()
    for key in [
        "apiKey",
        "authDomain",
        "projectId",
        "storageBucket",
        "messagingSenderId",
        "appId",
        "measurementId",
    ]:
        assert key in data
