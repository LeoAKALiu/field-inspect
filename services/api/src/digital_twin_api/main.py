"""FastAPI application factory and scaffold routes."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from digital_twin_api.config import settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Health check for orchestration and Docker."""
        return {"status": "ok", "service": "digital-twin-api"}

    return app


app = create_app()
