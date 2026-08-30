"""Celery task definitions.

Each task opens its OWN database session — in production, workers are separate
processes with no access to the request's session. In eager local mode tasks
run inline in the API process via run_async()'s thread bridge, reading
committed data through this same production session factory.
"""
import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import Notification, NotificationType
from app.workers.celery_app import celery_app

logger = logging.getLogger("teamflow.workers")

_engine = None


def run_async(coro):
    """Bridge Celery's sync task model to our async DB layer.

    Two contexts call this:
    - production workers: no event loop running -> plain asyncio.run
    - eager local/test mode: we are INSIDE the API's running loop, where
      asyncio.run() raises — so we execute the coroutine on its own thread
      with a fresh loop instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _production_session_factory() -> async_sessionmaker[AsyncSession]:
    """Lazily-built engine/factory owned by the worker layer, separate from
    the API's request-session machinery."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(str(get_settings().database_url))
    return async_sessionmaker(_engine, expire_on_commit=False)


@celery_app.task(name="notifications.create", ignore_result=True)
def create_notification(
    recipient_id: str,
    notification_type: str,
    payload: dict,
    idempotency_key: str | None = None,
) -> None:
    run_async(_create_notification(recipient_id, notification_type, payload, idempotency_key))


async def _create_notification(
    recipient: str, ntype: str, data: dict, idempotency_key: str | None
) -> None:
    factory = _production_session_factory()
    async with factory() as session:
        try:
            if idempotency_key is not None:
                existing = await session.scalar(
                    select(Notification).where(
                        Notification.recipient_id == uuid.UUID(recipient),
                        Notification.idempotency_key == idempotency_key,
                    )
                )
                if existing is not None:
                    # same event already delivered — drop the duplicate
                    logger.info(
                        "notification deduped: recipient=%s key=%s",
                        recipient,
                        idempotency_key,
                    )
                    return
            session.add(
                Notification(
                    recipient_id=uuid.UUID(recipient),
                    type=NotificationType(ntype).value,
                    payload=data,
                    idempotency_key=idempotency_key,
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    logger.info("notification created: recipient=%s type=%s", recipient, ntype)


@celery_app.task(name="emails.send", ignore_result=True)
def send_email(recipient_user_id: str, subject: str, body: str) -> None:
    """Console transport for development — emails print to worker logs.

    Takes a USER ID, not an address: trigger sites usually only hold the id,
    so resolving the address here keeps one source of truth (the users table).
    A production SMTP transport arrives with the Docker phase.
    """
    run_async(_send_email(recipient_user_id, subject, body))


async def _send_email(recipient_user_id: str, subject: str, body: str) -> None:
    from sqlalchemy import select

    from app.models import User

    factory = _production_session_factory()
    async with factory() as session:
        email = await session.scalar(
            select(User.email).where(User.id == uuid.UUID(recipient_user_id))
        )
    if email is None:
        # recipient vanished between enqueue and delivery — nothing to send
        logger.warning(
            "email skipped: user %s no longer exists", recipient_user_id
        )
        return
    logger.info("[EMAIL DEV TRANSPORT] to=%s subject=%r\n%s", email, subject, body)
