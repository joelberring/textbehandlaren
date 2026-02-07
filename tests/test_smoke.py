from fastapi.testclient import TestClient
from backend.app.main import app


client = TestClient(app)


def test_root_serves_index():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_static_auth_js():
    response = client.get("/static/firebase-auth.js")
    assert response.status_code == 200
    assert "javascript" in response.headers.get("content-type", "")
