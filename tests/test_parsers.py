import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from tgbot.services.avito import (
    Listing, Location, _filter_by_price, apply_dom_item, build_search_url, extract_state_blobs, is_block_page,
    is_empty_results_page, parse_dom_cards, parse_item_page, parse_locations, parse_proxy, parse_relative_date,
    parse_search_page, slugify_location,
)
from tgbot.utils.text import parse_price_range


def wrap_initial_data(data: dict) -> str:
    return f"<html><head><title>Купить iPhone</title><script>window.__initialData__ = \"{quote(json.dumps(data))}\";</script></head><body></body></html>"


def wrap_mfe_state(data: dict) -> str:
    escaped = json.dumps(data, ensure_ascii=False).replace("&", "&amp;").replace('"', "&quot;")
    return f'<html><head><title>iPhone</title></head><body><script type="mime/invalid" data-mfe-state="true">{escaped}</script></body></html>'


CATALOG_ITEMS = [
    {"id": 101, "title": "iPhone 13 128 ГБ", "priceDetailed": {"value": 38000, "string": "38 000 ₽"}, "urlPath": "/moskva/telefony/iphone_13_101",
     "images": [{"208x156": "//img.avito.st/s.jpg", "636x476": "//img.avito.st/l.jpg"}], "description": "Состояние отличное", "location": {"name": "Москва"},
     "sortTimeStamp": 1725000000000, "sellerInfo": {"name": "Иван", "userType": "private"}, "category": {"name": "Телефоны"}},
    {"id": 102, "title": "iPhone 13 Pro", "priceDetailed": {"value": 60000}, "urlPath": "/moskva/telefony/iphone_13_pro_102", "isReserved": True},
    {"id": 103, "type": "vipItem", "title": "iPhone 13 mini", "priceDetailed": {"value": 30000}, "urlPath": "/moskva/telefony/iphone_13_mini_103",
     "iva": {"DateInfoStep": [{"payload": {"absolute": "5 минут назад"}}]}},
]


def test_slugify_matches_avito_conventions():
    assert slugify_location("Нижний Новгород") == "nizhniy_novgorod"
    assert slugify_location("Санкт-Петербург") == "sankt-peterburg"
    assert slugify_location("Ростов-на-Дону") == "rostov-na-donu"


def test_build_search_url():
    url = build_search_url("iphone 13", "moskva", 30000, 45000, page=2)
    assert url == "https://www.avito.ru/moskva?q=iphone%2013&s=104&pmin=30000&pmax=45000&p=2"
    assert build_search_url("ps5", "all") == "https://www.avito.ru/all?q=ps5&s=104"


def test_parse_price_range_variants():
    assert parse_price_range("до 40 000") == (None, 40000)
    assert parse_price_range("30-45 тыс") == (30000, 45000)
    assert parse_price_range("от 20к") == (20000, None)
    assert parse_price_range("любая") == (None, None)
    assert parse_price_range("abc") is None


def test_parse_search_page_from_initial_data():
    html = wrap_initial_data({"@avito/bx-single-page": {"data": {"catalog": {"items": CATALOG_ITEMS}}}})
    listings = parse_search_page(html)
    assert [item.id for item in listings] == [101, 103]
    first = listings[0]
    assert first.price == 38000
    assert first.url == "https://www.avito.ru/moskva/telefony/iphone_13_101"
    assert first.images == ["https://img.avito.st/l.jpg"]
    assert first.location == "Москва" and first.seller_name == "Иван" and first.category == "Телефоны"
    assert first.published_at.year == 2024
    assert listings[1].published_at is not None and listings[1].source == "state"


def test_parse_search_page_from_mfe_state():
    html = wrap_mfe_state({"loaderData": {"data": {"catalog": {"items": CATALOG_ITEMS[:1]}}}})
    blobs = extract_state_blobs(html)
    assert len(blobs) == 1
    listings = parse_search_page(html)
    assert len(listings) == 1 and listings[0].title == "iPhone 13 128 ГБ"


def test_parse_dom_cards_fallback():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    cards = [
        {"id": "4242", "title": "PlayStation 5", "href": "/moskva/igry/ps5_4242", "price": "30000", "address": "Москва, Арбат", "date": "2 часа назад", "description": "Полный комплект", "image": "//img.avito.st/ps5.jpg"},
        {"id": "", "title": "Без id", "href": "/moskva/igry/xbox_2345678901?context=1", "price": "12 000 ₽", "address": "", "date": "вчера в 10:15", "description": "", "image": ""},
        {"id": "bad", "title": "x", "href": "/nothing", "price": "", "address": "", "date": "", "description": "", "image": ""},
    ]
    listings = parse_dom_cards(cards, now=now)
    assert [item.id for item in listings] == [4242, 2345678901]
    assert listings[0].price == 30000 and listings[0].images == ["https://img.avito.st/ps5.jpg"]
    assert listings[0].published_at == now - timedelta(hours=2)
    assert listings[1].price == 12000 and listings[1].published_at == datetime(2026, 9, 3, 10, 15, tzinfo=timezone.utc)
    assert listings[0].source == "dom"


def test_parse_relative_date_formats():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    assert parse_relative_date("только что", now) == now
    assert parse_relative_date("15 минут назад", now) == now - timedelta(minutes=15)
    assert parse_relative_date("3 дня назад", now) == now - timedelta(days=3)
    assert parse_relative_date("сегодня в 09:30", now) == now.replace(hour=9, minute=30)
    assert parse_relative_date("28 августа", now) == datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    assert parse_relative_date("", now) is None


def test_block_and_empty_detection():
    blocked = "<html><head><title>Доступ ограничен: проблема с IP</title></head><body>...</body></html>"
    assert is_block_page(blocked, 200)
    assert is_block_page("<html><title>ok</title></html>", 429)
    normal = "<html><head><title>Купить iPhone — Авито</title></head><body>captcha.js loaded</body></html>"
    assert not is_block_page(normal, 200)
    assert is_empty_results_page("<html><body><h2>Ничего не найдено в Москве</h2></body></html>")


def test_parse_item_page_from_state_and_dom():
    listing = Listing(id=101, title="Без названия", price=None, url="https://www.avito.ru/moskva/telefony/iphone_13_101")
    state = {"@avito/bx-item-view": {"buyerItem": {"item": {
        "id": 101, "title": "iPhone 13 128 ГБ синий", "description": "Куплен в 2022, АКБ 89%", "price": {"value": "38 000", "metric": "₽"},
        "address": "Москва, ул. Арбат", "seller": {"name": "Иван", "userType": "private"},
        "images": [{"640x480": "https://img.avito.st/1.jpg"}], "paramsBlock": {"items": [{"title": "Память", "description": "128 ГБ"}]},
    }}}}
    parsed = parse_item_page(wrap_initial_data(state), listing)
    assert parsed.title == "iPhone 13 128 ГБ синий" and parsed.price == 38000
    assert parsed.description.startswith("Куплен") and parsed.seller_name == "Иван" and parsed.params == {"Память": "128 ГБ"}
    dom = {"title": "", "price": "37500", "description": "Куплен в 2022, АКБ 89%, полный комплект", "address": "", "seller": "", "seller_type": "Частное лицо",
           "params": ["Состояние: Б/у", "Цвет: синий"], "images": ["//img.avito.st/2.jpg"]}
    parsed = apply_dom_item(dom, parsed)
    assert parsed.price == 37500 and parsed.params["Цвет"] == "синий" and parsed.seller_type == "private"
    assert "полный комплект" in parsed.description


def test_parse_item_page_json_ld_fallback():
    listing = Listing(id=5, title="x", price=None, url="https://www.avito.ru/5")
    html = '<html><script type="application/ld+json">{"@type":"Product","description":"Монитор 24","offers":{"price":"12500"},"image":["https://img/1.jpg"]}</script></html>'
    parsed = parse_item_page(html, listing)
    assert parsed.description == "Монитор 24" and parsed.price == 12500 and parsed.images == ["https://img/1.jpg"]


def test_parse_locations():
    data = {"result": {"locations": [{"id": 637640, "names": {"1": "Москва"}, "parent": {"names": {"1": "Россия"}}}, {"id": "bad"}]}}
    locations = parse_locations(data)
    assert len(locations) == 1
    assert locations[0].id == 637640 and locations[0].slug == "moskva" and locations[0].parent == "Россия"


def test_parse_proxy_formats():
    assert parse_proxy("") is None
    assert parse_proxy("1.2.3.4:8080") == {"server": "http://1.2.3.4:8080"}
    assert parse_proxy("socks5://user:p%40ss@host:1080") == {"server": "socks5://host:1080", "username": "user", "password": "p@ss"}


def test_price_filter_keeps_unknown_prices():
    items = [Listing(id=1, title="a", price=100, url="u"), Listing(id=2, title="b", price=None, url="u"), Listing(id=3, title="c", price=900, url="u")]
    assert [item.id for item in _filter_by_price(items, 200, 500)] == [2]


def test_listing_json_roundtrip():
    listing = Listing(id=1, title="t", price=5, url="u", images=["i"], params={"a": "b"})
    assert Listing.from_json(listing.to_json()) == listing


def test_location_web_slug_fallback():
    assert Location(name="Тюмень").web_slug == "tyumen"
    assert Location(name="Москва", slug="moskva").web_slug == "moskva"
