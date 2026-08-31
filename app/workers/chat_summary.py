"""Celery task for the short-term chat summary (docs/features/13 §3).

Anchored incremental fold:

    tail     = last AI_CHAT_HISTORY_MESSAGES (always raw)
    to_fold  = messages after watermark AND before the tail
    trigger  = len(to_fold) >= AI_CHAT_SUMMARY_EVERY
    extend   = old summary + to_fold   (never the tail, never from scratch)
    watermark = last folded message id

The worker opens its OWN production session (never reuses a request session) and
updates the watermark with a CAS so a stale job no-ops instead of double-folding.
It writes ONLY to chat_sessions.summary / summarized_upto_message_id — never to a
memory table.
"""
import uuid

from sqlalchemy import select

from app.ai.llm import LLMClient, build_llm_client
from app.ai.prompts import PROMPT_CHAT_SUMMARY_V1
from app.core.config import get_settings
from app.core.exceptions import AiNotConfiguredError, AiUpstreamError
from app.models import ChatMessage, ChatSession
from app.repositories.chat_repository import ChatRepository


def _truncate_summary(text: str, max_bytes: int) -> str:
    """Truncate at the four schema headings, keeping them intact."""
    headings = (
        "Decisions:",
        "Open questions:",
        "Constraints / preferences mentioned:",
        "Facts the user asserted:",
    )
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    lines = text.splitlines()
    kept: list[str] = []
    current = ""
    for line in lines:
        if line in headings:
            current = line
        else:
            kept.append(f"{current} {line}".strip() if current else line)
            current = ""
    out = "\n".join(kept)
    while len(out.encode("utf-8")) > max_bytes:
        out = out[: max_bytes]
        # never leave a half-encoded multibyte char
        try:
            out.encode("utf-8")
            break
        except UnicodeEncodeError:
            out = out[:-1]
    return out


async def summarize_chat_session(
    *,
    organization_id: str,
    session_id: str,
    expected_watermark: str | None,
) -> None:
    """Real async body. Public for direct testing with an injected LLM; the
    Celery task wraps this in run_async."""
    settings = get_settings()
    org = uuid.UUID(organization_id)
    sid = uuid.UUID(session_id)
    watermark_id = uuid.UUID(expected_watermark) if expected_watermark else None

    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        chat = ChatRepository(session)
        session_row = await session.scalar(
            select(ChatSession).where(
                ChatSession.id == sid, ChatSession.organization_id == org
            )
        )
        if session_row is None:
            return

        tail = await chat.recent_messages(
            sid,
            organization_id=org,
            user_id=session_row.user_id,
            limit=settings.ai_chat_history_messages,
        )
        tail_start_seq = tail[0].seq if tail else None

        watermark_seq = 0
        if watermark_id is not None:
            wm = await session.scalar(
                select(ChatMessage.seq).where(
                    ChatMessage.id == watermark_id, ChatMessage.session_id == sid
                )
            )
            if wm is not None:
                watermark_seq = wm

        conditions = [ChatMessage.session_id == sid]
        if watermark_seq:
            conditions.append(ChatMessage.seq > watermark_seq)
        if tail_start_seq is not None:
            conditions.append(ChatMessage.seq < tail_start_seq)
        to_fold = list(
            (
                await session.scalars(
                    select(ChatMessage).where(*conditions).order_by(ChatMessage.seq.asc())
                )
            ).all()
        )

        if len(to_fold) < settings.ai_chat_summary_every:
            return

        existing = (session_row.summary or "").strip() or "No summary yet."
        fold_text = "\n".join(f"{m.role}: {m.content}" for m in to_fold)

        llm: LLMClient = build_llm_client(model=settings.ai_chat_summary_model)
        try:
            raw = await llm.complete(
                system=PROMPT_CHAT_SUMMARY_V1,
                user=f"{existing}\n\nNew messages to fold:\n{fold_text}",
            )
        except AiNotConfiguredError:
            # Summary is a best-effort side effect; never fail the request.
            return
        except AiUpstreamError:
            # Leave old summary + old watermark. Chat still works.
            return

        summary = _truncate_summary(raw.strip(), settings.ai_chat_summary_max_bytes)
        if not summary:
            return

        last_folded = to_fold[-1].id
        advanced = await chat.advance_summary(
            session_id=sid,
            organization_id=org,
            user_id=session_row.user_id,
            expected_watermark=watermark_id,
            summary=summary,
            last_folded_message_id=last_folded,
        )
        if advanced:
            await session.commit()
