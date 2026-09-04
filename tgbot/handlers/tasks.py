from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import SearchTask, User
from tgbot.keyboards.user import MENU_TASKS, back_kb, min_rating_kb, task_delete_confirm_kb, task_kb, tasks_list_kb
from tgbot.services.search import SearchService
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import get_access
from tgbot.services.users import count_user_tasks
from tgbot.utils.cards import listing_details
from tgbot.utils.emoji import em
from tgbot.utils.text import h
from tgbot.utils.texts import task_text

router = Router(name="tasks")


async def _tasks_view(session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> tuple[str, object]:
    access = await get_access(session, user, is_admin, settings.values.free_searches, settings.values.default_check_interval)
    tasks = (await session.execute(select(SearchTask).where(SearchTask.user_id == user.id).order_by(SearchTask.created_at.desc()))).scalars().all()
    active = sum(1 for task in tasks if task.is_active)
    if not access.can_monitor:
        text = (
            f"{em('bell')} <b>Мои мониторинги</b>\n\n"
            f"{em('lock')} Мониторинг доступен по подписке: бот сам проверяет Авито и присылает новые объявления с высокой оценкой."
        )
        return text, back_kb("menu:subscription")
    if not tasks:
        text = f"{em('bell')} <b>Мои мониторинги</b>\n\nПока нет ни одного. Создайте через «Найти выгодное» → «Найти и включить мониторинг»."
    else:
        text = f"{em('bell')} <b>Мои мониторинги</b>\n\nАктивных: {active} из {access.max_tasks}. Нажмите на мониторинг, чтобы управлять им."
    return text, tasks_list_kb(list(tasks), active < access.max_tasks)


@router.message(F.text == MENU_TASKS)
async def menu_tasks_message(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.clear()
    text, keyboard = await _tasks_view(session, user, is_admin, settings)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:tasks")
async def menu_tasks_callback(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.clear()
    text, keyboard = await _tasks_view(session, user, is_admin, settings)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


async def _own_task(session: AsyncSession, task_id: int, user: User) -> SearchTask | None:
    task = await session.get(SearchTask, task_id)
    if task is None or task.user_id != user.id:
        return None
    return task


@router.callback_query(F.data.startswith("task:"))
async def callback_task_card(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    task = await _own_task(session, int(callback.data.split(":")[1]), user)
    if task is None:
        await callback.answer("Мониторинг не найден", show_alert=True)
        return
    await callback.message.edit_text(task_text(task), reply_markup=task_kb(task))
    await callback.answer()


@router.callback_query(F.data.startswith("task_toggle:"))
async def callback_task_toggle(callback: CallbackQuery, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    task = await _own_task(session, int(callback.data.split(":")[1]), user)
    if task is None:
        await callback.answer("Мониторинг не найден", show_alert=True)
        return
    if not task.is_active:
        access = await get_access(session, user, is_admin, settings.values.free_searches, settings.values.default_check_interval)
        if not access.can_monitor:
            await callback.answer("Нужна активная подписка", show_alert=True)
            return
        if await count_user_tasks(session, user.id) >= access.max_tasks:
            await callback.answer(f"Лимит активных мониторингов: {access.max_tasks}", show_alert=True)
            return
        task.check_interval = access.check_interval
        task.last_error = None
    task.is_active = not task.is_active
    await session.commit()
    await callback.message.edit_text(task_text(task), reply_markup=task_kb(task))
    await callback.answer("Возобновлён" if task.is_active else "На паузе")


@router.callback_query(F.data.startswith("task_del_yes:"))
async def callback_task_delete_confirmed(callback: CallbackQuery, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    task = await _own_task(session, int(callback.data.split(":")[1]), user)
    if task is not None:
        await session.delete(task)
        await session.commit()
    text, keyboard = await _tasks_view(session, user, is_admin, settings)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("task_del:"))
async def callback_task_delete(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    task = await _own_task(session, int(callback.data.split(":")[1]), user)
    if task is None:
        await callback.answer("Мониторинг не найден", show_alert=True)
        return
    await callback.message.edit_text(f"{em('trash')} Удалить мониторинг «{h(task.query)}»?", reply_markup=task_delete_confirm_kb(task.id))
    await callback.answer()


@router.callback_query(F.data.startswith("task_rating_set:"))
async def callback_task_rating_set(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    _, task_id, value = callback.data.split(":")
    task = await _own_task(session, int(task_id), user)
    if task is None:
        await callback.answer("Мониторинг не найден", show_alert=True)
        return
    task.min_rating = max(0, min(10, int(value)))
    await session.commit()
    await callback.message.edit_text(task_text(task), reply_markup=task_kb(task))
    await callback.answer(f"Минимальная оценка: {task.min_rating}")


@router.callback_query(F.data.startswith("task_rating:"))
async def callback_task_rating(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    task = await _own_task(session, int(callback.data.split(":")[1]), user)
    if task is None:
        await callback.answer("Мониторинг не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"{em('stats')} Присылать объявления с оценкой ИИ не ниже… (сейчас {task.min_rating})",
        reply_markup=min_rating_kb(task.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("det:"))
async def callback_listing_details(callback: CallbackQuery, search_service: SearchService) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return
    rated = await search_service.get_stored(int(parts[1]), parts[2])
    if rated is None:
        await callback.answer("Разбор устарел. Запустите поиск заново.", show_alert=True)
        return
    await callback.message.answer(listing_details(rated.listing, rated.evaluation), disable_web_page_preview=True)
    await callback.answer()
