from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.lifecycle import lifespan
from app.middleware.setup import configure_middleware


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        debug=settings.app_debug,
        lifespan=lifespan,
    )
    configure_middleware(app)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()
