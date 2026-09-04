from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.database.models import Payment, Tariff, User
from tgbot.services.cryptobot import CryptoPayClient, CryptoPayError
from tgbot.services.settings import RuntimeSettings
from tgbot.services.subscriptions import grant_subscription

logger = logging.getLogger(__name__)

METHOD_LABELS = {"stars": "Telegram Stars", "cryptobot": "CryptoBot", "balance": "Баланс", "admin": "Выдано админом"}
PURPOSE_LABELS = {"tariff": "Подписка", "topup": "Пополнение баланса"}


@dataclass(slots=True)
class PaymentResult:
    payment: Payment
    user_id: int
    message: str
    applied: bool


def rub_to_usd(price_rub: float, usd_rate: float) -> float:
    if usd_rate <= 0:
        return round(price_rub, 2)
    return round(price_rub / usd_rate, 2)


async def create_payment(
    session: AsyncSession,
    user: User,
    method: str,
    purpose: str,
    amount_rub: int,
    amount_native: float,
    currency: str,
    tariff: Tariff | None = None,
    external_id: str | None = None,
    invoice_url: str | None = None,
    status: str = "pending",
) -> Payment:
    payment = Payment(
        user_id=user.id,
        method=method,
        purpose=purpose,
        tariff_id=tariff.id if tariff else None,
        amount_rub=amount_rub,
        amount_native=amount_native,
        currency=currency,
        status=status,
        external_id=external_id,
        invoice_url=invoice_url,
    )
    session.add(payment)
    await session.commit()
    return payment


async def get_payment(session: AsyncSession, payment_id: int) -> Payment | None:
    return await session.get(Payment, payment_id)


async def find_payment_by_external_id(session: AsyncSession, method: str, external_id: str) -> Payment | None:
    query = select(Payment).where(Payment.method == method, Payment.external_id == external_id)
    return (await session.execute(query)).scalar_one_or_none()


async def list_pending(session: AsyncSession, method: str, max_age: timedelta) -> list[Payment]:
    since = datetime.utcnow() - max_age
    query = select(Payment).where(Payment.method == method, Payment.status == "pending", Payment.created_at >= since)
    return list((await session.execute(query)).scalars().all())


async def expire_payment(session: AsyncSession, payment: Payment) -> None:
    payment.status = "expired"
    await session.commit()


async def cancel_payment(session: AsyncSession, payment: Payment) -> None:
    if payment.status == "pending":
        payment.status = "cancelled"
        await session.commit()


async def apply_payment(session: AsyncSession, payment: Payment, external_id: str | None = None) -> PaymentResult:
    if payment.status == "paid":
        return PaymentResult(payment, payment.user_id, "Платёж уже обработан.", False)
    user = await session.get(User, payment.user_id)
    if user is None:
        return PaymentResult(payment, payment.user_id, "Пользователь не найден.", False)
    payment.status = "paid"
    payment.paid_at = datetime.utcnow()
    if external_id:
        payment.external_id = external_id
    await session.commit()
    if payment.purpose == "tariff":
        tariff = await session.get(Tariff, payment.tariff_id) if payment.tariff_id else None
        if tariff is None:
            user.balance = round(user.balance + payment.amount_rub, 2)
            await session.commit()
            return PaymentResult(payment, user.id, "Тариф больше недоступен, сумма зачислена на баланс.", True)
        subscription = await grant_subscription(session, user, tariff, source=payment.method)
        message = f"Подписка «{tariff.name}» активна до {subscription.expires_at.strftime('%d.%m.%Y %H:%M')}."
        return PaymentResult(payment, user.id, message, True)
    user.balance = round(user.balance + payment.amount_rub, 2)
    await session.commit()
    return PaymentResult(payment, user.id, f"Баланс пополнен на {payment.amount_rub} ₽. Текущий баланс: {user.balance:.0f} ₽.", True)


async def pay_from_balance(session: AsyncSession, user: User, tariff: Tariff) -> PaymentResult | None:
    if user.balance < tariff.price_rub:
        return None
    user.balance = round(user.balance - tariff.price_rub, 2)
    payment = await create_payment(session, user, "balance", "tariff", tariff.price_rub, float(tariff.price_rub), "RUB", tariff=tariff, status="pending")
    return await apply_payment(session, payment)


async def recent_payments(session: AsyncSession, limit: int = 15) -> list[Payment]:
    query = select(Payment).where(Payment.status == "paid").order_by(Payment.paid_at.desc()).limit(limit)
    return list((await session.execute(query)).scalars().all())


@dataclass(slots=True)
class CryptoInvoice:
    invoice_id: str
    url: str
    amount_native: float
    currency: str


async def build_crypto_invoice(settings: RuntimeSettings, amount_rub: int, description: str, payload: str, bot_username: str) -> CryptoInvoice:
    client = CryptoPayClient(settings.cryptobot_token, settings.cryptobot_network)
    if settings.cryptobot_currency_type == "fiat":
        invoice = await client.create_invoice(
            amount=float(amount_rub), currency_type="fiat", fiat="RUB",
            description=description, payload=payload,
            paid_btn_name="openBot", paid_btn_url=f"https://t.me/{bot_username}",
        )
        amount_native, currency = float(amount_rub), "RUB"
    else:
        amount_native = rub_to_usd(amount_rub, settings.usd_rate)
        invoice = await client.create_invoice(
            amount=amount_native, currency_type="crypto", asset=settings.cryptobot_asset,
            description=description, payload=payload,
            paid_btn_name="openBot", paid_btn_url=f"https://t.me/{bot_username}",
        )
        currency = settings.cryptobot_asset
    url = invoice.get("bot_invoice_url") or invoice.get("mini_app_invoice_url") or invoice.get("pay_url")
    invoice_id = invoice.get("invoice_id")
    if not url or invoice_id is None:
        raise CryptoPayError("invoice response has no url")
    return CryptoInvoice(str(invoice_id), str(url), amount_native, currency)
