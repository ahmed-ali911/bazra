from fastapi import FastAPI

from app.api.v1.router import api_router
from app.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="BAZRA API")
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
