from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import Subscription, Tariff, User

logger = logging.getLogger(__name__)

DEFAULT_TARIFFS = (
    {"name": "Старт", "description": "3 мониторинга, проверка каждые 10 минут", "days": 7, "price_rub": 299, "max_tasks": 3, "check_interval": 600, "sort_order": 1},
    {"name": "Про", "description": "10 мониторингов, проверка каждые 3 минуты", "days": 30, "price_rub": 990, "max_tasks": 10, "check_interval": 180, "sort_order": 2},
    {"name": "Перекуп", "description": "30 мониторингов, проверка каждую минуту", "days": 30, "price_rub": 2490, "max_tasks": 30, "check_interval": 60, "sort_order": 3},
)


@dataclass(slots=True)
class AccessInfo:
    has_subscription: bool
    is_admin: bool
    tariff_name: str
    expires_at: datetime | None
    max_tasks: int
    check_interval: int
    free_searches_left: int

    @property
    def can_search(self) -> bool:
        return self.has_subscription or self.is_admin or self.free_searches_left > 0

    @property
    def can_monitor(self) -> bool:
        return self.has_subscription or self.is_admin


def rub_to_stars(price_rub: int, stars_rate: float) -> int:
    if stars_rate <= 0:
        return max(1, price_rub)
    return max(1, int(-(-price_rub // stars_rate)))


async def ensure_default_tariffs(session: AsyncSession, stars_rate: float) -> None:
    count = (await session.execute(select(func.count(Tariff.id)))).scalar_one()
    if count:
        return
    for data in DEFAULT_TARIFFS:
        session.add(Tariff(price_stars=rub_to_stars(data["price_rub"], stars_rate), **data))
    await session.commit()
    logger.info("default tariffs created")


async def list_tariffs(session: AsyncSession, only_active: bool = True) -> list[Tariff]:
    query = select(Tariff).order_by(Tariff.sort_order, Tariff.id)
    if only_active:
        query = query.where(Tariff.is_active.is_(True))
    return list((await session.execute(query)).scalars().all())


async def get_tariff(session: AsyncSession, tariff_id: int) -> Tariff | None:
    return await session.get(Tariff, tariff_id)


async def create_tariff(session: AsyncSession, **data: object) -> Tariff:
    tariff = Tariff(**data)
    session.add(tariff)
    await session.commit()
    return tariff


async def delete_tariff(session: AsyncSession, tariff: Tariff) -> None:
    await session.delete(tariff)
    await session.commit()


async def get_active_subscription(session: AsyncSession, user_id: int, now: datetime | None = None) -> Subscription | None:
    now = now or datetime.utcnow()
    query = (
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.expires_at > now)
        .order_by(Subscription.expires_at.desc())
        .limit(1)
    )
    return (await session.execute(query)).scalar_one_or_none()


async def grant_subscription(session: AsyncSession, user: User, tariff: Tariff, source: str, days: int | None = None) -> Subscription:
    now = datetime.utcnow()
    current = await get_active_subscription(session, user.id, now)
    duration = timedelta(days=days if days is not None else tariff.days)
    if current and current.tariff_id == tariff.id:
        current.expires_at = current.expires_at + duration
        current.max_tasks = tariff.max_tasks
        current.check_interval = tariff.check_interval
        await session.commit()
        return current
    starts_at = now
    subscription = Subscription(
        user_id=user.id,
        tariff_id=tariff.id,
        tariff_name=tariff.name,
        max_tasks=tariff.max_tasks,
        check_interval=tariff.check_interval,
        source=source,
        started_at=starts_at,
        expires_at=starts_at + duration,
    )
    session.add(subscription)
    await session.commit()
    return subscription


async def revoke_subscription(session: AsyncSession, user_id: int) -> bool:
    now = datetime.utcnow()
    rows = (await session.execute(select(Subscription).where(Subscription.user_id == user_id, Subscription.expires_at > now))).scalars().all()
    for row in rows:
        row.expires_at = now
    await session.commit()
    return bool(rows)


async def get_access(session: AsyncSession, user: User, is_admin: bool, free_searches: int, default_interval: int) -> AccessInfo:
    subscription = await get_active_subscription(session, user.id)
    free_left = max(0, free_searches - user.free_searches_used)
    if subscription:
        return AccessInfo(True, is_admin, subscription.tariff_name, subscription.expires_at, subscription.max_tasks, subscription.check_interval, free_left)
    if is_admin:
        return AccessInfo(False, True, "Администратор", None, 50, max(60, default_interval // 2), free_left)
    return AccessInfo(False, False, "Без подписки", None, 0, default_interval, free_left)
