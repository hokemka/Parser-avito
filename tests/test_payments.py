import pytest
import pytest_asyncio
from aiogram.types import User as TgUser

from tgbot.database.engine import create_engine, create_session_factory, init_database
from tgbot.database.models import Tariff
from tgbot.services.payments import apply_payment, create_payment, pay_from_balance, rub_to_usd
from tgbot.services.subscriptions import get_access, get_active_subscription, grant_subscription, rub_to_stars, revoke_subscription
from tgbot.services.users import change_balance, export_users_text, find_user, get_or_create_user


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await init_database(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def test_rates():
    assert rub_to_stars(299, 1.6) == 187
    assert rub_to_stars(100, 0) == 100
    assert rub_to_usd(950, 95) == 10.0


@pytest.mark.asyncio
async def test_subscription_flow(session_factory):
    async with session_factory() as session:
        user, created = await get_or_create_user(session, TgUser(id=42, is_bot=False, first_name="Tester", username="tester"))
        assert created
        tariff = Tariff(name="Pro", days=30, price_rub=990, price_stars=619, max_tasks=10, check_interval=180)
        session.add(tariff)
        await session.commit()

        access = await get_access(session, user, False, 3, 300)
        assert not access.has_subscription and access.free_searches_left == 3 and access.can_search and not access.can_monitor

        payment = await create_payment(session, user, "stars", "tariff", 990, 619, "XTR", tariff=tariff)
        result = await apply_payment(session, payment, external_id="charge_1")
        assert result.applied and payment.status == "paid"
        again = await apply_payment(session, payment)
        assert not again.applied

        subscription = await get_active_subscription(session, user.id)
        assert subscription and subscription.tariff_name == "Pro"
        first_expiry = subscription.expires_at
        extended = await grant_subscription(session, user, tariff, "admin")
        assert extended.expires_at > first_expiry

        access = await get_access(session, user, False, 3, 300)
        assert access.has_subscription and access.max_tasks == 10 and access.can_monitor

        assert await revoke_subscription(session, user.id)
        assert await get_active_subscription(session, user.id) is None


@pytest.mark.asyncio
async def test_balance_payment_and_topup(session_factory):
    async with session_factory() as session:
        user, _ = await get_or_create_user(session, TgUser(id=7, is_bot=False, first_name="A", username="Alpha"))
        tariff = Tariff(name="Start", days=7, price_rub=299, price_stars=187)
        session.add(tariff)
        await session.commit()
        assert await pay_from_balance(session, user, tariff) is None
        topup = await create_payment(session, user, "cryptobot", "topup", 500, 500.0, "RUB", external_id="inv1")
        await apply_payment(session, topup)
        assert user.balance == 500
        result = await pay_from_balance(session, user, tariff)
        assert result and result.applied and user.balance == 201
        assert await find_user(session, "@alpha") is not None
        assert await find_user(session, "7") is not None
        assert await find_user(session, "nobody") is None
        await change_balance(session, user, -1)
        assert user.balance == 200
        text = await export_users_text(session)
        assert "ID: 7" in text and "Start" in text
