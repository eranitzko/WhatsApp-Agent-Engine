"""Admin panel router — serves static SPA and mounts API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.staticfiles import StaticFiles

from app.admin.api import router as api_router
from app.config import settings

_STATIC_DIR = Path(__file__).parent.parent / "static" / "admin"

router = APIRouter()

# Include API sub-router
router.include_router(api_router, prefix="/api")


@router.get("/")
@router.get("")
def admin_root():
    if not settings.admin_ui_password:
        return Response(
            content="ADMIN_UI_PASSWORD is not set. Add it to .env and restart.",
            status_code=503,
            media_type="text/plain",
        )
    index = _STATIC_DIR / "index.html"
    return Response(content=index.read_bytes(), media_type="text/html")


def get_static_dir() -> Path:
    return _STATIC_DIR
