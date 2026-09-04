from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape

PRICE_RE = re.compile(r"\d[\d\s]*")


def h(value: object) -> str:
    return escape(str(value), quote=False)


def format_price(value: int | float | None, currency: str = "₽") -> str:
    if value is None:
        return "цена не указана"
    return f"{int(round(value)):,}".replace(",", " ") + f" {currency}"


def format_money(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return f"{int(rounded):,}".replace(",", " ") + " ₽"
    return f"{rounded:,.2f}".replace(",", " ") + " ₽"


def parse_price_range(raw: str) -> tuple[int | None, int | None] | None:
    text = raw.lower().replace("₽", " ").replace("руб", " ").replace("р.", " ")
    if any(word in text for word in ("любая", "любой", "неважно", "без ограничений", "пропустить")):
        return None, None
    thousands = bool(re.search(r"(тыс|\d\s*к\b|\d\s*k\b)", text))
    numbers = [int(n.replace(" ", "")) for n in PRICE_RE.findall(text) if n.strip()]
    if not numbers:
        return None
    if thousands:
        numbers = [n * 1000 if n < 1000 else n for n in numbers]
    if len(numbers) == 1:
        value = numbers[0]
        if any(word in text for word in ("до", "макс", "не дороже", "<")):
            return None, value
        if any(word in text for word in ("от", "мин", ">")):
            return value, None
        return int(value * 0.75), int(value * 1.15)
    low, high = sorted(numbers[:2])
    return low, high


def format_price_range(price_min: int | None, price_max: int | None) -> str:
    if price_min is None and price_max is None:
        return "любая"
    if price_min is None:
        return f"до {format_price(price_max)}"
    if price_max is None:
        return f"от {format_price(price_min)}"
    return f"{format_price(price_min)} — {format_price(price_max)}"


def time_ago(moment: datetime | None) -> str:
    if moment is None:
        return "недавно"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - moment
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "только что"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    days = hours // 24
    if days < 30:
        return f"{days} дн назад"
    return moment.strftime("%d.%m.%Y")


def format_dt(moment: datetime | None) -> str:
    if moment is None:
        return "—"
    return moment.strftime("%d.%m.%Y %H:%M")


def rating_bar(rating: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(rating))))
    return "█" * filled + "░" * (width - filled)


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def pluralize(number: int, one: str, few: str, many: str) -> str:
    n = abs(number) % 100
    if 11 <= n <= 19:
        return f"{number} {many}"
    n %= 10
    if n == 1:
        return f"{number} {one}"
    if 2 <= n <= 4:
        return f"{number} {few}"
    return f"{number} {many}"


def parse_int(raw: str) -> int | None:
    cleaned = raw.replace(" ", "").replace(",", ".").strip()
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def parse_float(raw: str) -> float | None:
    cleaned = raw.replace(" ", "").replace(",", ".").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def is_valid_url(raw: str) -> bool:
    return bool(re.match(r"^(https?://|tg://)[^\s]+$", raw.strip(), flags=re.IGNORECASE))
