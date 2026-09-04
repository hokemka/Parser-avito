from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import User
from tgbot.fsm.states import TopUpBalance
from tgbot.keyboards.user import (
    MENU_PROFILE, MENU_SUBSCRIPTION, back_kb, crypto_invoice_kb, pay_methods_kb, profile_kb, tariffs_kb, topup_methods_kb,
)
from tgbot.services.cryptobot import CryptoPayClient, CryptoPayError
from tgbot.services.payments import apply_payment, build_crypto_invoice, cancel_payment, create_payment, get_payment, pay_from_balance
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import get_access, get_tariff, list_tariffs, rub_to_stars
from tgbot.services.users import count_user_tasks
from tgbot.utils.emoji import em
from tgbot.utils.text import format_money, h, parse_int
from tgbot.utils.texts import profile_text, tariff_text, tariffs_text

logger = logging.getLogger(__name__)
router = Router(name="profile")

MIN_TOPUP = 50
MAX_TOPUP = 100_000


async def _profile_view(session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> tuple[str, object]:
    access = await get_access(session, user, is_admin, settings.values.free_searches, settings.values.default_check_interval)
    active_tasks = await count_user_tasks(session, user.id)
    return profile_text(user, access, active_tasks), profile_kb(access.has_subscription)


@router.message(F.text == MENU_PROFILE)
async def menu_profile_message(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.clear()
    text, keyboard = await _profile_view(session, user, is_admin, settings)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:profile")
async def menu_profile_callback(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.clear()
    text, keyboard = await _profile_view(session, user, is_admin, settings)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


async def _subscription_view(session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> tuple[str, object]:
    access = await get_access(session, user, is_admin, settings.values.free_searches, settings.values.default_check_interval)
    tariffs = await list_tariffs(session)
    if not tariffs:
        return f"{tariffs_text(access, [])}\n{em('info')} Тарифы пока не настроены.", back_kb()
    return tariffs_text(access, tariffs), tariffs_kb(tariffs)


@router.message(F.text == MENU_SUBSCRIPTION)
async def menu_subscription_message(message: Message, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.clear()
    text, keyboard = await _subscription_view(session, user, is_admin, settings)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:subscription")
async def menu_subscription_callback(callback: CallbackQuery, state: FSMContext, session: AsyncSession, user: User, is_admin: bool, settings: SettingsService) -> None:
    await state.clear()
    text, keyboard = await _subscription_view(session, user, is_admin, settings)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("tariff:"))
async def callback_tariff_card(callback: CallbackQuery, session: AsyncSession, user: User, settings: SettingsService) -> None:
    tariff = await get_tariff(session, int(callback.data.split(":")[1]))
    if tariff is None or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    await callback.message.edit_text(tariff_text(tariff), reply_markup=pay_methods_kb(tariff, settings.values, user.balance))
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"))
async def callback_pay_tariff(callback: CallbackQuery, session: AsyncSession, user: User, settings: SettingsService, bot: Bot) -> None:
    _, tariff_id, method = callback.data.split(":")
    tariff = await get_tariff(session, int(tariff_id))
    if tariff is None or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    values = settings.values
    if method == "balance":
        result = await pay_from_balance(session, user, tariff)
        if result is None:
            await callback.answer("Недостаточно средств на балансе", show_alert=True)
            return
        await callback.message.edit_text(f"{em('party')} <b>Оплачено с баланса</b>\n\n{h(result.message)}\nОстаток: {format_money(user.balance)}", reply_markup=back_kb("menu:profile"))
        await callback.answer()
        return
    if method == "stars":
        if not values.stars_enabled:
            await callback.answer("Оплата Stars отключена", show_alert=True)
            return
        payment = await create_payment(session, user, "stars", "tariff", tariff.price_rub, float(tariff.price_stars), "XTR", tariff=tariff)
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title=f"Подписка «{tariff.name}»",
            description=f"{tariff.days} дн · до {tariff.max_tasks} мониторингов · проверка каждые {max(1, tariff.check_interval // 60)} мин",
            payload=f"pay:{payment.id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Подписка «{tariff.name}»", amount=tariff.price_stars)],
        )
        await callback.answer()
        return
    if method == "cryptobot":
        await _send_crypto_invoice(callback, session, user, settings, bot, purpose="tariff", amount_rub=tariff.price_rub, tariff=tariff)
        return
    await callback.answer()


async def _send_crypto_invoice(callback: CallbackQuery, session: AsyncSession, user: User, settings: SettingsService, bot: Bot, purpose: str, amount_rub: int, tariff=None) -> None:
    values = settings.values
    if not values.cryptobot_enabled or not values.cryptobot_token:
        await callback.answer("Оплата через CryptoBot отключена", show_alert=True)
        return
    bot_info = await bot.me()
    description = f"Подписка «{tariff.name}»" if tariff else f"Пополнение баланса на {amount_rub} ₽"
    payment = await create_payment(session, user, "cryptobot", purpose, amount_rub, 0.0, "RUB", tariff=tariff)
    try:
        invoice = await build_crypto_invoice(values, amount_rub, description, f"pay:{payment.id}", bot_info.username or "")
    except CryptoPayError as exc:
        logger.warning("cryptobot invoice failed: %s", exc)
        await cancel_payment(session, payment)
        await callback.answer("Не удалось создать счёт в CryptoBot. Попробуйте позже.", show_alert=True)
        return
    payment.external_id = invoice.invoice_id
    payment.invoice_url = invoice.url
    payment.amount_native = invoice.amount_native
    payment.currency = invoice.currency
    await session.commit()
    amount_line = f"{amount_rub} ₽" if invoice.currency == "RUB" else f"{invoice.amount_native} {invoice.currency} (≈ {amount_rub} ₽)"
    await callback.message.edit_text(
        f"{em('cryptobot')} <b>Счёт создан</b>\n\n{h(description)}\nК оплате: <b>{amount_line}</b>\n\n"
        f"Оплатите по кнопке ниже. Обычно зачисление происходит автоматически в течение минуты, либо нажмите «Проверить оплату».",
        reply_markup=crypto_invoice_kb(invoice.url, payment.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("check_pay:"))
async def callback_check_payment(callback: CallbackQuery, session: AsyncSession, user: User, settings: SettingsService) -> None:
    payment = await get_payment(session, int(callback.data.split(":")[1]))
    if payment is None or payment.user_id != user.id:
        await callback.answer("Платёж не найден", show_alert=True)
        return
    if payment.status == "paid":
        await callback.answer("Платёж уже зачислен", show_alert=True)
        return
    if payment.status != "pending":
        await callback.answer("Счёт отменён или истёк", show_alert=True)
        return
    client = CryptoPayClient(settings.values.cryptobot_token, settings.values.cryptobot_network)
    try:
        invoice = await client.get_invoice(payment.external_id or "")
    except CryptoPayError as exc:
        logger.warning("check invoice failed: %s", exc)
        await callback.answer("CryptoBot не отвечает, попробуйте позже", show_alert=True)
        return
    status = invoice.get("status") if invoice else None
    if status != "paid":
        await callback.answer("Оплата пока не поступила", show_alert=True)
        return
    result = await apply_payment(session, payment)
    await session.refresh(user)
    await callback.message.edit_text(f"{em('party')} <b>Оплата получена</b>\n\n{h(result.message)}", reply_markup=back_kb("menu:profile"))
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_pay:"))
async def callback_cancel_payment(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    payment = await get_payment(session, int(callback.data.split(":")[1]))
    if payment and payment.user_id == user.id:
        await cancel_payment(session, payment)
    await callback.message.edit_text(f"{em('cross')} Счёт отменён.", reply_markup=back_kb("menu:profile"))
    await callback.answer()


@router.callback_query(F.data == "topup:start")
async def callback_topup_start(callback: CallbackQuery, state: FSMContext, settings: SettingsService) -> None:
    values = settings.values
    if not values.stars_enabled and not (values.cryptobot_enabled and values.cryptobot_token):
        await callback.answer("Пополнение временно недоступно", show_alert=True)
        return
    await state.set_state(TopUpBalance.amount)
    await callback.message.edit_text(
        f"{em('wallet')} <b>Пополнение баланса</b>\n\nВведите сумму в рублях от {MIN_TOPUP} до {MAX_TOPUP:,}.".replace(",", " "),
        reply_markup=back_kb("menu:profile"),
    )
    await callback.answer()


@router.message(TopUpBalance.amount, F.text)
async def process_topup_amount(message: Message, state: FSMContext, settings: SettingsService) -> None:
    amount = parse_int(message.text)
    if amount is None or amount < MIN_TOPUP or amount > MAX_TOPUP:
        await message.answer(f"Введите сумму числом от {MIN_TOPUP} до {MAX_TOPUP}.", reply_markup=back_kb("menu:profile"))
        return
    await state.clear()
    stars = rub_to_stars(amount, settings.values.stars_rate)
    await message.answer(
        f"{em('wallet')} Пополнение на <b>{amount} ₽</b>. Выберите способ оплаты:",
        reply_markup=topup_methods_kb(amount, settings.values, stars),
    )


@router.callback_query(F.data.startswith("topup:pay:"))
async def callback_topup_pay(callback: CallbackQuery, session: AsyncSession, user: User, settings: SettingsService, bot: Bot) -> None:
    _, _, raw_amount, method = callback.data.split(":")
    amount = int(raw_amount)
    if amount < MIN_TOPUP or amount > MAX_TOPUP:
        await callback.answer("Некорректная сумма", show_alert=True)
        return
    if method == "stars":
        if not settings.values.stars_enabled:
            await callback.answer("Оплата Stars отключена", show_alert=True)
            return
        stars = rub_to_stars(amount, settings.values.stars_rate)
        payment = await create_payment(session, user, "stars", "topup", amount, float(stars), "XTR")
        await bot.send_invoice(
            chat_id=callback.from_user.id,
            title="Пополнение баланса",
            description=f"Зачисление {amount} ₽ на баланс в боте",
            payload=f"pay:{payment.id}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Пополнение {amount} ₽", amount=stars)],
        )
        await callback.answer()
        return
    if method == "cryptobot":
        await _send_crypto_invoice(callback, session, user, settings, bot, purpose="topup", amount_rub=amount)
        return
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(query: PreCheckoutQuery, session: AsyncSession) -> None:
    payload = query.invoice_payload or ""
    payment = await get_payment(session, int(payload.split(":")[1])) if payload.startswith("pay:") and payload.split(":")[1].isdigit() else None
    if payment is None or payment.status != "pending" or payment.user_id != query.from_user.id:
        await query.answer(ok=False, error_message="Счёт устарел. Создайте новый в разделе «Подписка».")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message, session: AsyncSession, user: User) -> None:
    payload = message.successful_payment.invoice_payload or ""
    payment_id = int(payload.split(":")[1]) if payload.startswith("pay:") and payload.split(":")[1].isdigit() else None
    payment = await get_payment(session, payment_id) if payment_id else None
    if payment is None:
        logger.error("successful payment with unknown payload %r from %s", payload, message.from_user.id)
        await message.answer(f"{em('info')} Платёж получен, но не найден в базе. Напишите в поддержку, укажите ID: <code>{message.successful_payment.telegram_payment_charge_id}</code>")
        return
    result = await apply_payment(session, payment, external_id=message.successful_payment.telegram_payment_charge_id)
    await session.refresh(user)
    await message.answer(f"{em('party')} <b>Оплата прошла</b>\n\n{h(result.message)}", reply_markup=back_kb("menu:profile"))
