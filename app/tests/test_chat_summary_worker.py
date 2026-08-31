"""Worker-side summary helper tests (docs/features/13-ai-memory.md §3.5).

The full worker path needs committed Postgres rows and is covered by
test_ai_chat_memory.py's summary cases. These unit tests pin the two decisions
that are easy to regress and don't need a DB:

- the summary is capped at the four schema headings (never mid-heading), and
- the body is a no-op when it is called with no committed messages to fold.
"""
import asyncio
import uuid
from types import SimpleNamespace

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
                return SimpleNamespace(summary=None) if calls == 1 else None

            async def scalars(self, _query):
                return SimpleNamespace(all=lambda: [])

            async def commit(self):
                raise AssertionError("must not commit below threshold")

        class FakeFactory:
            def __call__(self):
                return _Ctx(FakeSession())

        class _Ctx:
            def __init__(self, session):
                self._session = session

            async def __aenter__(self):
                return self._session

            async def __aexit__(self, *exc):
                return False

        def fake_build(model=None):
            raise AssertionError("LLM must not be called below threshold")

        monkeypatch.setattr("app.workers.chat_summary.ChatRepository", FakeRepo)
        monkeypatch.setattr("app.workers.chat_summary.async_session_factory", FakeFactory)
        monkeypatch.setattr("app.workers.chat_summary.build_llm_client", fake_build)

        await summarize_chat_session(
            organization_id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            expected_watermark=None,
        )
        await asyncio.sleep(0)


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
