from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings
from app.database.session import init_db, new_session
from app.providers.registry import seed_catalog
from app.workers import runner


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = new_session()
    try:
        seed_catalog(db)
    finally:
        db.close()
    if settings.run_worker:
        runner.start()
    yield
    runner.stop()


app = FastAPI(title="VGOH - AI Video Generation Harness", version="0.1.0", lifespan=lifespan)
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.get("/api/health")
def health():
    return {"ok": True}


app.include_router(router)

_fe = settings.frontend_dir
if _fe.exists():
    app.mount("/_next", StaticFiles(directory=_fe / "_next"), name="next") if (_fe / "_next").exists() else None

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        f = (_fe / path).resolve()
        if _fe.resolve() in f.parents and f.is_file():
            return FileResponse(f)
        for cand in (_fe / path / "index.html", _fe / f"{path}.html"):
            if cand.is_file():
                return FileResponse(cand)
        return FileResponse(_fe / "index.html")
