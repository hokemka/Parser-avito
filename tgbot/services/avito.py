from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote, urlencode

from curl_cffi.requests import AsyncSession, Response

logger = logging.getLogger(__name__)

MOBILE_API = "https://m.avito.ru/api"
WEB_BASE = "https://www.avito.ru"
DEFAULT_KEY = "af0deccbgcgidddjgnvljitntccdduijhdinfgjgfjir"

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://m.avito.ru",
    "Referer": "https://m.avito.ru/",
}
WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

BLOCK_MARKERS = ("Доступ ограничен", "проблема с IP", "Подтвердите, что вы не робот", "Доступ с вашего IP-адреса временно ограничен")
INITIAL_DATA_RE = re.compile(r"window\.__initialData__\s*=\s*\"(.*?)\";", re.DOTALL)
OG_DESCRIPTION_RE = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"', re.IGNORECASE)
JSON_LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)
IMAGE_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


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
    source: str = "mobile"

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
    Location(name="Вся Россия", id=621540, slug="all"),
    Location(name="Новосибирск", slug="novosibirsk"),
    Location(name="Екатеринбург", slug="ekaterinburg"),
    Location(name="Казань", slug="kazan"),
    Location(name="Нижний Новгород", slug="nizhniy_novgorod"),
    Location(name="Краснодар", slug="krasnodar"),
    Location(name="Ростов-на-Дону", slug="rostov-na-donu"),
    Location(name="Самара", slug="samara"),
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


def _pick_largest_image(images: Any) -> str | None:
    if not images:
        return None
    if isinstance(images, str):
        return images
    if isinstance(images, dict):
        best_area, best_url = -1, None
        for size, url in images.items():
            if not isinstance(url, str):
                continue
            match = IMAGE_SIZE_RE.match(str(size))
            area = int(match.group(1)) * int(match.group(2)) if match else 0
            if area > best_area:
                best_area, best_url = area, url
        return best_url
    if isinstance(images, list):
        for candidate in images:
            picked = _pick_largest_image(candidate)
            if picked:
                return picked
    return None


def _extract_images(raw: Any, limit: int = 6) -> list[str]:
    urls: list[str] = []
    if isinstance(raw, list):
        for entry in raw:
            picked = _pick_largest_image(entry)
            if picked and picked not in urls:
                urls.append(picked)
            if len(urls) >= limit:
                break
    elif raw:
        picked = _pick_largest_image(raw)
        if picked:
            urls.append(picked)
    return [u if u.startswith("http") else f"https:{u}" for u in urls]


def _parse_price(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        for key in ("value", "amount", "price"):
            if key in value:
                return _parse_price(value[key])
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


def _absolute_url(uri: str | None, listing_id: int) -> str:
    if not uri:
        return f"{WEB_BASE}/{listing_id}"
    if uri.startswith("http"):
        return uri
    return f"{WEB_BASE}/{uri.lstrip('/')}"


def _location_name(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("name", "title", "namePrepositional"):
            if isinstance(value.get(key), str):
                return value[key]
    return ""


def parse_mobile_item(entry: dict[str, Any]) -> Listing | None:
    entry_type = str(entry.get("type", "item"))
    if entry_type not in ("item", "xlItem", "xl_item", "vip", "premium"):
        return None
    value = entry.get("value") if isinstance(entry.get("value"), dict) else entry
    listing_id = value.get("id")
    if listing_id is None:
        return None
    try:
        listing_id = int(listing_id)
    except (TypeError, ValueError):
        return None
    price = _parse_price(value.get("priceDetailed")) or _parse_price(value.get("price"))
    seller = value.get("seller") if isinstance(value.get("seller"), dict) else {}
    category = value.get("category") if isinstance(value.get("category"), dict) else {}
    return Listing(
        id=listing_id,
        title=str(value.get("title") or "Без названия").strip(),
        price=price,
        url=_absolute_url(value.get("uri") or value.get("url") or value.get("urlPath"), listing_id),
        images=_extract_images(value.get("images")),
        location=_location_name(value.get("location")) or str(value.get("address") or ""),
        published_at=_parse_timestamp(value.get("time") or value.get("sortTimeStamp")),
        description=str(value.get("description") or "").strip(),
        seller_name=str(seller.get("name") or value.get("sellerName") or "").strip(),
        seller_type=str(value.get("userType") or seller.get("type") or "").strip(),
        category=str(category.get("name") or "").strip(),
        source="mobile",
    )


def parse_web_item(item: dict[str, Any]) -> Listing | None:
    if item.get("type") not in (None, "item", "xlItem"):
        return None
    listing_id = item.get("id")
    if listing_id is None:
        return None
    try:
        listing_id = int(listing_id)
    except (TypeError, ValueError):
        return None
    seller = item.get("sellerInfo") if isinstance(item.get("sellerInfo"), dict) else {}
    category = item.get("category") if isinstance(item.get("category"), dict) else {}
    return Listing(
        id=listing_id,
        title=str(item.get("title") or "Без названия").strip(),
        price=_parse_price(item.get("priceDetailed")) or _parse_price(item.get("price")),
        url=_absolute_url(item.get("urlPath") or item.get("url"), listing_id),
        images=_extract_images(item.get("images")),
        location=_location_name(item.get("location")) or _location_name(item.get("geo")),
        published_at=_parse_timestamp(item.get("sortTimeStamp") or item.get("time")),
        description=str(item.get("description") or "").strip(),
        seller_name=str(seller.get("name") or "").strip(),
        seller_type=str(seller.get("userType") or seller.get("type") or "").strip(),
        category=str(category.get("name") or "").strip(),
        source="web",
    )


def extract_initial_data(html: str) -> dict[str, Any] | None:
    match = INITIAL_DATA_RE.search(html)
    if not match:
        return None
    try:
        return json.loads(unquote(match.group(1)))
    except json.JSONDecodeError:
        return None


def find_catalog_items(data: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 6 or not isinstance(data, dict):
        return []
    catalog = data.get("catalog")
    if isinstance(catalog, dict) and isinstance(catalog.get("items"), list):
        return [i for i in catalog["items"] if isinstance(i, dict)]
    for value in data.values():
        if isinstance(value, dict):
            found = find_catalog_items(value, depth + 1)
            if found:
                return found
    return []


def parse_web_search_page(html: str) -> list[Listing]:
    data = extract_initial_data(html)
    if not data:
        return []
    listings: list[Listing] = []
    for item in find_catalog_items(data):
        parsed = parse_web_item(item)
        if parsed:
            listings.append(parsed)
    return listings


def parse_mobile_search(data: dict[str, Any]) -> list[Listing]:
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    items = result.get("items") if isinstance(result, dict) else None
    if not isinstance(items, list):
        return []
    listings: list[Listing] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        parsed = parse_mobile_item(entry)
        if parsed:
            listings.append(parsed)
    return listings


def parse_mobile_details(data: dict[str, Any], listing: Listing) -> Listing:
    body = data.get("result") if isinstance(data.get("result"), dict) else data
    if not isinstance(body, dict):
        return listing
    description = body.get("description")
    if isinstance(description, dict):
        description = description.get("text") or description.get("value")
    if isinstance(description, str) and description.strip():
        listing.description = description.strip()
    seller = body.get("seller") if isinstance(body.get("seller"), dict) else {}
    if seller:
        listing.seller_name = str(seller.get("name") or listing.seller_name).strip()
        listing.seller_type = str(seller.get("userType") or seller.get("type") or listing.seller_type).strip()
    address = body.get("address")
    if isinstance(address, str) and address.strip():
        listing.location = address.strip()
    images = _extract_images(body.get("images"))
    if images:
        listing.images = images
    parameters = body.get("parameters")
    flat = parameters.get("flat") if isinstance(parameters, dict) else parameters
    if isinstance(flat, list):
        for param in flat:
            if isinstance(param, dict) and param.get("title"):
                listing.params[str(param["title"])] = str(param.get("description") or param.get("value") or "")
    price = _parse_price(body.get("priceDetailed")) or _parse_price(body.get("price"))
    if price:
        listing.price = price
    if not listing.title or listing.title == "Без названия":
        listing.title = str(body.get("title") or listing.title)
    return listing


def parse_web_details(html: str, listing: Listing) -> Listing:
    for block in JSON_LD_RE.findall(html):
        try:
            payload = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        entries = payload if isinstance(payload, list) else [payload]
        for entry in entries:
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
            listing.description = unquote(match.group(1)).strip()
    return listing


def parse_locations(data: dict[str, Any]) -> list[Location]:
    result = data.get("result") if isinstance(data.get("result"), dict) else data
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
        locations.append(Location(name=str(name), id=location_id, slug=slugify_location(str(name)), parent=str(parent)))
    return locations


class AvitoClient:
    def __init__(self, key: str = DEFAULT_KEY, proxy: str = "", request_delay: float = 2.0, timeout: int = 30) -> None:
        self.key = key or DEFAULT_KEY
        self.proxy = proxy or None
        self.request_delay = request_delay
        self.timeout = timeout
        self._session: AsyncSession | None = None
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self.blocked_until = 0.0
        self.last_error: str | None = None

    def configure(self, proxy: str, request_delay: float) -> None:
        new_proxy = proxy or None
        if new_proxy != self.proxy:
            self.proxy = new_proxy
            self._session = None
        self.request_delay = request_delay

    @property
    def is_blocked(self) -> bool:
        return time.monotonic() < self.blocked_until

    def _get_session(self) -> AsyncSession:
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome", proxy=self.proxy, timeout=self.timeout, allow_redirects=True)
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _throttle(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.request_delay:
                await asyncio.sleep(self.request_delay - elapsed)
            self._last_request_at = time.monotonic()

    def _mark_blocked(self, reason: str, seconds: int = 600) -> AvitoBlockedError:
        self.blocked_until = time.monotonic() + seconds
        self.last_error = reason
        logger.warning("avito blocked us: %s (pause %ss)", reason, seconds)
        return AvitoBlockedError(reason)

    async def _request(self, url: str, params: dict[str, Any] | None, headers: dict[str, str]) -> Response:
        if self.is_blocked:
            raise AvitoBlockedError(self.last_error or "temporary block")
        last_exc: Exception | None = None
        for attempt in range(3):
            await self._throttle()
            try:
                response = await self._get_session().get(url, params=params, headers=headers)
            except Exception as exc:
                last_exc = exc
                logger.warning("avito request failed (%s/3) %s: %s", attempt + 1, url, exc)
                self._session = None
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if response.status_code in (403, 429):
                raise self._mark_blocked(f"HTTP {response.status_code}: доступ ограничен (IP/капча)")
            if "text/html" in response.headers.get("content-type", "") and any(marker in response.text[:6000] for marker in BLOCK_MARKERS):
                raise self._mark_blocked("страница блокировки вместо данных (IP/капча)")
            if response.status_code >= 500:
                last_exc = AvitoUnavailableError(f"HTTP {response.status_code}")
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            self.last_error = None
            return response
        self.last_error = str(last_exc)
        raise AvitoUnavailableError(str(last_exc))

    async def _get_json(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        response = await self._request(url, params, headers)
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise AvitoUnavailableError(f"bad json from {url}: {exc}") from exc
        if not isinstance(data, dict):
            raise AvitoUnavailableError(f"unexpected payload from {url}")
        if data.get("error"):
            raise AvitoUnavailableError(str(data["error"]))
        return data

    async def search(
        self,
        query: str,
        location: Location,
        price_min: int | None = None,
        price_max: int | None = None,
        pages: int = 1,
        limit: int = 50,
    ) -> list[Listing]:
        listings: list[Listing] = []
        errors: list[str] = []
        if location.id is not None:
            try:
                listings = await self._search_mobile(query, location.id, price_min, price_max, pages, limit)
            except AvitoBlockedError:
                raise
            except AvitoError as exc:
                errors.append(f"mobile: {exc}")
                logger.warning("mobile search failed, falling back to web: %s", exc)
        if not listings:
            try:
                listings = await self._search_web(query, location.web_slug, price_min, price_max, pages)
            except AvitoBlockedError:
                raise
            except AvitoError as exc:
                errors.append(f"web: {exc}")
        if not listings and errors:
            raise AvitoUnavailableError("; ".join(errors))
        return _filter_by_price(_dedupe(listings), price_min, price_max)

    async def _search_mobile(self, query: str, location_id: int, price_min: int | None, price_max: int | None, pages: int, limit: int) -> list[Listing]:
        collected: list[Listing] = []
        for page in range(1, pages + 1):
            params: dict[str, Any] = {
                "key": self.key,
                "query": query,
                "locationId": location_id,
                "sort": "date",
                "page": page,
                "limit": limit,
                "display": "list",
                "withImagesOnly": "false",
            }
            if price_min:
                params["priceMin"] = price_min
            if price_max:
                params["priceMax"] = price_max
            data: dict[str, Any] | None = None
            for version in (11, 9):
                try:
                    data = await self._get_json(f"{MOBILE_API}/{version}/items", params, MOBILE_HEADERS)
                    break
                except AvitoUnavailableError as exc:
                    logger.info("mobile api v%s failed: %s", version, exc)
            if data is None:
                raise AvitoUnavailableError("mobile api unavailable")
            page_items = parse_mobile_search(data)
            collected.extend(page_items)
            if len(page_items) < limit // 2:
                break
        return collected

    async def _search_web(self, query: str, slug: str, price_min: int | None, price_max: int | None, pages: int) -> list[Listing]:
        collected: list[Listing] = []
        for page in range(1, pages + 1):
            params: dict[str, Any] = {"q": query, "s": 104, "cd": 1}
            if price_min:
                params["pmin"] = price_min
            if price_max:
                params["pmax"] = price_max
            if page > 1:
                params["p"] = page
            url = f"{WEB_BASE}/{slug}?{urlencode(params, quote_via=quote)}"
            response = await self._request(url, None, WEB_HEADERS)
            page_items = parse_web_search_page(response.text)
            if not page_items:
                if page == 1:
                    raise AvitoUnavailableError("web page has no catalog data")
                break
            collected.extend(page_items)
        return collected

    async def fetch_details(self, listing: Listing) -> Listing:
        try:
            data = await self._get_json(f"{MOBILE_API}/15/items/{listing.id}", {"key": self.key}, MOBILE_HEADERS)
            return parse_mobile_details(data, listing)
        except AvitoBlockedError:
            raise
        except AvitoError as exc:
            logger.info("mobile details failed for %s: %s", listing.id, exc)
        try:
            response = await self._request(listing.url, None, WEB_HEADERS)
            return parse_web_details(response.text, listing)
        except AvitoBlockedError:
            raise
        except AvitoError as exc:
            logger.info("web details failed for %s: %s", listing.id, exc)
        return listing

    async def find_locations(self, query: str, limit: int = 8) -> list[Location]:
        query = query.strip()
        if not query:
            return []
        lowered = query.lower()
        local = [loc for loc in POPULAR_LOCATIONS if lowered in loc.name.lower()]
        try:
            data = await self._get_json(f"{MOBILE_API}/1/slocations", {"key": self.key, "q": query, "limit": limit}, MOBILE_HEADERS)
            remote = parse_locations(data)
        except AvitoError as exc:
            logger.info("slocations failed: %s", exc)
            remote = []
        merged: list[Location] = []
        seen: set[str] = set()
        for loc in [*local, *remote]:
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
