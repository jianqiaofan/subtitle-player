from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import Settings
from app.database import create_engine_from_url, session_factory
from app.models import Base


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = create_engine_from_url(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(engine)
        yield

    app = FastAPI(title="字幕云同步", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory(engine)
    app.include_router(router)
    return app


app = create_app()
