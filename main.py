from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from config import Config, load_config
from tgbot.database.engine import create_engine, create_session_factory, init_database
from tgbot.handlers import admin as admin_handlers
from tgbot.handlers import profile, search, start, tasks
from tgbot.middlewares.database import DatabaseMiddleware, UserMiddleware
from tgbot.middlewares.throttling import ThrottlingMiddleware
from tgbot.services.ai import ListingEvaluator, OneMinClient
from tgbot.services.avito import AvitoClient
from tgbot.services.monitor import MonitorService
from tgbot.services.payment_watcher import CryptoInvoiceWatcher
from tgbot.services.search import SearchService
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import ensure_default_tariffs
from tgbot.utils import emoji

logger = logging.getLogger("main")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


async def set_bot_commands(bot: Bot, config: Config) -> None:
    user_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="search", description="Найти выгодное объявление"),
        BotCommand(command="menu", description="Показать меню"),
        BotCommand(command="cancel", description="Отменить действие"),
    ]
    await bot.set_my_commands(user_commands)
    for admin_id in config.bot.admin_ids:
        try:
            await bot.set_my_commands([*user_commands, BotCommand(command="admin", description="Админ-панель")], scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as exc:
            logger.info("cannot set admin commands for %s: %s", admin_id, exc)


def build_dispatcher(config: Config, session_factory, settings: SettingsService, search_service: SearchService, monitor: MonitorService, throttle_rate: float = 0.4) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["config"] = config
    dp["settings"] = settings
    dp["session_factory"] = session_factory
    dp["search_service"] = search_service
    dp["evaluator"] = search_service.evaluator
    dp["monitor"] = monitor

    dp.update.outer_middleware(DatabaseMiddleware(session_factory))
    dp.update.outer_middleware(UserMiddleware(config.bot.admin_ids))
    dp.message.outer_middleware(ThrottlingMiddleware(rate=throttle_rate))
    dp.callback_query.outer_middleware(ThrottlingMiddleware(rate=throttle_rate))

    dp.include_routers(
        start.router,
        admin_handlers.panel.router,
        admin_handlers.broadcast.router,
        admin_handlers.users.router,
        admin_handlers.tariffs.router,
        search.router,
        tasks.router,
        profile.router,
    )
    return dp


async def main() -> None:
    setup_logging()
    config = load_config()
    if not config.bot.token or "REPLACE" in config.bot.token:
        raise SystemExit("Укажите токен бота в settings.ini ([bot] token)")

    engine = create_engine(config.database.url)
    await init_database(engine)
    session_factory = create_session_factory(engine)

    settings = SettingsService(session_factory, config)
    await settings.load()
    emoji.configure(settings.values.premium_emoji)

    async with session_factory() as session:
        await ensure_default_tariffs(session, settings.values.stars_rate)

    avito = AvitoClient(key=config.avito.key, proxy=settings.values.avito_proxy, request_delay=settings.values.avito_request_delay)
    evaluator = ListingEvaluator(OneMinClient(config.ai.api_key, timeout=config.ai.timeout))
    search_service = SearchService(avito, evaluator, session_factory, settings)

    bot = Bot(token=config.bot.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    monitor = MonitorService(bot, session_factory, search_service, settings, config.bot.admin_ids, config.avito.monitor_tick)
    crypto_watcher = CryptoInvoiceWatcher(bot, session_factory, settings)
    dp = build_dispatcher(config, session_factory, settings, search_service, monitor)

    await bot.delete_webhook(drop_pending_updates=True)
    await set_bot_commands(bot, config)
    monitor.start()
    crypto_watcher.start()
    logger.info("bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await monitor.stop()
        await crypto_watcher.stop()
        await evaluator.close()
        await avito.close()
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            print(exc.code)
