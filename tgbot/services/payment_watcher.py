from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tgbot.services.cryptobot import CryptoPayClient, CryptoPayError
from tgbot.services.payments import apply_payment, expire_payment, list_pending
from tgbot.services.settings import SettingsService
from tgbot.utils.emoji import em

logger = logging.getLogger(__name__)

PENDING_TTL = timedelta(hours=2)


class CryptoInvoiceWatcher:
    def __init__(self, bot: Bot, session_factory: async_sessionmaker[AsyncSession], settings: SettingsService, interval: int = 45) -> None:
        self.bot = bot
        self._session_factory = session_factory
        self.settings = settings
        self.interval = interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="cryptobot-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.check_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cryptobot watcher tick failed")
            await asyncio.sleep(self.interval)

    async def check_pending(self) -> None:
        values = self.settings.values
        if not values.cryptobot_token:
            return
        client = CryptoPayClient(values.cryptobot_token, values.cryptobot_network)
        async with self._session_factory() as session:
            pending = await list_pending(session, "cryptobot", PENDING_TTL + timedelta(hours=22))
            if not pending:
                return
            by_invoice = {payment.external_id: payment for payment in pending if payment.external_id}
            try:
                invoices = await client.get_invoices(list(by_invoice.keys()))
            except CryptoPayError as exc:
                logger.warning("cryptobot getInvoices failed: %s", exc)
                return
            for invoice in invoices:
                payment = by_invoice.get(str(invoice.get("invoice_id")))
                if payment is None:
                    continue
                status = invoice.get("status")
                if status == "paid":
                    result = await apply_payment(session, payment)
                    if result.applied:
                        await self._notify(result.user_id, f"{em('check')} <b>Оплата получена</b>\n{result.message}")
                elif status == "expired":
                    await expire_payment(session, payment)

    async def _notify(self, user_id: int, text: str) -> None:
        try:
            await self.bot.send_message(user_id, text)
        except Exception as exc:
            logger.info("payment notify %s failed: %s", user_id, exc)
