from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import Config
from tgbot.database.models import SearchTask, User
from tgbot.fsm.states import CreateSearch
from tgbot.keyboards.user import (
    MENU_SEARCH, back_kb, cancel_kb, confirm_search_kb, found_locations_kb, listing_kb, locations_kb, price_kb,
    results_footer_kb, wishes_kb,
)
from tgbot.services.ai import SearchRequest
from tgbot.services.avito import POPULAR_LOCATIONS, AvitoBlockedError, AvitoError, Location
from tgbot.services.search import RatedListing, SearchService
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import AccessInfo, get_access
from tgbot.services.users import count_user_tasks
from tgbot.utils.cards import listing_card
from tgbot.utils.emoji import em
from tgbot.utils.text import format_price_range, h, parse_price_range
from tgbot.utils.texts import search_summary_text

logger = logging.getLogger(__name__)
router = Router(name="search")

MAX_RESULTS_TO_SEND = 10


async def load_access(session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> AccessInfo:
    return await get_access(session, user, is_admin, settings.values.free_searches, settings.values.default_check_interval)


def _location_from_data(data: dict[str, Any]) -> Location:
    return Location(name=data["location_name"], id=data.get("location_id"), slug=data.get("location_slug"))


def _request_from_data(data: dict[str, Any]) -> SearchRequest:
    return SearchRequest(data["query"], data["location_name"], data.get("price_min"), data.get("price_max"), data.get("wishes"))


async def start_search(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    access = await load_access(session, user, is_admin, settings)
    if not access.can_search:
        await message.answer(
            f"{em('lock')} <b>Бесплатные поиски закончились</b>\n\nОформите подписку, чтобы искать без ограничений и включить мониторинг.",
            reply_markup=back_kb("menu:subscription"),
        )
        return
    await state.clear()
    await state.set_state(CreateSearch.query)
    await message.answer(
        f"{em('eye')} <b>Что ищем?</b>\n\nНапишите только товар, как можно точнее: модель, память, поколение. "
        f"Город и бюджет спрошу следующими шагами.\n"
        f"Например: <i>iPhone 13 128gb</i>, <i>PlayStation 5 Slim</i>, <i>MacBook Air M2</i>.",
        reply_markup=cancel_kb(),
    )


@router.message(F.text == MENU_SEARCH)
async def menu_search_message(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await start_search(message, state, session, user, is_admin, settings)


@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await start_search(message, state, session, user, is_admin, settings)


@router.callback_query(F.data == "menu:search")
async def menu_search_callback(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await callback.answer()
    await start_search(callback.message, state, session, user, is_admin, settings)


@router.callback_query(F.data == "search:cancel")
async def callback_search_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(f"{em('cross')} Поиск отменён.", reply_markup=back_kb())
    await callback.answer()


@router.callback_query(F.data == "search:restart")
async def callback_search_restart(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateSearch.query)
    await callback.message.edit_text(f"{em('eye')} <b>Что ищем?</b>\n\nНапишите товар как можно точнее.", reply_markup=cancel_kb())
    await callback.answer()


@router.message(CreateSearch.query, F.text)
async def process_query_input(message: Message, state: FSMContext) -> None:
    query = " ".join(message.text.split())
    if len(query) < 2 or len(query) > 120:
        await message.answer("Слишком коротко или длинно. Напишите название товара от 2 до 120 символов.", reply_markup=cancel_kb())
        return
    await state.update_data(query=query)
    await state.set_state(CreateSearch.location)
    await message.answer(
        f"{em('pin')} <b>Где искать?</b>\n\nВыберите город из списка или введите свой.",
        reply_markup=locations_kb(list(POPULAR_LOCATIONS)),
    )


async def _ask_price(message: Message, state: FSMContext, location: Location, edit: bool = False) -> None:
    await state.update_data(location_name=location.name, location_id=location.id, location_slug=location.web_slug, found_locations=None)
    await state.set_state(CreateSearch.price)
    text = (
        f"{em('money')} <b>Какой бюджет?</b>\n\n"
        f"Город: {h(location.name)}\n\n"
        f"Напишите цену: <i>до 40000</i>, <i>30-45 тыс</i>, <i>от 20000</i> или просто <i>35000</i> — тогда возьму диапазон около этой суммы."
    )
    if edit:
        await message.edit_text(text, reply_markup=price_kb())
    else:
        await message.answer(text, reply_markup=price_kb())


@router.callback_query(CreateSearch.location, F.data.startswith("search:loc:"))
async def callback_pick_location(callback: CallbackQuery, state: FSMContext, search_service: SearchService) -> None:
    index = int(callback.data.split(":")[-1])
    if index >= len(POPULAR_LOCATIONS):
        await callback.answer("Не нашёл такой город", show_alert=True)
        return
    location = POPULAR_LOCATIONS[index]
    if location.id is None:
        try:
            location = await search_service.avito.resolve_location(location)
        except Exception as exc:
            logger.info("location resolve failed: %s", exc)
    await callback.answer()
    await _ask_price(callback.message, state, location, edit=True)


@router.callback_query(F.data == "search:loc_manual")
async def callback_location_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateSearch.location_manual)
    await callback.message.edit_text(f"{em('write')} Напишите название города или региона.", reply_markup=cancel_kb())
    await callback.answer()


@router.message(CreateSearch.location_manual, F.text)
async def process_location_input(message: Message, state: FSMContext, search_service: SearchService) -> None:
    query = message.text.strip()
    if len(query) < 2:
        await message.answer("Слишком коротко. Напишите название города.", reply_markup=cancel_kb())
        return
    waiting = await message.answer(f"{em('loading')} Ищу город…")
    try:
        locations = await search_service.avito.find_locations(query)
    except Exception as exc:
        logger.info("find_locations failed: %s", exc)
        locations = []
    if not locations:
        locations = [Location(name=query.title())]
        await waiting.edit_text(
            f"{em('info')} Не нашёл «{h(query)}» в справочнике Авито. Можно продолжить с этим названием — поиск пойдёт по веб-версии.",
            reply_markup=found_locations_kb(locations),
        )
    else:
        await waiting.edit_text(f"{em('pin')} Выберите подходящий вариант:", reply_markup=found_locations_kb(locations))
    await state.update_data(found_locations=[asdict(loc) for loc in locations])


@router.callback_query(CreateSearch.location_manual, F.data.startswith("search:found_loc:"))
async def callback_pick_found_location(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    found = data.get("found_locations") or []
    index = int(callback.data.split(":")[-1])
    if index >= len(found):
        await callback.answer("Список устарел, введите город заново", show_alert=True)
        return
    location = Location(**found[index])
    await callback.answer()
    await _ask_price(callback.message, state, location, edit=True)


async def _ask_wishes(message: Message, state: FSMContext, edit: bool = False) -> None:
    await state.set_state(CreateSearch.wishes)
    text = (
        f"{em('write')} <b>Пожелания к товару</b>\n\n"
        f"Например: <i>без ремонта, АКБ выше 85%, полный комплект, только Face ID рабочий</i>.\n"
        f"ИИ учтёт это при оценке. Можно пропустить."
    )
    if edit:
        await message.edit_text(text, reply_markup=wishes_kb())
    else:
        await message.answer(text, reply_markup=wishes_kb())


@router.message(CreateSearch.price, F.text)
async def process_price_input(message: Message, state: FSMContext) -> None:
    parsed = parse_price_range(message.text)
    if parsed is None:
        await message.answer("Не понял цену. Напишите, например, <i>до 40000</i> или <i>30-45 тыс</i>.", reply_markup=price_kb())
        return
    price_min, price_max = parsed
    await state.update_data(price_min=price_min, price_max=price_max)
    await message.answer(f"{em('check')} Бюджет: {format_price_range(price_min, price_max)}")
    await _ask_wishes(message, state)


@router.callback_query(CreateSearch.price, F.data == "search:price_any")
async def callback_price_any(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(price_min=None, price_max=None)
    await callback.answer()
    await _ask_wishes(callback.message, state, edit=True)


async def _show_summary(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService, edit: bool = False) -> None:
    data = await state.get_data()
    access = await load_access(session, user, is_admin, settings)
    await state.set_state(CreateSearch.confirm)
    text = search_summary_text(data["query"], data["location_name"], data.get("price_min"), data.get("price_max"), data.get("wishes"))
    if edit:
        await message.edit_text(text, reply_markup=confirm_search_kb(access.can_monitor))
    else:
        await message.answer(text, reply_markup=confirm_search_kb(access.can_monitor))


@router.message(CreateSearch.wishes, F.text)
async def process_wishes_input(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    wishes = message.text.strip()[:300]
    await state.update_data(wishes=wishes)
    await _show_summary(message, state, session, user, is_admin, settings)


@router.callback_query(CreateSearch.wishes, F.data == "search:wishes_skip")
async def callback_wishes_skip(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.update_data(wishes=None)
    await callback.answer()
    await _show_summary(callback.message, state, session, user, is_admin, settings, edit=True)


@router.callback_query(F.data == "search:need_sub")
async def callback_need_subscription(callback: CallbackQuery) -> None:
    await callback.answer("Мониторинг доступен по подписке. Откройте раздел «Подписка».", show_alert=True)


async def create_task_from_data(session: AsyncSession, user: User, access: AccessInfo, data: dict[str, Any], settings: SettingsService) -> SearchTask | None:
    active = await count_user_tasks(session, user.id)
    if active >= access.max_tasks:
        return None
    task = SearchTask(
        user_id=user.id,
        query=data["query"],
        location_id=data.get("location_id"),
        location_name=data["location_name"],
        location_slug=data.get("location_slug"),
        price_min=data.get("price_min"),
        price_max=data.get("price_max"),
        wishes=data.get("wishes"),
        min_rating=settings.values.default_min_rating,
        check_interval=access.check_interval,
    )
    session.add(task)
    await session.commit()
    return task


async def send_rated_listing(message: Message, rated: RatedListing, request_hash: str, index: int) -> None:
    caption = listing_card(rated.listing, rated.evaluation, index)
    keyboard = listing_kb(rated.listing, request_hash)
    if rated.listing.cover:
        try:
            await message.answer_photo(rated.listing.cover, caption=caption, reply_markup=keyboard)
            return
        except Exception as exc:
            logger.info("photo send failed for %s: %s", rated.listing.id, exc)
    await message.answer(caption, reply_markup=keyboard, disable_web_page_preview=True)


@router.callback_query(CreateSearch.confirm, F.data.in_({"search:run", "search:run_monitor"}))
async def callback_run_search(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    is_admin: bool,
    settings: SettingsService,
    search_service: SearchService,
    config: Config,
) -> None:
    data = await state.get_data()
    access = await load_access(session, user, is_admin, settings)
    if not access.can_search:
        await callback.answer("Бесплатные поиски закончились — нужна подписка", show_alert=True)
        return
    with_monitor = callback.data == "search:run_monitor" and access.can_monitor
    await callback.answer()
    await state.set_state(None)
    request = _request_from_data(data)
    location = _location_from_data(data)
    progress = await callback.message.edit_text(f"{em('loading')} Ищу объявления на Авито…")
    last_text = ""

    async def on_progress(text: str) -> None:
        nonlocal last_text
        if text == last_text:
            return
        last_text = text
        try:
            await progress.edit_text(f"{em('loading')} {text}")
        except Exception:
            pass

    try:
        rated, total = await search_service.search_and_rate(request, location, on_progress)
    except AvitoBlockedError:
        await progress.edit_text(
            f"{em('cross')} Авито временно ограничил доступ парсера. Попробуйте через 10–15 минут.",
            reply_markup=back_kb(),
        )
        return
    except AvitoError as exc:
        logger.warning("search failed: %s", exc)
        details = f"\n\n<code>{h(str(exc)[:300])}</code>\n{em('info')} Проверьте прокси и статус браузера в админке → «Парсер и ИИ»." if is_admin else ""
        await progress.edit_text(f"{em('cross')} Не удалось получить объявления с Авито. Попробуйте позже.{details}", reply_markup=back_kb())
        return
    except Exception:
        logger.exception("unexpected search failure")
        await progress.edit_text(f"{em('cross')} Что-то пошло не так. Попробуйте ещё раз.", reply_markup=back_kb())
        return

    if not access.has_subscription and not access.is_admin:
        user.free_searches_used += 1
    user.searches_count += 1
    await session.commit()

    if not rated:
        await progress.edit_text(
            f"{em('info')} По запросу «{h(request.query)}» в городе {h(location.name)} ничего не нашлось. Попробуйте изменить формулировку или бюджет.",
            reply_markup=results_footer_kb(access.can_monitor),
        )
        await state.set_data({"last_search": data})
        return

    good = [item for item in rated if item.rating >= 5][:MAX_RESULTS_TO_SEND] or rated[:3]
    await progress.edit_text(
        f"{em('check')} Проверено {total} объявлений, оценено {len(rated)}. Показываю лучшие {len(good)} по оценке ИИ:"
    )
    for index, item in enumerate(good, start=1):
        await send_rated_listing(callback.message, item, request.fingerprint, index)

    footer = f"{em('stats')} Готово. Лучшая оценка: <b>{good[0].rating:g}/10</b>."
    created_task: SearchTask | None = None
    if with_monitor:
        created_task = await create_task_from_data(session, user, access, data, settings)
        if created_task:
            footer += f"\n{em('bell')} Мониторинг включён: новые объявления с оценкой от {created_task.min_rating} будут приходить автоматически."
        else:
            footer += f"\n{em('lock')} Лимит мониторингов ({access.max_tasks}) исчерпан — удалите старый, чтобы добавить новый."
    await callback.message.answer(footer, reply_markup=results_footer_kb(access.can_monitor and created_task is None))
    await state.set_data({"last_search": data})


@router.callback_query(F.data == "search:monitor_last")
async def callback_monitor_last(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    data = (await state.get_data()).get("last_search")
    if not data:
        await callback.answer("Запрос устарел, выполните поиск заново", show_alert=True)
        return
    access = await load_access(session, user, is_admin, settings)
    if not access.can_monitor:
        await callback.answer("Мониторинг доступен по подписке", show_alert=True)
        return
    task = await create_task_from_data(session, user, access, data, settings)
    if task is None:
        await callback.answer(f"Лимит мониторингов: {access.max_tasks}. Удалите старый.", show_alert=True)
        return
    await callback.answer("Мониторинг включён")
    await callback.message.answer(
        f"{em('bell')} <b>Мониторинг включён</b>\n\n«{h(task.query)}» · {h(task.location_name)} · {format_price_range(task.price_min, task.price_max)}\n"
        f"Новые объявления с оценкой от {task.min_rating}/10 будут приходить автоматически.",
        reply_markup=back_kb("menu:tasks"),
    )
