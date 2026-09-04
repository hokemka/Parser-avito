from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.fsm.states import AdminSettings
from tgbot.keyboards.admin import admin_back_kb, admin_cancel_kb, admin_menu_kb, general_settings_kb, parser_settings_kb, payment_settings_kb
from tgbot.keyboards.user import MENU_ADMIN
from tgbot.services.ai import AiError, ListingEvaluator, SearchRequest
from tgbot.services.avito import POPULAR_LOCATIONS, AvitoError
from tgbot.services.cryptobot import CryptoPayClient, CryptoPayError
from tgbot.services.monitor import MonitorService
from tgbot.services.payments import METHOD_LABELS, PURPOSE_LABELS, recent_payments
from tgbot.services.search import SearchService
from tgbot.services.settings import SettingsService
from tgbot.services.users import collect_stats
from tgbot.utils import emoji
from tgbot.utils.emoji import em
from tgbot.utils.filters import IsAdmin
from tgbot.utils.text import format_dt, h, parse_float, parse_int, time_ago

logger = logging.getLogger(__name__)
router = Router(name="admin_panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

SETTING_PROMPTS: dict[str, str] = {
    "cryptobot_token": "Отправьте токен приложения Crypto Pay (из @CryptoBot → Crypto Pay → My Apps).",
    "stars_rate": "Сколько рублей стоит 1 звезда? Используется для пересчёта цен в Stars. Например: 1.6",
    "usd_rate": "Курс доллара в рублях для крипто-счетов. Например: 95",
    "free_searches": "Сколько бесплатных поисков давать пользователю без подписки? Число.",
    "default_min_rating": "Минимальная оценка ИИ для новых мониторингов по умолчанию (0–10).",
    "default_check_interval": "Интервал проверки в секундах для мониторингов без тарифа (например, 300).",
    "ai_model": "ID модели 1min.ai. Бесплатные/дешёвые: qwen3-8b, qwen3-vl-8b-thinking, qwen3.7-flash. Сильнее: gpt-4o-mini, gpt-4.1, claude-sonnet-4-6, gemini-2.5-flash. Все, кроме qwen3.7-max, qwen-plus и gpt-4.1-nano, умеют смотреть фото.",
    "ai_candidates_per_search": "Сколько лучших объявлений отправлять в ИИ за один поиск (5–20).",
    "listings_per_search": "Сколько объявлений забирать с Авито за один поиск (10–50).",
    "avito_proxy": "Прокси для Авито в формате http://user:pass@host:port или socks5://host:port. Отправьте «-», чтобы убрать.",
    "avito_request_delay": "Пауза между запросами к Авито в секундах, например 2.0",
}

SETTING_SCREENS: dict[str, str] = {
    "cryptobot_token": "admin:payments", "stars_rate": "admin:payments", "usd_rate": "admin:payments",
    "free_searches": "admin:settings", "default_min_rating": "admin:settings", "default_check_interval": "admin:settings",
    "ai_model": "admin:parser", "ai_candidates_per_search": "admin:parser", "listings_per_search": "admin:parser",
    "avito_proxy": "admin:parser", "avito_request_delay": "admin:parser",
}


def admin_text() -> str:
    return f"{em('settings')} <b>Админ-панель</b>\n\nВыберите раздел."


@router.message(Command("admin"))
@router.message(F.text == MENU_ADMIN)
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(admin_text(), reply_markup=admin_menu_kb())


@router.callback_query(F.data == "admin:menu")
async def callback_admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.edit_text(admin_text(), reply_markup=admin_menu_kb())
    except Exception:
        await callback.message.answer(admin_text(), reply_markup=admin_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def callback_admin_stats(callback: CallbackQuery, session: AsyncSession, monitor: MonitorService) -> None:
    stats = await collect_stats(session)
    payments = await recent_payments(session, limit=5)
    recent = "\n".join(
        f"• {format_dt(p.paid_at)} · {p.user_id} · {PURPOSE_LABELS.get(p.purpose, p.purpose)} · {p.amount_rub} ₽ ({METHOD_LABELS.get(p.method, p.method)})"
        for p in payments
    ) or "пока нет"
    text = (
        f"{em('stats')} <b>Статистика</b>\n\n"
        f"{em('users')} Пользователей: <b>{stats['total']}</b> (+{stats['today']} за сутки, +{stats['week']} за неделю)\n"
        f"{em('eye')} Активных за сутки: {stats['active_day']}\n"
        f"{em('star')} С подпиской: <b>{stats['subscribers']}</b>\n"
        f"{em('user_ban')} Забанено: {stats['banned']} · заблокировали бота: {stats['blocked']}\n\n"
        f"{em('bell')} Активных мониторингов: {stats['tasks_active']}\n"
        f"{em('box')} Поисков выполнено: {stats['searches']}\n"
        f"{em('loading')} Проверок мониторинга за сессию: {monitor.checked_total} · отправлено объявлений: {monitor.notified_total}\n\n"
        f"{em('money')} Оплат: {stats['payments_count']} на <b>{stats['revenue']} ₽</b> (за сутки: {stats['revenue_day']} ₽)\n"
        f"{em('receive_money')} Последние оплаты:\n{recent}"
    )
    await callback.message.edit_text(text, reply_markup=admin_back_kb())
    await callback.answer()


def payments_text(settings: SettingsService) -> str:
    values = settings.values
    token = f"…{values.cryptobot_token[-6:]}" if values.cryptobot_token else "не задан"
    return (
        f"{em('wallet')} <b>Настройки оплаты</b>\n\n"
        f"{em('star')} Telegram Stars: {'включены' if values.stars_enabled else 'выключены'} · курс {values.stars_rate:g} ₽/⭐\n"
        f"{em('cryptobot')} CryptoBot: {'включён' if values.cryptobot_enabled else 'выключен'} · сеть {values.cryptobot_network} · токен {h(token)}\n"
        f"{em('money')} Счета CryptoBot: {'в рублях (fiat)' if values.cryptobot_currency_type == 'fiat' else values.cryptobot_asset + ' по курсу ' + f'{values.usd_rate:g}'} \n"
    )


@router.callback_query(F.data == "admin:payments")
async def callback_admin_payments(callback: CallbackQuery, state: FSMContext, settings: SettingsService) -> None:
    await state.clear()
    await callback.message.edit_text(payments_text(settings), reply_markup=payment_settings_kb(settings.values))
    await callback.answer()


def general_text(settings: SettingsService) -> str:
    values = settings.values
    return (
        f"{em('settings')} <b>Общие настройки</b>\n\n"
        f"{em('gift')} Бесплатных поисков: {values.free_searches}\n"
        f"{em('stats')} Мин. оценка для мониторинга по умолчанию: {values.default_min_rating}\n"
        f"{em('clock')} Интервал без тарифа: {values.default_check_interval} сек\n"
        f"{em('brush')} Премиум-эмодзи: {'включены' if values.premium_emoji else 'выключены'}"
    )


@router.callback_query(F.data == "admin:settings")
async def callback_admin_settings(callback: CallbackQuery, state: FSMContext, settings: SettingsService) -> None:
    await state.clear()
    await callback.message.edit_text(general_text(settings), reply_markup=general_settings_kb(settings.values))
    await callback.answer()


def parser_text(settings: SettingsService, search_service: SearchService, monitor: MonitorService) -> str:
    values = settings.values
    avito = search_service.avito
    info = avito.status()
    if info["blocked"]:
        status = f"{em('red')} блокировка ({h(info['last_error'] or '')})"
    elif info["last_error"]:
        status = f"{em('blue')} ошибка: {h(info['last_error'])}"
    else:
        status = f"{em('green')} работает"
    browser = f"{info['engine']} · {'запущен' if info['running'] else 'не запущен'} · страниц: {info['pages']} · блокировок: {info['blocks']}"
    last_tick = time_ago(monitor.last_tick_at) if monitor.last_tick_at else "ещё не было"
    proxy = values.avito_proxy
    proxy_shown = h(re.sub(r"//[^@]+@", "//***@", proxy)) if proxy else "нет"
    return (
        f"{em('bot')} <b>Парсер и ИИ</b>\n\n"
        f"Статус Авито: {status}\n"
        f"{em('apps')} Браузер: {browser}\n"
        f"{em('link')} Прокси: {proxy_shown}\n"
        f"{em('clock')} Пауза между запросами: {values.avito_request_delay:g} с · последний тик мониторинга: {last_tick}\n"
        f"{em('box')} Объявлений за поиск: {values.listings_per_search} · кандидатов для ИИ: {values.ai_candidates_per_search}\n\n"
        f"{em('bot')} Модель: <code>{h(values.ai_model)}</code> · ключ 1min.ai: {'задан' if search_service.evaluator.client.enabled else 'НЕ задан (settings.ini)'}\n"
        f"{em('photo')} Анализ фото: {'включён' if values.ai_analyze_images else 'выключен'} (до {values.ai_max_images} фото)"
    )


@router.callback_query(F.data == "admin:parser")
async def callback_admin_parser(callback: CallbackQuery, state: FSMContext, settings: SettingsService, search_service: SearchService, monitor: MonitorService) -> None:
    await state.clear()
    await callback.message.edit_text(parser_text(settings, search_service, monitor), reply_markup=parser_settings_kb(settings.values))
    await callback.answer()


async def _render_screen(callback_or_message, screen: str, settings: SettingsService, search_service: SearchService, monitor: MonitorService, edit: bool) -> None:
    if screen == "admin:payments":
        text, keyboard = payments_text(settings), payment_settings_kb(settings.values)
    elif screen == "admin:parser":
        text, keyboard = parser_text(settings, search_service, monitor), parser_settings_kb(settings.values)
    else:
        text, keyboard = general_text(settings), general_settings_kb(settings.values)
    if edit:
        await callback_or_message.edit_text(text, reply_markup=keyboard)
    else:
        await callback_or_message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("settings:toggle:"))
async def callback_toggle_setting(callback: CallbackQuery, settings: SettingsService, search_service: SearchService, monitor: MonitorService) -> None:
    key = callback.data.split(":")[-1]
    if key not in settings.keys:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return
    value = await settings.toggle(key)
    if key == "premium_emoji":
        emoji.configure(value)
    screen = "admin:payments" if key.endswith("_enabled") else "admin:parser" if key.startswith("ai_") else "admin:settings"
    await _render_screen(callback.message, screen, settings, search_service, monitor, edit=True)
    await callback.answer("Включено" if value else "Выключено")


@router.callback_query(F.data == "settings:toggle_network")
async def callback_toggle_network(callback: CallbackQuery, settings: SettingsService, search_service: SearchService, monitor: MonitorService) -> None:
    new_value = "testnet" if settings.values.cryptobot_network == "mainnet" else "mainnet"
    await settings.set("cryptobot_network", new_value)
    await _render_screen(callback.message, "admin:payments", settings, search_service, monitor, edit=True)
    await callback.answer(f"Сеть: {new_value}")


@router.callback_query(F.data.startswith("settings:edit:"))
async def callback_edit_setting(callback: CallbackQuery, state: FSMContext, settings: SettingsService) -> None:
    key = callback.data.split(":")[-1]
    if key not in SETTING_PROMPTS:
        await callback.answer("Неизвестная настройка", show_alert=True)
        return
    current = getattr(settings.values, key)
    shown = "скрыт" if key == "cryptobot_token" and current else h(str(current) or "пусто")
    await state.set_state(AdminSettings.edit_value)
    await state.update_data(setting_key=key)
    await callback.message.edit_text(
        f"{em('edit')} <b>Изменение настройки</b>\n\n{SETTING_PROMPTS[key]}\n\nТекущее значение: <code>{shown}</code>",
        reply_markup=admin_cancel_kb(SETTING_SCREENS[key]),
    )
    await callback.answer()


@router.message(AdminSettings.edit_value, F.text)
async def process_setting_value(message: Message, state: FSMContext, settings: SettingsService, search_service: SearchService, monitor: MonitorService) -> None:
    key = (await state.get_data()).get("setting_key")
    if key not in SETTING_PROMPTS:
        await state.clear()
        return
    raw = message.text.strip()
    current = getattr(settings.values, key)
    value: object
    if key == "avito_proxy" and raw == "-":
        value = ""
    elif isinstance(current, bool):
        value = raw.lower() in ("1", "да", "on", "true", "вкл")
    elif isinstance(current, int):
        parsed_int = parse_int(raw)
        if parsed_int is None or parsed_int < 0:
            await message.answer("Нужно целое неотрицательное число.", reply_markup=admin_cancel_kb(SETTING_SCREENS[key]))
            return
        value = parsed_int
    elif isinstance(current, float):
        parsed_float = parse_float(raw)
        if parsed_float is None or parsed_float < 0:
            await message.answer("Нужно число, например 1.5", reply_markup=admin_cancel_kb(SETTING_SCREENS[key]))
            return
        value = parsed_float
    else:
        value = raw
    await settings.set(key, value)
    if key in ("avito_proxy", "avito_request_delay"):
        search_service.avito.configure(settings.values.avito_proxy, settings.values.avito_request_delay)
        search_service.avito.blocked_until = 0.0
    await state.clear()
    await message.answer(f"{em('check')} Сохранено.")
    await _render_screen(message, SETTING_SCREENS[key], settings, search_service, monitor, edit=False)


@router.callback_query(F.data == "settings:check_cryptobot")
async def callback_check_cryptobot(callback: CallbackQuery, settings: SettingsService) -> None:
    values = settings.values
    if not values.cryptobot_token:
        await callback.answer("Сначала задайте токен", show_alert=True)
        return
    client = CryptoPayClient(values.cryptobot_token, values.cryptobot_network)
    try:
        info = await client.get_me()
    except CryptoPayError as exc:
        await callback.answer(f"Ошибка: {str(exc)[:150]}", show_alert=True)
        return
    await callback.answer(f"OK: приложение «{info.get('name', '?')}» (id {info.get('app_id', '?')})", show_alert=True)


@router.callback_query(F.data == "settings:restart_browser")
async def callback_restart_browser(callback: CallbackQuery, settings: SettingsService, search_service: SearchService, monitor: MonitorService) -> None:
    await callback.answer("Перезапускаю браузер…")
    avito = search_service.avito
    avito.blocked_until = 0.0
    avito.last_error = None
    try:
        await avito.browser.restart()
    except Exception as exc:
        logger.warning("browser restart failed: %s", exc)
        avito.last_error = f"браузер не запустился: {str(exc)[:120]}"
    await _render_screen(callback.message, "admin:parser", settings, search_service, monitor, edit=True)


@router.callback_query(F.data == "settings:test_parser")
async def callback_test_parser(callback: CallbackQuery, search_service: SearchService) -> None:
    await callback.answer("Проверяю Авито…")
    waiting = await callback.message.answer(f"{em('loading')} Запрашиваю «iphone» в Москве…")
    try:
        listings = await search_service.find_listings(SearchRequest("iphone", "Москва"), POPULAR_LOCATIONS[0], pages=1, limit=10)
    except AvitoError as exc:
        await waiting.edit_text(f"{em('cross')} Ошибка парсера: {h(str(exc)[:300])}", reply_markup=admin_back_kb("admin:parser"))
        return
    sample = "\n".join(f"• {h(item.title[:50])} — {item.price or '?'} ₽ ({item.source})" for item in listings[:5]) or "пусто"
    await waiting.edit_text(f"{em('check')} Получено {len(listings)} объявлений:\n{sample}", reply_markup=admin_back_kb("admin:parser"))


@router.callback_query(F.data == "settings:test_ai")
async def callback_test_ai(callback: CallbackQuery, settings: SettingsService, evaluator: ListingEvaluator) -> None:
    if not evaluator.client.enabled:
        await callback.answer("Ключ 1min.ai не задан в settings.ini", show_alert=True)
        return
    await callback.answer("Проверяю 1min.ai…")
    waiting = await callback.message.answer(f"{em('loading')} Отправляю тестовый запрос модели {h(settings.values.ai_model)}…")
    try:
        answer = await evaluator.client.chat("Ответь одним словом: OK", model=settings.values.ai_model)
    except AiError as exc:
        await waiting.edit_text(f"{em('cross')} Ошибка 1min.ai: {h(str(exc)[:300])}", reply_markup=admin_back_kb("admin:parser"))
        return
    await waiting.edit_text(f"{em('check')} Ответ модели: <code>{h(answer[:200])}</code>", reply_markup=admin_back_kb("admin:parser"))
