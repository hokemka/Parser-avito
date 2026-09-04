from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import load_config
from main import build_dispatcher
from tests.fake_telegram import FakeSession, last_markup, make_callback, make_message, outgoing, text_of
from tgbot.database.engine import create_engine, create_session_factory, init_database
from tgbot.services.ai import Evaluation, ListingEvaluator, OneMinClient
from tgbot.services.avito import AvitoClient, Listing, Location
from tgbot.services.monitor import MonitorService
from tgbot.services.search import SearchService
from tgbot.services.settings import SettingsService
from tgbot.services.subscriptions import ensure_default_tariffs

ADMIN_ID = 123456789


class StubAvito(AvitoClient):
    def __init__(self) -> None:
        super().__init__()
        self.searches: list[tuple] = []

    async def search(self, query, location, price_min=None, price_max=None, pages=1, limit=50):
        self.searches.append((query, location.name, price_min, price_max))
        return [
            Listing(id=101, title=f"{query} отличное состояние", price=36000, url="https://www.avito.ru/101", images=["https://img/1.jpg"], description="Полный комплект", location="Москва"),
            Listing(id=102, title=f"{query} после ремонта", price=42000, url="https://www.avito.ru/102", description="Менялся экран", location="Москва"),
        ]

    async def fetch_details(self, listing):
        return listing

    async def find_locations(self, query, limit=8):
        return [Location(name="Казань", id=640860, slug="kazan", parent="Татарстан")]


class StubEvaluator(ListingEvaluator):
    def __init__(self) -> None:
        super().__init__(OneMinClient(""))

    async def evaluate(self, request, listing, model, analyze_images, max_images):
        rating = 8.5 if listing.id in (101, 777) else 4.0
        return Evaluation(rating=rating, verdict="buy" if rating > 7 else "skip", matches_request=True, condition="хорошее",
                          condition_score=7.0, summary="Тест", pros=["p"], cons=["c"], red_flags=[], market_price=40000,
                          recommended_offer=34000, profit_potential="есть", questions_to_seller=["q"])


_user_counter = 1000


def new_user() -> int:
    global _user_counter
    _user_counter += 1
    return _user_counter


@pytest_asyncio.fixture(scope="session")
async def env(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("flows")
    config = load_config(Path(__file__).resolve().parent.parent / "settings.example.ini")
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'flows.db'}")
    await init_database(engine)
    factory = create_session_factory(engine)
    settings = SettingsService(factory, config)
    await settings.load()
    async with factory() as session:
        await ensure_default_tariffs(session, settings.values.stars_rate)
    avito = StubAvito()
    search_service = SearchService(avito, StubEvaluator(), factory, settings)
    session = FakeSession()
    bot = Bot(token="42:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    monitor = MonitorService(bot, factory, search_service, settings, config.bot.admin_ids, 30)
    dp = build_dispatcher(config, factory, settings, search_service, monitor, throttle_rate=0)
    yield {"dp": dp, "bot": bot, "session": session, "factory": factory, "avito": avito, "monitor": monitor, "settings": settings}
    await engine.dispose()


async def feed(env, update):
    await env["dp"].feed_update(env["bot"], update)
    await asyncio.sleep(0)


def sent_texts(env) -> list[str]:
    return [text_of(call) for call in outgoing(env["session"].calls)]


@pytest.mark.asyncio
async def test_start_and_menu(env):
    USER_ID = new_user()
    await feed(env, make_message(USER_ID, "/start"))
    texts = sent_texts(env)
    assert any("Привет" in text for text in texts)
    await feed(env, make_message(USER_ID, "Помощь"))
    assert any("Как это работает" in text for text in sent_texts(env))
    await feed(env, make_message(USER_ID, "Профиль"))
    assert any("Баланс" in text for text in sent_texts(env))
    await feed(env, make_message(USER_ID, "Подписка"))
    assert any("Старт" in text or "Тариф" in text for text in sent_texts(env))


@pytest.mark.asyncio
async def test_search_wizard_and_results(env):
    USER_ID = new_user()
    await feed(env, make_message(USER_ID, "/start"))
    await feed(env, make_message(USER_ID, "Найти выгодное"))
    assert "Что ищем" in sent_texts(env)[-1]
    await feed(env, make_message(USER_ID, "iPhone 13 128gb"))
    assert "Где искать" in sent_texts(env)[-1]
    await feed(env, make_callback(USER_ID, "search:loc:0"))
    assert "бюджет" in sent_texts(env)[-1].lower()
    await feed(env, make_message(USER_ID, "30-45 тыс"))
    assert "Пожелания" in sent_texts(env)[-1]
    await feed(env, make_message(USER_ID, "без ремонта"))
    assert "Проверьте запрос" in sent_texts(env)[-1]
    await feed(env, make_callback(USER_ID, "search:run"))
    texts = sent_texts(env)
    assert env["avito"].searches == [("iPhone 13 128gb", "Москва", 30000, 45000)]
    assert any("8.5/10" in text for text in texts)
    assert any("Лучшая оценка" in text for text in texts)
    details_calls = [call for call in env["session"].calls if type(call).__name__ == "SendPhoto"]
    assert details_calls, "photo card expected"
    keyboard = details_calls[0].reply_markup
    details_data = keyboard.inline_keyboard[1][0].callback_data
    await feed(env, make_callback(USER_ID, details_data))
    assert any("Плюсы" in text for text in sent_texts(env))
    async with env["factory"]() as session:
        from tgbot.database.models import User
        user = await session.get(User, USER_ID)
        assert user.free_searches_used == 1 and user.searches_count == 1


@pytest.mark.asyncio
async def test_manual_location_and_monitor_requires_subscription(env):
    USER_ID = new_user()
    await feed(env, make_message(USER_ID, "/start"))
    await feed(env, make_message(USER_ID, "/search"))
    await feed(env, make_message(USER_ID, "PS5"))
    await feed(env, make_callback(USER_ID, "search:loc_manual"))
    await feed(env, make_message(USER_ID, "Казань"))
    assert "Выберите подходящий вариант" in sent_texts(env)[-1]
    await feed(env, make_callback(USER_ID, "search:found_loc:0"))
    await feed(env, make_callback(USER_ID, "search:price_any"))
    await feed(env, make_callback(USER_ID, "search:wishes_skip"))
    labels = [btn.text for row in last_markup(env["session"].calls).inline_keyboard for btn in row]
    assert any("нужна подписка" in label for label in labels)
    await feed(env, make_callback(USER_ID, "search:run"))
    assert env["avito"].searches[-1] == ("PS5", "Казань", None, None)


@pytest.mark.asyncio
async def test_admin_grants_subscription_then_monitoring_works(env):
    USER_ID = new_user()
    await feed(env, make_message(USER_ID, "/start"))
    await feed(env, make_message(ADMIN_ID, "/admin", username="admin"))
    assert "Админ-панель" in sent_texts(env)[-1]
    await feed(env, make_callback(ADMIN_ID, "admin:users", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "users:search", username="admin"))
    await feed(env, make_message(ADMIN_ID, str(USER_ID), username="admin"))
    assert "Пользователь" in sent_texts(env)[-1]
    await feed(env, make_callback(ADMIN_ID, f"users:grant:{USER_ID}", username="admin"))
    grant_data = last_markup(env["session"].calls).inline_keyboard[0][0].callback_data
    await feed(env, make_callback(ADMIN_ID, grant_data, username="admin"))
    assert any("выдана подписка" in text for text in sent_texts(env))
    await feed(env, make_callback(ADMIN_ID, f"users:balance:{USER_ID}", username="admin"))
    await feed(env, make_message(ADMIN_ID, "+500", username="admin"))
    assert any("Баланс обновлён" in text for text in sent_texts(env))
    await feed(env, make_callback(ADMIN_ID, "users:export", username="admin"))
    assert any(type(call).__name__ == "SendDocument" for call in env["session"].calls)

    await feed(env, make_message(USER_ID, "Найти выгодное"))
    await feed(env, make_message(USER_ID, "MacBook Air M2"))
    await feed(env, make_callback(USER_ID, "search:loc:0"))
    await feed(env, make_message(USER_ID, "до 80000"))
    await feed(env, make_callback(USER_ID, "search:wishes_skip"))
    await feed(env, make_callback(USER_ID, "search:run_monitor"))
    assert any("Мониторинг включён" in text for text in sent_texts(env))
    await feed(env, make_message(USER_ID, "Мои мониторинги"))
    assert "Активных: 1" in sent_texts(env)[-1]

    async with env["factory"]() as session:
        from sqlalchemy import select
        from tgbot.database.models import SearchTask
        task = (await session.execute(select(SearchTask))).scalars().first()
    calls_before = len(env["session"].calls)
    await env["monitor"].check_task(task)
    assert len(env["session"].calls) == calls_before, "baseline run must not notify"
    env["avito"].search = _new_listing_search(env["avito"])
    await env["monitor"]._tick()
    async with env["factory"]() as session:
        task = await session.get(SearchTask, task.id)
        task.last_checked_at = None
        from datetime import datetime, timedelta
        task.last_checked_at = datetime.utcnow() - timedelta(days=1)
        await session.commit()
    await env["monitor"]._tick()
    assert any("Новое по мониторингу" in text for text in sent_texts(env))

    await feed(env, make_callback(USER_ID, f"task:{task.id}"))
    assert "Мониторинг «MacBook Air M2»" in sent_texts(env)[-1]
    await feed(env, make_callback(USER_ID, f"task_toggle:{task.id}"))
    assert "на паузе" in sent_texts(env)[-1]
    await feed(env, make_callback(USER_ID, f"task_del:{task.id}"))
    await feed(env, make_callback(USER_ID, f"task_del_yes:{task.id}"))
    assert "Пока нет ни одного" in sent_texts(env)[-1]


def _new_listing_search(avito):
    async def search(query, location, price_min=None, price_max=None, pages=1, limit=50):
        return [Listing(id=777, title=f"{query} новый", price=70000, url="https://www.avito.ru/777", images=["https://img/7.jpg"], description="Новый", location="Москва")]
    return search


@pytest.mark.asyncio
async def test_broadcast_flow(env):
    USER_ID = new_user()
    await feed(env, make_message(USER_ID, "/start"))
    await feed(env, make_message(ADMIN_ID, "/admin", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "admin:broadcast", username="admin"))
    await feed(env, make_message(ADMIN_ID, "Всем привет!", username="admin"))
    assert "Хотите добавить кнопки" in sent_texts(env)[-1]
    await feed(env, make_callback(ADMIN_ID, "bc:add_button", username="admin"))
    await feed(env, make_message(ADMIN_ID, "Наш канал", username="admin"))
    await feed(env, make_message(ADMIN_ID, "not a url", username="admin"))
    assert "Ссылка должна" in sent_texts(env)[-1]
    await feed(env, make_message(ADMIN_ID, "https://t.me/channel", username="admin"))
    assert "Наш канал" in sent_texts(env)[-1]
    await feed(env, make_callback(ADMIN_ID, "bc:continue", username="admin"))
    assert any("Разослать" in text for text in sent_texts(env))
    await feed(env, make_callback(ADMIN_ID, "bc:send", username="admin"))
    await asyncio.sleep(0.5)
    assert any("Рассылка завершена" in text for text in sent_texts(env))
    copies = [call for call in env["session"].calls if type(call).__name__ == "CopyMessage"]
    assert len(copies) >= 3
    assert copies[-1].reply_markup.inline_keyboard[0][0].url == "https://t.me/channel"


@pytest.mark.asyncio
async def test_tariff_wizard_and_payment_settings(env):
    await feed(env, make_message(ADMIN_ID, "/admin", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "admin:tariffs", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "tariffs:add", username="admin"))
    await feed(env, make_message(ADMIN_ID, "VIP", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "tariffs:skip_description", username="admin"))
    await feed(env, make_message(ADMIN_ID, "90", username="admin"))
    await feed(env, make_message(ADMIN_ID, "4990", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "tariffs:skip_stars", username="admin"))
    await feed(env, make_message(ADMIN_ID, "50", username="admin"))
    await feed(env, make_message(ADMIN_ID, "60", username="admin"))
    assert any("Тариф создан" in text for text in sent_texts(env))
    assert "VIP" in sent_texts(env)[-1]
    await feed(env, make_callback(ADMIN_ID, "admin:payments", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "settings:toggle:stars_enabled", username="admin"))
    assert env["settings"].values.stars_enabled is False
    await feed(env, make_callback(ADMIN_ID, "settings:edit:stars_rate", username="admin"))
    await feed(env, make_message(ADMIN_ID, "2,5", username="admin"))
    assert env["settings"].values.stars_rate == 2.5
    await feed(env, make_callback(ADMIN_ID, "admin:parser", username="admin"))
    await feed(env, make_callback(ADMIN_ID, "settings:edit:avito_proxy", username="admin"))
    await feed(env, make_message(ADMIN_ID, "http://user:pass@1.2.3.4:8080", username="admin"))
    assert env["avito"].proxy == "http://user:pass@1.2.3.4:8080"
    await feed(env, make_callback(ADMIN_ID, "admin:stats", username="admin"))
    assert "Статистика" in sent_texts(env)[-1]


@pytest.mark.asyncio
async def test_stars_invoice_and_successful_payment(env):
    USER_ID = new_user()
    await env["settings"].set("stars_enabled", True)
    await feed(env, make_message(USER_ID, "/start"))
    await feed(env, make_callback(USER_ID, "menu:subscription"))
    tariff_data = last_markup(env["session"].calls).inline_keyboard[0][0].callback_data
    await feed(env, make_callback(USER_ID, tariff_data))
    tariff_id = tariff_data.split(":")[1]
    await feed(env, make_callback(USER_ID, f"pay:{tariff_id}:stars"))
    invoice = [call for call in env["session"].calls if type(call).__name__ == "SendInvoice"][-1]
    assert invoice.currency == "XTR" and invoice.payload.startswith("pay:")
    payment_id = int(invoice.payload.split(":")[1])

    import datetime as dt
    from aiogram.types import Chat, Message, PreCheckoutQuery, SuccessfulPayment, Update, User as TgUser
    pre = Update(update_id=90001, pre_checkout_query=PreCheckoutQuery(
        id="pc1", from_user=TgUser(id=USER_ID, is_bot=False, first_name="T"), currency="XTR", total_amount=invoice.prices[0].amount, invoice_payload=invoice.payload))
    await feed(env, pre)
    answer = [call for call in env["session"].calls if type(call).__name__ == "AnswerPreCheckoutQuery"][-1]
    assert answer.ok is True
    paid = Update(update_id=90002, message=Message(
        message_id=90002, date=dt.datetime.now(), chat=Chat(id=USER_ID, type="private"),
        from_user=TgUser(id=USER_ID, is_bot=False, first_name="T", username="tester"),
        successful_payment=SuccessfulPayment(currency="XTR", total_amount=invoice.prices[0].amount, invoice_payload=invoice.payload,
                                             telegram_payment_charge_id="chg", provider_payment_charge_id="prov"),
    ))
    await feed(env, paid)
    assert any("Оплата прошла" in text for text in sent_texts(env))
    async with env["factory"]() as session:
        from tgbot.database.models import Payment
        payment = await session.get(Payment, payment_id)
        assert payment.status == "paid" and payment.external_id == "chg"
