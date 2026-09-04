from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgbot.services.users import get_or_create_user

logger = logging.getLogger(__name__)


class DatabaseMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class UserMiddleware(BaseMiddleware):
    def __init__(self, admin_ids: tuple[int, ...]) -> None:
        self._admin_ids = set(admin_ids)

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        if tg_user is None or session is None or tg_user.is_bot:
            return await handler(event, data)
        user, created = await get_or_create_user(session, tg_user)
        is_admin = tg_user.id in self._admin_ids
        if user.is_banned and not is_admin:
            await self._reject(event)
            return None
        data["user"] = user
        data["is_admin"] = is_admin
        data["is_new_user"] = created
        return await handler(event, data)

    @staticmethod
    async def _reject(event: TelegramObject) -> None:
        text = "Доступ к боту ограничен."
        try:
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            elif isinstance(event, Message):
                await event.answer(text)
        except Exception as exc:
            logger.debug("reject notice failed: %s", exc)
