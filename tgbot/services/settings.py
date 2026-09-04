from __future__ import annotations

import logging
from dataclasses import dataclass, fields

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import Config
from tgbot.database.models import Setting

logger = logging.getLogger(__name__)


@dataclass
class RuntimeSettings:
    stars_enabled: bool
    stars_rate: float
    cryptobot_enabled: bool
    cryptobot_token: str
    cryptobot_network: str
    cryptobot_currency_type: str
    cryptobot_asset: str
    usd_rate: float
    ai_model: str
    ai_analyze_images: bool
    ai_max_images: int
    free_searches: int
    default_min_rating: int
    default_check_interval: int
    avito_proxy: str
    avito_request_delay: float
    listings_per_search: int
    ai_candidates_per_search: int
    premium_emoji: bool

    @classmethod
    def from_config(cls, config: Config) -> "RuntimeSettings":
        return cls(
            stars_enabled=config.payments.stars_enabled,
            stars_rate=config.payments.stars_rate,
            cryptobot_enabled=config.payments.cryptobot_enabled,
            cryptobot_token=config.payments.cryptobot_token,
            cryptobot_network=config.payments.cryptobot_network,
            cryptobot_currency_type=config.payments.cryptobot_currency_type,
            cryptobot_asset=config.payments.cryptobot_asset,
            usd_rate=config.payments.usd_rate,
            ai_model=config.ai.model,
            ai_analyze_images=config.ai.analyze_images,
            ai_max_images=config.ai.max_images,
            free_searches=config.limits.free_searches,
            default_min_rating=config.limits.default_min_rating,
            default_check_interval=config.avito.default_check_interval,
            avito_proxy=config.avito.proxy,
            avito_request_delay=config.avito.request_delay,
            listings_per_search=config.avito.listings_per_search,
            ai_candidates_per_search=config.avito.ai_candidates_per_search,
            premium_emoji=config.bot.premium_emoji,
        )


def _coerce(current: object, raw: str) -> object:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(float(raw))
    if isinstance(current, float):
        return float(raw)
    return raw


class SettingsService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], config: Config) -> None:
        self._session_factory = session_factory
        self.values = RuntimeSettings.from_config(config)

    @property
    def keys(self) -> list[str]:
        return [f.name for f in fields(RuntimeSettings)]

    async def load(self) -> None:
        async with self._session_factory() as session:
            rows = (await session.execute(select(Setting))).scalars().all()
        for row in rows:
            if not hasattr(self.values, row.key):
                continue
            try:
                setattr(self.values, row.key, _coerce(getattr(self.values, row.key), row.value))
            except ValueError:
                logger.warning("bad stored setting %s=%r, ignored", row.key, row.value)
        logger.info("runtime settings loaded (%d overrides)", len(rows))

    async def set(self, key: str, value: object) -> None:
        if not hasattr(self.values, key):
            raise KeyError(key)
        current = getattr(self.values, key)
        coerced = _coerce(current, str(value)) if isinstance(value, str) else value
        setattr(self.values, key, coerced)
        async with self._session_factory() as session:
            row = await session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=str(coerced)))
            else:
                row.value = str(coerced)
            await session.commit()

    async def toggle(self, key: str) -> bool:
        new_value = not bool(getattr(self.values, key))
        await self.set(key, new_value)
        return new_value
