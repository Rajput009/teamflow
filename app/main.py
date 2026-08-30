import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.error_handlers import (
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.exceptions import AppError


def create_app() -> FastAPI:
    """App factory: build and configure a fresh FastAPI instance.

    A factory (instead of one global `app = FastAPI()`) lets tests spin up
    isolated app instances and makes configuration explicit.
    """
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    app = FastAPI(
        title="TeamFlow API",
        version="0.1.0",
        description="Project & team management backend",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # CORS allowlist (DECISIONS.md WF-6): default "" => no cross-origin
    # access at all. Credentials require an explicit origin (never "*").
    allow_origins = [
        origin.strip()
        for origin in settings.cors_origins.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    app.include_router(api_router, prefix="/api/v1")

    # Every non-2xx response leaves the API in the documented envelope shape.
    # FastAPI matches handlers by exception type: AppError (domain) and
    # StarletteHTTPException (framework) are separate branches; Exception is
    # the last-resort catch-all.
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    return app


app = create_app()
