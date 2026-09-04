from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.ini"


@dataclass(frozen=True)
class BotConfig:
    token: str
    admin_ids: tuple[int, ...]
    support_username: str
    premium_emoji: bool
    proxy: str


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path

    @property
    def url(self) -> str:
        return f"sqlite+aiosqlite:///{self.path}"


@dataclass(frozen=True)
class AvitoConfig:
    engine: str
    headless: str
    proxy: str
    profile_dir: Path
    locale: str
    browser_os: str
    block_images: bool
    humanize: bool
    geoip: bool
    page_timeout: float
    chromium_path: str
    block_cooldown: int
    request_delay: float
    monitor_tick: int
    default_check_interval: int
    max_pages: int
    listings_per_search: int
    ai_candidates_per_search: int


@dataclass(frozen=True)
class AiConfig:
    api_key: str
    model: str
    vision_model: str
    analyze_images: bool
    max_images: int
    timeout: int


@dataclass(frozen=True)
class PaymentsConfig:
    stars_enabled: bool
    stars_rate: float
    cryptobot_enabled: bool
    cryptobot_token: str
    cryptobot_network: str
    cryptobot_currency_type: str
    cryptobot_asset: str
    usd_rate: float


@dataclass(frozen=True)
class LimitsConfig:
    free_searches: int
    default_min_rating: int


@dataclass(frozen=True)
class Config:
    bot: BotConfig
    database: DatabaseConfig
    avito: AvitoConfig
    ai: AiConfig
    payments: PaymentsConfig
    limits: LimitsConfig
    base_dir: Path = field(default=BASE_DIR)


def _resolve_proxy(raw: str) -> str:
    raw = raw.strip()
    if raw.lower() == "env":
        return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""
    return raw


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    ids: list[int] = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.append(int(chunk))
    return tuple(ids)


def load_config(path: Path | str = SETTINGS_PATH) -> Config:
    parser = configparser.ConfigParser(interpolation=None)
    read_files = parser.read(path, encoding="utf-8")
    if not read_files:
        raise FileNotFoundError(f"settings file not found: {path}")

    bot = parser["bot"]
    database = parser["database"]
    avito = parser["avito"]
    ai = parser["ai"]
    payments = parser["payments"]
    limits = parser["limits"]

    db_path = Path(database.get("path", "data/bot.db"))
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(avito.get("profile_dir", "data/browser_profile"))
    if not profile_dir.is_absolute():
        profile_dir = BASE_DIR / profile_dir

    return Config(
        bot=BotConfig(
            token=bot.get("token", "").strip(),
            admin_ids=_parse_admin_ids(bot.get("admin_ids", "")),
            support_username=bot.get("support_username", "").strip().lstrip("@"),
            premium_emoji=bot.getboolean("premium_emoji", fallback=True),
            proxy=_resolve_proxy(bot.get("proxy", "")),
        ),
        database=DatabaseConfig(path=db_path),
        avito=AvitoConfig(
            engine=avito.get("engine", "camoufox").strip().lower(),
            headless=avito.get("headless", "true").strip(),
            proxy=avito.get("proxy", "").strip(),
            profile_dir=profile_dir,
            locale=avito.get("locale", "ru-RU").strip(),
            browser_os=avito.get("browser_os", "windows").strip().lower(),
            block_images=avito.getboolean("block_images", fallback=True),
            humanize=avito.getboolean("humanize", fallback=True),
            geoip=avito.getboolean("geoip", fallback=True),
            page_timeout=avito.getfloat("page_timeout", fallback=45.0),
            chromium_path=avito.get("chromium_path", "").strip(),
            block_cooldown=avito.getint("block_cooldown", fallback=600),
            request_delay=avito.getfloat("request_delay", fallback=2.0),
            monitor_tick=avito.getint("monitor_tick", fallback=30),
            default_check_interval=avito.getint("default_check_interval", fallback=300),
            max_pages=avito.getint("max_pages", fallback=2),
            listings_per_search=avito.getint("listings_per_search", fallback=40),
            ai_candidates_per_search=avito.getint("ai_candidates_per_search", fallback=10),
        ),
        ai=AiConfig(
            api_key=ai.get("api_key", "").strip(),
            model=ai.get("model", "qwen3-8b").strip(),
            vision_model=ai.get("vision_model", "qwen3-vl-flash").strip(),
            analyze_images=ai.getboolean("analyze_images", fallback=False),
            max_images=ai.getint("max_images", fallback=3),
            timeout=ai.getint("timeout", fallback=90),
        ),
        payments=PaymentsConfig(
            stars_enabled=payments.getboolean("stars_enabled", fallback=True),
            stars_rate=payments.getfloat("stars_rate", fallback=1.6),
            cryptobot_enabled=payments.getboolean("cryptobot_enabled", fallback=False),
            cryptobot_token=payments.get("cryptobot_token", "").strip(),
            cryptobot_network=payments.get("cryptobot_network", "mainnet").strip().lower(),
            cryptobot_currency_type=payments.get("cryptobot_currency_type", "fiat").strip().lower(),
            cryptobot_asset=payments.get("cryptobot_asset", "USDT").strip().upper(),
            usd_rate=payments.getfloat("usd_rate", fallback=95.0),
        ),
        limits=LimitsConfig(
            free_searches=limits.getint("free_searches", fallback=3),
            default_min_rating=limits.getint("default_min_rating", fallback=6),
        ),
    )
