from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgbot.database.models import Broadcast
from tgbot.fsm.states import AdminBroadcast
from tgbot.keyboards.admin import admin_back_kb, admin_cancel_kb, broadcast_buttons_kb, broadcast_confirm_kb, broadcast_links_kb
from tgbot.services.users import list_broadcast_targets, mark_bot_blocked
from tgbot.utils.emoji import em
from tgbot.utils.filters import IsAdmin
from tgbot.utils.text import h, is_valid_url

logger = logging.getLogger(__name__)
router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SEND_DELAY = 0.05
PROGRESS_EVERY = 50


def _buttons_menu_text(buttons: list[dict[str, str]]) -> str:
    if not buttons:
        return f"{em('link')} <b>Хотите добавить кнопки к сообщению?</b>\n\nКнопки со ссылками появятся под сообщением рассылки."
    listed = "\n".join(f"• {h(b['title'])} → {h(b['url'])}" for b in buttons)
    return f"{em('link')} <b>Кнопки под сообщением</b>\n\n{listed}\n\nДобавить ещё или продолжить?"


@router.callback_query(F.data == "admin:broadcast")
async def callback_broadcast_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminBroadcast.waiting_message)
    await callback.message.edit_text(
        f"{em('megaphone')} <b>Отправьте сообщение для рассылки.</b>\n\n"
        f"Подойдёт любой формат: текст с форматированием, фото, видео, документ, голосовое — я разошлю его как есть.",
        reply_markup=admin_cancel_kb("bc:cancel"),
    )
    await callback.answer()


@router.message(AdminBroadcast.waiting_message)
async def process_broadcast_message(message: Message, state: FSMContext) -> None:
    await state.update_data(source_chat_id=message.chat.id, source_message_id=message.message_id, buttons=[])
    await state.set_state(AdminBroadcast.buttons_menu)
    await message.answer(_buttons_menu_text([]), reply_markup=broadcast_buttons_kb(False))


@router.callback_query(AdminBroadcast.buttons_menu, F.data == "bc:add_button")
async def callback_add_button(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminBroadcast.button_title)
    await callback.message.edit_text(f"{em('add_text')} Напишите название кнопки.", reply_markup=admin_cancel_kb("bc:cancel"))
    await callback.answer()


@router.message(AdminBroadcast.button_title, F.text)
async def process_button_title(message: Message, state: FSMContext) -> None:
    title = message.text.strip()
    if not title or len(title) > 60:
        await message.answer("Название должно быть от 1 до 60 символов.", reply_markup=admin_cancel_kb("bc:cancel"))
        return
    await state.update_data(pending_title=title)
    await state.set_state(AdminBroadcast.button_url)
    await message.answer(f"{em('link')} Теперь отправьте ссылку, куда будет вести кнопка «{h(title)}».", reply_markup=admin_cancel_kb("bc:cancel"))


@router.message(AdminBroadcast.button_url, F.text)
async def process_button_url(message: Message, state: FSMContext) -> None:
    url = message.text.strip()
    if not is_valid_url(url):
        await message.answer("Ссылка должна начинаться с http://, https:// или tg://. Попробуйте ещё раз.", reply_markup=admin_cancel_kb("bc:cancel"))
        return
    data = await state.get_data()
    buttons = list(data.get("buttons") or [])
    buttons.append({"title": data["pending_title"], "url": url})
    await state.update_data(buttons=buttons, pending_title=None)
    await state.set_state(AdminBroadcast.buttons_menu)
    await message.answer(_buttons_menu_text(buttons), reply_markup=broadcast_buttons_kb(True))


@router.callback_query(AdminBroadcast.buttons_menu, F.data == "bc:remove_button")
async def callback_remove_button(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    buttons = list(data.get("buttons") or [])
    if buttons:
        buttons.pop()
    await state.update_data(buttons=buttons)
    await callback.message.edit_text(_buttons_menu_text(buttons), reply_markup=broadcast_buttons_kb(bool(buttons)))
    await callback.answer()


@router.callback_query(AdminBroadcast.buttons_menu, F.data == "bc:continue")
async def callback_broadcast_preview(callback: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession) -> None:
    data = await state.get_data()
    buttons = data.get("buttons") or []
    await callback.answer()
    await callback.message.edit_text(f"{em('eye')} <b>Так будет выглядеть сообщение:</b>")
    try:
        await bot.copy_message(
            chat_id=callback.from_user.id,
            from_chat_id=data["source_chat_id"],
            message_id=data["source_message_id"],
            reply_markup=broadcast_links_kb(buttons),
        )
    except TelegramBadRequest as exc:
        await state.clear()
        await callback.message.answer(f"{em('cross')} Не удалось скопировать сообщение: {h(str(exc))}", reply_markup=admin_back_kb())
        return
    total = len(await list_broadcast_targets(session))
    await state.set_state(AdminBroadcast.confirm)
    await callback.message.answer(
        f"{em('megaphone')} <b>Разослать {total} пользователям?</b>",
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(F.data == "bc:cancel")
async def callback_broadcast_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(f"{em('cross')} Рассылка отменена.", reply_markup=admin_back_kb())
    await callback.answer()


@router.callback_query(AdminBroadcast.confirm, F.data == "bc:send")
async def callback_broadcast_send(callback: CallbackQuery, state: FSMContext, bot: Bot, session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]) -> None:
    data = await state.get_data()
    await state.clear()
    targets = await list_broadcast_targets(session)
    status = await callback.message.edit_text(f"{em('loading')} Рассылка запущена: 0 из {len(targets)}…")
    await callback.answer()
    asyncio.create_task(_run_broadcast_safely(bot, session_factory, callback.from_user.id, status, targets, data))


async def _run_broadcast_safely(bot: Bot, session_factory: async_sessionmaker[AsyncSession], admin_id: int, status: Message, targets: list[int], data: dict) -> None:
    try:
        await run_broadcast(bot, session_factory, admin_id, status, targets, data)
    except Exception:
        logger.exception("broadcast crashed")
        try:
            await bot.send_message(admin_id, f"{em('cross')} Рассылка прервана из-за ошибки, смотрите логи.")
        except Exception:
            pass


async def run_broadcast(bot: Bot, session_factory: async_sessionmaker[AsyncSession], admin_id: int, status: Message, targets: list[int], data: dict) -> None:
    keyboard = broadcast_links_kb(data.get("buttons") or [])
    started = time.monotonic()
    sent = failed = blocked = 0
    async with session_factory() as session:
        record = Broadcast(admin_id=admin_id, total=len(targets))
        session.add(record)
        await session.commit()
        record_id = record.id
    for index, user_id in enumerate(targets, start=1):
        delivered = await _deliver(bot, user_id, data, keyboard)
        if delivered == "sent":
            sent += 1
        elif delivered == "blocked":
            blocked += 1
            async with session_factory() as session:
                await mark_bot_blocked(session, user_id)
        else:
            failed += 1
        if index % PROGRESS_EVERY == 0:
            try:
                await status.edit_text(f"{em('loading')} Рассылка: {index} из {len(targets)} · доставлено {sent}")
            except TelegramBadRequest:
                pass
        await asyncio.sleep(SEND_DELAY)
    elapsed = int(time.monotonic() - started)
    async with session_factory() as session:
        record = await session.get(Broadcast, record_id)
        if record:
            record.sent, record.failed, record.finished_at = sent, failed + blocked, datetime.utcnow()
            await session.commit()
    summary = (
        f"{em('check')} <b>Рассылка завершена</b>\n\n"
        f"{em('users')} Всего: {len(targets)}\n"
        f"{em('send')} Доставлено: <b>{sent}</b>\n"
        f"{em('user_ban')} Заблокировали бота: {blocked}\n"
        f"{em('cross')} Ошибок: {failed}\n"
        f"{em('clock')} Время: {elapsed} сек"
    )
    try:
        await status.edit_text(summary, reply_markup=admin_back_kb())
    except TelegramBadRequest:
        await bot.send_message(admin_id, summary, reply_markup=admin_back_kb())


async def _deliver(bot: Bot, user_id: int, data: dict, keyboard) -> str:
    for _ in range(3):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=data["source_chat_id"],
                message_id=data["source_message_id"],
                reply_markup=keyboard,
            )
            return "sent"
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after + 0.5)
        except TelegramForbiddenError:
            return "blocked"
        except TelegramBadRequest as exc:
            if "chat not found" in str(exc).lower() or "user is deactivated" in str(exc).lower():
                return "blocked"
            logger.info("broadcast to %s failed: %s", user_id, exc)
            return "failed"
        except Exception as exc:
            logger.warning("broadcast to %s failed: %s", user_id, exc)
            return "failed"
    return "failed"
