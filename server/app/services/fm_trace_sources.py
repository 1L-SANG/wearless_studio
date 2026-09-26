"""자동 출처 순찰의 수집기 — 허용된 곳에서, 예의 있게, 대표 이미지까지만(2026-09-27).

어디서 무엇을(2026-09-26~27 robots.txt·이용약관 원문 확인, 오너 결정):
  - 네이버 쇼핑: **공식 검색 API**(openapi.naver.com/v1/search/shop.json, 무료 하루 25,000건).
    키(NAVER_SEARCH_CLIENT_ID/SECRET)가 없으면 어댑터를 만들지 않는다(fail-closed).
    🔴 검색 API 특약(2026-09-07 개정) 2.3·2.4 — 결과 데이터는 서버에 최대 21일(이력 조회 목적).
       원장 쪽(fm_trace_findings)이 21일 삭제를 맡는다. 여기서는 아무것도 저장하지 않는다.
  - 지그재그: robots 는 AhrefsBot·GPTBot 외 전부 Allow, 약관에 크롤링 금지 조항은 없다
    (제13조④ "영리 목적 복제·이용 금지"는 회색 — 오너가 켜기로 결정). 웹 검색 화면이 부르는 GraphQL
    을 **필요한 필드만** 담은 최소 쿼리로 한 페이지만 부르고, 목록의 대표 이미지만 받는다.
  - 만들지 않는 곳: 29CM(무신사 통합약관 v2.11 제11조② 크롤러·스크립트 수집 명시 금지), 무신사(robots
    가 허가 목록 봇 외 전부 Disallow), 에이블리(Cloudflare 봇 챌린지), 쿠팡(봇 거부).

예의(PoliteFetcher):
  - robots.txt 를 호스트마다 한 번 읽고 따른다. RFC 9309: 4xx = 규칙 없음(전부 허용),
    5xx·연결 실패 = 전부 금지로 본다. 공식 API 호출(네이버)만 robots 대상이 아니다.
  - 같은 호스트 요청 사이 최소 간격(기본 3초), User-Agent 에 이름·연락처.
  - 이미지는 크기 상한·이미지 content-type 만. 상세페이지 본문 이미지는 받지 않는다(약관 위험).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("facemarket.trace_sources")

UA_TOKEN = "WearlessFaceMarketTrace"
UA_VERSION = "1.0"
DEFAULT_MIN_INTERVAL = 3.0
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass(frozen=True)
class SearchHit:
    platform: str
    product_id: str
    product_url: str | None
    image_url: str
    title: str | None
    store_name: str | None
    store_id: str | None


class PoliteFetcher:
    """호스트별 최소 간격 + robots.txt + 연락처 UA + 크기 상한. 한 순찰 실행 동안 하나를 쓴다."""

    def __init__(self, client: httpx.AsyncClient, *, contact: str,
                 min_interval: float = DEFAULT_MIN_INTERVAL,
                 max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
                 clock=time.monotonic, sleep=asyncio.sleep):
        self.client = client
        self.user_agent = f"{UA_TOKEN}/{UA_VERSION} (+{contact})"
        self.min_interval = min_interval
        self.max_image_bytes = max_image_bytes
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | bool] = {}
        self._lock = asyncio.Lock()

    async def _pace(self, host: str) -> None:
        async with self._lock:
            last = self._last.get(host)
            if last is not None:
                wait = self.min_interval - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last[host] = self._clock()

    async def _robots_for(self, scheme: str, host: str):
        key = f"{scheme}://{host}"
        if key in self._robots:
            return self._robots[key]
        await self._pace(host)
        try:
            res = await self.client.get(f"{key}/robots.txt", timeout=_TIMEOUT,
                                        headers={"User-Agent": self.user_agent},
                                        follow_redirects=True)
        except httpx.HTTPError:
            self._robots[key] = False           # 연결 실패 = 전부 금지(RFC 9309)
            return False
        if 400 <= res.status_code < 500:
            self._robots[key] = True            # 규칙 없음 = 전부 허용
        elif res.status_code >= 500:
            self._robots[key] = False
        else:
            parser = RobotFileParser()
            parser.parse(res.text.splitlines())
            self._robots[key] = parser
        return self._robots[key]

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        rules = await self._robots_for(parts.scheme, parts.netloc)
        if isinstance(rules, bool):
            return rules
        return rules.can_fetch(UA_TOKEN, url)

    async def request(self, method: str, url: str, *, respect_robots: bool = True,
                      **kw) -> httpx.Response | None:
        if respect_robots and not await self.allowed(url):
            log.info("trace patrol robots disallow host=%s", urlsplit(url).netloc)
            return None
        await self._pace(urlsplit(url).netloc)
        headers = {"User-Agent": self.user_agent, **(kw.pop("headers", None) or {})}
        try:
            return await self.client.request(method, url, headers=headers, timeout=_TIMEOUT, **kw)
        except httpx.HTTPError as exc:
            log.info("trace patrol request failed host=%s err=%s", urlsplit(url).netloc,
                     type(exc).__name__)
            return None

    async def get(self, url: str, **kw) -> httpx.Response | None:
        return await self.request("GET", url, **kw)

    async def fetch_image(self, url: str) -> bytes | None:
        """대표 이미지 1장. 이미지가 아니거나 상한을 넘으면 None(버린다)."""
        if not await self.allowed(url):
            return None
        await self._pace(urlsplit(url).netloc)
        try:
            async with self.client.stream("GET", url, timeout=_TIMEOUT, follow_redirects=True,
                                          headers={"User-Agent": self.user_agent}) as res:
                if res.status_code != 200:
                    return None
                if not res.headers.get("content-type", "").lower().startswith("image/"):
                    return None
                declared = res.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > self.max_image_bytes:
                    return None
                buf = bytearray()
                async for chunk in res.aiter_bytes():
                    buf += chunk
                    if len(buf) > self.max_image_bytes:
                        return None
                return bytes(buf)
        except httpx.HTTPError:
            return None


_TAG = re.compile(r"<[^>]+>")


def _clean_title(raw: str | None) -> str | None:
    if not raw:
        return None
    return html.unescape(_TAG.sub("", raw)).strip() or None


class NaverShopAdapter:
    """네이버 쇼핑 검색 API(공식). 결과 대표 이미지(image)만 받는다."""

    platform = "naver"
    ENDPOINT = "https://openapi.naver.com/v1/search/shop.json"

    def __init__(self, fetcher: PoliteFetcher, *, client_id: str, client_secret: str,
                 display: int = 40):
        self.fetcher = fetcher
        self._id = client_id
        self._secret = client_secret
        self.display = max(1, min(100, display))

    async def search(self, query: str) -> list[SearchHit]:
        res = await self.fetcher.get(
            self.ENDPOINT, respect_robots=False,
            params={"query": query, "display": str(self.display), "start": "1", "sort": "sim"},
            headers={"X-Naver-Client-Id": self._id, "X-Naver-Client-Secret": self._secret},
        )
        if res is None or res.status_code != 200:
            if res is not None:
                log.warning("naver shop search status=%s", res.status_code)
            return []
        hits = []
        for item in (res.json() or {}).get("items") or []:
            image = (item.get("image") or "").strip()
            pid = str(item.get("productId") or "").strip()
            if not image or not pid:
                continue
            mall = (item.get("mallName") or "").strip() or None
            hits.append(SearchHit(platform="naver", product_id=pid,
                                  product_url=(item.get("link") or "").strip() or None,
                                  image_url=image, title=_clean_title(item.get("title")),
                                  store_name=mall, store_id=mall))
        return hits


class ZigzagAdapter:
    """지그재그 웹 검색이 부르는 GraphQL 을 필요한 필드만 담아 첫 페이지만 부른다."""

    platform = "zigzag"
    ENDPOINT = "https://api.zigzag.kr/api/2/graphql/GetSearchResult"
    QUERY = (
        "query GetSearchResult($input: SearchResultInput!) { search_result(input: $input) { "
        "ui_item_list { __typename ... on UxGoodsCardItem { image_url product_url title "
        "catalog_product_id shop_id shop_name } } } }"
    )

    def __init__(self, fetcher: PoliteFetcher, *, limit: int = 40):
        self.fetcher = fetcher
        self.limit = limit

    @staticmethod
    def _original(url: str) -> str:
        """목록 썸네일 주소의 리사이즈 쿼리를 떼면 원본 비율 그대로의 대표 이미지다(2026-09-27 실측)."""
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    async def search(self, query: str) -> list[SearchHit]:
        res = await self.fetcher.request("POST", self.ENDPOINT, json={
            "operationName": "GetSearchResult", "query": self.QUERY,
            "variables": {"input": {"initial": True, "page_id": "srp_item", "q": query,
                                    "filter_id_list": ["205"], "filter_list": [],
                                    "sub_filter_id_list": [], "after": None}},
        })
        if res is None or res.status_code != 200:
            if res is not None:
                log.warning("zigzag search status=%s", res.status_code)
            return []
        try:
            items = res.json()["data"]["search_result"]["ui_item_list"] or []
        except (KeyError, TypeError, ValueError):
            log.warning("zigzag search shape changed")
            return []
        hits = []
        for item in items:
            if item.get("__typename") != "UxGoodsCardItem":
                continue
            image = (item.get("image_url") or "").strip()
            pid = str(item.get("catalog_product_id") or "").strip()
            if not image or not pid:
                continue
            hits.append(SearchHit(platform="zigzag", product_id=pid,
                                  product_url=(item.get("product_url") or "").strip() or None,
                                  image_url=self._original(image), title=_clean_title(item.get("title")),
                                  store_name=(item.get("shop_name") or "").strip() or None,
                                  store_id=str(item.get("shop_id") or "").strip() or None))
            if len(hits) >= self.limit:
                break
        return hits


ADAPTERS = {"naver": NaverShopAdapter, "zigzag": ZigzagAdapter}


def build_adapters(settings, fetcher: PoliteFetcher) -> list:
    """설정 순서대로 켜진 어댑터. 네이버는 키 둘 다 있어야 만든다(fail-closed)."""
    out = []
    for name in settings.fm_trace_patrol_platforms:
        if name == "naver":
            if settings.naver_search_client_id and settings.naver_search_client_secret:
                out.append(NaverShopAdapter(fetcher, client_id=settings.naver_search_client_id,
                                            client_secret=settings.naver_search_client_secret))
            else:
                log.info("naver trace adapter off (search API keys missing)")
        elif name == "zigzag":
            out.append(ZigzagAdapter(fetcher))
    return out
