from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate: float = 0.5) -> None:
        self._rate = rate
        self._last_seen: dict[int, float] = {}

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)
        now = time.monotonic()
        last = self._last_seen.get(tg_user.id, 0.0)
        self._last_seen[tg_user.id] = now
        if now - last < self._rate and isinstance(event, (Message, CallbackQuery)):
            if isinstance(event, CallbackQuery):
                await event.answer()
            return None
        if len(self._last_seen) > 10_000:
            cutoff = now - 60
            self._last_seen = {uid: ts for uid, ts in self._last_seen.items() if ts > cutoff}
        return await handler(event, data)
