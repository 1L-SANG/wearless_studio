"""순찰 수집기(fm_trace_sources) — 허용된 곳만, 예의 있게, 대표 이미지까지만(2026-09-27).

지키는 것:
  - robots.txt 를 읽고 따른다(4xx=전부 허용, 5xx·연결 실패=전부 금지 — RFC 9309).
  - 같은 호스트 요청 사이 최소 간격, User-Agent 에 연락처.
  - 네이버는 공식 검색 API(키 없으면 어댑터가 아예 안 만들어진다 — fail-closed).
  - 지그재그는 검색 결과의 대표 이미지만. 상세 이미지는 받지 않는다.
  - 29CM·무신사·에이블리·쿠팡은 만들 수 없다(약관·봇 차단).
"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import fm_trace_sources as T


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    async def sleep(self, s):
        self.slept.append(round(s, 3))
        self.now += s


def _fetcher(handler, clock=None, **kw):
    clock = clock or Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    f = T.PoliteFetcher(client, contact="https://facemarket.wearless.kr",
                        clock=clock.monotonic, sleep=clock.sleep, **kw)
    return f, clock


def test_user_agent_names_us_and_contact():
    f, _ = _fetcher(lambda r: httpx.Response(200))
    assert f.user_agent.startswith("WearlessFaceMarketTrace/1.0")
    assert "+https://facemarket.wearless.kr" in f.user_agent


def test_robots_disallow_is_respected_and_cached():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, content=b"ok")

    f, _ = _fetcher(handler)

    async def run():
        assert await f.allowed("https://shop.example/private/x") is False
        assert await f.allowed("https://shop.example/public/x") is True
        assert (await f.get("https://shop.example/private/y")) is None
    asyncio.run(run())
    assert seen.count("https://shop.example/robots.txt") == 1
    assert "https://shop.example/private/y" not in seen


@pytest.mark.parametrize("status,allowed", [(404, True), (403, True), (500, False)])
def test_robots_unavailable_vs_unreachable(status, allowed):
    f, _ = _fetcher(lambda r: httpx.Response(status) if r.url.path == "/robots.txt"
                    else httpx.Response(200))
    assert asyncio.run(f.allowed("https://cdn.example/a.jpg")) is allowed


def test_min_interval_per_host():
    f, clock = _fetcher(lambda r: httpx.Response(404) if r.url.path == "/robots.txt"
                        else httpx.Response(200, content=b"x"), min_interval=3.0)

    async def run():   # robots.txt 읽기도 요청이라 간격에 든다 — 여기선 간격만 보려고 끈다
        await f.get("https://a.example/1", respect_robots=False)
        await f.get("https://a.example/2", respect_robots=False)   # 같은 호스트 → 3초 기다린다
        await f.get("https://b.example/1", respect_robots=False)   # 다른 호스트 → 바로
    asyncio.run(run())
    assert clock.slept == [3.0]


def test_image_fetch_caps_size_and_requires_image_type():
    big = b"\xff" * 2000

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/big.jpg":
            return httpx.Response(200, content=big, headers={"content-type": "image/jpeg"})
        if request.url.path == "/page.html":
            return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"img", headers={"content-type": "image/png"})

    f, _ = _fetcher(handler, max_image_bytes=1000)

    async def run():
        assert await f.fetch_image("https://c.example/big.jpg") is None
        assert await f.fetch_image("https://c.example/page.html") is None
        assert await f.fetch_image("https://c.example/ok.png") == b"img"
    asyncio.run(run())


NAVER_BODY = {"items": [
    {"title": "<b>데님</b> 셔츠 &amp; 팬츠", "link": "https://smartstore.naver.com/a/products/1",
     "image": "https://shopping-phinf.pstatic.net/main_1/1.jpg", "mallName": "가게A",
     "productId": "111", "brand": "", "maker": ""},
    {"title": "이미지 없음", "link": "https://x", "image": "", "mallName": "B", "productId": "222"},
]}


def test_naver_uses_official_api_with_keys_and_parses_hits():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "openapi.naver.com":
            return httpx.Response(200, json=NAVER_BODY)
        return httpx.Response(404)

    f, _ = _fetcher(handler)
    a = T.NaverShopAdapter(f, client_id="id-1", client_secret="sec-1", display=40)
    hits = asyncio.run(a.search("데님 셔츠"))
    api = [c for c in calls if c.url.host == "openapi.naver.com"][0]
    assert api.url.path == "/v1/search/shop.json"
    assert api.url.params["query"] == "데님 셔츠" and api.url.params["display"] == "40"
    assert api.headers["X-Naver-Client-Id"] == "id-1"
    assert api.headers["X-Naver-Client-Secret"] == "sec-1"
    # 공식 API 는 robots 대상이 아니다(openapi.naver.com robots 는 크롤러용 Disallow: /)
    assert not any(c.url.path == "/robots.txt" and c.url.host == "openapi.naver.com" for c in calls)
    assert hits == [T.SearchHit(platform="naver", product_id="111",
                                product_url="https://smartstore.naver.com/a/products/1",
                                image_url="https://shopping-phinf.pstatic.net/main_1/1.jpg",
                                title="데님 셔츠 & 팬츠", store_name="가게A", store_id="가게A")]


ZIGZAG_BODY = {"data": {"search_result": {"ui_item_list": [
    {"__typename": "UxSearchResultFilterBar"},
    {"__typename": "UxGoodsCardItem",
     "image_url": "https://cf.product-image.s.zigzag.kr/original/c/1/2.jpeg?width=720&height=720&quality=80&format=jpeg",
     "product_url": "https://store.zigzag.kr/app/catalog/products/169632655?browsing_type=NATIVE_BROWSER",
     "title": "데님 셔츠", "catalog_product_id": "169632655", "shop_id": "20", "shop_name": "프롬"},
]}}}


def test_zigzag_minimal_graphql_and_original_image():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, json=ZIGZAG_BODY)

    f, _ = _fetcher(handler)
    hits = asyncio.run(T.ZigzagAdapter(f).search("데님 셔츠"))
    post = [c for c in calls if c.method == "POST"][0]
    assert str(post.url) == "https://api.zigzag.kr/api/2/graphql/GetSearchResult"
    body = json.loads(post.content)
    assert body["variables"]["input"]["q"] == "데님 셔츠"
    assert "UxGoodsCardItem" in body["query"] and "detail" not in body["query"].lower()
    assert hits == [T.SearchHit(
        platform="zigzag", product_id="169632655",
        product_url="https://store.zigzag.kr/app/catalog/products/169632655?browsing_type=NATIVE_BROWSER",
        image_url="https://cf.product-image.s.zigzag.kr/original/c/1/2.jpeg",
        title="데님 셔츠", store_name="프롬", store_id="20")]


def _settings(**kw):
    base = dict(fm_trace_patrol_platforms=("naver", "zigzag"), naver_search_client_id=None,
                naver_search_client_secret=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_naver_is_off_without_keys_fail_closed():
    f, _ = _fetcher(lambda r: httpx.Response(404))
    names = [a.platform for a in T.build_adapters(_settings(), f)]
    assert names == ["zigzag"]
    names = [a.platform for a in T.build_adapters(_settings(
        naver_search_client_id="i", naver_search_client_secret="s"), f)]
    assert names == ["naver", "zigzag"]


def test_platforms_setting_parses_only_allowed(monkeypatch):
    from app.config import load_settings
    monkeypatch.setenv("FM_TRACE_PATROL_PLATFORMS", "zigzag, 29cm, musinsa, NAVER")
    s = load_settings()
    assert s.fm_trace_patrol_platforms == ("zigzag", "naver")
    monkeypatch.delenv("FM_TRACE_PATROL_PLATFORMS")
    monkeypatch.delenv("FM_TRACE_PATROL", raising=False)
    s = load_settings()
    assert s.fm_trace_patrol == "off"                      # 기본은 꺼짐
    assert s.fm_trace_patrol_platforms == ("naver", "zigzag")
    monkeypatch.setenv("NAVER_SEARCH_CLIENT_ID", "  ")
    assert load_settings().naver_search_client_id is None  # 빈 값은 없는 것


def test_no_adapter_for_forbidden_platforms():
    assert set(T.ADAPTERS) == {"naver", "zigzag"}
