from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import User
from tgbot.fsm.states import AdminUsers
from tgbot.keyboards.admin import admin_back_kb, admin_cancel_kb, grant_tariff_kb, user_card_kb, users_menu_kb
from tgbot.services.subscriptions import get_active_subscription, get_tariff, grant_subscription, list_tariffs, revoke_subscription
from tgbot.services.users import change_balance, count_user_tasks, export_users_text, find_user, set_ban, user_payment_summary
from tgbot.utils.emoji import em
from tgbot.utils.filters import IsAdmin
from tgbot.utils.text import format_dt, format_money, h, parse_float

logger = logging.getLogger(__name__)
router = Router(name="admin_users")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def user_card_text(session: AsyncSession, target: User) -> tuple[str, bool]:
    subscription = await get_active_subscription(session, target.id)
    orders, spent = await user_payment_summary(session, target.id)
    tasks = await count_user_tasks(session, target.id)
    sub_line = f"{h(subscription.tariff_name)} до {format_dt(subscription.expires_at)}" if subscription else "нет"
    status = f"{em('red')} забанен" if target.is_banned else f"{em('green')} активен"
    blocked = f"\n{em('user_ban')} Заблокировал бота" if target.bot_blocked else ""
    text = (
        f"{em('profile')} <b>Пользователь</b>\n\n"
        f"{em('tag')} ID: <code>{target.id}</code>\n"
        f"{em('font')} Username: {'@' + h(target.username) if target.username else '—'}\n"
        f"{em('smile')} Имя: {h(target.first_name or '—')}\n"
        f"{em('calendar')} Регистрация: {format_dt(target.registered_at)}\n"
        f"{em('elapsed')} Активность: {format_dt(target.last_active_at)}\n"
        f"{em('star')} Подписка: {sub_line}\n"
        f"{em('wallet')} Баланс: <b>{format_money(target.balance)}</b>\n"
        f"{em('receive_money')} Заказов: {orders} на {spent} ₽\n"
        f"{em('bell')} Активных мониторингов: {tasks} · поисков: {target.searches_count}\n"
        f"Статус: {status}{blocked}"
    )
    return text, subscription is not None


async def _show_card(message: Message, session: AsyncSession, target: User, edit: bool) -> None:
    text, has_sub = await user_card_text(session, target)
    if edit:
        await message.edit_text(text, reply_markup=user_card_kb(target, has_sub))
    else:
        await message.answer(text, reply_markup=user_card_kb(target, has_sub))


@router.callback_query(F.data == "admin:users")
async def callback_users_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(f"{em('users')} <b>Пользователи</b>\n\nПоиск по ID или @username, либо экспорт всей базы в txt.", reply_markup=users_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "users:search")
async def callback_users_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminUsers.search)
    await callback.message.edit_text(f"{em('eye')} Отправьте ID пользователя или его @username.", reply_markup=admin_cancel_kb("admin:users"))
    await callback.answer()


@router.message(AdminUsers.search, F.text)
async def process_user_search(message: Message, state: FSMContext, session: AsyncSession) -> None:
    target = await find_user(session, message.text)
    if target is None:
        await message.answer(f"{em('cross')} Пользователь «{h(message.text.strip())}» не найден. Попробуйте ещё раз.", reply_markup=admin_cancel_kb("admin:users"))
        return
    await state.clear()
    await _show_card(message, session, target, edit=False)


@router.callback_query(F.data.startswith("users:card:"))
async def callback_user_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    target = await session.get(User, int(callback.data.split(":")[-1]))
    if target is None:
        await callback.answer("Не найден", show_alert=True)
        return
    await _show_card(callback.message, session, target, edit=True)
    await callback.answer()


@router.callback_query(F.data == "users:export")
async def callback_users_export(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.answer("Готовлю файл…")
    text = await export_users_text(session)
    filename = f"users_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.txt"
    await callback.message.answer_document(BufferedInputFile(text.encode("utf-8"), filename=filename), caption=f"{em('download')} Экспорт пользователей")


@router.callback_query(F.data.startswith("users:ban:") | F.data.startswith("users:unban:"))
async def callback_user_ban(callback: CallbackQuery, session: AsyncSession, is_admin: bool) -> None:
    action, user_id = callback.data.split(":")[1], int(callback.data.split(":")[2])
    target = await session.get(User, user_id)
    if target is None:
        await callback.answer("Не найден", show_alert=True)
        return
    if action == "ban" and target.id == callback.from_user.id:
        await callback.answer("Себя банить нельзя", show_alert=True)
        return
    await set_ban(session, target, action == "ban")
    await _show_card(callback.message, session, target, edit=True)
    await callback.answer("Забанен" if target.is_banned else "Разблокирован")


@router.callback_query(F.data.startswith("users:balance:"))
async def callback_user_balance(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    user_id = int(callback.data.split(":")[-1])
    target = await session.get(User, user_id)
    if target is None:
        await callback.answer("Не найден", show_alert=True)
        return
    await state.set_state(AdminUsers.change_balance)
    await state.update_data(target_id=user_id)
    await callback.message.edit_text(
        f"{em('wallet')} Баланс пользователя {target.id}: <b>{format_money(target.balance)}</b>\n\n"
        f"Отправьте изменение: <code>+500</code> — начислить, <code>-200</code> — списать, <code>=1000</code> — установить.",
        reply_markup=admin_cancel_kb(f"users:card:{user_id}"),
    )
    await callback.answer()


@router.message(AdminUsers.change_balance, F.text)
async def process_balance_change(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    data = await state.get_data()
    target = await session.get(User, data.get("target_id", 0))
    if target is None:
        await state.clear()
        await message.answer("Пользователь не найден.", reply_markup=admin_back_kb("admin:users"))
        return
    raw = message.text.strip().replace(" ", "")
    mode = raw[0] if raw and raw[0] in "+-=" else "+"
    amount = parse_float(raw[1:] if raw and raw[0] in "+-=" else raw)
    if amount is None:
        await message.answer("Не понял сумму. Пример: <code>+500</code>", reply_markup=admin_cancel_kb(f"users:card:{target.id}"))
        return
    if mode == "=":
        delta = amount - target.balance
    elif mode == "-":
        delta = -amount
    else:
        delta = amount
    new_balance = await change_balance(session, target, delta)
    await state.clear()
    try:
        await bot.send_message(target.id, f"{em('wallet')} Ваш баланс изменён администратором: <b>{format_money(new_balance)}</b>")
    except Exception as exc:
        logger.info("balance notify failed: %s", exc)
    await message.answer(f"{em('check')} Баланс обновлён: {format_money(new_balance)}")
    await _show_card(message, session, target, edit=False)


@router.callback_query(F.data.startswith("users:grant_do:"))
async def callback_grant_do(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    _, _, user_id, tariff_id = callback.data.split(":")
    target = await session.get(User, int(user_id))
    tariff = await get_tariff(session, int(tariff_id))
    if target is None or tariff is None:
        await callback.answer("Не найдено", show_alert=True)
        return
    subscription = await grant_subscription(session, target, tariff, source="admin")
    try:
        await bot.send_message(target.id, f"{em('gift')} Вам выдана подписка «{h(tariff.name)}» до {format_dt(subscription.expires_at)}.")
    except Exception as exc:
        logger.info("grant notify failed: %s", exc)
    await _show_card(callback.message, session, target, edit=True)
    await callback.answer("Подписка выдана")


@router.callback_query(F.data.startswith("users:grant:"))
async def callback_grant_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = int(callback.data.split(":")[-1])
    tariffs = await list_tariffs(session, only_active=False)
    if not tariffs:
        await callback.answer("Сначала создайте тариф", show_alert=True)
        return
    await callback.message.edit_text(f"{em('gift')} Какой тариф выдать пользователю {user_id}?", reply_markup=grant_tariff_kb(user_id, tariffs))
    await callback.answer()


@router.callback_query(F.data.startswith("users:revoke:"))
async def callback_revoke(callback: CallbackQuery, session: AsyncSession) -> None:
    user_id = int(callback.data.split(":")[-1])
    target = await session.get(User, user_id)
    if target is None:
        await callback.answer("Не найден", show_alert=True)
        return
    await revoke_subscription(session, user_id)
    await _show_card(callback.message, session, target, edit=True)
    await callback.answer("Подписка снята")
