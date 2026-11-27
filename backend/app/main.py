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


import logging
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.providers.base import scrub


_orig_factory = logging.getLogRecordFactory()


def _scrubbing_factory(*a, **k):
    """No registered credential reaches any log line, even from an uncaught exception (applies to every logger and handler)."""
    rec = _orig_factory(*a, **k)
    try:
        rec.msg = scrub(rec.getMessage()); rec.args = ()
        if rec.exc_info:
            rec.exc_text = scrub(logging.Formatter().formatException(rec.exc_info)); rec.exc_info = None
    except Exception:  # noqa: BLE001
        pass
    return rec


logging.setLogRecordFactory(_scrubbing_factory)


@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException):
    d = exc.detail
    return JSONResponse({"detail": scrub(d) if isinstance(d, str) else d}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def _any_exc(request: Request, exc: Exception):
    logging.getLogger("vgoh").error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)   # class only, never the message
    return JSONResponse({"detail": "internal error"}, status_code=500)


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
