from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api import documents, chat, export, assistants, libraries, templates, admin, images, users, projects, health, config
from backend.app.core.config import settings

app = FastAPI(title="Textbehandlaren", description="Intelligent Document Manager")

# CORS - uses configured origins (restricted in production)
allowed_origins = settings.ALLOWED_ORIGINS.split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers with /api prefix
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(export.router, prefix="/api/export", tags=["export"])
app.include_router(assistants.router, prefix="/api/assistants", tags=["assistants"])
app.include_router(libraries.router, prefix="/api/libraries", tags=["libraries"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(health.router, prefix="/api/health", tags=["health"])
app.include_router(images.router, prefix="/api/images", tags=["images"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(config.router, prefix="/api/config", tags=["config"])

# Serve static files last - this catches all remaining requests
app.mount("/static", StaticFiles(directory="backend/app/static", html=False), name="static_files")

# For serving index.html at root
from fastapi.responses import FileResponse
import os

@app.get("/")
async def serve_root():
    return FileResponse("backend/app/static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
