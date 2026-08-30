import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("teamflow.workers")


class PostCommitQueue:
    """Buffers job specs and dispatches them only AFTER the surrounding
    transaction commits — the 'enqueue after commit' rule from
    07-background-jobs.md. Without this, a rollback would leave phantom jobs
    describing state changes that never happened.

    Jobs live on the session's info dict; an after_commit listener dispatches
    them. In eager mode .delay() runs inline; in broker mode it hands the job
    to Redis. Either way, ordering guarantees are identical.
    """

    KEY = "_post_commit_jobs"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        if not self._pending():
            event.listen(session.sync_session, "after_commit", self._dispatch)

    def _pending(self) -> list:
        return self._session.sync_session.info.setdefault(type(self).KEY, [])

    def enqueue(self, task_func, *args) -> None:
        self._pending().append((task_func, args))

    def _dispatch(self, session) -> None:
        jobs = session.info.pop(type(self).KEY, [])
        for task_func, args in jobs:
            try:
                task_func(*args)
            except Exception:
                logger.exception("post-commit job failed: %s", task_func.name)

    @staticmethod
    def dispatch_pending(session: AsyncSession) -> None:
        """Manual dispatch for test sessions, which never truly commit.

        Operates directly on the session info instead of constructing a
        PostCommitQueue — constructing one would register another
        after_commit listener on every call, accumulating listeners (and
        their closures) for the lifetime of the shared test session.
        """
        jobs = session.sync_session.info.pop(PostCommitQueue.KEY, [])
        for task_func, args in jobs:
            try:
                task_func(*args)
            except Exception:
                logger.exception("post-commit job failed: %s", task_func.name)
