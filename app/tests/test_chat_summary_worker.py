"""Worker-side summary tests (docs/features/13-ai-memory.md §3.1/§3.3/§3.5).

Three layers:
- unit tests for the byte-cap helper (no DB),
- unit tests for the fold-boundary worker body (fake session, no DB),
- integration tests with committed Postgres rows so the worker's production
  session factory can see what it must fold.
"""
import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AiUpstreamError
from app.models import (
    ChatMessage,
    ChatSession,
    Membership,
    Organization,
    OrgRole,
    Project,
    User,
)
from app.workers.chat_summary import _truncate_summary, summarize_chat_session


class TestWorkerBody:
    async def test_noop_when_nothing_folded(self, monkeypatch):
        """Below AI_CHAT_SUMMARY_EVERY the worker returns without calling the
        LLM or touching the watermark (the no-fold path)."""
        calls = 0

        class FakeRepo:
            def __init__(self, session):
                self._session = session

            async def recent_messages(self, *args, **kwargs):
                return []

            async def advance_summary(self, **kwargs):
                raise AssertionError("must not advance below threshold")

        class FakeSession:
            async def scalar(self, _query):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return SimpleNamespace(summary=None, user_id=uuid.UUID(int=1))
                return None

            async def scalars(self, _query):
                return SimpleNamespace(all=lambda: [])

            async def commit(self):
                raise AssertionError("must not commit below threshold")

        class _Ctx:
            def __init__(self, session):
                self._session = session

            async def __aenter__(self):
                return self._session

            async def __aexit__(self, *exc):
                return False

        class FakeFactory:
            def __call__(self):
                return _Ctx(FakeSession())

        def fake_build(model=None):
            raise AssertionError("LLM must not be called below threshold")

        monkeypatch.setattr("app.workers.chat_summary.ChatRepository", FakeRepo)
        monkeypatch.setattr("app.db.session.async_session_factory", FakeFactory())
        monkeypatch.setattr("app.workers.chat_summary.build_llm_client", fake_build)

        await summarize_chat_session(
            organization_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            expected_watermark=None,
        )
        await asyncio.sleep(0)

    async def test_trailing_user_is_dropped_before_fold(self, monkeypatch):
        """§3.3 fold-boundary: a user message straddling the watermark/tail edge
        must NOT be folded; the watermark advances only to an assistant row."""
        next_seq = 0
        capture: dict[str, Any] = {}

        def make_msg(seq, role):
            return SimpleNamespace(
                id=uuid.UUID(int=seq),
                seq=seq,
                role=role,
                content=f"content-{seq}",
            )

        # 11 messages, last one a bare user message (incomplete turn).
        messages = [
            make_msg(i, "assistant" if i % 2 == 0 else "user")
            for i in range(1, 12)
        ]
        messages[-1] = make_msg(11, "user")

        class FakeLLM:
            async def complete(self, *, system, user):
                capture["user"] = user
                return (
                    "Decisions: x\n"
                    "Open questions: y\n"
                    "Constraints / preferences mentioned: z\n"
                    "Facts the user asserted: w"
                )

        class FakeRepo:
            def __init__(self, session):
                self._session = session

            async def recent_messages(self, *args, **kwargs):
                return []

            async def advance_summary(self, **kwargs):
                capture["last_folded"] = kwargs["last_folded_message_id"]
                return True

        class FakeSession:
            async def scalar(self, _query):
                nonlocal next_seq
                next_seq += 1
                if next_seq == 1:
                    return SimpleNamespace(summary=None, user_id=uuid.UUID(int=1))
                return None

            async def scalars(self, _query):
                return SimpleNamespace(all=lambda: messages.copy())

            async def commit(self):
                capture["committed"] = True

        class _Ctx:
            def __init__(self, session):
                self._session = session

            async def __aenter__(self):
                return self._session

            async def __aexit__(self, *exc):
                return False

        class FakeFactory:
            def __call__(self):
                return _Ctx(FakeSession())

        monkeypatch.setattr("app.workers.chat_summary.ChatRepository", FakeRepo)
        monkeypatch.setattr("app.db.session.async_session_factory", FakeFactory())
        monkeypatch.setattr(
            "app.workers.chat_summary.build_llm_client",
            lambda model=None: FakeLLM(),
        )

        await summarize_chat_session(
            organization_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            expected_watermark=None,
        )
        assert capture["last_folded"] == uuid.UUID(int=10)
        assert capture["committed"] is True
        assert "content-11" not in capture["user"]  # trailing user not folded
        assert "content-10" in capture["user"]  # its assistant reply is folded


class TestTruncateSummary:
    def test_short_summary_is_unchanged(self):
        text = "Decisions:\n- ship phase one\n"
        assert _truncate_summary(text, 1000) == text

    def test_cap_keeps_heading_prefixes(self):
        text = (
            "Decisions:\n- ship phase one\n"
            "Open questions:\n- who owns QA?\n"
            "Constraints / preferences mentioned:\n- no streaming\n"
            "Facts the user asserted:\n- sql is postgres\n"
        )
        capped = _truncate_summary(text, 80)
        assert len(capped.encode("utf-8")) <= 80
        assert "Decisions:" in capped
        assert "Open questions:" in capped or "Decisions:" in capped
        assert "Facts the user asserted:" in capped or "Constraints / " in capped

    def test_never_splits_a_multibyte_char(self):
        text = "Decisions:\n- café " * 20
        capped = _truncate_summary(text, 20)
        capped.encode("utf-8")  # must not raise UnicodeEncodeError
        assert len(capped.encode("utf-8")) <= 20


class TestWorkerIntegration:
    """Real Postgres rows, committed, read back by the worker helper."""

    async def _seed(
        self,
        db_engine,
        message_count: int = 31,
        summary: str | None = None,
        watermark_id: uuid.UUID | None = None,
    ) -> dict[str, uuid.UUID]:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        project_id = uuid.uuid4()
        session_id = uuid.uuid4()
        suffix = uuid.uuid4().hex[:8]

        async with factory() as session:
            user = User(
                id=user_id,
                email=f"worker-{suffix}@test.com",
                hashed_password="x",
                full_name="Worker",
            )
            session.add(user)
            await session.flush()

            session.add(
                Organization(
                    id=org_id,
                    name="Worker Org",
                    slug=f"worker-{suffix}",
                    created_by_id=user_id,
                )
            )
            session.add(
                Membership(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    user_id=user_id,
                    role=OrgRole.OWNER,
                )
            )
            session.add(
                Project(
                    id=project_id,
                    organization_id=org_id,
                    name="Worker Project",
                    created_by_id=user_id,
                )
            )
            session.add(
                ChatSession(
                    id=session_id,
                    organization_id=org_id,
                    project_id=project_id,
                    user_id=user_id,
                    title=None,
                    summary=summary,
                    summarized_upto_message_id=watermark_id,
                    meta={},
                    is_active=True,
                )
            )
            for i in range(1, message_count + 1):
                session.add(
                    ChatMessage(
                        id=uuid.UUID(int=i),
                        session_id=session_id,
                        seq=i,
                        role="user" if i % 2 else "assistant",
                        content=f"content-{i}",
                        meta={},
                    )
                )
            await session.commit()

        return {
            "organization_id": org_id,
            "user_id": user_id,
            "project_id": project_id,
            "session_id": session_id,
        }

    async def _read_session(self, db_engine, session_id: uuid.UUID) -> ChatSession:
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            return await session.get(ChatSession, session_id)

    @pytest_asyncio.fixture(autouse=True)
    def _fake_llm_factory(self, monkeypatch):
        class OkLLM:
            async def complete(self, *, system, user):
                return (
                    "Decisions: folded\n"
                    "Open questions: still open\n"
                    "Constraints / preferences mentioned: none\n"
                    "Facts the user asserted: test data"
                )

        factory = OkLLM()
        monkeypatch.setattr(
            "app.workers.chat_summary.build_llm_client",
            lambda model=None: factory,
        )
        return factory

    async def test_worker_folds_and_advances_watermark(self, db_engine):
        ids = await self._seed(db_engine, message_count=31)
        await summarize_chat_session(
            organization_id=str(ids["organization_id"]),
            session_id=str(ids["session_id"]),
            expected_watermark=None,
        )

        session = await self._read_session(db_engine, ids["session_id"])
        assert session.summary is not None
        assert "Decisions:" in session.summary
        # to_fold = seq 1..11 minus trailing user seq11 => up to seq10 (assistant)
        assert session.summarized_upto_message_id == uuid.UUID(int=10)

    async def test_provider_failure_leaves_old_summary_and_watermark(
        self, db_engine, monkeypatch
    ):
        ids = await self._seed(db_engine, message_count=31)

        class BrokenLLM:
            async def complete(self, *, system, user):
                raise AiUpstreamError()

        monkeypatch.setattr(
            "app.workers.chat_summary.build_llm_client",
            lambda model=None: BrokenLLM(),
        )
        await summarize_chat_session(
            organization_id=str(ids["organization_id"]),
            session_id=str(ids["session_id"]),
            expected_watermark=None,
        )

        session = await self._read_session(db_engine, ids["session_id"])
        assert session.summary is None
        assert session.summarized_upto_message_id is None

    async def test_stale_watermark_job_noops(self, db_engine):
        ids = await self._seed(
            db_engine,
            message_count=31,
            summary="Decisions: old",
            watermark_id=uuid.UUID(int=10),
        )
        # A stale job carries expected_watermark=None, but the row has already
        # advanced to seq10; the CAS must reject the double-fold.
        await summarize_chat_session(
            organization_id=str(ids["organization_id"]),
            session_id=str(ids["session_id"]),
            expected_watermark=None,
        )

        session = await self._read_session(db_engine, ids["session_id"])
        assert session.summary == "Decisions: old"
        assert session.summarized_upto_message_id == uuid.UUID(int=10)
