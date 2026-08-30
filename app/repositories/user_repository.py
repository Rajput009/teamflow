import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EmailAlreadyExistsError
from app.models import User


class UserRepository:
    """All user queries live here.

    Repositories take a session, never create one — the request-scoped
    session flows down from the get_db dependency.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email)
        return await self._session.scalar(stmt)

    async def exists_by_email(self, email: str) -> bool:
        stmt = select(func.count()).select_from(User).where(User.email == email)
        return bool(await self._session.scalar(stmt))

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def create(self, *, email: str, hashed_password: str, full_name: str) -> User:
        """Insert inside a SAVEPOINT: a double-clicked Register button fires
        two concurrent requests whose exists_by_email pre-checks both pass;
        the loser hits the unique index on users.email. The savepoint keeps
        the transaction usable so we can translate to the designed 409
        instead of an IntegrityError -> 500."""
        user = User(email=email, hashed_password=hashed_password, full_name=full_name)
        self._session.add(user)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            raise EmailAlreadyExistsError() from None
        return user
