from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.workers.tasks  # noqa: F401  (registers pipeline dispatch)
from app.api import auth, covers, health, media, projects, settings_llm, shots, style
from app.core.bootstrap import ensure_admin_user
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import setup_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    with get_sessionmaker()() as db:
        ensure_admin_user(db)
        from app.services.style import ensure_seeded

        ensure_seeded(db)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="mhsquare API",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.env == "development" else None,
        openapi_url="/api/openapi.json" if settings.env == "development" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.ui_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    api = APIRouter(prefix="/api")
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(settings_llm.router)
    api.include_router(projects.router)
    api.include_router(media.router)
    api.include_router(shots.router)
    api.include_router(style.router)
    api.include_router(covers.router)
    app.include_router(api)
    return app


app = create_app()
