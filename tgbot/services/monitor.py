from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgbot.database.models import SearchTask, SeenListing, User
from tgbot.keyboards.user import listing_kb
from tgbot.services.ai import SearchRequest
from tgbot.services.avito import AvitoBlockedError, AvitoError, Listing, Location
from tgbot.services.search import SearchService
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import get_active_subscription
from tgbot.utils.cards import listing_card
from tgbot.utils.emoji import em
from tgbot.utils.text import h

logger = logging.getLogger(__name__)

MAX_NEW_PER_TICK = 6
ADMIN_ALERT_COOLDOWN = 1800


class MonitorService:
    def __init__(
        self,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        search: SearchService,
        settings: SettingsService,
        admin_ids: tuple[int, ...],
        tick_seconds: int,
    ) -> None:
        self.bot = bot
        self._session_factory = session_factory
        self.search = search
        self.settings = settings
        self.admin_ids = admin_ids
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._last_admin_alert = 0.0
        self.last_tick_at: datetime | None = None
        self.checked_total = 0
        self.notified_total = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="avito-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("monitor loop started (tick %ss)", self.tick_seconds)
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("monitor tick failed")
            await asyncio.sleep(self.tick_seconds)

    async def _due_tasks(self) -> list[SearchTask]:
        now = datetime.utcnow()
        async with self._session_factory() as session:
            rows = (await session.execute(
                select(SearchTask).join(User, User.id == SearchTask.user_id)
                .where(SearchTask.is_active.is_(True), User.is_banned.is_(False), User.bot_blocked.is_(False))
                .order_by(SearchTask.last_checked_at.asc().nullsfirst())
            )).scalars().all()
        due: list[SearchTask] = []
        for task in rows:
            if task.last_checked_at is None or task.last_checked_at + timedelta(seconds=task.check_interval) <= now:
                due.append(task)
        return due

    async def _tick(self) -> None:
        self.last_tick_at = datetime.utcnow()
        if self.search.avito.is_blocked:
            return
        for task in await self._due_tasks():
            try:
                await self.check_task(task)
            except AvitoBlockedError as exc:
                await self._alert_admins(f"Авито ограничил доступ: {h(exc)}. Мониторинг на паузе ~10 минут. Проверьте прокси в настройках.")
                return
            except Exception:
                logger.exception("task %s check failed", task.id)
                await self._save_error(task.id, "внутренняя ошибка")

    async def _save_error(self, task_id: int, error: str | None) -> None:
        async with self._session_factory() as session:
            task = await session.get(SearchTask, task_id)
            if task:
                task.last_error = error[:250] if error else None
                task.last_checked_at = datetime.utcnow()
                await session.commit()

    async def _has_access(self, task: SearchTask) -> bool:
        if task.user_id in self.admin_ids:
            return True
        async with self._session_factory() as session:
            subscription = await get_active_subscription(session, task.user_id)
            if subscription:
                return True
            db_task = await session.get(SearchTask, task.id)
            if db_task:
                db_task.is_active = False
                db_task.last_error = "подписка закончилась"
                await session.commit()
        await self._notify(task.user_id, f"{em('lock')} <b>Подписка закончилась</b>\nМониторинг «{h(task.query)}» поставлен на паузу. Продлите подписку в разделе «Подписка».")
        return False

    async def check_task(self, task: SearchTask) -> None:
        if not await self._has_access(task):
            return
        request = SearchRequest(task.query, task.location_name, task.price_min, task.price_max, task.wishes)
        location = Location(name=task.location_name, id=task.location_id, slug=task.location_slug)
        try:
            listings = await self.search.find_listings(request, location, pages=1)
        except AvitoBlockedError:
            await self._save_error(task.id, "Авито ограничил доступ")
            raise
        except AvitoError as exc:
            await self._save_error(task.id, str(exc))
            return
        self.checked_total += 1
        baseline = task.last_checked_at is None
        fresh = await self._register_new(task.id, listings)
        async with self._session_factory() as session:
            db_task = await session.get(SearchTask, task.id)
            if db_task is None:
                return
            db_task.last_checked_at = datetime.utcnow()
            db_task.last_error = None
            db_task.found_count += len(fresh)
            await session.commit()
        if baseline or not fresh:
            return
        notified = 0
        for listing in fresh[:MAX_NEW_PER_TICK]:
            rated = await self.search.evaluate(request, listing)
            if rated.rating < task.min_rating:
                continue
            sent = await self._send_listing(task, rated.listing, rated.evaluation, request.fingerprint)
            if sent:
                notified += 1
        if notified:
            self.notified_total += notified
            async with self._session_factory() as session:
                db_task = await session.get(SearchTask, task.id)
                if db_task:
                    db_task.notified_count += notified
                    await session.commit()

    async def _register_new(self, task_id: int, listings: list[Listing]) -> list[Listing]:
        if not listings:
            return []
        ids = [listing.id for listing in listings]
        async with self._session_factory() as session:
            rows = await session.execute(select(SeenListing.listing_id).where(SeenListing.task_id == task_id, SeenListing.listing_id.in_(ids)))
            seen = {row[0] for row in rows}
            fresh = [listing for listing in listings if listing.id not in seen]
            for listing in fresh:
                session.add(SeenListing(task_id=task_id, listing_id=listing.id))
            await session.commit()
        return fresh

    async def _send_listing(self, task: SearchTask, listing: Listing, evaluation, request_hash: str) -> bool:
        header = f"{em('bell')} <b>Новое по мониторингу «{h(task.query)}»</b>\n\n"
        caption = header + listing_card(listing, evaluation)
        keyboard = listing_kb(listing, request_hash)
        try:
            if listing.cover:
                try:
                    await self.bot.send_photo(task.user_id, listing.cover, caption=caption, reply_markup=keyboard)
                    return True
                except Exception as exc:
                    logger.info("photo send failed for %s, falling back to text: %s", listing.id, exc)
            await self.bot.send_message(task.user_id, caption, reply_markup=keyboard, disable_web_page_preview=True)
            return True
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            return False
        except TelegramForbiddenError:
            async with self._session_factory() as session:
                user = await session.get(User, task.user_id)
                if user:
                    user.bot_blocked = True
                    await session.commit()
            return False
        except Exception:
            logger.exception("failed to deliver listing %s to %s", listing.id, task.user_id)
            return False

    async def _notify(self, user_id: int, text: str) -> None:
        try:
            await self.bot.send_message(user_id, text)
        except Exception as exc:
            logger.info("notify %s failed: %s", user_id, exc)

    async def _alert_admins(self, text: str) -> None:
        if time.monotonic() - self._last_admin_alert < ADMIN_ALERT_COOLDOWN:
            return
        self._last_admin_alert = time.monotonic()
        for admin_id in self.admin_ids:
            await self._notify(admin_id, f"{em('bot')} <b>Парсер</b>\n{text}")
