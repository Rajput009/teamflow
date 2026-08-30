import os

# Set BEFORE any app import: Settings and the engine read these at import time.
# Env vars take priority over the .env file, so this redirects everything
# (including Alembic) to the dedicated test database.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://teamflow:teamflow@localhost:5432/teamflow_test",
)
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ENVIRONMENT", "local")

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

# Truncating these cascades to every dependent table (memberships, tasks,
# refresh_tokens, ...), giving each test a pristine database.
_ROOT_TABLES = "users, organizations"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(os.environ["DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(db_engine):
    """Wipe all data AFTER each test (autouse = applies even to unit tests)."""
    yield
    async with db_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_ROOT_TABLES} RESTART IDENTITY CASCADE"))


class _NullAsyncContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest_asyncio.fixture(autouse=True)
def inline_worker_jobs(db_session, monkeypatch):
    """Replace eager Celery task bodies with synchronous equivalents writing
    through the SHARED test session.

    Why not run the real task bodies? In eager mode they execute inline on the
    API's event loop; driving the shared asyncpg connection from another thread
    (run_async's bridge) corrupts the connection state. The synchronous stubs
    below preserve exactly what tests need to observe: a Notification row
    appearing in the recipient's inbox after the request completes. The real
    bodies still run under uvicorn (separate session, committed data) and later
    under real workers.
    """
    import uuid as _uuid

    from app.models import Notification
    from app.workers import tasks as worker_tasks

    def fake_create_notification(
        recipient_id, notification_type, payload, idempotency_key=None
    ):
        # mirror the real worker's idempotency dedupe (see _create_notification).
        # This sync stub can't query the DB, so it checks the pending-set for an
        # already-added key within the same (uncommitted) test session.
        if idempotency_key is not None:
            for pending in db_session.new:
                if (
                    isinstance(pending, Notification)
                    and pending.recipient_id == _uuid.UUID(recipient_id)
                    and pending.idempotency_key == idempotency_key
                ):
                    return
        db_session.add(
            Notification(
                recipient_id=_uuid.UUID(recipient_id),
                type=notification_type,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        )

    monkeypatch.setattr(
        worker_tasks.create_notification, "run", fake_create_notification
    )


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session):
    """HTTP client wired to the real app, with get_db swapped for the test session.

    Every request exercises routers -> services -> repositories for real;
    only the session is test plumbing. The app object is attached as `.app`
    so per-test fixtures can add more dependency overrides (e.g. the AI
    layer's FakeLLMClient).
    """
    from app.api.deps import get_db
    from app.main import create_app

    app = create_app()

    async def _override_get_db():
        yield db_session
        # test sessions never commit, so the after_commit listener can't fire;
        # dispatch buffered background jobs manually (eager mode runs inline)
        from app.workers.queue import PostCommitQueue

        PostCommitQueue.dispatch_pending(db_session)

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app
        yield c


class ScriptableLLM:
    """Test double for the LLMClient protocol: a queue of canned replies.

    Queue entries are raw strings (model output) or Exception instances
    (raised to simulate provider failures). An empty queue raises loudly â€”
    a test hitting the LLM unexpectedly is a bug in the test, not silence.
    """

    def __init__(self) -> None:
        self._queue: list = []
        self.calls: list[tuple[str, str]] = []

    def queue(self, *items) -> None:
        self._queue.extend(items)

    async def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._queue:
            raise AssertionError("FakeLLM got an unexpected complete() call")
        item = self._queue.pop(0)
        # The protocol promises complete() translates transport failures into
        # AiUpstreamError â€” the fake must honor the same contract the real
        # adapter does, so tests exercise the service path faithfully.
        try:
            if isinstance(item, Exception):
                raise item
            return item
        except httpx.HTTPError:
            from app.core.exceptions import AiUpstreamError

            raise AiUpstreamError() from None


@pytest_asyncio.fixture
async def fake_llm(client):
    """Swap the real LLM client (which would need an API key + network) for
    a scriptable fake â€” same dependency-override pattern as get_db."""
    from app.api.v1.endpoints.ai import get_llm

    fake = ScriptableLLM()
    client.app.dependency_overrides[get_llm] = lambda: fake
    yield fake
    client.app.dependency_overrides.pop(get_llm, None)
