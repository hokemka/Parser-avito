from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from tgbot.database.models import Tariff, User
from tgbot.services.settings import RuntimeSettings
from tgbot.utils.emoji import BACK_TEXT, icon


BLUE = "primary"
GREEN = "success"
RED = "danger"


def _btn(text: str, callback: str | None = None, url: str | None = None, emoji: str | None = None, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback, url=url, icon_custom_emoji_id=icon(emoji) if emoji else None, style=style)


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Статистика", "admin:stats", emoji="stats", style=BLUE), _btn("Рассылка", "admin:broadcast", emoji="megaphone", style=BLUE)],
        [_btn("Пользователи", "admin:users", emoji="users"), _btn("Тарифы", "admin:tariffs", emoji="tag")],
        [_btn("Оплата", "admin:payments", emoji="wallet"), _btn("Настройки", "admin:settings", emoji="settings")],
        [_btn("Парсер и ИИ", "admin:parser", emoji="bot")],
        [_btn("В меню", "menu:main", emoji="home")],
    ])


def admin_back_kb(callback: str = "admin:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(BACK_TEXT, callback)]])


def admin_cancel_kb(callback: str = "admin:menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("Отмена", callback, emoji="cross", style=RED)]])


def broadcast_buttons_kb(has_buttons: bool) -> InlineKeyboardMarkup:
    rows = [[_btn("Добавить кнопку", "bc:add_button", emoji="link", style=BLUE)]]
    if has_buttons:
        rows.append([_btn("Удалить последнюю", "bc:remove_button", emoji="trash", style=RED)])
    rows.append([_btn("Продолжить" if has_buttons else "Нет, продолжить", "bc:continue", emoji="check", style=GREEN)])
    rows.append([_btn("Отмена", "bc:cancel", emoji="cross", style=RED)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Разослать", "bc:send", emoji="megaphone", style=GREEN), _btn("Нет", "bc:cancel", emoji="cross", style=RED)],
    ])


def broadcast_links_kb(buttons: list[dict[str, str]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button["title"], url=button["url"])] for button in buttons])


def users_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Поиск пользователя", "users:search", emoji="eye", style=BLUE)],
        [_btn("Экспорт (txt)", "users:export", emoji="download")],
        [_btn(BACK_TEXT, "admin:menu")],
    ])


def user_card_kb(user: User, has_subscription: bool) -> InlineKeyboardMarkup:
    ban = _btn("Разблокировать", f"users:unban:{user.id}", emoji="unlock", style=GREEN) if user.is_banned else _btn("Забанить", f"users:ban:{user.id}", emoji="lock", style=RED)
    rows = [
        [_btn("Изменить баланс", f"users:balance:{user.id}", emoji="wallet", style=BLUE), ban],
        [_btn("Выдать подписку", f"users:grant:{user.id}", emoji="gift", style=GREEN)],
    ]
    if has_subscription:
        rows.append([_btn("Снять подписку", f"users:revoke:{user.id}", emoji="cross", style=RED)])
    rows.append([_btn("Найти другого", "users:search", emoji="eye"), _btn(BACK_TEXT, "admin:users")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def grant_tariff_kb(user_id: int, tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(text=f"{tariff.name} ({tariff.days} дн)", callback_data=f"users:grant_do:{user_id}:{tariff.id}", icon_custom_emoji_id=icon("tag"))
    builder.adjust(1)
    builder.row(_btn(BACK_TEXT, f"users:card:{user_id}"))
    return builder.as_markup()


def tariffs_admin_kb(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for tariff in tariffs:
        builder.button(
            text=f"{tariff.name} · {tariff.price_rub} ₽ · {tariff.days} дн",
            callback_data=f"tariffs:card:{tariff.id}",
            icon_custom_emoji_id=icon("green" if tariff.is_active else "red"),
        )
    builder.adjust(1)
    builder.row(_btn("Добавить тариф", "tariffs:add", emoji="gift", style=GREEN))
    builder.row(_btn(BACK_TEXT, "admin:menu"))
    return builder.as_markup()


def tariff_card_kb(tariff: Tariff) -> InlineKeyboardMarkup:
    toggle = _btn("Скрыть", f"tariffs:toggle:{tariff.id}", emoji="hidden") if tariff.is_active else _btn("Показать", f"tariffs:toggle:{tariff.id}", emoji="eye", style=GREEN)
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Название", f"tariffs:edit:{tariff.id}:name", emoji="edit"), _btn("Описание", f"tariffs:edit:{tariff.id}:description", emoji="font")],
        [_btn("Дней", f"tariffs:edit:{tariff.id}:days", emoji="calendar"), _btn("Цена ₽", f"tariffs:edit:{tariff.id}:price_rub", emoji="money")],
        [_btn("Цена ⭐", f"tariffs:edit:{tariff.id}:price_stars", emoji="star"), _btn("Лимит мониторингов", f"tariffs:edit:{tariff.id}:max_tasks", emoji="bell")],
        [_btn("Интервал (сек)", f"tariffs:edit:{tariff.id}:check_interval", emoji="clock"), _btn("Порядок", f"tariffs:edit:{tariff.id}:sort_order", emoji="down")],
        [toggle, _btn("Удалить", f"tariffs:delete:{tariff.id}", emoji="trash", style=RED)],
        [_btn(BACK_TEXT, "admin:tariffs")],
    ])


def tariff_delete_kb(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Да, удалить", f"tariffs:delete_yes:{tariff_id}", emoji="trash", style=RED), _btn("Нет", f"tariffs:card:{tariff_id}", emoji="cross")],
    ])


def tariff_wizard_kb(skip_callback: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if skip_callback:
        rows.append([_btn("Пропустить", skip_callback, emoji="down")])
    rows.append([_btn("Отмена", "admin:tariffs", emoji="cross", style=RED)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _flag(value: bool) -> str:
    return "включено" if value else "выключено"


def _flag_style(value: bool) -> str:
    return GREEN if value else RED


def payment_settings_kb(settings: RuntimeSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"Stars: {_flag(settings.stars_enabled)}", "settings:toggle:stars_enabled", emoji="star", style=_flag_style(settings.stars_enabled))],
        [_btn(f"CryptoBot: {_flag(settings.cryptobot_enabled)}", "settings:toggle:cryptobot_enabled", emoji="cryptobot", style=_flag_style(settings.cryptobot_enabled))],
        [_btn("Токен CryptoBot", "settings:edit:cryptobot_token", emoji="code"), _btn("Сеть CryptoBot", "settings:toggle_network", emoji="cryptobot")],
        [_btn("Курс ⭐ (₽ за звезду)", "settings:edit:stars_rate", emoji="star"), _btn("Курс USD", "settings:edit:usd_rate", emoji="money")],
        [_btn("Проверить CryptoBot", "settings:check_cryptobot", emoji="verify", style=BLUE)],
        [_btn(BACK_TEXT, "admin:menu")],
    ])


def general_settings_kb(settings: RuntimeSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Бесплатных поисков", "settings:edit:free_searches", emoji="gift"), _btn("Мин. оценка по умолч.", "settings:edit:default_min_rating", emoji="stats")],
        [_btn("Интервал без подписки", "settings:edit:default_check_interval", emoji="clock")],
        [_btn(f"Премиум-эмодзи: {_flag(settings.premium_emoji)}", "settings:toggle:premium_emoji", emoji="brush", style=_flag_style(settings.premium_emoji))],
        [_btn(BACK_TEXT, "admin:menu")],
    ])


def parser_settings_kb(settings: RuntimeSettings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("Модель ИИ", "settings:edit:ai_model", emoji="bot"), _btn(f"Фото в ИИ: {_flag(settings.ai_analyze_images)}", "settings:toggle:ai_analyze_images", emoji="photo", style=_flag_style(settings.ai_analyze_images))],
        [_btn("Кандидатов для ИИ", "settings:edit:ai_candidates_per_search", emoji="stats"), _btn("Объявлений за поиск", "settings:edit:listings_per_search", emoji="box")],
        [_btn("Прокси Авито", "settings:edit:avito_proxy", emoji="link"), _btn("Пауза между запросами", "settings:edit:avito_request_delay", emoji="clock")],
        [_btn("Тест парсера", "settings:test_parser", emoji="verify", style=BLUE), _btn("Тест ИИ", "settings:test_ai", emoji="bot", style=BLUE)],
        [_btn("Перезапустить браузер", "settings:restart_browser", emoji="loading", style=RED)],
        [_btn(BACK_TEXT, "admin:menu")],
    ])
