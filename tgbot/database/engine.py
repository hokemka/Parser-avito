from __future__ import annotations

import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from tgbot.database.models import Base

logger = logging.getLogger(__name__)


def create_engine(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_database(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("PRAGMA journal_mode=WAL"))
    logger.info("database ready")
