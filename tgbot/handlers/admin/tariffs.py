from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import Tariff
from tgbot.fsm.states import AdminTariff
from tgbot.keyboards.admin import admin_back_kb, admin_cancel_kb, tariff_card_kb, tariff_delete_kb, tariff_wizard_kb, tariffs_admin_kb
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import create_tariff, delete_tariff, get_tariff, list_tariffs, rub_to_stars
from tgbot.utils.emoji import em
from tgbot.utils.filters import IsAdmin
from tgbot.utils.text import h, parse_int, pluralize

router = Router(name="admin_tariffs")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

FIELD_LABELS = {
    "name": "название", "description": "описание", "days": "срок в днях", "price_rub": "цену в рублях",
    "price_stars": "цену в Stars", "max_tasks": "лимит мониторингов", "check_interval": "интервал проверки в секундах",
    "sort_order": "порядок сортировки",
}
INT_FIELDS = {"days": (1, 3650), "price_rub": (0, 10_000_000), "price_stars": (1, 1_000_000), "max_tasks": (1, 500), "check_interval": (30, 86400), "sort_order": (0, 1000)}


def tariff_admin_text(tariff: Tariff) -> str:
    return (
        f"{em('tag')} <b>{h(tariff.name)}</b> {'(активен)' if tariff.is_active else '(скрыт)'}\n\n"
        f"{em('font')} {h(tariff.description) or 'без описания'}\n"
        f"{em('calendar')} Срок: {pluralize(tariff.days, 'день', 'дня', 'дней')}\n"
        f"{em('money')} Цена: {tariff.price_rub} ₽ · {tariff.price_stars} ⭐\n"
        f"{em('bell')} Мониторингов: {tariff.max_tasks}\n"
        f"{em('clock')} Интервал: {tariff.check_interval} сек\n"
        f"{em('down')} Порядок: {tariff.sort_order}"
    )


async def _tariffs_list(session: AsyncSession) -> tuple[str, object]:
    tariffs = await list_tariffs(session, only_active=False)
    text = f"{em('tag')} <b>Тарифы</b>\n\nЗелёный — виден пользователям, красный — скрыт."
    return text, tariffs_admin_kb(tariffs)


@router.callback_query(F.data == "admin:tariffs")
async def callback_tariffs(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    text, keyboard = await _tariffs_list(session)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("tariffs:card:"))
async def callback_tariff_card(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    tariff = await get_tariff(session, int(callback.data.split(":")[-1]))
    if tariff is None:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    await callback.message.edit_text(tariff_admin_text(tariff), reply_markup=tariff_card_kb(tariff))
    await callback.answer()


@router.callback_query(F.data.startswith("tariffs:toggle:"))
async def callback_tariff_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    tariff = await get_tariff(session, int(callback.data.split(":")[-1]))
    if tariff is None:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    tariff.is_active = not tariff.is_active
    await session.commit()
    await callback.message.edit_text(tariff_admin_text(tariff), reply_markup=tariff_card_kb(tariff))
    await callback.answer("Показан" if tariff.is_active else "Скрыт")


@router.callback_query(F.data.startswith("tariffs:delete_yes:"))
async def callback_tariff_delete_confirmed(callback: CallbackQuery, session: AsyncSession) -> None:
    tariff = await get_tariff(session, int(callback.data.split(":")[-1]))
    if tariff is not None:
        await delete_tariff(session, tariff)
    text, keyboard = await _tariffs_list(session)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Удалён")


@router.callback_query(F.data.startswith("tariffs:delete:"))
async def callback_tariff_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    tariff = await get_tariff(session, int(callback.data.split(":")[-1]))
    if tariff is None:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    await callback.message.edit_text(f"{em('trash')} Удалить тариф «{h(tariff.name)}»? Активные подписки продолжат работать до конца срока.", reply_markup=tariff_delete_kb(tariff.id))
    await callback.answer()


@router.callback_query(F.data.startswith("tariffs:edit:"))
async def callback_tariff_edit(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, _, tariff_id, field = callback.data.split(":")
    tariff = await get_tariff(session, int(tariff_id))
    if tariff is None or field not in FIELD_LABELS:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    await state.set_state(AdminTariff.edit_value)
    await state.update_data(tariff_id=tariff.id, field=field)
    await callback.message.edit_text(
        f"{em('edit')} Введите {FIELD_LABELS[field]} для тарифа «{h(tariff.name)}».\nСейчас: <code>{h(str(getattr(tariff, field)))}</code>",
        reply_markup=admin_cancel_kb(f"tariffs:card:{tariff.id}"),
    )
    await callback.answer()


@router.message(AdminTariff.edit_value, F.text)
async def process_tariff_edit(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    tariff = await get_tariff(session, data.get("tariff_id", 0))
    field = data.get("field")
    if tariff is None or field not in FIELD_LABELS:
        await state.clear()
        await message.answer("Тариф не найден.", reply_markup=admin_back_kb("admin:tariffs"))
        return
    raw = message.text.strip()
    if field in INT_FIELDS:
        low, high = INT_FIELDS[field]
        value = parse_int(raw)
        if value is None or not low <= value <= high:
            await message.answer(f"Нужно целое число от {low} до {high}.", reply_markup=admin_cancel_kb(f"tariffs:card:{tariff.id}"))
            return
        setattr(tariff, field, value)
    else:
        if field == "name" and not 1 <= len(raw) <= 64:
            await message.answer("Название от 1 до 64 символов.", reply_markup=admin_cancel_kb(f"tariffs:card:{tariff.id}"))
            return
        setattr(tariff, field, raw[:500])
    await session.commit()
    await state.clear()
    await message.answer(f"{em('check')} Сохранено.")
    await message.answer(tariff_admin_text(tariff), reply_markup=tariff_card_kb(tariff))


@router.callback_query(F.data == "tariffs:add")
async def callback_tariff_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(AdminTariff.name)
    await callback.message.edit_text(f"{em('gift')} <b>Новый тариф</b>\n\nШаг 1/7. Введите название.", reply_markup=tariff_wizard_kb())
    await callback.answer()


@router.message(AdminTariff.name, F.text)
async def process_new_tariff_name(message: Message, state: FSMContext) -> None:
    name = message.text.strip()
    if not 1 <= len(name) <= 64:
        await message.answer("Название от 1 до 64 символов.", reply_markup=tariff_wizard_kb())
        return
    await state.update_data(name=name)
    await state.set_state(AdminTariff.description)
    await message.answer("Шаг 2/7. Введите описание (что входит в тариф) или пропустите.", reply_markup=tariff_wizard_kb("tariffs:skip_description"))


@router.callback_query(AdminTariff.description, F.data == "tariffs:skip_description")
async def callback_skip_description(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(description="")
    await state.set_state(AdminTariff.days)
    await callback.message.edit_text("Шаг 3/7. Срок подписки в днях.", reply_markup=tariff_wizard_kb())
    await callback.answer()


@router.message(AdminTariff.description, F.text)
async def process_new_tariff_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text.strip()[:500])
    await state.set_state(AdminTariff.days)
    await message.answer("Шаг 3/7. Срок подписки в днях.", reply_markup=tariff_wizard_kb())


async def _ask_int_step(message: Message, state: FSMContext, field: str, value_raw: str, next_state, next_prompt: str, skip_callback: str | None = None) -> None:
    low, high = INT_FIELDS[field]
    value = parse_int(value_raw)
    if value is None or not low <= value <= high:
        await message.answer(f"Нужно целое число от {low} до {high}.", reply_markup=tariff_wizard_kb())
        return
    await state.update_data(**{field: value})
    await state.set_state(next_state)
    await message.answer(next_prompt, reply_markup=tariff_wizard_kb(skip_callback))


@router.message(AdminTariff.days, F.text)
async def process_new_tariff_days(message: Message, state: FSMContext) -> None:
    await _ask_int_step(message, state, "days", message.text, AdminTariff.price_rub, "Шаг 4/7. Цена в рублях.")


@router.message(AdminTariff.price_rub, F.text)
async def process_new_tariff_price(message: Message, state: FSMContext, settings: SettingsService) -> None:
    value = parse_int(message.text)
    low, high = INT_FIELDS["price_rub"]
    if value is None or not low <= value <= high:
        await message.answer(f"Нужно целое число от {low} до {high}.", reply_markup=tariff_wizard_kb())
        return
    await state.update_data(price_rub=value)
    await state.set_state(AdminTariff.price_stars)
    auto = rub_to_stars(value, settings.values.stars_rate)
    await message.answer(f"Шаг 5/7. Цена в Telegram Stars. По курсу выходит {auto} ⭐ — можно пропустить, чтобы взять это значение.", reply_markup=tariff_wizard_kb("tariffs:skip_stars"))


@router.callback_query(AdminTariff.price_stars, F.data == "tariffs:skip_stars")
async def callback_skip_stars(callback: CallbackQuery, state: FSMContext, settings: SettingsService) -> None:
    data = await state.get_data()
    await state.update_data(price_stars=rub_to_stars(int(data["price_rub"]), settings.values.stars_rate))
    await state.set_state(AdminTariff.max_tasks)
    await callback.message.edit_text("Шаг 6/7. Сколько мониторингов разрешено одновременно?", reply_markup=tariff_wizard_kb())
    await callback.answer()


@router.message(AdminTariff.price_stars, F.text)
async def process_new_tariff_stars(message: Message, state: FSMContext) -> None:
    await _ask_int_step(message, state, "price_stars", message.text, AdminTariff.max_tasks, "Шаг 6/7. Сколько мониторингов разрешено одновременно?")


@router.message(AdminTariff.max_tasks, F.text)
async def process_new_tariff_tasks(message: Message, state: FSMContext) -> None:
    await _ask_int_step(message, state, "max_tasks", message.text, AdminTariff.check_interval, "Шаг 7/7. Интервал проверки новых объявлений в секундах (например, 300).")


@router.message(AdminTariff.check_interval, F.text)
async def process_new_tariff_interval(message: Message, state: FSMContext, session: AsyncSession) -> None:
    low, high = INT_FIELDS["check_interval"]
    value = parse_int(message.text)
    if value is None or not low <= value <= high:
        await message.answer(f"Нужно целое число от {low} до {high}.", reply_markup=tariff_wizard_kb())
        return
    data = await state.get_data()
    await state.clear()
    existing = await list_tariffs(session, only_active=False)
    tariff = await create_tariff(
        session,
        name=data["name"], description=data.get("description", ""), days=int(data["days"]), price_rub=int(data["price_rub"]),
        price_stars=int(data["price_stars"]), max_tasks=int(data["max_tasks"]), check_interval=value, sort_order=len(existing) + 1,
    )
    await message.answer(f"{em('party')} Тариф создан.")
    await message.answer(tariff_admin_text(tariff), reply_markup=tariff_card_kb(tariff))
