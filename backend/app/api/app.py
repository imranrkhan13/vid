"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.deps import _REGISTRY
from app.api.routes import router
from app.bootstrap import seed_defaults
from app.core.config import Settings
from app.database.base import configure_from_settings, create_all, get_session

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


def create_app(*, configure_database: bool = True, seed: bool = True) -> FastAPI:
    app = FastAPI(title="AI Video Generation Harness (VGOH)", version=__version__)

    if configure_database:
        settings = Settings.from_env()
        configure_from_settings(settings)
        create_all()
        if seed:
            session = get_session()
            try:
                seed_defaults(session, _REGISTRY)
                session.commit()
            finally:
                session.close()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    app.include_router(router)

    # Frontend SPA (section 22): served as static assets, no build step.
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(str(WEB_DIR / "index.html"))

    return app


app = create_app()
