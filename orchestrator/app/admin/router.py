"""Admin panel router — serves static SPA and mounts API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.staticfiles import StaticFiles

from app.admin.api import router as api_router
from app.config import settings

_STATIC_DIR = Path(__file__).parent.parent / "static" / "admin"

router = APIRouter()


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles that always revalidates with the server instead of
    trusting the browser's heuristic cache. Without this, a browser can
    keep running yesterday's app.js after a deploy until its own cache
    happens to expire — the ETag/Last-Modified headers StaticFiles already
    sets still make a 304 the common case, so this costs one small
    round-trip per load rather than a full re-download."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

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
