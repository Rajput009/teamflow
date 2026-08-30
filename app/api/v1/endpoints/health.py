from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    environment: str


class DbHealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe: is the process up and serving?

    Deliberately checks NOTHING external (DB, Redis) — that would turn a
    process problem into a dependency problem.
    """
    settings = get_settings()
    return HealthResponse(status="ok", environment=settings.environment)


@router.get("/health/db", response_model=DbHealthResponse)
async def db_health() -> DbHealthResponse:
    """Readiness probe for the database.

    Separate from /health on purpose: if this fails, the API process is fine
    but Postgres is not. Monitoring can then alert the right person.
    """
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return DbHealthResponse(status="ok", database="reachable")
