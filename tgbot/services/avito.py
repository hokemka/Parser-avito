from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

logger = logging.getLogger(__name__)

WEB_BASE = "https://www.avito.ru"
SORT_BY_DATE = 104
RUSSIA_LOCATION_ID = 621540

BLOCK_TITLE_MARKERS = ("Доступ ограничен", "Access denied", "Доступ временно ограничен")
BLOCK_TEXT_MARKERS = (
    "проблема с IP",
    "Доступ с вашего IP",
    "Подтвердите, что вы не робот",
    "подозрительную активность",
    "geetest_captcha",
    "firewall-title",
)
EMPTY_RESULT_MARKERS = ("ничего не найдено", "ничего не нашлось", "по вашему запросу нет объявлений")

INITIAL_DATA_RE = re.compile(r'window\.__initialData__\s*=\s*"(.*?)";', re.DOTALL)
MFE_STATE_RE = re.compile(r'<script[^>]*data-mfe-state="true"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
OG_DESCRIPTION_RE = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"', re.IGNORECASE)
JSON_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)
IMAGE_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
ITEM_ID_RE = re.compile(r"_(\d{6,})(?:[/?#]|$)")
RELATIVE_DATE_RE = re.compile(r"(\d+)\s+(минут|мин|час|дн|день|дня|дней|недел)")
CLOCK_RE = re.compile(r"(\d{1,2}):(\d{2})")

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

SEARCH_CARDS_JS = """
() => Array.from(document.querySelectorAll('[data-marker="item"]')).map((card) => {
  const pick = (selectors) => {
    for (const selector of selectors) {
      const node = card.querySelector(selector);
      if (node) return node;
    }
    return null;
  };
  const titleNode = pick(['[data-marker="item-title"]', 'a[itemprop="url"]', 'h3 a', 'a[href*="_"]']);
  const priceMeta = pick(['[data-marker="item-price"] meta[itemprop="price"]', 'meta[itemprop="price"]']);
  const priceNode = pick(['[data-marker="item-price"]', '[itemprop="price"]']);
  const descriptionMeta = pick(['meta[itemprop="description"]']);
  const descriptionNode = pick(['[data-marker="item-description"]', '[data-marker="item-specific-params"]']);
  const image = pick(['[data-marker="item-photo"] img', 'img[itemprop="image"]', 'img']);
  const link = titleNode || pick(['a[href]']);
  return {
    id: card.getAttribute('data-item-id') || (card.id || '').replace(/^i/, ''),
    title: titleNode ? (titleNode.getAttribute('title') || titleNode.textContent || '').trim() : '',
    href: link ? link.getAttribute('href') : '',
    price: priceMeta ? priceMeta.getAttribute('content') : (priceNode ? priceNode.textContent : ''),
    address: (pick(['[data-marker="item-address"]', '[data-marker="item-address/georeferences"]', '[data-marker="item-location"]']) || {}).textContent || '',
    date: (pick(['[data-marker="item-date"]']) || {}).textContent || '',
    description: descriptionMeta ? descriptionMeta.getAttribute('content') : (descriptionNode ? descriptionNode.textContent : ''),
    image: image ? (image.getAttribute('src') || image.getAttribute('data-src') || (image.getAttribute('srcset') || '').split(' ')[0] || '') : '',
  };
})
"""

ITEM_PAGE_JS = """
() => {
  const pick = (selectors) => {
    for (const selector of selectors) {
      const node = document.querySelector(selector);
      if (node) return node;
    }
    return null;
  };
  const text = (node) => (node ? (node.textContent || '').trim() : '');
  const priceMeta = pick(['[data-marker="item-view/item-price"] meta[itemprop="price"]', 'meta[itemprop="price"]', '[itemprop="price"]']);
  const params = Array.from(document.querySelectorAll('[data-marker="item-view/item-params"] li, [data-marker="item-params"] li, [data-marker="item-view/item-params"] p'))
    .map((node) => (node.textContent || '').trim()).filter(Boolean);
  const images = Array.from(document.querySelectorAll('[data-marker="image-frame/image"], [data-marker="image-preview/item"] img, img[itemprop="image"], [data-marker="item-view/gallery"] img'))
    .map((node) => node.getAttribute('src') || node.getAttribute('data-src') || (node.getAttribute('srcset') || '').split(' ')[0] || '')
    .filter(Boolean);
  return {
    title: text(pick(['h1[data-marker="item-view/title-info"]', 'h1'])),
    price: priceMeta ? (priceMeta.getAttribute('content') || priceMeta.textContent) : text(pick(['[data-marker="item-view/item-price"]'])),
    description: text(pick(['[data-marker="item-view/item-description"]', '[itemprop="description"]'])),
    address: text(pick(['[data-marker="item-view/item-address"]', '[itemprop="address"]'])),
    seller: text(pick(['[data-marker="seller-info/name"]', '[data-marker="seller-link/link"]', '[data-marker="seller-info/label"]'])),
    seller_type: text(pick(['[data-marker="seller-info/label"]'])),
    params: params,
    images: images,
  };
}
"""


class AvitoError(Exception):
    pass


class AvitoBlockedError(AvitoError):
    pass


class AvitoUnavailableError(AvitoError):
    pass


@dataclass(slots=True)
class Location:
    name: str
    id: int | None = None
    slug: str | None = None
    parent: str = ""

    @property
    def full_name(self) -> str:
        if self.parent and self.parent != self.name:
            return f"{self.name}, {self.parent}"
        return self.name

    @property
    def web_slug(self) -> str:
        return self.slug or slugify_location(self.name)


@dataclass(slots=True)
class Listing:
    id: int
    title: str
    price: int | None
    url: str
    images: list[str] = field(default_factory=list)
    location: str = ""
    published_at: datetime | None = None
    description: str = ""
    seller_name: str = ""
    seller_type: str = ""
    category: str = ""
    params: dict[str, str] = field(default_factory=dict)
    source: str = "state"

    @property
    def cover(self) -> str | None:
        return self.images[0] if self.images else None

    def to_json(self) -> str:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "Listing":
        data = json.loads(raw)
        published = data.get("published_at")
        data["published_at"] = datetime.fromisoformat(published) if published else None
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__ if key in data})


POPULAR_LOCATIONS: tuple[Location, ...] = (
    Location(name="Москва", id=637640, slug="moskva"),
    Location(name="Санкт-Петербург", id=653240, slug="sankt-peterburg"),
    Location(name="Московская область", id=637680, slug="moskovskaya_oblast"),
    Location(name="Вся Россия", id=RUSSIA_LOCATION_ID, slug="all"),
    Location(name="Новосибирск", id=641780, slug="novosibirsk"),
    Location(name="Екатеринбург", slug="ekaterinburg"),
    Location(name="Казань", id=650400, slug="kazan"),
    Location(name="Нижний Новгород", id=640860, slug="nizhniy_novgorod"),
    Location(name="Краснодар", slug="krasnodar"),
    Location(name="Ростов-на-Дону", slug="rostov-na-donu"),
    Location(name="Самара", id=653040, slug="samara"),
    Location(name="Челябинск", slug="chelyabinsk"),
    Location(name="Уфа", slug="ufa"),
    Location(name="Красноярск", slug="krasnoyarsk"),
    Location(name="Воронеж", slug="voronezh"),
    Location(name="Пермь", slug="perm"),
)


def slugify_location(name: str) -> str:
    result: list[str] = []
    for char in name.strip().lower():
        if char in TRANSLIT:
            result.append(TRANSLIT[char])
        elif char.isalnum():
            result.append(char)
        elif char == "-":
            result.append("-")
        elif char.isspace():
            result.append("_")
    slug = "".join(result)
    return re.sub(r"_+", "_", slug).strip("_-") or "all"


def build_search_url(query: str, slug: str, price_min: int | None = None, price_max: int | None = None, page: int = 1, base: str = WEB_BASE) -> str:
    params: dict[str, Any] = {"q": query, "s": SORT_BY_DATE}
    if price_min:
        params["pmin"] = price_min
    if price_max:
        params["pmax"] = price_max
    if page > 1:
        params["p"] = page
    return f"{base}/{slug}?{urlencode(params, quote_via=quote)}"


def build_slocations_url(query: str, limit: int = 8, base: str = WEB_BASE) -> str:
    return f"{base}/web/1/slocations?{urlencode({'locationId': RUSSIA_LOCATION_ID, 'limit': limit, 'q': query}, quote_via=quote)}"


def _pick_largest_image(images: Any) -> str | None:
    if not images:
        return None
    if isinstance(images, str):
        return images
    if isinstance(images, dict):
        if isinstance(images.get("main"), dict):
            return _pick_largest_image(images["main"])
        best_area, best_url = -1, None
        for size, url in images.items():
            if not isinstance(url, str):
                continue
            match = IMAGE_SIZE_RE.match(str(size))
            area = int(match.group(1)) * int(match.group(2)) if match else 0
            if area > best_area:
                best_area, best_url = area, url
        if best_url:
            return best_url
        for key in ("url", "src", "originalUrl", "1280x960", "640x480"):
            if isinstance(images.get(key), str):
                return images[key]
        return None
    if isinstance(images, list):
        for candidate in images:
            picked = _pick_largest_image(candidate)
            if picked:
                return picked
    return None


def _normalize_image_url(url: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{WEB_BASE}{url}"
    return url


def _extract_images(raw: Any, limit: int = 6) -> list[str]:
    urls: list[str] = []
    if isinstance(raw, dict) and isinstance(raw.get("main"), dict):
        raw = [raw["main"]]
    if isinstance(raw, dict) and isinstance(raw.get("images"), list):
        raw = raw["images"]
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("value"), dict):
                entry = entry["value"]
            picked = _pick_largest_image(entry)
            if picked and picked not in urls:
                urls.append(picked)
            if len(urls) >= limit:
                break
    elif raw:
        picked = _pick_largest_image(raw)
        if picked:
            urls.append(picked)
    return [_normalize_image_url(url) for url in urls if url.startswith(("http", "/"))]


def _parse_price(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if isinstance(value, dict):
        for key in ("value", "amount", "price", "string", "value_signed"):
            if key in value:
                parsed = _parse_price(value[key])
                if parsed:
                    return parsed
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else None


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1e11:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_relative_date(text: str, now: datetime | None = None) -> datetime | None:
    text = " ".join(text.lower().split())
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    if "только что" in text or "сейчас" in text:
        return now
    match = RELATIVE_DATE_RE.search(text)
    if match and "назад" in text:
        amount = int(match.group(1))
        unit = match.group(2)
        if unit.startswith("мин"):
            return now - timedelta(minutes=amount)
        if unit.startswith("час"):
            return now - timedelta(hours=amount)
        if unit.startswith("недел"):
            return now - timedelta(weeks=amount)
        return now - timedelta(days=amount)
    clock = CLOCK_RE.search(text)
    hour, minute = (int(clock.group(1)), int(clock.group(2))) if clock else (12, 0)
    if "сегодня" in text:
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if "вчера" in text:
        return (now - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    for month_name, month in RU_MONTHS.items():
        if month_name in text:
            day_match = re.search(r"(\d{1,2})\s+" + month_name, text)
            if not day_match:
                continue
            day = int(day_match.group(1))
            year_match = re.search(month_name + r"\s+(\d{4})", text)
            year = int(year_match.group(1)) if year_match else now.year
            try:
                candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
            except ValueError:
                return None
            if candidate > now + timedelta(days=1):
                candidate = candidate.replace(year=year - 1)
            return candidate
    return None


def _absolute_url(path: str | None, listing_id: int, base: str = WEB_BASE) -> str:
    if not path:
        return f"{base}/{listing_id}"
    if path.startswith("http"):
        return path
    return f"{base}/{path.lstrip('/')}"


def _location_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "title", "formattedAddress", "namePrepositional"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def _listing_id_from(value: Any, url: str = "") -> int | None:
    if isinstance(value, dict):
        value = value.get("id") or value.get("value")
    try:
        if value is not None and str(value).strip():
            return int(str(value).strip())
    except ValueError:
        pass
    match = ITEM_ID_RE.search(url or "")
    return int(match.group(1)) if match else None


def extract_state_blobs(html: str) -> list[Any]:
    blobs: list[Any] = []
    for match in INITIAL_DATA_RE.finditer(html):
        try:
            blobs.append(json.loads(unquote(match.group(1))))
        except json.JSONDecodeError:
            logger.debug("bad __initialData__ blob")
    for match in MFE_STATE_RE.finditer(html):
        raw = html_lib.unescape(match.group(1)).strip()
        if not raw:
            continue
        try:
            blobs.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.debug("bad mfe-state blob")
    return blobs


def _looks_like_catalog(items: Any) -> bool:
    if not isinstance(items, list) or not items:
        return False
    sample = next((entry for entry in items if isinstance(entry, dict)), None)
    return bool(sample) and "id" in sample and any(key in sample for key in ("title", "urlPath", "priceDetailed"))


def find_catalog_items(data: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 12:
        return []
    if isinstance(data, dict):
        catalog = data.get("catalog")
        if isinstance(catalog, dict) and _looks_like_catalog(catalog.get("items")):
            return [entry for entry in catalog["items"] if isinstance(entry, dict)]
        if _looks_like_catalog(data.get("items")):
            return [entry for entry in data["items"] if isinstance(entry, dict)]
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = find_catalog_items(value, depth + 1)
                if found:
                    return found
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, (dict, list)):
                found = find_catalog_items(value, depth + 1)
                if found:
                    return found
    return []


def parse_catalog_item(item: dict[str, Any], base: str = WEB_BASE) -> Listing | None:
    entry_type = item.get("type")
    if entry_type not in (None, "item", "xlItem", "vipItem", "premiumItem"):
        return None
    if item.get("isReserved") or item.get("closed") or item.get("isClosed"):
        return None
    url_path = item.get("urlPath") or item.get("url") or item.get("uri_mweb")
    listing_id = _listing_id_from(item.get("id"), str(url_path or ""))
    if listing_id is None:
        return None
    seller = item.get("sellerInfo") if isinstance(item.get("sellerInfo"), dict) else {}
    category = item.get("category") if isinstance(item.get("category"), dict) else {}
    images = _extract_images(item.get("images")) or _extract_images(item.get("gallery")) or _extract_images(item.get("galleryItems"))
    location = _location_name(item.get("location")) or _location_name(item.get("geo")) or str(item.get("address") or "")
    published = _parse_timestamp(item.get("sortTimeStamp") or item.get("time") or item.get("timeStamp"))
    if published is None and isinstance(item.get("iva"), dict):
        date_step = item["iva"].get("DateInfoStep")
        if isinstance(date_step, list):
            for step in date_step:
                payload = step.get("payload") if isinstance(step, dict) else None
                if isinstance(payload, dict) and isinstance(payload.get("absolute"), str):
                    published = parse_relative_date(payload["absolute"])
                    break
    return Listing(
        id=listing_id,
        title=str(item.get("title") or "Без названия").strip(),
        price=_parse_price(item.get("priceDetailed")) or _parse_price(item.get("price")),
        url=_absolute_url(url_path if isinstance(url_path, str) else None, listing_id, base),
        images=images,
        location=location,
        published_at=published,
        description=str(item.get("description") or "").strip(),
        seller_name=str(seller.get("name") or "").strip(),
        seller_type=str(seller.get("userType") or seller.get("type") or item.get("userType") or "").strip(),
        category=str(category.get("name") or "").strip(),
        source="state",
    )


def parse_search_page(html: str, base: str = WEB_BASE) -> list[Listing]:
    listings: list[Listing] = []
    for blob in extract_state_blobs(html):
        for item in find_catalog_items(blob):
            parsed = parse_catalog_item(item, base)
            if parsed:
                listings.append(parsed)
        if listings:
            break
    return listings


def parse_dom_cards(cards: Any, now: datetime | None = None, base: str = WEB_BASE) -> list[Listing]:
    if not isinstance(cards, list):
        return []
    listings: list[Listing] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        href = str(card.get("href") or "")
        listing_id = _listing_id_from(card.get("id"), href)
        if listing_id is None:
            continue
        title = " ".join(str(card.get("title") or "").split()) or "Без названия"
        image = str(card.get("image") or "")
        listings.append(Listing(
            id=listing_id,
            title=title,
            price=_parse_price(card.get("price")),
            url=_absolute_url(href or None, listing_id, base),
            images=[_normalize_image_url(image)] if image.startswith(("http", "/")) else [],
            location=" ".join(str(card.get("address") or "").split()),
            published_at=parse_relative_date(str(card.get("date") or ""), now),
            description=" ".join(str(card.get("description") or "").split()),
            source="dom",
        ))
    return listings


def _params_from_list(raw: Any) -> dict[str, str]:
    params: dict[str, str] = {}
    if not isinstance(raw, list):
        return params
    for entry in raw:
        if isinstance(entry, dict):
            title = entry.get("title") or entry.get("name") or entry.get("label")
            value = entry.get("description") or entry.get("value") or entry.get("text")
            if title and value and isinstance(value, (str, int, float)):
                params[str(title).strip()] = str(value).strip()
        elif isinstance(entry, str) and ":" in entry:
            title, value = entry.split(":", 1)
            if title.strip() and value.strip():
                params[title.strip()] = value.strip()
    return params


def find_item_view(data: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > 12:
        return None
    if isinstance(data, dict):
        description = data.get("description")
        if isinstance(description, str) and description.strip() and ("title" in data or "price" in data or "priceDetailed" in data):
            return data
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = find_item_view(value, depth + 1)
                if found:
                    return found
    elif isinstance(data, list):
        for value in data:
            if isinstance(value, (dict, list)):
                found = find_item_view(value, depth + 1)
                if found:
                    return found
    return None


def apply_item_view(view: dict[str, Any], listing: Listing) -> Listing:
    description = view.get("description")
    if isinstance(description, str) and description.strip():
        listing.description = description.strip()
    title = view.get("title")
    if isinstance(title, str) and title.strip():
        listing.title = title.strip()
    price = _parse_price(view.get("priceDetailed")) or _parse_price(view.get("price"))
    if price:
        listing.price = price
    seller = view.get("seller") if isinstance(view.get("seller"), dict) else view.get("sellerInfo") if isinstance(view.get("sellerInfo"), dict) else {}
    if seller:
        listing.seller_name = str(seller.get("name") or seller.get("title") or listing.seller_name).strip()
        listing.seller_type = str(seller.get("userType") or seller.get("type") or listing.seller_type).strip()
    address = view.get("address") or _location_name(view.get("location")) or _location_name(view.get("geo"))
    if isinstance(address, str) and address.strip():
        listing.location = address.strip()
    images = _extract_images(view.get("images")) or _extract_images(view.get("gallery")) or _extract_images(view.get("imagesInfo"))
    if images:
        listing.images = images
    for key in ("parameters", "paramsBlock", "attributes", "params"):
        raw = view.get(key)
        if isinstance(raw, dict):
            raw = raw.get("flat") or raw.get("items") or raw.get("list")
        params = _params_from_list(raw)
        if params:
            listing.params.update(params)
    published = _parse_timestamp(view.get("time") or view.get("sortTimeStamp"))
    if published:
        listing.published_at = published
    return listing


def parse_item_page(html: str, listing: Listing) -> Listing:
    for blob in extract_state_blobs(html):
        view = find_item_view(blob)
        if view:
            return apply_item_view(view, listing)
    for block in JSON_LD_RE.findall(html):
        try:
            payload = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for entry in payload if isinstance(payload, list) else [payload]:
            if not isinstance(entry, dict):
                continue
            description = entry.get("description")
            if isinstance(description, str) and description.strip():
                listing.description = description.strip()
            offers = entry.get("offers") if isinstance(entry.get("offers"), dict) else {}
            price = _parse_price(offers.get("price"))
            if price:
                listing.price = price
            image = entry.get("image")
            images = _extract_images(image if isinstance(image, list) else [image] if image else [])
            if images and not listing.images:
                listing.images = images
    if not listing.description:
        match = OG_DESCRIPTION_RE.search(html)
        if match:
            listing.description = html_lib.unescape(match.group(1)).strip()
    return listing


def apply_dom_item(dom: Any, listing: Listing) -> Listing:
    if not isinstance(dom, dict):
        return listing
    description = " ".join(str(dom.get("description") or "").split())
    if description and (not listing.description or len(description) > len(listing.description)):
        listing.description = description
    title = " ".join(str(dom.get("title") or "").split())
    if title and (listing.title in ("", "Без названия") or len(title) > len(listing.title)):
        listing.title = title
    price = _parse_price(dom.get("price"))
    if price:
        listing.price = price
    address = " ".join(str(dom.get("address") or "").split())
    if address:
        listing.location = address
    seller = " ".join(str(dom.get("seller") or "").split())
    if seller and not listing.seller_name:
        listing.seller_name = seller
    seller_type = " ".join(str(dom.get("seller_type") or "").split())
    if seller_type and not listing.seller_type:
        listing.seller_type = seller_type
    params = _params_from_list(dom.get("params"))
    if params:
        listing.params.update(params)
    images = [_normalize_image_url(str(url)) for url in dom.get("images") or [] if str(url).startswith(("http", "/"))]
    if images and not listing.images:
        listing.images = images[:6]
    return listing


def parse_locations(data: Any) -> list[Location]:
    result = data.get("result") if isinstance(data, dict) and isinstance(data.get("result"), dict) else data
    raw_locations = result.get("locations") if isinstance(result, dict) else None
    if not isinstance(raw_locations, list):
        return []
    locations: list[Location] = []
    for raw in raw_locations:
        if not isinstance(raw, dict) or raw.get("id") is None:
            continue
        names = raw.get("names") if isinstance(raw.get("names"), dict) else {}
        name = names.get("1") or raw.get("name") or raw.get("title")
        if not name:
            continue
        parent_raw = raw.get("parent") if isinstance(raw.get("parent"), dict) else {}
        parent_names = parent_raw.get("names") if isinstance(parent_raw.get("names"), dict) else {}
        parent = parent_names.get("1") or parent_raw.get("name") or ""
        try:
            location_id = int(raw["id"])
        except (TypeError, ValueError):
            continue
        slug = raw.get("slug") if isinstance(raw.get("slug"), str) else slugify_location(str(name))
        locations.append(Location(name=str(name), id=location_id, slug=slug, parent=str(parent)))
    return locations


def page_title(html: str) -> str:
    match = TITLE_RE.search(html)
    return html_lib.unescape(match.group(1)).strip() if match else ""


def is_block_page(html: str, status: int = 200) -> bool:
    if status in (403, 429):
        return True
    head = html[:20000]
    title = page_title(head)
    if any(marker.lower() in title.lower() for marker in BLOCK_TITLE_MARKERS):
        return True
    return any(marker.lower() in head.lower() for marker in BLOCK_TEXT_MARKERS)


def is_empty_results_page(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in EMPTY_RESULT_MARKERS)


def parse_proxy(raw: str) -> dict[str, str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"http://{raw}"
    parts = urlsplit(raw)
    if not parts.hostname:
        return None
    proxy = {"server": f"{parts.scheme}://{parts.hostname}:{parts.port or 8080}"}
    if parts.username:
        proxy["username"] = unquote(parts.username)
    if parts.password:
        proxy["password"] = unquote(parts.password)
    return proxy


@dataclass
class BrowserConfig:
    engine: str = "camoufox"
    headless: str = "true"
    proxy: str = ""
    profile_dir: Path = Path("data/browser_profile")
    locale: str = "ru-RU"
    os_name: str = "windows"
    block_images: bool = True
    humanize: bool = True
    geoip: bool = True
    page_timeout: float = 45.0
    chromium_path: str = ""

    @property
    def headless_value(self) -> bool | str:
        lowered = self.headless.strip().lower()
        if lowered == "virtual":
            return "virtual"
        return lowered in ("1", "true", "yes", "on")


@dataclass(slots=True)
class FetchedPage:
    url: str
    final_url: str
    status: int
    html: str
    dom: Any = None


class AvitoBrowser:
    def __init__(self, config: BrowserConfig) -> None:
        self.config = config
        self._context: Any = None
        self._camoufox: Any = None
        self._playwright: Any = None
        self._lock = asyncio.Lock()
        self.started_at: float | None = None
        self.pages_fetched = 0

    @property
    def is_running(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        if self._context is not None:
            return
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        proxy = parse_proxy(self.config.proxy)
        if self.config.engine == "chromium":
            await self._start_chromium(proxy)
        else:
            await self._start_camoufox(proxy)
        self.started_at = time.monotonic()
        logger.info("avito browser started (engine=%s, proxy=%s)", self.config.engine, "yes" if proxy else "no")

    async def _start_camoufox(self, proxy: dict[str, str] | None) -> None:
        from camoufox.async_api import AsyncCamoufox

        options: dict[str, Any] = {
            "headless": self.config.headless_value,
            "locale": self.config.locale,
            "os": self.config.os_name,
            "block_images": self.config.block_images,
            "humanize": self.config.humanize,
            "persistent_context": True,
            "user_data_dir": str(self.config.profile_dir),
            "i_know_what_im_doing": True,
        }
        if proxy:
            options["proxy"] = proxy
            options["geoip"] = self.config.geoip
        self._camoufox = AsyncCamoufox(**options)
        self._context = await self._camoufox.__aenter__()

    async def _start_chromium(self, proxy: dict[str, str] | None) -> None:
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        options: dict[str, Any] = {
            "headless": self.config.headless_value is not False,
            "locale": self.config.locale,
            "viewport": {"width": 1366, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if proxy:
            options["proxy"] = proxy
        if self.config.chromium_path:
            options["executable_path"] = self.config.chromium_path
        self._context = await self._playwright.chromium.launch_persistent_context(str(self.config.profile_dir), **options)
        if self.config.block_images:
            await self._context.route("**/*", lambda route: asyncio.ensure_future(
                route.abort() if route.request.resource_type in ("image", "media", "font") else route.continue_()
            ))

    async def stop(self) -> None:
        context, camoufox, playwright = self._context, self._camoufox, self._playwright
        self._context = self._camoufox = self._playwright = None
        try:
            if camoufox is not None:
                await camoufox.__aexit__(None, None, None)
            elif context is not None:
                await context.close()
        except Exception as exc:
            logger.info("browser close error: %s", exc)
        try:
            if playwright is not None:
                await playwright.stop()
        except Exception as exc:
            logger.info("playwright stop error: %s", exc)

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def fetch(self, url: str, script: str | None = None, wait_selector: str | None = None, settle_ms: int = 800) -> FetchedPage:
        async with self._lock:
            await self.start()
            page = await self._context.new_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=self.config.page_timeout * 1000)
                status = response.status if response else 0
                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=min(15000, self.config.page_timeout * 500))
                    except Exception:
                        logger.debug("selector %s did not appear on %s", wait_selector, url)
                await page.wait_for_timeout(settle_ms)
                html = await page.content()
                if is_block_page(html, status):
                    await page.wait_for_timeout(6000)
                    html = await page.content()
                    status = 200 if not is_block_page(html, 200) and status in (403, 429) else status
                dom = None
                if script and not is_block_page(html, status):
                    try:
                        dom = await page.evaluate(script)
                    except Exception as exc:
                        logger.info("dom script failed on %s: %s", url, exc)
                self.pages_fetched += 1
                return FetchedPage(url=url, final_url=page.url, status=status, html=html, dom=dom)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass

    async def fetch_json(self, url: str, referer: str = f"{WEB_BASE}/") -> Any:
        async with self._lock:
            await self.start()
            page = await self._context.new_page()
            try:
                await page.goto(referer, wait_until="domcontentloaded", timeout=self.config.page_timeout * 1000)
                text = await page.evaluate(
                    "url => fetch(url, {credentials: 'include', headers: {accept: 'application/json'}}).then(r => r.text())",
                    url,
                )
                return json.loads(text)
            finally:
                try:
                    await page.close()
                except Exception:
                    pass


class AvitoClient:
    def __init__(self, browser: AvitoBrowser | None = None, request_delay: float = 2.0, block_cooldown: int = 600, base_url: str = WEB_BASE) -> None:
        self.browser = browser or AvitoBrowser(BrowserConfig(engine="chromium"))
        self.request_delay = request_delay
        self.block_cooldown = block_cooldown
        self.base_url = base_url.rstrip("/")
        self._last_request_at = 0.0
        self._restart_needed = False
        self.blocked_until = 0.0
        self.last_error: str | None = None
        self.blocks_count = 0
        self.requests_count = 0

    @property
    def proxy(self) -> str | None:
        return self.browser.config.proxy or None

    @property
    def is_blocked(self) -> bool:
        return time.monotonic() < self.blocked_until

    def configure(self, proxy: str, request_delay: float) -> None:
        proxy = proxy or ""
        if proxy != self.browser.config.proxy:
            self.browser.config.proxy = proxy
            self._restart_needed = True
        self.request_delay = request_delay

    async def close(self) -> None:
        await self.browser.stop()

    def status(self) -> dict[str, Any]:
        return {
            "engine": self.browser.config.engine,
            "running": self.browser.is_running,
            "proxy": bool(self.browser.config.proxy),
            "requests": self.requests_count,
            "pages": self.browser.pages_fetched,
            "blocks": self.blocks_count,
            "blocked": self.is_blocked,
            "last_error": self.last_error,
        }

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        delay = self.request_delay + random.uniform(0.2, 1.2)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_at = time.monotonic()

    def _mark_blocked(self, reason: str) -> AvitoBlockedError:
        self.blocked_until = time.monotonic() + self.block_cooldown
        self.blocks_count += 1
        self.last_error = reason
        logger.warning("avito blocked us: %s (cooldown %ss)", reason, self.block_cooldown)
        return AvitoBlockedError(reason)

    async def _fetch(self, url: str, script: str | None = None, wait_selector: str | None = None) -> FetchedPage:
        if self.is_blocked:
            raise AvitoBlockedError(self.last_error or "temporary block")
        if self._restart_needed:
            self._restart_needed = False
            await self.browser.stop()
        await self._throttle()
        last_error = "unknown"
        for attempt in range(2):
            try:
                page = await self.browser.fetch(url, script=script, wait_selector=wait_selector)
            except AvitoError:
                raise
            except Exception as exc:
                first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                last_error = f"{type(exc).__name__}: {first_line[:160]}"
                logger.warning("browser fetch failed (%s/2) %s: %s", attempt + 1, url, last_error)
                await self.browser.stop()
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            self.requests_count += 1
            if is_block_page(page.html, page.status):
                raise self._mark_blocked(f"HTTP {page.status}: {page_title(page.html) or 'доступ ограничен'}")
            self.last_error = None
            return page
        self.last_error = last_error
        raise AvitoUnavailableError(last_error)

    async def search(
        self,
        query: str,
        location: Location,
        price_min: int | None = None,
        price_max: int | None = None,
        pages: int = 1,
        limit: int = 50,
    ) -> list[Listing]:
        collected: list[Listing] = []
        for page_no in range(1, max(1, pages) + 1):
            url = build_search_url(query, location.web_slug, price_min, price_max, page_no, self.base_url)
            page = await self._fetch(url, script=SEARCH_CARDS_JS, wait_selector='[data-marker="item"]')
            listings = parse_search_page(page.html, self.base_url) or parse_dom_cards(page.dom, base=self.base_url)
            if not listings:
                if page_no == 1 and not is_empty_results_page(page.html):
                    self.last_error = "не удалось разобрать страницу выдачи"
                    raise AvitoUnavailableError("search page has no listings data")
                break
            collected.extend(listings)
            if len(collected) >= limit or len(listings) < 10:
                break
        return _filter_by_price(_dedupe(collected), price_min, price_max)[:limit]

    async def fetch_details(self, listing: Listing) -> Listing:
        page = await self._fetch(listing.url, script=ITEM_PAGE_JS, wait_selector='[data-marker="item-view/item-description"]')
        listing = parse_item_page(page.html, listing)
        return apply_dom_item(page.dom, listing)

    async def find_locations(self, query: str, limit: int = 8) -> list[Location]:
        query = query.strip()
        if not query:
            return []
        lowered = query.lower()
        local = [loc for loc in POPULAR_LOCATIONS if lowered in loc.name.lower()]
        remote: list[Location] = []
        if not self.is_blocked:
            try:
                await self._throttle()
                data = await self.browser.fetch_json(build_slocations_url(query, limit, self.base_url), referer=f"{self.base_url}/")
                remote = parse_locations(data)
            except Exception as exc:
                logger.info("slocations lookup failed: %s", exc)
        merged: list[Location] = []
        seen: set[str] = set()
        for loc in [*remote, *local]:
            marker = f"{loc.id}:{loc.name.lower()}"
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(loc)
        return merged[:limit]

    async def resolve_location(self, location: Location) -> Location:
        if location.id is not None:
            return location
        for candidate in await self.find_locations(location.name):
            if candidate.id is not None and candidate.name.lower() == location.name.lower():
                return Location(name=location.name, id=candidate.id, slug=location.slug or candidate.slug, parent=candidate.parent)
        return location


def _dedupe(listings: list[Listing]) -> list[Listing]:
    seen: set[int] = set()
    unique: list[Listing] = []
    for listing in listings:
        if listing.id in seen:
            continue
        seen.add(listing.id)
        unique.append(listing)
    return unique


def _filter_by_price(listings: list[Listing], price_min: int | None, price_max: int | None) -> list[Listing]:
    result: list[Listing] = []
    for listing in listings:
        if listing.price is None:
            result.append(listing)
            continue
        if price_min and listing.price < price_min:
            continue
        if price_max and listing.price > price_max:
            continue
        result.append(listing)
    return result
