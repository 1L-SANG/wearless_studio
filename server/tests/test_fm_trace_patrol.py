"""하루 1회 출처 순찰(fm_trace_patrol) — 2026-09-27.

지키는 것:
  - 상품명은 정리·중복 제거해서 검색한다(같은 이름을 두 번 찌르지 않는다).
  - 같은 이미지는 한 번만 받는다. 한 번에 받는 이미지 수에 상한이 있다.
  - 한 곳이 실패해도 다른 곳은 계속 돈다. 멈추라면 바로 멈춘다.
  - 매칭된 것만 원장에 남는다(대표 후보 1개 + 상위 후보 요약).
"""
import asyncio
import contextlib
from datetime import datetime, timezone

from app import fm_trace_findings
from app.services.fm_trace_sources import SearchHit
from app.workers import fm_trace_patrol as P


def test_queries_are_normalized_and_deduped():
    names = ["데님_셔츠", "데님 셔츠", " 나시 ", "", None, "a", "회색   후드 티셔츠", "나시", "데님셔츠"]
    assert P.queries_from_names(names) == ["데님 셔츠", "나시", "회색 후드 티셔츠"]
    assert len(P.queries_from_names([f"상품 {i}" for i in range(500)])) == P.MAX_QUERIES
    assert len(P.queries_from_names([f"상품 {i}" for i in range(500)], limit=None)) == 500


def _hit(platform, pid, image=None):
    return SearchHit(platform=platform, product_id=pid, product_url=f"https://{platform}/{pid}",
                     image_url=image or f"https://img/{pid}.jpg", title=f"상품 {pid}",
                     store_name="가게", store_id="s1")


class Adapter:
    def __init__(self, platform, hits, fail=False):
        self.platform = platform
        self.hits = hits
        self.fail = fail
        self.queries = []

    async def search(self, query):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("boom")
        return self.hits


class Fetcher:
    def __init__(self):
        self.fetched = []

    async def fetch_image(self, url):
        self.fetched.append(url)
        return None if "broken" in url else url.encode()


class Pool:
    def __init__(self):
        self.commits = 0

    @contextlib.asynccontextmanager
    async def connection(self):
        pool = self

        class Conn:
            async def commit(self):
                pool.commits += 1

        yield Conn()


def _cand(target_id="o1"):
    return {"target": "cut", "outputRecordId": target_id, "confidence": "medium",
            "evidence": {"watermark": False, "phashDistance": 2},
            "model": {"id": "m1"}, "seller": {"id": "s1"}}


def _run(adapters, *, matches, monkeypatch, **kw):
    recorded = []

    async def fake_record(conn, **args):
        recorded.append(args)
        return {"id": f"f{len(recorded)}", "inserted": True, "status": "new"}

    async def fake_trace(conn, data, *, origin, rows=None):
        assert rows == ["fp-rows"]                     # 지문은 실행마다 한 번만 적재
        url = data.decode()
        if url == "https://img/bad.jpg":
            raise ValueError("invalid image")
        return {"image": {"sha256": "ab" * 32}, "candidates": matches.get(url, [])}

    monkeypatch.setattr(fm_trace_findings, "record_patrol_finding", fake_record)
    fetcher = Fetcher()
    stats = asyncio.run(P.patrol_once(
        Pool(), adapters=adapters, fetcher=fetcher, queries=["데님 셔츠", "나시"],
        rows=["fp-rows"], origin="https://ai.wearless.kr", trace=fake_trace, **kw))
    return stats, recorded, fetcher


def test_patrol_records_only_matches_and_fetches_each_image_once(monkeypatch):
    zig = Adapter("zigzag", [_hit("zigzag", "1"), _hit("zigzag", "2"), _hit("zigzag", "bad"),
                             _hit("zigzag", "3", image="https://img/broken.jpg")])
    stats, recorded, fetcher = _run([zig], matches={"https://img/1.jpg": [_cand(), _cand("o9")]},
                                    monkeypatch=monkeypatch)
    assert zig.queries == ["데님 셔츠", "나시"]
    # 두 검색이 같은 상품을 돌려줘도 이미지는 한 번씩만 받는다
    assert fetcher.fetched == ["https://img/1.jpg", "https://img/2.jpg", "https://img/bad.jpg",
                               "https://img/broken.jpg"]
    assert len(recorded) == 1
    rec = recorded[0]
    assert rec["platform"] == "zigzag" and rec["product_id"] == "1"
    assert rec["candidate"]["outputRecordId"] == "o1" and len(rec["candidates"]) == 2
    assert rec["image_sha256"] == "ab" * 32 and rec["store_id"] == "s1"
    assert stats["zigzag"] == {"queries": 2, "hits": 8, "images": 3, "matched": 1,
                               "new": 1, "errors": 0, "budget": False}


def test_one_platform_failing_does_not_stop_the_other(monkeypatch):
    bad = Adapter("naver", [], fail=True)
    zig = Adapter("zigzag", [_hit("zigzag", "1")])
    stats, recorded, _ = _run([bad, zig], matches={"https://img/1.jpg": [_cand()]},
                              monkeypatch=monkeypatch)
    assert stats["naver"]["errors"] == 2 and stats["zigzag"]["matched"] == 1
    assert len(recorded) == 1


def test_image_budget_is_per_platform_and_counts_only_downloaded(monkeypatch):
    """앞 플랫폼이 예산을 다 써도 뒤 플랫폼은 돈다. 받지 못한 이미지(robots 거부·404)는 세지 않는다."""
    zig = Adapter("zigzag", [_hit("zigzag", "broken-a", image="https://img/broken-a.jpg")]
                  + [_hit("zigzag", str(i)) for i in range(10)])
    nav = Adapter("naver", [_hit("naver", f"n{i}") for i in range(10)])
    stats, _, fetcher = _run([zig, nav], matches={}, monkeypatch=monkeypatch, max_images=3)
    zig_fetched = [u for u in fetcher.fetched if "/n" not in u]
    assert zig_fetched[0] == "https://img/broken-a.jpg" and len(zig_fetched) == 4   # 실패 1 + 성공 3
    assert stats["zigzag"]["images"] == 3 and stats["zigzag"]["budget"] is True
    assert stats["naver"]["images"] == 3 and stats["naver"]["budget"] is True
    assert stats["stopped"] is None


def test_queries_rotate_by_day_so_old_projects_get_their_turn():
    names = [f"상품 {i}" for i in range(P.MAX_QUERIES + 50)]
    from datetime import date
    day1 = P.daily_queries(names, date(2026, 9, 27))
    day2 = P.daily_queries(names, date(2026, 9, 28))
    assert len(day1) == len(day2) == P.MAX_QUERIES
    assert day1 != day2
    everyone = set()
    for d in range(1, 30):
        everyone |= set(P.daily_queries(names, date(2026, 10, d)))
    assert everyone == set(P.queries_from_names(names, limit=None))


def test_stop_flag(monkeypatch):
    zig = Adapter("zigzag", [_hit("zigzag", str(i)) for i in range(10)])
    stats, _, fetcher = _run([zig], matches={}, monkeypatch=monkeypatch, stop=lambda: True)
    assert fetcher.fetched == [] and stats["stopped"] == "shutdown"


def test_due_after_the_configured_kst_hour():
    kst_3am = datetime(2026, 9, 26, 18, 0, tzinfo=timezone.utc)     # KST 09-27 03:00
    kst_4am = datetime(2026, 9, 26, 19, 0, tzinfo=timezone.utc)     # KST 09-27 04:00
    assert P.kst_due(kst_3am, hour=4) is None
    assert P.kst_due(kst_4am, hour=4).isoformat() == "2026-09-27"


def test_worker_is_wired_behind_its_own_switch():
    from pathlib import Path
    src = (Path(P.__file__).resolve().parents[1] / "main.py").read_text()
    assert 'settings.fm_trace_patrol == "on"' in src
    assert "TracePatrol(app)" in src


def test_api_manifest_turns_patrol_on_with_allowed_platforms_only():
    from pathlib import Path

    import yaml
    root = Path(P.__file__).resolve().parents[3]
    manifest = yaml.safe_load((root / "copilot/api/manifest.yml").read_text())
    env = manifest["variables"]
    assert env["FM_TRACE_PATROL"] == "on"
    assert set(env["FM_TRACE_PATROL_PLATFORMS"].split(",")) <= {"naver", "zigzag"}
    assert 0 <= int(env["FM_TRACE_PATROL_HOUR_KST"]) <= 23
    worker = yaml.safe_load((root / "copilot/detail-worker/manifest.yml").read_text())
    assert "FM_TRACE_PATROL" not in (worker.get("variables") or {})    # 순찰은 API 태스크에서만
