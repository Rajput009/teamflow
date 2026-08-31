import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, ChatSession


class ChatRepository:
    """Server-owned conversation store. GOLDEN RULE: every read/write resolves
    the session through (organization_id, user_id) — an unscoped session_id is
    never enough. Cross-org / other-user sessions are 404s, indistinguishable
    from nonexistent."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _owned_session_ids(organization_id: uuid.UUID, user_id: uuid.UUID) -> select:
        return (
            select(ChatSession.id)
            .where(
                ChatSession.organization_id == organization_id,
                ChatSession.user_id == user_id,
            )
        )

    # --- sessions ---

    async def create_session(
        self, *, organization_id: uuid.UUID, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> ChatSession:
        session = ChatSession(
            organization_id=organization_id,
            project_id=project_id,
            user_id=user_id,
            meta={},
        )
        self._session.add(session)
        await self._session.flush()
        return session

    async def get_owned(
        self, session_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> ChatSession | None:
        """Owner-scoped fetch. Foreign or another-user's session => None."""
        stmt = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.organization_id == organization_id,
            ChatSession.user_id == user_id,
        )
        return await self._session.scalar(stmt)

    async def list_for_user(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> tuple[list[ChatSession], int]:
        conditions = [
            ChatSession.organization_id == organization_id,
            ChatSession.user_id == user_id,
        ]
        if project_id is not None:
            conditions.append(ChatSession.project_id == project_id)
        total = await self._session.scalar(
            select(func.count()).select_from(ChatSession).where(*conditions)
        )
        stmt = (
            select(ChatSession)
            .where(*conditions)
            .order_by(ChatSession.last_message_at.desc(), ChatSession.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        items = list((await self._session.scalars(stmt)).all())
        return items, int(total or 0)

    async def delete(
        self,
        session: ChatSession,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Hard delete (GDPR erase). Cascades chat_messages via FK. The row was
        loaded owner-scoped; the ids are re-checked here as a safety net."""
        if (
            session.organization_id != organization_id
            or session.user_id != user_id
        ):
            return
        await self._session.delete(session)
        await self._session.flush()

    # --- messages ---

    async def lock_for_append(
        self, session_id: uuid.UUID, *, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> ChatSession | None:
        """SELECT ... FOR UPDATE on the owner-scoped row so two concurrent
        appends cannot interleave seq / last_message_at. Returns None for
        foreign sessions (404 at the service)."""
        stmt = (
            select(ChatSession)
            .where(
                ChatSession.id == session_id,
                ChatSession.organization_id == organization_id,
                ChatSession.user_id == user_id,
            )
            .with_for_update()
        )
        return await self._session.scalar(stmt)

    async def next_seq(
        self,
        session_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> int:
        """Max seq + 1. Called under the append lock, so no race; the id filter
        keeps the golden rule even on the message table."""
        current = await self._session.scalar(
            select(func.max(ChatMessage.seq)).where(
                ChatMessage.session_id == session_id,
                ChatMessage.session_id.in_(
                    self._owned_session_ids(organization_id, user_id)
                ),
            )
        )
        return int(current or 0) + 1

    async def append(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        seq: int,
        role: str,
        content: str,
    ) -> ChatMessage:
        """Insert one message. The owner row was already locked by the caller
        (`lock_for_append`); ids stay in the signature so every repo method
        remains owner-scoped by construction."""
        message = ChatMessage(
            session_id=session_id,
            seq=seq,
            role=role,
            content=content,
            meta={},
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def recent_messages(
        self,
        session_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int,
        only_before_seq: int | None = None,
    ) -> list[ChatMessage]:
        """Chronological tail (oldest first) by `seq`, never `created_at`.
        `only_before_seq` lets the summarizer look strictly behind the window."""
        conditions = [
            ChatMessage.session_id == session_id,
            ChatMessage.session_id.in_(
                self._owned_session_ids(organization_id, user_id)
            ),
        ]
        if only_before_seq is not None:
            conditions.append(ChatMessage.seq < only_before_seq)
        stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.seq.desc())
            .limit(limit)
        )
        rows = list((await self._session.scalars(stmt)).all())
        return list(reversed(rows))

    async def tail_for_prompt(
        self,
        session_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[ChatMessage]:
        return await self.recent_messages(
            session_id,
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
        )

    async def list_messages(
        self,
        session_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[ChatMessage], int]:
        """Chronological (oldest→newest) page. Phase 1 uses TeamFlow's standard
        offset Page envelope; cursor pagination is a Phase-2 enhancement."""
        conditions = [
            ChatMessage.session_id == session_id,
            ChatMessage.session_id.in_(
                self._owned_session_ids(organization_id, user_id)
            ),
        ]
        total = await self._session.scalar(
            select(func.count()).select_from(ChatMessage).where(*conditions)
        )
        stmt = (
            select(ChatMessage)
            .where(*conditions)
            .order_by(ChatMessage.seq.asc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        items = list((await self._session.scalars(stmt)).all())
        return items, int(total or 0)

    # --- summary / watermark ---

    async def summary_watermark(
        self,
        session_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> uuid.UUID | None:
        return await self._session.scalar(
            select(ChatSession.summarized_upto_message_id).where(
                ChatSession.id == session_id,
                ChatSession.organization_id == organization_id,
                ChatSession.user_id == user_id,
            )
        )

    async def advance_summary(
        self,
        *,
        session_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        expected_watermark: uuid.UUID | None,
        summary: str,
        last_folded_message_id: uuid.UUID | None,
    ) -> bool:
        """CAS watermark update. Returns False (no-op) if the row moved since
        this worker loaded it, so a stale job never double-folds."""
        stmt = (
            update(ChatSession)
            .where(
                ChatSession.id == session_id,
                ChatSession.organization_id == organization_id,
                ChatSession.user_id == user_id,
                ChatSession.summarized_upto_message_id.is_not_distinct_from(
                    expected_watermark
                ),
            )
            .values(
                summary=summary,
                summarized_upto_message_id=last_folded_message_id,
            )
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount == 1)
