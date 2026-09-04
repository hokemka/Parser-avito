from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import quote

import pytest
import pytest_asyncio
from aiohttp import web

from tgbot.services.avito import AvitoBlockedError, AvitoBrowser, AvitoClient, BrowserConfig, Location

CHROMIUM = "/opt/pw-browsers/chromium"
pytestmark = pytest.mark.skipif(not os.path.exists(CHROMIUM), reason="local chromium build is not available")

CATALOG = {"@avito/bx-single-page": {"data": {"catalog": {"items": [
    {"id": 101, "title": "iPhone 13 128 ГБ", "priceDetailed": {"value": 38000}, "urlPath": "/moskva/telefony/iphone_13_101",
     "images": [{"636x476": "/img/101.jpg"}], "description": "Отличное состояние", "location": {"name": "Москва"}, "sortTimeStamp": 1725000000000},
]}}}}

SEARCH_STATE_HTML = f"<html><head><title>iPhone — Авито</title><script>window.__initialData__ = \"{quote(json.dumps(CATALOG))}\";</script></head><body><div data-marker=\"item\" data-item-id=\"101\"></div></body></html>"
SEARCH_DOM_HTML = """<html><head><title>iPhone — Авито</title></head><body>
<div data-marker="item" data-item-id="202"><a data-marker="item-title" href="/moskva/telefony/iphone_12_202" title="iPhone 12 64 ГБ">iPhone 12</a>
<span data-marker="item-price"><meta itemprop="price" content="25000">25 000 ₽</span><div data-marker="item-address">Москва, Тверская</div>
<p data-marker="item-date">3 часа назад</p><meta itemprop="description" content="Без царапин"><img src="/img/202.jpg"></div>
<div data-marker="item" data-item-id="203"><a data-marker="item-title" href="/moskva/telefony/iphone_11_203" title="iPhone 11">iPhone 11</a><span data-marker="item-price">15 000 ₽</span></div>
</body></html>"""
ITEM_HTML = """<html><head><title>iPhone 13 — Авито</title></head><body><h1 data-marker="item-view/title-info">iPhone 13 128 ГБ синий</h1>
<span data-marker="item-view/item-price"><meta itemprop="price" content="38000">38 000 ₽</span>
<div data-marker="item-view/item-description"><p>Куплен в 2022, АКБ 89%</p><p>Полный комплект</p></div>
<div data-marker="item-view/item-address">Москва, Арбат</div><div data-marker="seller-info/name">Иван</div><div data-marker="seller-info/label">Частное лицо</div>
<ul data-marker="item-view/item-params"><li>Память: 128 ГБ</li><li>Цвет: синий</li></ul>
<img data-marker="image-frame/image" src="/img/101_big.jpg"></body></html>"""
BLOCK_HTML = "<html><head><title>Доступ ограничен: проблема с IP</title></head><body>Доступ с вашего IP-адреса временно ограничен</body></html>"
EMPTY_HTML = "<html><head><title>Авито</title></head><body><h2>Ничего не найдено</h2></body></html>"


def make_app(state: dict) -> web.Application:
    async def search(request: web.Request) -> web.Response:
        state["hits"] = state.get("hits", 0) + 1
        query = request.query.get("q", "")
        if state.get("block"):
            return web.Response(text=BLOCK_HTML, content_type="text/html", status=429)
        if query == "nothing":
            return web.Response(text=EMPTY_HTML, content_type="text/html")
        if query == "dom":
            return web.Response(text=SEARCH_DOM_HTML, content_type="text/html")
        return web.Response(text=SEARCH_STATE_HTML, content_type="text/html")

    async def item(request: web.Request) -> web.Response:
        return web.Response(text=ITEM_HTML, content_type="text/html")

    async def slocations(request: web.Request) -> web.Response:
        return web.json_response({"result": {"locations": [{"id": 650400, "names": {"1": "Казань"}, "parent": {"names": {"1": "Татарстан"}}}]}})

    async def root(request: web.Request) -> web.Response:
        return web.Response(text="<html><title>Авито</title></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", root)
    app.router.add_get("/web/1/slocations", slocations)
    app.router.add_get("/moskva/telefony/{slug}", item)
    app.router.add_get("/{city}", search)
    return app


@pytest_asyncio.fixture
async def server(tmp_path):
    state: dict = {}
    runner = web.AppRunner(make_app(state))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    browser = AvitoBrowser(BrowserConfig(engine="chromium", headless="true", profile_dir=tmp_path / "profile", chromium_path=CHROMIUM, page_timeout=20, block_images=False))
    client = AvitoClient(browser, request_delay=0.0, block_cooldown=5, base_url=f"http://127.0.0.1:{port}")
    yield client, state
    await client.close()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_search_reads_embedded_state(server):
    client, state = server
    listings = await client.search("iphone 13", Location(name="Москва", slug="moskva"), 30000, 45000)
    assert [item.id for item in listings] == [101]
    assert listings[0].source == "state" and listings[0].images == ["https://www.avito.ru/img/101.jpg"]
    details = await client.fetch_details(listings[0])
    assert details.title == "iPhone 13 128 ГБ синий" and "Полный комплект" in details.description
    assert details.params == {"Память": "128 ГБ", "Цвет": "синий"} and details.seller_name == "Иван"
    assert client.status()["pages"] == 2


@pytest.mark.asyncio
async def test_search_falls_back_to_dom(server):
    client, _ = server
    listings = await client.search("dom", Location(name="Москва", slug="moskva"))
    assert [item.id for item in listings] == [202, 203]
    assert listings[0].price == 25000 and listings[0].location == "Москва, Тверская" and listings[0].description == "Без царапин"
    assert listings[0].published_at is not None and listings[0].source == "dom"
    assert listings[1].price == 15000


@pytest.mark.asyncio
async def test_empty_results_and_block(server):
    client, state = server
    assert await client.search("nothing", Location(name="Москва", slug="moskva")) == []
    state["block"] = True
    with pytest.raises(AvitoBlockedError):
        await client.search("iphone", Location(name="Москва", slug="moskva"))
    assert client.is_blocked and client.blocks_count == 1
    with pytest.raises(AvitoBlockedError):
        await client.search("iphone", Location(name="Москва", slug="moskva"))


@pytest.mark.asyncio
async def test_find_locations_via_browser_fetch(server):
    client, _ = server
    locations = await client.find_locations("Татарстан")
    assert [(loc.id, loc.parent) for loc in locations] == [(650400, "Татарстан")]
