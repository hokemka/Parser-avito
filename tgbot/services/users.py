from __future__ import annotations

import logging
from datetime import datetime, timedelta

from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import Payment, SearchTask, Subscription, User

logger = logging.getLogger(__name__)


async def get_or_create_user(session: AsyncSession, tg_user: TgUser) -> tuple[User, bool]:
    user = await session.get(User, tg_user.id)
    created = False
    if user is None:
        user = User(id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name)
        session.add(user)
        created = True
    else:
        user.username = tg_user.username
        user.first_name = tg_user.first_name
        user.bot_blocked = False
    user.last_active_at = datetime.utcnow()
    await session.commit()
    return user, created


async def find_user(session: AsyncSession, raw: str) -> User | None:
    raw = raw.strip().lstrip("@")
    if raw.lstrip("-").isdigit():
        user = await session.get(User, int(raw))
        if user:
            return user
    return (await session.execute(select(User).where(func.lower(User.username) == raw.lower()))).scalar_one_or_none()


async def set_ban(session: AsyncSession, user: User, banned: bool) -> None:
    user.is_banned = banned
    await session.commit()


async def change_balance(session: AsyncSession, user: User, delta: float) -> float:
    user.balance = round(user.balance + delta, 2)
    await session.commit()
    return user.balance


async def mark_bot_blocked(session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user:
        user.bot_blocked = True
        await session.commit()


async def list_broadcast_targets(session: AsyncSession) -> list[int]:
    rows = await session.execute(select(User.id).where(User.is_banned.is_(False), User.bot_blocked.is_(False)))
    return [row[0] for row in rows]


async def count_user_tasks(session: AsyncSession, user_id: int, only_active: bool = True) -> int:
    query = select(func.count(SearchTask.id)).where(SearchTask.user_id == user_id)
    if only_active:
        query = query.where(SearchTask.is_active.is_(True))
    return int((await session.execute(query)).scalar_one())


async def user_payment_summary(session: AsyncSession, user_id: int) -> tuple[int, int]:
    row = (await session.execute(
        select(func.count(Payment.id), func.coalesce(func.sum(Payment.amount_rub), 0))
        .where(Payment.user_id == user_id, Payment.status == "paid")
    )).one()
    return int(row[0]), int(row[1])


async def collect_stats(session: AsyncSession) -> dict[str, int]:
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    total = (await session.execute(select(func.count(User.id)))).scalar_one()
    today = (await session.execute(select(func.count(User.id)).where(User.registered_at >= day_ago))).scalar_one()
    week = (await session.execute(select(func.count(User.id)).where(User.registered_at >= week_ago))).scalar_one()
    active_day = (await session.execute(select(func.count(User.id)).where(User.last_active_at >= day_ago))).scalar_one()
    banned = (await session.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))).scalar_one()
    blocked = (await session.execute(select(func.count(User.id)).where(User.bot_blocked.is_(True)))).scalar_one()
    subscribers = (await session.execute(
        select(func.count(func.distinct(Subscription.user_id))).where(Subscription.expires_at > now)
    )).scalar_one()
    tasks_active = (await session.execute(select(func.count(SearchTask.id)).where(SearchTask.is_active.is_(True)))).scalar_one()
    payments_count = (await session.execute(select(func.count(Payment.id)).where(Payment.status == "paid"))).scalar_one()
    revenue = (await session.execute(select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(Payment.status == "paid"))).scalar_one()
    revenue_day = (await session.execute(
        select(func.coalesce(func.sum(Payment.amount_rub), 0)).where(Payment.status == "paid", Payment.paid_at >= day_ago)
    )).scalar_one()
    searches = (await session.execute(select(func.coalesce(func.sum(User.searches_count), 0)))).scalar_one()
    return {
        "total": int(total), "today": int(today), "week": int(week), "active_day": int(active_day),
        "banned": int(banned), "blocked": int(blocked), "subscribers": int(subscribers),
        "tasks_active": int(tasks_active), "payments_count": int(payments_count), "revenue": int(revenue),
        "revenue_day": int(revenue_day), "searches": int(searches),
    }


async def export_users_text(session: AsyncSession) -> str:
    now = datetime.utcnow()
    users = (await session.execute(select(User).order_by(User.registered_at))).scalars().all()
    payment_rows = (await session.execute(
        select(Payment.user_id, func.count(Payment.id), func.coalesce(func.sum(Payment.amount_rub), 0))
        .where(Payment.status == "paid").group_by(Payment.user_id)
    )).all()
    payments = {row[0]: (int(row[1]), int(row[2])) for row in payment_rows}
    task_rows = (await session.execute(
        select(SearchTask.user_id, func.count(SearchTask.id)).where(SearchTask.is_active.is_(True)).group_by(SearchTask.user_id)
    )).all()
    tasks = {row[0]: int(row[1]) for row in task_rows}
    lines = [f"Экспорт пользователей — {now.strftime('%d.%m.%Y %H:%M')} UTC", f"Всего: {len(users)}", ""]
    for user in users:
        active = [s for s in user.subscriptions if s.expires_at > now]
        subscription = f"{active[0].tariff_name} до {active[0].expires_at.strftime('%d.%m.%Y')}" if active else "нет"
        orders, spent = payments.get(user.id, (0, 0))
        lines.append(
            f"ID: {user.id} | @{user.username or '-'} | {user.first_name or '-'} | "
            f"регистрация: {user.registered_at.strftime('%d.%m.%Y %H:%M')} | "
            f"подписка: {subscription} | заказов: {orders} на {spent} ₽ | баланс: {user.balance:.2f} ₽ | "
            f"мониторингов: {tasks.get(user.id, 0)} | поисков: {user.searches_count} | "
            f"{'ЗАБАНЕН' if user.is_banned else 'активен'}{' | заблокировал бота' if user.bot_blocked else ''}"
        )
    return "\n".join(lines)
