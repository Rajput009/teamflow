from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

# One engine per process. It owns the connection pool; requests borrow
# connections from it and return them. pool_pre_ping issues a cheap "SELECT 1"
# before handing out a connection, so a connection killed by the DB server
# (idle timeout) is replaced transparently instead of causing a 500.
engine = create_async_engine(
    str(settings.database_url),
    echo=False,
    pool_pre_ping=True,
)

# Factory that produces AsyncSession objects bound to this engine.
#
# expire_on_commit=False: after session.commit(), loaded attributes remain
# usable without new queries. With the default (True), ANY access to an
# attribute after commit triggers a refresh query — which in async code raises
# MissingGreenlet errors because implicit IO can't happen outside await.
async_session_factory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)
