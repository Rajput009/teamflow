from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "teamflow",
    # In eager mode the broker is never contacted; this URL matters only when
    # task_always_eager=False (Docker phase).
    broker=settings.redis_url,
    backend=None,
    # Without this, production workers start knowing zero tasks and silently
    # drop every job. Harmless in eager mode.
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_always_eager=settings.task_always_eager,
    # a failed side-effect must not 500 an already-committed request
    task_eager_propagates=False,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
)
