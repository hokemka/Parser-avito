from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tgbot.database.models import SearchTask, Tariff
from tgbot.services.avito import Listing, Location
from tgbot.services.settings import RuntimeSettings
from tgbot.utils.emoji import BACK_TEXT, icon

MENU_SEARCH = "Найти выгодное"
MENU_TASKS = "Мои мониторинги"
MENU_PROFILE = "Профиль"
MENU_SUBSCRIPTION = "Подписка"
MENU_HELP = "Помощь"
MENU_ADMIN = "Админ-панель"


BLUE = "primary"
GREEN = "success"
RED = "danger"


def _btn(text: str, callback: str | None = None, url: str | None = None, emoji: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback, url=url, icon_custom_emoji_id=icon(emoji) if emoji else None, style=style)


def _reply_btn(text: str, emoji: str | None = None, style: str | None = None) -> KeyboardButton:
    return KeyboardButton(text=text, icon_custom_emoji_id=icon(emoji) if emoji else None, style=style)


def main_menu_reply(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [_reply_btn(MENU_SEARCH, "eye", BLUE)],
        [_reply_btn(MENU_TASKS, "bell"), _reply_btn(MENU_PROFILE, "profile")],
        [_reply_btn(MENU_SUBSCRIPTION, "star", GREEN), _reply_btn(MENU_HELP, "info")],
    ]
    if is_admin:
        rows.append([_reply_btn(MENU_ADMIN, "settings", RED)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, input_field_placeholder="Выберите раздел")


def main_menu_inline(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_btn("Найти выгодное", "menu:search", emoji="eye", style=BLUE)],
        [_btn("Мои мониторинги", "menu:tasks", emoji="bell"), _btn("Профиль", "menu:profile", emoji="profile")],
        [_btn("Подписка", "menu:subscription", emoji="star", style=GREEN), _btn("Помощь", "menu:help", emoji="info")],
    ]
    if is_admin:
        rows.append([_btn("Админ-панель", "admin:menu", emoji="settings", style=RED)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb(callback: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(BACK_TEXT, callback)]])


def cancel_kb(callback: str = "search:cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("Отмена", callback, emoji="cross", style=RED)]])


def locations_kb(locations: list[Location]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, location in enumerate(locations):
        builder.button(text=location.name, callback_data=f"search:loc:{index}", icon_custom_emoji_id=icon("pin"))
    builder.adjust(2)
    builder.row(_btn("Ввести другой город", "search:loc_manual", emoji="write"))
    builder.row(_btn("Отмена", "search:cancel", emoji="cross", style=RED))
    return builder.as_markup()


def found_locations_kb(locations: list[Location]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, location in enumerate(locations):
        builder.button(text=location.full_name, callback_data=f"search:found_loc:{index}", icon_custom_emoji_id=icon("pin"))
    builder.adjust(1)
    builder.row(_btn("Ввести заново", "search:loc_manual", emoji="write"), _btn("Отмена", "search:cancel", emoji="cross", style=RED))
    return builder.as_markup()


def price_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Любая цена", "search:price_any", emoji="money")],
        [_btn("Отмена", "search:cancel", emoji="cross", style=RED)],
    ])


def wishes_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Пропустить", "search:wishes_skip", emoji="down")],
        [_btn("Отмена", "search:cancel", emoji="cross", style=RED)],
    ])


def confirm_search_kb(can_monitor: bool) -> InlineKeyboardMarkup:
    rows = [[_btn("Найти сейчас", "search:run", emoji="eye", style=GREEN)]]
    if can_monitor:
        rows.append([_btn("Найти и включить мониторинг", "search:run_monitor", emoji="bell", style=BLUE)])
    else:
        rows.append([_btn("Мониторинг (нужна подписка)", "search:need_sub", emoji="lock")])
    rows.append([_btn("Изменить запрос", "search:restart", emoji="edit"), _btn("Отмена", "search:cancel", emoji="cross", style=RED)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def listing_kb(listing: Listing, request_hash: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Открыть на Авито", url=listing.url, emoji="link", style=BLUE)],
        [_btn("Подробный разбор", f"det:{listing.id}:{request_hash}", emoji="eye")],
    ])


def results_footer_kb(can_monitor: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_monitor:
        rows.append([_btn("Включить мониторинг по этому запросу", "search:monitor_last", emoji="bell", style=BLUE)])
    else:
        rows.append([_btn("Мониторинг — оформить подписку", "menu:subscription", emoji="star", style=GREEN)])
    rows.append([_btn("Новый поиск", "menu:search", emoji="eye"), _btn("В меню", "menu:main", emoji="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_list_kb(tasks: list[SearchTask], can_add: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for task in tasks:
        status = "green" if task.is_active else "red"
        builder.button(text=f"{task.query[:28]} · {task.location_name[:14]}", callback_data=f"task:{task.id}", icon_custom_emoji_id=icon(status))
    builder.adjust(1)
    if can_add:
        builder.row(_btn("Добавить мониторинг", "menu:search", emoji="bell", style=BLUE))
    builder.row(_btn(BACK_TEXT, "menu:main"))
    return builder.as_markup()


def task_kb(task: SearchTask) -> InlineKeyboardMarkup:
    toggle = _btn("Поставить на паузу", f"task_toggle:{task.id}", emoji="lock") if task.is_active else _btn("Возобновить", f"task_toggle:{task.id}", emoji="unlock", style=GREEN)
    return InlineKeyboardMarkup(inline_keyboard=[
        [toggle],
        [_btn("Мин. оценка", f"task_rating:{task.id}", emoji="stats"), _btn("Удалить", f"task_del:{task.id}", emoji="trash", style=RED)],
        [_btn(BACK_TEXT, "menu:tasks")],
    ])


def task_delete_confirm_kb(task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Да, удалить", f"task_del_yes:{task_id}", emoji="trash", style=RED), _btn("Нет", f"task:{task_id}", emoji="cross")],
    ])


def min_rating_kb(task_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in (5, 6, 7, 8, 9):
        builder.button(text=f"от {value}", callback_data=f"task_rating_set:{task_id}:{value}")
    builder.adjust(5)
    builder.row(_btn(BACK_TEXT, f"task:{task_id}"))
    return builder.as_markup()


def profile_kb(has_subscription: bool) -> InlineKeyboardMarkup:
    rows = [
        [_btn("Продлить подписку" if has_subscription else "Оформить подписку", "menu:subscription", emoji="star", style=GREEN)],
        [_btn("Пополнить баланс", "topup:start", emoji="wallet", style=BLUE), _btn("Мои мониторинги", "menu:tasks", emoji="bell")],
        [_btn(BACK_TEXT, "menu:main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tariffs_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(text=f"{tariff.name} — {tariff.price_rub} ₽ / {tariff.days} дн", callback_data=f"tariff:{tariff.id}", icon_custom_emoji_id=icon("tag"))
    builder.adjust(1)
    builder.row(_btn(BACK_TEXT, "menu:main"))
    return builder.as_markup()


def pay_methods_kb(tariff: Tariff, settings: RuntimeSettings, balance: float) -> InlineKeyboardMarkup:
    rows = []
    if balance >= tariff.price_rub:
        rows.append([_btn(f"Оплатить с баланса ({tariff.price_rub} ₽)", f"pay:{tariff.id}:balance", emoji="wallet", style=GREEN)])
    if settings.stars_enabled:
        rows.append([_btn(f"Telegram Stars ({tariff.price_stars} ⭐)", f"pay:{tariff.id}:stars", emoji="star", style=GREEN)])
    if settings.cryptobot_enabled and settings.cryptobot_token:
        rows.append([_btn("CryptoBot (крипта)", f"pay:{tariff.id}:cryptobot", emoji="cryptobot", style=GREEN)])
    if not rows:
        rows.append([_btn("Оплата временно недоступна", "menu:subscription", emoji="lock")])
    rows.append([_btn(BACK_TEXT, "menu:subscription")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topup_methods_kb(amount: int, settings: RuntimeSettings, stars_amount: int) -> InlineKeyboardMarkup:
    rows = []
    if settings.stars_enabled:
        rows.append([_btn(f"Telegram Stars ({stars_amount} ⭐)", f"topup:pay:{amount}:stars", emoji="star", style=GREEN)])
    if settings.cryptobot_enabled and settings.cryptobot_token:
        rows.append([_btn("CryptoBot (крипта)", f"topup:pay:{amount}:cryptobot", emoji="cryptobot", style=GREEN)])
    if not rows:
        rows.append([_btn("Оплата временно недоступна", "menu:profile", emoji="lock")])
    rows.append([_btn(BACK_TEXT, "menu:profile")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_invoice_kb(invoice_url: str, payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Оплатить через CryptoBot", url=invoice_url, emoji="cryptobot", style=GREEN)],
        [_btn("Проверить оплату", f"check_pay:{payment_id}", emoji="loading", style=BLUE)],
        [_btn("Отменить", f"cancel_pay:{payment_id}", emoji="cross", style=RED)],
    ])


def help_kb(support_username: str) -> InlineKeyboardMarkup:
    rows = []
    if support_username:
        rows.append([_btn("Написать в поддержку", url=f"https://t.me/{support_username}", emoji="write", style=BLUE)])
    rows.append([_btn(BACK_TEXT, "menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
