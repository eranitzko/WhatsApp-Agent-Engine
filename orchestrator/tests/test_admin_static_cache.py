"""Regression: the admin panel's static JS/CSS was served with no
Cache-Control header at all, so browsers apply heuristic caching and can
keep running old app.js after a deploy until the cache happens to expire —
found live while verifying an app.js fix actually took effect in a browser
that had the admin panel open moments before the deploy."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin.router import NoCacheStaticFiles


def test_admin_static_files_are_served_no_cache(tmp_path):
    (tmp_path / "app.js").write_text("console.log('hi');")

    app = FastAPI()
    app.mount("/static", NoCacheStaticFiles(directory=str(tmp_path)), name="static")
    client = TestClient(app)

    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"
