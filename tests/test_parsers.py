import json
from urllib.parse import quote

from tgbot.services.avito import (
    Listing, Location, _filter_by_price, parse_locations, parse_mobile_details, parse_mobile_search, parse_web_search_page,
    slugify_location,
)
from tgbot.utils.text import parse_price_range


def test_slugify_matches_avito_conventions():
    assert slugify_location("Нижний Новгород") == "nizhniy_novgorod"
    assert slugify_location("Санкт-Петербург") == "sankt-peterburg"
    assert slugify_location("Ростов-на-Дону") == "rostov-na-donu"


def test_parse_price_range_variants():
    assert parse_price_range("до 40 000") == (None, 40000)
    assert parse_price_range("30-45 тыс") == (30000, 45000)
    assert parse_price_range("от 20к") == (20000, None)
    assert parse_price_range("любая") == (None, None)
    assert parse_price_range("abc") is None
    low, high = parse_price_range("40000")
    assert low < 40000 < high


def test_parse_mobile_search_handles_price_string_and_detailed():
    payload = {"result": {"items": [
        {"type": "item", "value": {"id": 1, "title": "iPhone 13", "price": "38 000 ₽", "uri": "/moskva/telefony/x_1",
                                   "images": [{"208x156": "//img.avito.st/small.jpg", "640x480": "https://img.avito.st/big.jpg"}],
                                   "location": {"name": "Москва"}, "time": 1725000000, "userType": "private"}},
        {"type": "xlItem", "value": {"id": "2", "title": "iPhone 13 Pro", "priceDetailed": {"value": 55000, "string": "55 000 ₽"}, "uri": "https://www.avito.ru/x_2", "images": []}},
        {"type": "adv", "value": {"id": 3}},
    ]}}
    listings = parse_mobile_search(payload)
    assert [item.id for item in listings] == [1, 2]
    assert listings[0].price == 38000
    assert listings[0].images == ["https://img.avito.st/big.jpg"]
    assert listings[0].url == "https://www.avito.ru/moskva/telefony/x_1"
    assert listings[0].location == "Москва"
    assert listings[1].price == 55000
    assert listings[1].url == "https://www.avito.ru/x_2"


def test_parse_web_search_page_extracts_catalog():
    data = {"@avito/bx-single-page": {"data": {"catalog": {"items": [
        {"id": 10, "title": "PS5", "priceDetailed": {"value": 30000}, "urlPath": "/moskva/igry/ps5_10",
         "images": [{"636x476": "https://img/1.jpg"}], "description": "Отличное состояние", "location": {"name": "Москва"}, "sortTimeStamp": 1725000000000},
        {"id": 11, "type": "vipItem", "title": "skip me"},
    ]}}}}
    html = f"<html><script>window.__initialData__ = \"{quote(json.dumps(data))}\";</script></html>"
    listings = parse_web_search_page(html)
    assert len(listings) == 1
    assert listings[0].id == 10 and listings[0].price == 30000
    assert listings[0].description == "Отличное состояние"
    assert listings[0].published_at.year == 2024
    assert listings[0].source == "web"


def test_parse_mobile_details_fills_description_and_params():
    listing = Listing(id=5, title="x", price=None, url="https://www.avito.ru/5")
    data = {"description": "Продаю телефон", "seller": {"name": "Иван", "userType": "private"}, "address": "Москва, Арбат",
            "parameters": {"flat": [{"title": "Память", "description": "128 ГБ"}]}, "price": {"value": 12000}}
    parsed = parse_mobile_details(data, listing)
    assert parsed.description == "Продаю телефон"
    assert parsed.seller_name == "Иван"
    assert parsed.params == {"Память": "128 ГБ"}
    assert parsed.price == 12000


def test_parse_locations():
    data = {"result": {"locations": [{"id": 637640, "names": {"1": "Москва"}, "parent": {"names": {"1": "Россия"}}}, {"id": "bad"}]}}
    locations = parse_locations(data)
    assert len(locations) == 1
    assert locations[0].id == 637640 and locations[0].slug == "moskva" and locations[0].parent == "Россия"


def test_price_filter_keeps_unknown_prices():
    items = [Listing(id=1, title="a", price=100, url="u"), Listing(id=2, title="b", price=None, url="u"), Listing(id=3, title="c", price=900, url="u")]
    kept = _filter_by_price(items, 200, 500)
    assert [item.id for item in kept] == [2]


def test_listing_json_roundtrip():
    listing = Listing(id=1, title="t", price=5, url="u", images=["i"], params={"a": "b"})
    restored = Listing.from_json(listing.to_json())
    assert restored == listing


def test_location_web_slug_fallback():
    assert Location(name="Тюмень").web_slug == "tyumen"
    assert Location(name="Москва", slug="moskva").web_slug == "moskva"
