# 커스텀 매칭 의류 누끼 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 셀러가 올린 커스텀 매칭 의류의 배경을 SAM2 로 제거해 시드 카탈로그처럼 회색 배경 컷으로 화면·생성입력에 쓰이게 한다.

**Architecture:** 기존 `sam_client.segment_garment`(투명 컷아웃) + `garment_grid`(합성) + `sam_preprocess` 무과금 잡 패턴을 재사용한다. 커스텀 매칭 등록 커밋 후 신규 무과금 잡 `matching_cutout` 을 건다. 워커가 각 원본을 누끼→회색배경 합성→grid 재합성→asset 스왑한다. 실패는 원본 유지(fail-open). SAM 서비스 코드는 건드리지 않는다(각 원본을 `"Front"` view 로 순차 호출해 view 제약 우회).

**Tech Stack:** Python/FastAPI, Pillow, psycopg, 기존 SAM2 HTTP 서비스, React(Vite).

**Spec:** `docs/superpowers/specs/2026-08-13-matching-cutout-design.md`

## Global Constraints

- 무과금: `credits_reserved=0`, Gemini/VLM/이미지생성 호출 절대 없음.
- fail-open: SAM 미설정/실패/오선택 어떤 경우도 매칭 등록·선택을 막지 않는다. 원본 유지.
- 배경색 상수: `MATCHING_CUTOUT_BG = (232, 232, 230)` (시드 카탈로그 모서리색 실측값).
- 건드리지 않는다: `sam_service/*`(SAM 서비스), `segmentation.py`, `sam2-grid8-v2`, 톤 에디터, untuck 예산, 크레딧 정책, `matching.py` 추천 랭킹.
- 플래그 `MATCHING_CUTOUT`(기본 `off`) 뒤에 숨긴다 — 프로덕션 자동 영향 방지.
- 배포 금지. 마이그레이션은 파일만 만들고 실행하지 않는다.
- 테스트: `cd server && uv run pytest <path> -q`, 프론트 `node --test tests/frontend/<file>`.

---

## File Structure

- `server/app/services/matching_cutout.py` (신규) — 회색배경 합성 + 파생 asset 기록 + 알고리즘 버전. 순수/DB 헬퍼.
- `server/app/workers/matching_cutout_job.py` (신규) — 워커. 원본→누끼→합성→grid→스왑.
- `server/app/workers/dispatcher.py` (수정) — `_WORKERS` 에 등록.
- `server/app/config.py` (수정) — `matching_cutout` 플래그.
- `server/app/routes.py` (수정) — `add_custom_match_item` 커밋 후 enqueue 훅.
- `server/app/repo.py` (수정) — 매칭 조회에 `cutout_status`, asset 스왑 함수.
- `supabase/migrations/20260813010000_matching_cutout_job_kind.sql` (신규).
- `src/lib/api/matchingItems.js` (수정) — `cutoutStatus` 매핑.
- `src/features/analysis/AnalysisForm.jsx` (수정) — 카드 처리중/준비됨 렌더 + 안내.
- 테스트: `server/tests/test_matching_cutout.py`, `server/tests/test_matching_cutout_job.py`, `tests/frontend/matching-cutout-status.test.mjs`.

---

## Task 1: 회색배경 합성 서비스

**Files:**
- Create: `server/app/services/matching_cutout.py`
- Test: `server/tests/test_matching_cutout.py`

**Interfaces:**
- Produces:
  - `MATCHING_CUTOUT_BG = (232, 232, 230)`
  - `ALGORITHM_VERSION = "matching-cutout-v1"`
  - `flatten_on_bg(rgba_png: bytes) -> bytes` — 투명 RGBA PNG → 회색배경 불투명 PNG
  - `CUTOUT_KIND = "matchingCutout"`, `metadata_for(...) -> dict`

- [ ] **Step 1: 실패 테스트 작성**

```python
# server/tests/test_matching_cutout.py
import io
from PIL import Image
from app.services import matching_cutout as mc


def _rgba(alpha_box):
    img = Image.new("RGBA", (40, 60), (0, 0, 0, 0))
    for y in range(alpha_box[1], alpha_box[3]):
        for x in range(alpha_box[0], alpha_box[2]):
            img.putpixel((x, y), (200, 40, 40, 255))
    buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue()


def test_flatten_fills_transparent_with_seed_grey():
    out = mc.flatten_on_bg(_rgba((10, 10, 30, 50)))
    img = Image.open(io.BytesIO(out))
    assert img.mode == "RGB", "불투명이어야 한다"
    assert img.size == (40, 60), "소스 크기 유지"
    assert img.getpixel((0, 0)) == mc.MATCHING_CUTOUT_BG, "투명부는 시드 회색"
    assert img.getpixel((20, 30)) == (200, 40, 40), "옷 픽셀은 그대로"


def test_flatten_is_deterministic():
    src = _rgba((10, 10, 30, 50))
    assert mc.flatten_on_bg(src) == mc.flatten_on_bg(src)


def test_metadata_carries_provenance():
    meta = mc.metadata_for(source_hash="abc", source_asset_id="s1",
                           matching_item_id="custom_x")
    assert meta["type"] == mc.CUTOUT_KIND == "matchingCutout"
    assert meta["algorithmVersion"] == mc.ALGORITHM_VERSION
    for k in ("sourceHash", "sourceAssetId", "matchingItemId"):
        assert meta[k] is not None
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout.py -q`
Expected: FAIL (`No module named app.services.matching_cutout`)

- [ ] **Step 3: 최소 구현**

```python
# server/app/services/matching_cutout.py
"""커스텀 매칭 의류 누끼 결과를 시드 카탈로그 톤으로 정돈한다.

캐노니컬 컷아웃(sam_client)은 투명 RGBA 를 준다. 시드 카탈로그는 회색 스튜디오
flat-lay 라, 화면·생성입력에서 나란히 놨을 때 이질감이 없으려면 같은 회색 배경 위에
얹어 불투명으로 만든다. 배경색은 시드 이미지 모서리에서 실측한 상수 하나다.
"""
from __future__ import annotations

import io

from PIL import Image

#: 시드 카탈로그(seed/matching/*.png) 모서리에서 측정한 회색. 상수 하나로 고정.
MATCHING_CUTOUT_BG = (232, 232, 230)
#: 누끼 파생 asset 의 알고리즘 신원. 소스 해시와 함께 재처리 중복을 막는다.
ALGORITHM_VERSION = "matching-cutout-v1"
CUTOUT_KIND = "matchingCutout"
PRODUCER = "sam2-matching-cutout"


def flatten_on_bg(rgba_png: bytes) -> bytes:
    """투명 RGBA PNG → 회색배경 불투명 PNG. 소스 크기·옷 픽셀 보존."""
    with Image.open(io.BytesIO(rgba_png)) as opened:
        cut = opened.convert("RGBA")
        bg = Image.new("RGB", cut.size, MATCHING_CUTOUT_BG)
        bg.paste(cut, (0, 0), cut)  # 알파를 마스크로 — 투명부만 배경이 남는다
    out = io.BytesIO()
    bg.save(out, "PNG", optimize=False)
    return out.getvalue()


def metadata_for(*, source_hash: str | None, source_asset_id: str,
                 matching_item_id: str) -> dict:
    """누끼 파생 asset 의 provenance."""
    return {
        "type": CUTOUT_KIND,
        "producer": PRODUCER,
        "algorithmVersion": ALGORITHM_VERSION,
        "sourceHash": source_hash,
        "sourceAssetId": source_asset_id,
        "matchingItemId": matching_item_id,
    }
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: 커밋**

```bash
git add server/app/services/matching_cutout.py server/tests/test_matching_cutout.py
git commit -m "feat(matching): 누끼 결과를 시드 회색 배경으로 합성하는 서비스"
```

---

## Task 2: 인프라 — 마이그레이션·잡 등록·플래그

**Files:**
- Create: `supabase/migrations/20260813010000_matching_cutout_job_kind.sql`
- Modify: `server/app/workers/dispatcher.py`
- Modify: `server/app/config.py`
- Test: `server/tests/test_matching_cutout_job.py` (등록 가드만 이 태스크에서)

**Interfaces:**
- Produces: `_WORKERS["matching_cutout"]`, `settings.matching_cutout` (`"off"|"on"`)

- [ ] **Step 1: 등록 가드 테스트 작성**

```python
# server/tests/test_matching_cutout_job.py
import pathlib
from app.workers.dispatcher import _WORKERS

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_kind_registered_and_in_db_constraint():
    assert "matching_cutout" in _WORKERS
    migrations = sorted((SERVER.parent / "supabase" / "migrations").glob("*.sql"))
    latest = ""
    for p in migrations:
        text = p.read_text(encoding="utf-8")
        if "jobs_kind_check" in text and "add constraint" in text:
            latest = text
    assert "'matching_cutout'" in latest


def test_flag_defaults_off():
    from app.config import Settings
    assert Settings.__dataclass_fields__["matching_cutout"].default == "off"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_job.py -q`
Expected: FAIL (`matching_cutout` not in `_WORKERS`)

- [ ] **Step 3: 마이그레이션 작성**

가장 최근 `jobs_kind_check` 제약을 만드는 마이그레이션을 찾아 그 kind 목록을 그대로 복사하고 `'matching_cutout'` 을 추가한다. 활성 유니크 인덱스도 같이 재생성한다(커스텀 매칭은 프로젝트당 하나라 제외 목록에 넣지 않아도 유니크 충돌이 없지만, 재생성 시 최신 정의를 보존한다).

```sql
-- supabase/migrations/20260813010000_matching_cutout_job_kind.sql
-- 커스텀 매칭 의류 누끼(배경 제거) 잡 kind `matching_cutout` 추가.
--
-- 셀러가 올린 커스텀 매칭 의류의 배경을 SAM2 로 제거해 시드 카탈로그처럼 회색 배경
-- 컷으로 만드는 무과금 잡이다. 이미지 생성·크레딧 소비 없음. 실패해도 매칭 등록·선택에
-- 영향이 없다(원본 유지). 커스텀 매칭은 프로젝트당 하나라 활성 유니크 인덱스 정책은
-- 기존과 동일하게 유지한다.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs add constraint jobs_kind_check
  check (kind in ('analyze', 'mannequin', 'mannequin_adjust', 'detail_page', 'editor_image',
                  'personalization_generation', 'personalization_purge',
                  'fm_model_asset_build', 'export', 'sam_preprocess',
                  'base_fidelity_observe', 'editor_garment_mask', 'matching_cutout'));
```

주의: 위 kind 목록은 origin/main 최신의 `jobs_kind_check` 와 정확히 일치해야 한다. 구현 시 최신 마이그레이션을 열어 목록을 복사하고 `'matching_cutout'` 만 덧붙일 것. 목록이 다르면 CD 의 `supabase db push` 가 실패한다.

- [ ] **Step 4: config 플래그 추가**

`server/app/config.py` 의 Settings dataclass에 필드 추가(다른 `_flag` 필드 옆):

```python
    matching_cutout: str = "off"  # 커스텀 매칭 의류 누끼(배경 제거). off면 잡 안 돎.
```

`load_settings()` 의 반환 dict 에 추가(다른 `_flag(...)` 호출 옆):

```python
        matching_cutout=_flag("MATCHING_CUTOUT", "off", {"off", "on"}),
```

- [ ] **Step 5: dispatcher 등록**

`server/app/workers/dispatcher.py` 상단 import 에 추가:

```python
from .matching_cutout_job import run_matching_cutout_job
```

`_WORKERS` dict 에 추가:

```python
    # 커스텀 매칭 의류 누끼(무과금·이미지 생성 없음). 커스텀 매칭 등록 후 백그라운드로 돈다.
    "matching_cutout": run_matching_cutout_job,
```

(주의: 이 시점에 `matching_cutout_job.py` 가 없으면 import 에러. Task 3 의 워커 파일을 먼저 스텁으로 만들거나, 이 스텝을 Task 3 완료 후로 미룬다. 실행 순서상 Task 3 의 Step 1~3 을 먼저 하고 이 스텝을 마지막에 둔다.)

- [ ] **Step 6: 통과 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_job.py::test_kind_registered_and_in_db_constraint tests/test_matching_cutout_job.py::test_flag_defaults_off -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add supabase/migrations/20260813010000_matching_cutout_job_kind.sql server/app/config.py server/app/workers/dispatcher.py server/tests/test_matching_cutout_job.py
git commit -m "feat(matching): matching_cutout 잡 kind·플래그·마이그레이션"
```

---

## Task 3: 워커 — 원본→누끼→합성→grid→스왑

**Files:**
- Create: `server/app/workers/matching_cutout_job.py`
- Modify: `server/app/repo.py` (asset 스왑 함수 `swap_matching_item_assets`)
- Test: `server/tests/test_matching_cutout_job.py` (동작 테스트 추가)

**Interfaces:**
- Consumes: `sam_client.segment_garment`, `matching_cutout.flatten_on_bg`, `garment_grid.compose_garment_grid`, `repo.finalize_uncharged_job`
- Produces:
  - `run_matching_cutout_job(app, job: dict) -> None`
  - `repo.swap_matching_item_assets(conn, *, matching_item_id, project_id, thumbnail_asset_id, image_asset_id) -> None`
  - job payload: `{"matchingItemId": str, "sourceAssetIds": list[str], "sourceKeys": list[str]}`

- [ ] **Step 1: 동작 테스트 작성**

각 원본을 `segment_garment(s, {"Front": key})` 로 순차 호출하고, 결과 컷아웃을 회색배경 합성해 새 asset 으로 저장하고, grid 를 재합성해 매칭 아이템의 두 asset 을 스왑하는 것을 검증한다. SAM·R2·repo 는 가짜로 주입한다.

```python
# server/tests/test_matching_cutout_job.py 에 추가
import asyncio
import types
from app.services.sam_client import SamViewResult
from app.workers import matching_cutout_job as job


class _FakeR2:
    def __init__(self, cut_png): self._cut = cut_png; self.puts = []
    def get_bytes(self, key): return self._cut  # 컷아웃 PNG 반환
    def put_bytes(self, key, data, mime, cache=None): self.puts.append((key, data, mime))


def _settings(**over):
    base = dict(matching_cutout="on", sam_service_url="http://sam", sam_internal_token="t",
                r2_bucket="b")
    base.update(over)
    return types.SimpleNamespace(**base)


def _run(app, job_dict):
    return asyncio.run(job.run_matching_cutout_job(app, job_dict))


def test_worker_cutouts_each_source_and_swaps_assets(monkeypatch):
    import io
    from PIL import Image
    # 투명 컷아웃 PNG 하나를 SAM 결과 R2 객체로 돌려준다
    rgba = Image.new("RGBA", (30, 40), (10, 120, 200, 255))
    buf = io.BytesIO(); rgba.save(buf, "PNG"); cut_png = buf.getvalue()

    calls = {"segment": [], "swap": None, "finalize": None}

    async def fake_segment(settings, views):
        calls["segment"].append(views)
        # cutout_key = 소스키 기반 가짜
        (v, k), = views.items()
        return {v: SamViewResult(view=v, ready=True, cutout_key=f"cut/{k}",
                                 source_hash="h"+k, width=30, height=40)}

    monkeypatch.setattr(job.sam_client, "segment_garment", fake_segment)

    async def fake_swap(conn, *, matching_item_id, project_id, thumbnail_asset_id, image_asset_id):
        calls["swap"] = (matching_item_id, thumbnail_asset_id, image_asset_id)

    monkeypatch.setattr(job.repo, "swap_matching_item_assets", fake_swap)

    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        calls["finalize"] = (status, result.get("state"))

    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)

    # DB 커넥션·이벤트는 no-op
    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass
    class _Pool:
        def connection(self): return _Conn()
    r2 = _FakeR2(cut_png)
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(), pool=_Pool(), r2=r2))
    job_dict = {"id": "j1", "project_id": "p1", "user_id": "u1", "lease_token": "lt",
                "payload": {"matchingItemId": "custom_x",
                            "sourceAssetIds": ["a1", "a2"],
                            "sourceKeys": ["users/u/projects/p/uploads/a1.jpg",
                                           "users/u/projects/p/uploads/a2.jpg"]}}
    _run(app, job_dict)

    assert len(calls["segment"]) == 2, "원본 2장 각각 누끼"
    assert all("Front" in v for v in calls["segment"]), "view=Front 로 우회"
    assert calls["swap"] is not None, "asset 스왑됨"
    assert calls["finalize"][0] == "done" and calls["finalize"][1] == "ready"
    # 회색배경 합성본 + grid = R2 put 최소 3회(장2 + grid1)
    assert len(r2.puts) >= 3


def test_worker_skips_when_flag_off(monkeypatch):
    calls = {"finalize": None}
    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        calls["finalize"] = (status, result.get("state"))
    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)
    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass
    class _Pool:
        def connection(self): return _Conn()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(matching_cutout="off"), pool=_Pool(), r2=_FakeR2(b"")))
    _run(app, {"id": "j", "project_id": "p", "user_id": "u", "lease_token": "lt",
               "payload": {"matchingItemId": "x", "sourceAssetIds": [], "sourceKeys": []}})
    assert calls["finalize"][1] == "skipped"


def test_worker_keeps_original_when_sam_fails(monkeypatch):
    from app.services.sam_client import SamUnavailable
    calls = {"swap": False, "finalize": None}
    async def fake_segment(settings, views):
        raise SamUnavailable("down")
    monkeypatch.setattr(job.sam_client, "segment_garment", fake_segment)
    async def fake_swap(conn, **k): calls["swap"] = True
    monkeypatch.setattr(job.repo, "swap_matching_item_assets", fake_swap)
    async def fake_finalize(conn, *, job_id, lease_token, status, result):
        calls["finalize"] = (status, result.get("state"))
    monkeypatch.setattr(job.repo, "finalize_uncharged_job", fake_finalize)
    class _Conn:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def commit(self): pass
    class _Pool:
        def connection(self): return _Conn()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=_settings(), pool=_Pool(), r2=_FakeR2(b"")))
    _run(app, {"id": "j", "project_id": "p", "user_id": "u", "lease_token": "lt",
               "payload": {"matchingItemId": "x", "sourceAssetIds": ["a1"],
                           "sourceKeys": ["users/u/projects/p/uploads/a1.jpg"]}})
    assert calls["swap"] is False, "실패 시 스왑 안 함 = 원본 유지"
    assert calls["finalize"][0] in ("error", "done")
    assert calls["finalize"][1] in ("unavailable", "failed")
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_job.py -q`
Expected: FAIL (`No module named app.workers.matching_cutout_job`)

- [ ] **Step 3: 워커 구현**

```python
# server/app/workers/matching_cutout_job.py
"""`matching_cutout` — 커스텀 매칭 의류의 배경을 SAM2 로 제거한다.

커스텀 매칭 등록이 커밋된 뒤 백그라운드로 돈다. 셀러 화면에는 이미 원본이 떠 있고,
이 잡은 그걸 시드 카탈로그 톤(회색 배경 컷)으로 조용히 교체한다. 아무것도 이 잡의
성공에 걸려 있지 않다 — SAM 미설정·다운·오선택 어떤 경우도 원본을 그대로 두고,
매칭 등록·선택은 내내 가능하다. 무과금·이미지 생성 없음.

SAM 서비스는 view 를 Front/Back 으로 강제하므로, 각 매칭 원본을 `"Front"` 로 순차
호출해 우회한다(뷰 이름은 SAM 캐시 키의 일부일 뿐, 매칭에선 의미 없다).
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from app import repo
from app.config import load_settings
from app.services import garment_grid, matching_cutout, sam_client
from app.r2 import derived_key

log = logging.getLogger("wearless.matching_cutout")

SKIP_DISABLED = "matching_cutout_disabled"
SKIP_SAM = "sam_not_configured"
SKIP_NO_SOURCES = "no_source_assets"


async def run_matching_cutout_job(app, job: dict) -> None:
    pool = app.state.pool
    r2 = app.state.r2
    job_id, project_id = job["id"], job["project_id"]
    user_id, lease_token = job["user_id"], job["lease_token"]
    s = load_settings()
    payload = job.get("payload") or {}
    matching_item_id = payload.get("matchingItemId")
    source_keys = payload.get("sourceKeys") or []

    async def finish(status: str, detail: dict) -> None:
        async with pool.connection() as conn:
            await repo.finalize_uncharged_job(
                conn, job_id=job_id, lease_token=lease_token, status=status, result=detail)
            await conn.commit()
        log.info("matching_cutout job=%s project=%s %s %s",
                 job_id, project_id, status, detail.get("state"))

    async def skip(reason: str) -> None:
        await finish("done", {"state": "skipped", "reason": reason,
                              "matchingItemId": matching_item_id})

    if getattr(s, "matching_cutout", "off") != "on":
        await skip(SKIP_DISABLED)
        return
    if not sam_client.configured(s):
        await skip(SKIP_SAM)
        return
    if not matching_item_id or not source_keys:
        await skip(SKIP_NO_SOURCES)
        return

    # 1) 각 원본을 누끼 → 회색배경 합성 → derived R2. view 는 Front 로 우회.
    cut_pngs: list[bytes] = []
    try:
        for key in source_keys:
            results = await sam_client.segment_garment(s, {"Front": key})
            view = results.get("Front")
            if view is None or not view.ready or not view.cutout_key:
                await finish("done", {"state": "failed", "reason": "no_cutout",
                                      "matchingItemId": matching_item_id})
                return
            cutout_bytes = await asyncio.to_thread(r2.get_bytes, view.cutout_key)
            cut_pngs.append(await asyncio.to_thread(
                matching_cutout.flatten_on_bg, cutout_bytes))
    except sam_client.SamUnavailable as exc:
        await finish("error", {"state": "unavailable", "reason": str(exc),
                               "matchingItemId": matching_item_id})
        return

    # 2) 회색배경 컷을 각각 asset 으로 저장 (썸네일 = 첫 장)
    thumb_asset_id = str(uuid.uuid4())
    thumb_key = derived_key(user_id, project_id, thumb_asset_id, "png")
    await asyncio.to_thread(r2.put_bytes, thumb_key, cut_pngs[0], "image/png")

    # 3) 누끼본으로 grid 재합성 (마네킹 생성 입력)
    grid_bytes = await asyncio.to_thread(garment_grid.compose_garment_grid, cut_pngs)
    grid_asset_id = str(uuid.uuid4())
    grid_key = derived_key(user_id, project_id, grid_asset_id, "jpg")
    await asyncio.to_thread(r2.put_bytes, grid_key, grid_bytes, "image/jpeg")

    # 4) asset 행 생성 + 매칭 아이템 스왑 (원자적)
    async with pool.connection() as conn:
        await repo.create_asset(
            conn, asset_id=thumb_asset_id, user_id=user_id, project_id=project_id,
            source="derived", bucket=s.r2_bucket, key=thumb_key, mime="image/png",
            size=len(cut_pngs[0]), original_filename=None)
        await repo.create_asset(
            conn, asset_id=grid_asset_id, user_id=user_id, project_id=project_id,
            source="derived", bucket=s.r2_bucket, key=grid_key, mime="image/jpeg",
            size=len(grid_bytes), original_filename=None)
        await repo.swap_matching_item_assets(
            conn, matching_item_id=matching_item_id, project_id=project_id,
            thumbnail_asset_id=thumb_asset_id, image_asset_id=grid_asset_id)
        await conn.commit()

    await finish("done", {"state": "ready", "matchingItemId": matching_item_id,
                          "thumbnailAssetId": thumb_asset_id, "imageAssetId": grid_asset_id})
```

- [ ] **Step 4: repo 스왑 함수 추가**

`server/app/repo.py` 에 `insert_custom_matching_item` 근처(매칭 관련 함수들 옆)에 추가:

```python
async def swap_matching_item_assets(
    conn: AsyncConnection, *, matching_item_id: str, project_id: str,
    thumbnail_asset_id: str, image_asset_id: str,
) -> None:
    """커스텀 매칭 아이템의 표시·생성입력 asset 을 누끼본으로 교체한다.

    project_id 로 한 번 더 스코프해 다른 프로젝트의 아이템을 건드리지 않는다.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            "update matching_items set thumbnail_asset_id = %s, image_asset_id = %s "
            "where id = %s and project_id = %s and owner_user_id is not null",
            (thumbnail_asset_id, image_asset_id, matching_item_id, project_id))
```

- [ ] **Step 5: dispatcher import 스텁 해제**

Task 2 Step 5 에서 미뤘다면 지금 `dispatcher.py` 의 import·`_WORKERS` 등록을 넣는다.

- [ ] **Step 6: 통과 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_job.py -q`
Expected: PASS (등록 가드 2 + 동작 3 = 5 passed)

- [ ] **Step 7: 커밋**

```bash
git add server/app/workers/matching_cutout_job.py server/app/repo.py server/app/workers/dispatcher.py server/tests/test_matching_cutout_job.py
git commit -m "feat(matching): 누끼 워커 — 원본→컷아웃→회색합성→grid→asset 스왑"
```

---

## Task 4: 라우트 enqueue 훅

**Files:**
- Modify: `server/app/routes.py` (`add_custom_match_item` 끝 커밋 후)
- Test: `server/tests/test_matching_cutout_enqueue.py`

**Interfaces:**
- Consumes: `repo.create_job`
- Produces: 커스텀 매칭 등록 성공 후 `matching_cutout` 잡 enqueue (커밋 뒤·예외 삼킴)

- [ ] **Step 1: 테스트 작성 (소스 구조 검증)**

enqueue 가 커밋 뒤에 있고, 무과금이고, 실패를 삼키는지 소스 레벨로 검증한다(전체 라우트 통합은 무겁다).

```python
# server/tests/test_matching_cutout_enqueue.py
import inspect
import pathlib
from app.app_factory import build_matching_cutout_enqueue  # 없으면 아래 함수명에 맞춰 수정

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_enqueue_is_uncharged_and_swallows_and_after_commit():
    src = (SERVER / "app" / "routes.py").read_text(encoding="utf-8")
    fn_start = src.index("async def _enqueue_matching_cutout")
    fn = src[fn_start:fn_start + 1500]
    assert "credits_reserved=0" in fn, "무과금"
    assert "matching_cutout" in fn
    assert "except Exception" in fn, "큐잉 실패를 삼켜야 등록이 안 죽는다"
    # 호출부가 insert_custom_matching_item 커밋 뒤인지
    call = src.index("await _enqueue_matching_cutout(")
    commit_before = src.rfind("await conn.commit()", 0, call)
    insert = src.rfind("insert_custom_matching_item", 0, call)
    assert insert < commit_before < call, "enqueue 는 insert·커밋 뒤"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_enqueue.py -q`
Expected: FAIL (`_enqueue_matching_cutout` 없음)

- [ ] **Step 3: enqueue 헬퍼 + 호출 추가**

`server/app/routes.py` 에 헬퍼 추가(다른 `_enqueue_*` 옆):

```python
async def _enqueue_matching_cutout(conn, *, user_id, project_id, matching_item_id,
                                   source_asset_ids, source_keys):
    """커스텀 매칭 의류 누끼 잡을 건다. 절대 등록을 막지 않는다.

    커밋 뒤에 별도로 돈다 — 큐잉 실패가 방금 성공한 매칭 등록을 되돌리면 본말전도다
    (2026-08-12 sam_preprocess·tone editor 에서 같은 규율).
    """
    import contextlib
    try:
        await repo.create_job(
            conn, user_id=user_id, project_id=project_id, kind="matching_cutout",
            payload={"matchingItemId": matching_item_id,
                     "sourceAssetIds": source_asset_ids, "sourceKeys": source_keys},
            idempotency_key=(f"{project_id}:matching_cutout:{matching_item_id}:"
                             f"{matching_cutout.ALGORITHM_VERSION}"),
            credits_reserved=0, metadata={})
        await conn.commit()
    except Exception:  # noqa: BLE001 - 큐잉 실패가 매칭 등록을 되돌리지 않는다
        with contextlib.suppress(Exception):
            await conn.rollback()
        logger.warning("matching_cutout enqueue failed project=%s item=%s",
                       project_id, matching_item_id, exc_info=True)
```

`add_custom_match_item` 의 `insert_custom_matching_item` 이 커밋된 **뒤**에 호출한다. 반환된 매칭 아이템 id 와 `asset_ids`(소스), 그 소스들의 R2 키를 넘긴다. 소스 키는 이미 `assets` 조회에서 `r2_key` 로 얻을 수 있다(라우트 상단 `get_uploaded_assets_for_project` 결과). 필요한 import: 파일 상단에 `from .services import matching_cutout` 추가. `_wake_dispatcher(request)` 도 호출해 즉시 클레임.

- [ ] **Step 4: 통과 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_enqueue.py -q`
Expected: PASS

- [ ] **Step 5: 앱 기동 + 라우트 회귀 확인**

Run: `cd server && uv run python -c "from app.main import create_app; create_app(); print('boots')"`
Expected: `boots`

Run: `cd server && uv run pytest tests/ -q -k "custom_match or matching" 2>&1 | tail -3`
Expected: 기존 커스텀 매칭 테스트 통과

- [ ] **Step 6: 커밋**

```bash
git add server/app/routes.py server/tests/test_matching_cutout_enqueue.py
git commit -m "feat(matching): 커스텀 매칭 등록 커밋 후 누끼 잡 enqueue"
```

---

## Task 5: 상태 노출 — 매칭 조회에 cutout_status

**Files:**
- Modify: `server/app/repo.py` (`list_active_matching_items` SELECT)
- Modify: `server/app/routes.py` (`match-candidates` 응답 매핑)
- Test: `server/tests/test_matching_cutout_status.py`

**Interfaces:**
- Produces: 매칭 아이템 응답에 `cutoutStatus: "processing"|"ready"|"failed"|null`

- [ ] **Step 1: 상태 판정 로직 테스트**

상태는 커스텀 아이템의 현재 asset 이 누끼 파생인지로 판정한다: `image_asset` 의 metadata `type == "matchingCutout"` 이면 `ready`, 아니면(원본 grid 면) 진행 중 잡이 있으면 `processing`, 없으면 원본 그대로. 순수 판정 함수로 뺀다.

```python
# server/tests/test_matching_cutout_status.py
from app.services.matching_cutout import cutout_status_for


def test_ready_when_asset_is_cutout_derived():
    assert cutout_status_for(is_custom=True, image_meta={"type": "matchingCutout"},
                             has_active_job=False) == "ready"


def test_processing_when_job_active_and_not_yet_swapped():
    assert cutout_status_for(is_custom=True, image_meta={}, has_active_job=True) == "processing"


def test_none_for_seed_items():
    assert cutout_status_for(is_custom=False, image_meta={}, has_active_job=False) is None


def test_failed_when_no_job_and_not_cutout():
    # 잡이 끝났는데(active 아님) 여전히 원본이면 실패로 본다
    assert cutout_status_for(is_custom=True, image_meta={}, has_active_job=False) == "failed"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_status.py -q`
Expected: FAIL (`cutout_status_for` 없음)

- [ ] **Step 3: 판정 함수 구현**

`server/app/services/matching_cutout.py` 에 추가:

```python
def cutout_status_for(*, is_custom: bool, image_meta: dict | None,
                      has_active_job: bool) -> str | None:
    """커스텀 매칭 아이템의 누끼 상태. 시드는 항상 None.

    - ready: 현재 생성입력 asset 이 이미 누끼 파생이다(스왑 완료).
    - processing: 아직 원본인데 누끼 잡이 돌고 있다.
    - failed: 잡이 끝났는데 여전히 원본이다(SAM 실패 등). 화면은 원본을 그대로 보여준다.
    """
    if not is_custom:
        return None
    if isinstance(image_meta, dict) and image_meta.get("type") == CUTOUT_KIND:
        return "ready"
    return "processing" if has_active_job else "failed"
```

- [ ] **Step 4: repo 조회 확장**

`list_active_matching_items` 의 SELECT 에 `img.metadata` 와, 프로젝트에 활성 `matching_cutout` 잡이 있는지를 함께 읽는다. `owner_user_id` 로 커스텀 여부 판정. 라우트에서 `cutout_status_for` 로 `cutoutStatus` 를 만들어 응답에 싣는다.

(SELECT 에 `img.metadata as image_meta`, `mi.owner_user_id` 추가. 활성 잡은 별도 쿼리 `select exists(select 1 from jobs where project_id=%s and kind='matching_cutout' and status in ('pending','running'))` 를 한 번 조회해 모든 커스텀 아이템에 공유.)

- [ ] **Step 5: 라우트 응답 매핑**

`match_candidates` 핸들러에서 각 아이템의 `cutoutStatus` 를 계산해 응답 dict 에 추가. 프론트 계약(`toMatchItem`)이 읽을 camelCase 키 `cutoutStatus`.

- [ ] **Step 6: 통과 확인**

Run: `cd server && uv run pytest tests/test_matching_cutout_status.py -q`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add server/app/services/matching_cutout.py server/app/repo.py server/app/routes.py server/tests/test_matching_cutout_status.py
git commit -m "feat(matching): 매칭 조회에 cutoutStatus 노출 (기존 폴링 재사용)"
```

---

## Task 6: 프론트 — 카드 처리중/준비됨 + 안내

**Files:**
- Modify: `src/lib/api/matchingItems.js` (`toMatchItem` 에 `cutoutStatus`)
- Modify: `src/features/analysis/AnalysisForm.jsx` (카드 렌더 + 폴링)
- Test: `tests/frontend/matching-cutout-status.test.mjs`

**Interfaces:**
- Consumes: API 응답 `cutoutStatus`
- Produces: 처리 중 카드 = 스켈레톤 + 안내, 준비 시 이미지 표시

- [ ] **Step 1: 매퍼 테스트 작성**

```javascript
// tests/frontend/matching-cutout-status.test.mjs
import assert from 'node:assert/strict';
import test from 'node:test';
import { toMatchItem } from '../../src/lib/api/matchingItems.js';

test('toMatchItem 이 cutoutStatus 를 통과시킨다', () => {
  const item = toMatchItem({ id: 'custom_x', name: '내 바지', isCustom: true,
    cutoutStatus: 'processing' }, null);
  assert.equal(item.cutoutStatus, 'processing');
});

test('시드 아이템은 cutoutStatus 가 없다', () => {
  const item = toMatchItem({ id: 'match_women_top_01', name: '시드' }, null);
  assert.equal(item.cutoutStatus ?? null, null);
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/frontend/matching-cutout-status.test.mjs`
Expected: FAIL (`cutoutStatus` undefined)

- [ ] **Step 3: 매퍼 수정**

`src/lib/api/matchingItems.js` 의 `toMatchItem` 반환 객체에 추가:

```javascript
  cutoutStatus: item.cutoutStatus ?? null,
```

- [ ] **Step 4: 통과 확인**

Run: `node --test tests/frontend/matching-cutout-status.test.mjs`
Expected: PASS

- [ ] **Step 5: 카드 렌더 + 폴링 (수동 UI)**

`AnalysisForm.jsx` 의 매칭 카드 렌더에서 `cutoutStatus === 'processing'` 이면 이미지 대신 스켈레톤 + 안내 문구를 보여준다:

```jsx
{item.cutoutStatus === 'processing' ? (
  <div className="match-card-cutout-pending">
    <Icon name="loader" className="spin" size={16} />
    <span>이미지 업로드됐어요! 지금 배경 정리 중이에요</span>
  </div>
) : (
  <img src={item.thumbnailUrl || item.thumb} alt={item.name} loading="lazy" />
)}
```

`processing` 상태인 커스텀 아이템이 하나라도 있으면 `refreshMatchClothing` 을 일정 간격(예: 5s)으로 폴링하고, `ready`/`failed` 로 바뀌면 폴링 중단. 기존 `refreshMatchClothing` 을 재사용한다(새 API 없음). CSS `.match-card-cutout-pending` 는 기존 스켈레톤·surface 토큰을 따른다.

- [ ] **Step 6: 프론트 스위트 회귀**

Run: `node --test tests/frontend/*.test.mjs 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: fail 0

Run: `npm run build 2>&1 | tail -1`
Expected: `✓ built`

- [ ] **Step 7: 커밋**

```bash
git add src/lib/api/matchingItems.js src/features/analysis/AnalysisForm.jsx tests/frontend/matching-cutout-status.test.mjs
git commit -m "feat(matching): 누끼 처리중 카드 안내 + 준비되면 폴링 교체"
```

---

## Task 7: 시각 QA 품질 게이트 (스크래치, 커밋 안 함)

**Files:**
- Scratchpad only: `scratchpad/matching_cutout_qa.py` (레포 커밋 X)

**목적:** 매장 행거 사진(배경 옷들·비스듬 각도)에서 캐노니컬 selector 가 옷만 제대로 따는지 **눈으로** 확인한다. 숫자 지표가 아니라 오버레이. 통과 못 하면 배선을 켜지 말고 보고한다.

- [ ] **Step 1: 실제 커스텀 업로드 샘플 수집**

prod DB(읽기만)에서 `owner_user_id is not null` 인 커스텀 매칭 아이템의 소스 원본 R2 키를 5~10개 뽑아 로컬로 받는다. (이 세션에서 확인한 실물 = 매장 행거 사진이 대표 케이스.)

- [ ] **Step 2: 각 원본 누끼 → 회색합성 오버레이 시트**

`sam_client.segment_garment`(로컬 SAM :8090) 또는 `Sam2Segmenter` 직접 호출로 컷아웃 → `flatten_on_bg` → [원본 | 컷 오버레이 | 회색합성] 컨택트 시트 생성.

- [ ] **Step 3: 눈으로 판정**

각 샘플: 배경 옷을 골랐나 / 옷을 잘못 잘랐나 / 깔끔한가. 사용 가능 비율 기록.

- [ ] **Step 4: 게이트 결정**

- 대부분 깨끗 → 플래그 켤 준비 완료로 보고.
- 배경 옷 오선택이 잦음 → **플래그 off 유지**, 원인(selector 가정 vs 행거 사진 분포)과 대안(예: 커스텀만 다른 selector 파라미터, 신뢰도 게이트) 보고. 배선은 코드에 있되 프로덕션은 off.

- [ ] **Step 5: 보고 (커밋 없음)**

시각 결과와 게이트 판정을 사용자에게 보고한다. 스크래치 파일은 커밋하지 않는다.

---

## Self-Review 결과

- **Spec 커버리지:** 스코프(B)→Task3 grid 재합성, 배경 회색→Task1, 신규만→라우트 훅(등록 시점만), 등록 UX(처리중 안내·폴링)→Task6, fail-open→Task3, 무과금 잡→Task2/3, 상태 노출(기존 폴링)→Task5/6, 품질 게이트→Task7, 마이그레이션→Task2. 전 항목 태스크 있음.
- **Placeholder:** 없음. 각 코드 스텝에 실제 코드.
- **타입 일관성:** `flatten_on_bg`/`ALGORITHM_VERSION`/`CUTOUT_KIND`/`cutout_status_for`/`swap_matching_item_assets`/`run_matching_cutout_job` 이름이 정의 태스크와 사용 태스크에서 일치.
- **주의(실행자):** Task2 dispatcher import 는 Task3 워커 파일 생성 후에 넣는다(순환/import 에러 방지). `list_active_matching_items` 의 실제 SELECT·`match_candidates` 응답 dict 구조는 구현 시 현재 코드를 열어 정확한 컬럼·키에 맞춘다(Task5 는 추가할 필드만 명시).
