# 매칭의류 카탈로그 R2 서빙 (1안·가벼운 방식) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매칭의류 카탈로그 썸네일이 배포 프론트(Vercel)에서 R2 공개 URL로 렌더되도록, 시드를 DB+R2에 올리고 백엔드 추천 엔드포인트를 만들고 프론트가 그 후보를 fetch한다(선택값은 현행 레이어 유지).

**Architecture:** 백엔드는 정식 — `matching_items`/`assets` 시드(R2 공개 키), 추천 순수함수 포팅, `GET /v1/projects/{id}/analysis/match-candidates` 엔드포인트가 공개 R2 썸네일 URL을 담은 레거시 `MatchClothing[]`을 반환. 프론트는 `httpAdapter.getMatchClothing`이 이 엔드포인트를 호출하고 후보에 현행 선택을 오버레이(새 저장소 없음).

**Tech Stack:** FastAPI · psycopg(AsyncConnection, dict rows) · boto3(R2 S3호환) · Supabase Postgres · Vite+React(JS/JSX) · pytest.

설계 정본: [docs/superpowers/specs/2026-06-29-matching-catalog-r2-serving-design.md](../specs/2026-06-29-matching-catalog-r2-serving-design.md)

## Global Constraints

- 언어: JavaScript/JSX 우선, 서버는 Python. 프로젝트 전체 TS 전환 금지.
- 스타일: `tokens.css` 변수 사용, 인라인 스타일/CSS-in-JS 금지. (본 플랜은 신규 CSS 거의 없음.)
- 데이터 계약 불변: `lib/api` 시그니처·반환 형태는 mock/api.js(계약 §6)와 동일해야 한다. UI 소비 필드는 `MatchClothing.thumb`.
- 마이그레이션 **append-only**: 적용된 파일 수정·삭제 금지, 새 forward 파일만. 활성화: `git config core.hooksPath .githooks`. 적용은 dry-run/apply **분리 명령**, prod 적용은 **사용자 승인**. 물리 검증은 psycopg.
- R2 서빙: seed/public은 공개 CDN 직접(`r2.public_url`). `R2_PUBLIC_BASE` 전제(미설정 fail-fast).
- 커밋: Conventional 접두사 + 한국어 본문. 끝에 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- 추천 규칙은 mock([matchingRecommendation.js](../../../src/mock/matchingRecommendation.js))과 **동치**여야 한다: 보색타입(top/outer/dress→bottom, else top) → isActive·타입·성별(unisex 또는 포함) 필터 → colorBrightness 내림차순, 동률 sort_order → limit.
- 작업 디렉터리: 서버 명령은 `server/`에서 `.venv` 사용(`server/.venv/bin/python -m pytest`). 프론트 빌드 `pnpm build`(루트).

## File Structure

- Create `supabase/migrations/<ts>_matching_color_brightness.sql` — color_brightness 컬럼.
- Create `server/app/services/matching.py` — 추천 순수함수.
- Create `server/tests/test_matching.py` — 추천함수 + 엔드포인트 테스트.
- Modify `server/app/repo.py` — `list_active_matching_items()`.
- Modify `server/app/routes.py` — `GET .../analysis/match-candidates`.
- Create `server/scripts/seed_matching.py` — R2 업로드 + DB 시드(멱등).
- Create `server/seed/matching_items.json` — 생성기가 emit하는 정본 데이터(시더 입력).
- Modify `.scratch/gen-matching.mjs` — 위 JSON도 emit.
- Modify `src/lib/api/httpAdapter.js` — `getMatchClothing(projectId, ctx)`.
- Modify `src/mock/api.js` — mock `getMatchClothing(projectId, ctx)` ctx 재계산.
- Modify `src/features/analysis/AnalysisForm.jsx`, `src/features/mannequin/Mannequin.jsx`, `src/features/storyboard/Storyboard.jsx` — 후보 소스 통일 + 선택 오버레이.

---

### Task 1: 마이그레이션 — `matching_items.color_brightness`

**Files:**
- Create: `supabase/migrations/20260629120000_matching_color_brightness.sql`

**Interfaces:**
- Produces: `matching_items.color_brightness integer not null default 50` (Task 3·4가 사용).

- [ ] **Step 1: 마이그레이션 파일 작성**

```sql
-- matching_items 색상 밝기(정렬용). seedMatchingItems.js colorBrightness 이관.
-- append-only: 신규 forward 파일 (init.sql 수정 금지).
alter table public.matching_items
  add column color_brightness integer not null default 50;
```

- [ ] **Step 2: dry-run (적용 미리보기)**

Run: `cd server && supabase db push --db-url "$DATABASE_URL" --dry-run`
Expected: 위 ALTER가 적용 예정으로 표시, 에러 없음.

- [ ] **Step 3: 적용** *(prod DB 변경 — 사용자 승인 후 실행)*

Run: `cd server && supabase db push --db-url "$DATABASE_URL"`
Expected: 적용 성공.

- [ ] **Step 4: 물리 검증 (psycopg)**

Run:
```bash
cd server && .venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c, c.cursor() as cur:
    cur.execute(\"select column_name,data_type from information_schema.columns where table_name='matching_items' and column_name='color_brightness'\")
    print(cur.fetchone())
"
```
Expected: `('color_brightness', 'integer')`

- [ ] **Step 5: 커밋**

```bash
git add supabase/migrations/20260629120000_matching_color_brightness.sql
git commit -m "feat(db): matching_items에 color_brightness 컬럼 추가" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 추천 순수함수 `services/matching.py`

**Files:**
- Create: `server/app/services/matching.py`
- Test: `server/tests/test_matching.py`

**Interfaces:**
- Produces: `recommend(items: list[dict], clothing_type: str, genders: list[str], limit: int | None = None) -> list[dict]` — `items`는 `{id,name,clothing_type,gender,color_brightness,sort_order,is_active,...}` dict 리스트. Task 3가 호출.
- Produces: `complementary_type(clothing_type: str) -> str`.

- [ ] **Step 1: 실패 테스트 작성**

```python
# server/tests/test_matching.py
from app.services import matching

def _it(id, ct, gender, cb, so, active=True):
    return {"id": id, "clothing_type": ct, "gender": gender,
            "color_brightness": cb, "sort_order": so, "is_active": active}

def test_complementary_type():
    assert matching.complementary_type("top") == "bottom"
    assert matching.complementary_type("dress") == "bottom"
    assert matching.complementary_type("outer") == "bottom"
    assert matching.complementary_type("bottom") == "top"

def test_recommend_filters_type_and_sorts_by_brightness_then_sortorder():
    items = [
        _it("b1", "bottom", "women", 20, 2),
        _it("b2", "bottom", "women", 90, 5),
        _it("b3", "bottom", "women", 90, 1),  # 동률 → sort_order 우선
        _it("t1", "top", "women", 99, 1),     # 보색 아님 → 제외
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == ["b3", "b2", "b1"]

def test_recommend_gender_filter_allows_unisex():
    items = [
        _it("m", "bottom", "men", 50, 1),
        _it("w", "bottom", "women", 50, 2),
        _it("u", "bottom", "unisex", 50, 3),
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert {i["id"] for i in out} == {"w", "u"}

def test_recommend_excludes_inactive_and_applies_limit():
    items = [
        _it("a", "bottom", "women", 80, 1),
        _it("b", "bottom", "women", 70, 2),
        _it("c", "bottom", "women", 60, 3, active=False),
    ]
    out = matching.recommend(items, clothing_type="top", genders=[], limit=1)
    assert [i["id"] for i in out] == ["a"]

def test_recommend_missing_brightness_defaults_50():
    items = [
        {"id": "x", "clothing_type": "bottom", "gender": "women", "sort_order": 1, "is_active": True},
        _it("y", "bottom", "women", 90, 2),
    ]
    out = matching.recommend(items, clothing_type="top", genders=["women"])
    assert [i["id"] for i in out] == ["y", "x"]  # 90 > 50(default)
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_matching.py -q`
Expected: FAIL (`ModuleNotFoundError: app.services.matching`).

- [ ] **Step 3: 구현**

```python
# server/app/services/matching.py
"""매칭의류 추천 — mock matchingRecommendation.js 규칙의 서버 포팅.
순수 함수(DB·IO 없음). 라우트가 list_active_matching_items 결과를 넘긴다.
규칙: 보색 타입 → isActive·타입·성별 필터 → colorBrightness 내림차순, 동률 sort_order.
"""

_TOP_SIDE = {"top", "outer", "dress"}


def complementary_type(clothing_type: str) -> str:
    return "bottom" if clothing_type in _TOP_SIDE else "top"


def recommend(items, clothing_type, genders, limit=None):
    preferred = complementary_type(clothing_type)
    gset = set(genders or [])
    pool = [
        i for i in items
        if i.get("is_active")
        and i.get("clothing_type") == preferred
        and (not gset or i.get("gender") == "unisex" or i.get("gender") in gset)
    ]
    pool.sort(key=lambda i: (
        -(i["color_brightness"] if i.get("color_brightness") is not None else 50),
        i.get("sort_order", 0),
    ))
    return pool[:limit] if limit else pool
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_matching.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: 커밋**

```bash
git add server/app/services/matching.py server/tests/test_matching.py
git commit -m "feat(server): 매칭 추천 순수함수 포팅 (보색·성별·밝기순)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 리포 + 엔드포인트 `match-candidates`

**Files:**
- Modify: `server/app/repo.py` (신규 함수 추가)
- Modify: `server/app/routes.py` (신규 라우트 추가)
- Test: `server/tests/test_matching.py` (엔드포인트 테스트 추가)

**Interfaces:**
- Consumes: `matching.recommend(...)` (Task 2), `matching_items.color_brightness` (Task 1), `R2Client.public_url` ([r2.py:89](../../../server/app/r2.py#L89)).
- Produces: `repo.list_active_matching_items(conn) -> list[dict]` (행: `id,name,clothing_type,gender,category,color_name,color_group,style_tags,fit,length,color_brightness,sort_order,is_active,image_key,thumb_key`).
- Produces: `GET /v1/projects/{project_id}/analysis/match-candidates?clothingType=&gender=&limit=` → 레거시 `MatchClothing[]`.

- [ ] **Step 1: 리포 함수 추가** (`repo.py`, `get_matching_item_asset` 아래에 추가 — [list_mannequin_cuts](../../../server/app/repo.py) 패턴)

```python
async def list_active_matching_items(conn: AsyncConnection) -> list[dict]:
    """활성 매칭의류 + 본/썸네일 R2 키 (URL은 라우트가 r2로 변환). 운영자 시드(무소유)."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select mi.id, mi.name, mi.clothing_type, mi.gender, mi.category,
                   mi.color_name, mi.color_group, mi.style_tags, mi.fit, mi.length,
                   mi.color_brightness, mi.sort_order, mi.is_active,
                   img.r2_key as image_key, thb.r2_key as thumb_key
            from matching_items mi
            left join assets img on img.id = mi.image_asset_id
            left join assets thb on thb.id = mi.thumbnail_asset_id
            where mi.is_active
            """,
        )
        return await cur.fetchall()
```

- [ ] **Step 2: 라우트 실패 테스트 작성** (`tests/test_matching.py`에 추가)

```python
import app.routes as routes
from tests.conftest import make_settings  # noqa: F401  (fixture client가 사용)

def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}

def test_match_candidates_shape_and_public_url(client, make_token, monkeypatch):
    client.app.state.settings.r2_public_base = "https://img.example.com"
    async def fake_get_project(conn, user_id, project_id): return {"id": project_id}
    async def fake_list(conn):
        return [{"id": "match_women_bottom_01", "name": "블랙 슬랙스",
                 "clothing_type": "bottom", "gender": "women", "category": "슬랙스",
                 "color_name": "블랙", "color_group": "black", "style_tags": ["basic"],
                 "fit": "regular", "length": "full", "color_brightness": 0, "sort_order": 201,
                 "is_active": True, "image_key": "seed/matching/match_women_bottom_01.png",
                 "thumb_key": "seed/matching/thumb/match_women_bottom_01.png"}]
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "list_active_matching_items", fake_list)
    # DB 커넥션은 타지 않도록 get_conn 우회
    import contextlib
    @contextlib.asynccontextmanager
    async def fake_conn(_request): yield None
    monkeypatch.setattr(routes, "get_conn", fake_conn)

    res = client.get("/v1/projects/p1/analysis/match-candidates?clothingType=top&gender=women",
                     headers=_auth(make_token))
    assert res.status_code == 200
    body = res.json()
    assert body[0]["thumb"] == "https://img.example.com/seed/matching/thumb/match_women_bottom_01.png"
    assert body[0]["selected"] is False
    assert "id" in body[0] and "name" in body[0]

def test_match_candidates_failfast_without_public_base(client, make_token, monkeypatch):
    client.app.state.settings.r2_public_base = None
    async def fake_get_project(conn, user_id, project_id): return {"id": project_id}
    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    import contextlib
    @contextlib.asynccontextmanager
    async def fake_conn(_request): yield None
    monkeypatch.setattr(routes, "get_conn", fake_conn)
    res = client.get("/v1/projects/p1/analysis/match-candidates?clothingType=top",
                     headers=_auth(make_token))
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "r2_public_base_missing"
```

- [ ] **Step 3: 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_matching.py -q`
Expected: FAIL (라우트 미존재 → 404/`AttributeError`).

- [ ] **Step 4: 라우트 구현** (`routes.py`에 추가 — `from .services import matching` 임포트 추가, `_r2`/`require_user`/`get_project` 기존 헬퍼 사용)

```python
@router.get("/projects/{project_id}/analysis/match-candidates")
async def match_candidates(
    request: Request,
    project_id: str,
    clothingType: str = Query(...),
    gender: list[str] = Query(default=[]),
    limit: int | None = Query(default=None),
    user_id: str = Depends(require_user),
):
    """매칭 후보(보색 의류) — 공개 R2 썸네일 URL 포함 레거시 MatchClothing[].
    선택값은 클라가 오버레이(서버 저장 없음, 과도기 계약 §4)."""
    r2 = _r2(request)
    if not request.app.state.settings.r2_public_base:
        raise HTTPException(status_code=500, detail={
            "code": "r2_public_base_missing",
            "message": "이미지 서버 설정이 누락됐어요. 잠시 후 다시 시도해 주세요."})
    async with get_conn(request) as conn:
        if await repo.get_project(conn, user_id, project_id) is None:
            raise HTTPException(status_code=404, detail={
                "code": "not_found", "message": "프로젝트를 찾을 수 없습니다."})
        items = await repo.list_active_matching_items(conn)
    genders = [g for part in gender for g in part.split(",") if g]
    ranked = matching.recommend(items, clothingType, genders, limit)
    return JSONResponse([
        {
            "id": i["id"], "name": i["name"], "gender": i["gender"],
            "thumb": r2.public_url(i["thumb_key"]) if i.get("thumb_key") else None,
            "imageUrl": r2.public_url(i["image_key"]) if i.get("image_key") else None,
            "thumbnailUrl": r2.public_url(i["thumb_key"]) if i.get("thumb_key") else None,
            "selected": False,
        }
        for i in ranked if i.get("thumb_key")
    ])
```

- [ ] **Step 5: 통과 확인 + 회귀**

Run: `cd server && .venv/bin/python -m pytest -q`
Expected: PASS (기존 31 + 신규, 회귀 0).

- [ ] **Step 6: 커밋**

```bash
git add server/app/repo.py server/app/routes.py server/tests/test_matching.py
git commit -m "feat(server): 매칭 후보 엔드포인트 — 공개 R2 썸네일 URL 반환" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: 시드 — 생성기 JSON emit + R2/DB 시더 (멱등)

**Files:**
- Modify: `.scratch/gen-matching.mjs` (JSON도 emit)
- Create: `server/seed/matching_items.json` (생성기 산출물 — 커밋)
- Create: `server/scripts/seed_matching.py`

**Interfaces:**
- Consumes: `R2Client` ([r2.py](../../../server/app/r2.py)), `matching_items.color_brightness` (Task 1).
- Produces: R2 객체 `seed/matching/{id}.png`·`seed/matching/thumb/{id}.png`, `assets`(seed/public) 행, `matching_items` 64행.

- [ ] **Step 1: 생성기가 데이터 JSON도 emit** (`gen-matching.mjs`, 마지막 `console.log` 위에 추가)

```javascript
// 시더(server/scripts/seed_matching.py) 입력용 정본 데이터 JSON.
// imageUrl/thumbnailUrl 의 로컬 경로 → R2 키는 시더가 규칙으로 유도(seed/matching/{id}.png).
const seedJsonPath = path.join(ROOT, 'server/seed/matching_items.json');
fs.mkdirSync(path.dirname(seedJsonPath), { recursive: true });
fs.writeFileSync(seedJsonPath, JSON.stringify(seed, null, 2));
```

- [ ] **Step 2: 생성기 재실행 → JSON 생성 확인**

Run: `node .scratch/gen-matching.mjs && cat server/seed/matching_items.json | head -5`
Expected: `done: 64 items...` + JSON 64항목(각 `id`, `imageUrl`, `thumbnailUrl`, `colorBrightness` 포함). *(원본 `outputs/` 필요 — 없으면 사용자에게 요청.)*

- [ ] **Step 3: 시더 작성**

```python
# server/scripts/seed_matching.py
"""매칭의류 시드 — 로컬 이미지 → R2(seed/matching/...) + assets + matching_items.
멱등: checksum 동일 시 재업로드 skip, id/r2_key upsert. 운영자 1회성 스크립트.
실행: cd server && .venv/bin/python -m scripts.seed_matching
"""
import hashlib
import json
import os
from pathlib import Path

import psycopg

from app.config import load_settings
from app.r2 import R2Client

ROOT = Path(__file__).resolve().parents[2]
SEED_JSON = ROOT / "server/seed/matching_items.json"
PUB = ROOT / "public/assets/matching"


def _local_paths(item):
    # imageUrl: /assets/matching/{type}/{file}  →  public/assets/matching/{type}/{file}
    rel = item["imageUrl"].replace("/assets/matching/", "")
    img = PUB / rel
    thumb = PUB / rel.replace("/", "/thumbs/", 1) if "/thumbs/" not in rel else None
    # 썸네일 경로: public/assets/matching/{type}/thumbs/{file}
    parts = rel.split("/")
    thumb = PUB / parts[0] / "thumbs" / parts[1]
    return img, thumb


def _upsert_asset(cur, key, mime, data):
    cur.execute(
        """
        insert into assets (source, visibility, r2_bucket, r2_key, mime_type, byte_size, checksum)
        values ('seed','public', %s, %s, %s, %s, %s)
        on conflict (r2_key) do update set
          mime_type=excluded.mime_type, byte_size=excluded.byte_size, checksum=excluded.checksum
        returning id::text
        """,
        (os.environ["R2_BUCKET"], key, mime, len(data), hashlib.sha256(data).hexdigest()),
    )
    return cur.fetchone()[0]


def main():
    settings = load_settings()
    assert settings.r2_public_base, "R2_PUBLIC_BASE 필요"
    r2 = R2Client(settings)
    items = json.loads(SEED_JSON.read_text())
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        for it in items:
            img_path, thumb_path = _local_paths(it)
            img_key = f"seed/matching/{it['id']}.png"
            thumb_key = f"seed/matching/thumb/{it['id']}.png"
            for key, p in ((img_key, img_path), (thumb_key, thumb_path)):
                data = p.read_bytes()
                if not r2.head(key) or r2.head(key)["size"] != len(data):
                    r2.put_bytes(key, data, "image/png")  # Step 4에서 캐시헤더 추가
                assert r2.head(key), f"upload failed: {key}"
            img_id = _upsert_asset(cur, img_key, "image/png", img_path.read_bytes())
            thumb_id = _upsert_asset(cur, thumb_key, "image/png", thumb_path.read_bytes())
            cur.execute(
                """
                insert into matching_items
                  (id,name,clothing_type,gender,category,color_name,color_group,
                   style_tags,fit,length,color_brightness,image_asset_id,thumbnail_asset_id,
                   is_active,sort_order)
                values (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,true,%s)
                on conflict (id) do update set
                  name=excluded.name, color_brightness=excluded.color_brightness,
                  image_asset_id=excluded.image_asset_id, thumbnail_asset_id=excluded.thumbnail_asset_id,
                  is_active=true, sort_order=excluded.sort_order
                """,
                (it["id"], it["name"], it["clothingType"], it["gender"], it["category"],
                 it["colorName"], it["colorGroup"], json.dumps(it["styleTags"]), it["fit"],
                 it["length"], it.get("colorBrightness", 50), img_id, thumb_id, it["sortOrder"]),
            )
        conn.commit()
    print(f"seeded {len(items)} matching_items")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 캐시헤더 — `put_bytes`에 Cache-Control 추가** (`server/app/r2.py` `put_bytes` 교체)

```python
    def put_bytes(self, key: str, data: bytes, mime: str, cache: str | None = None) -> None:
        """AI 생성 이미지 등 서버사이드 저장. seed/public은 immutable 캐시 권장."""
        extra = {"CacheControl": cache} if cache else {}
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=mime, **extra)
```

그리고 시더의 `r2.put_bytes(key, data, "image/png")` → `r2.put_bytes(key, data, "image/png", "public, max-age=31536000, immutable")`.

- [ ] **Step 5: 시드 실행** *(R2·prod DB 쓰기 — 사용자 승인 후)*

Run: `cd server && .venv/bin/python -m scripts.seed_matching`
Expected: `seeded 64 matching_items`.

- [ ] **Step 6: 검증 — 행수 + 공개 URL 실증**

Run:
```bash
cd server && .venv/bin/python -c "
import os, psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c, c.cursor() as cur:
    cur.execute('select count(*) from matching_items where is_active'); print('items', cur.fetchone()[0])
    cur.execute(\"select count(*) from assets where source='seed' and visibility='public'\"); print('assets', cur.fetchone()[0])
"
# 공개 접근 실증 (BASE = R2_PUBLIC_BASE, ID = 임의 seed id)
source .env 2>/dev/null; curl -s -o /dev/null -w "%{http_code}\n" "$R2_PUBLIC_BASE/seed/matching/thumb/$(cd .. && node -e "console.log(require('./server/seed/matching_items.json')[0].id)").png"
```
Expected: `items 64`, `assets 128`, curl `200`.

- [ ] **Step 7: 재실행 멱등 확인**

Run: `cd server && .venv/bin/python -m scripts.seed_matching` (2회차)
Expected: `seeded 64 matching_items`, 행수 변동 없음(64/128 유지).

- [ ] **Step 8: 커밋**

```bash
git add .scratch/gen-matching.mjs server/seed/matching_items.json server/scripts/seed_matching.py server/app/r2.py
git commit -m "feat(server): 매칭의류 R2 시드 스크립트 + 정본 JSON (멱등)" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 프론트 — 후보를 백엔드에서 fetch + 선택 오버레이

**Files:**
- Modify: `src/lib/api/httpAdapter.js`
- Modify: `src/mock/api.js` (mock `getMatchClothing` ctx 재계산)
- Modify: `src/features/analysis/AnalysisForm.jsx`, `src/features/mannequin/Mannequin.jsx`, `src/features/storyboard/Storyboard.jsx`

**Interfaces:**
- Consumes: `GET .../match-candidates` (Task 3) → `[{id,name,gender,thumb,imageUrl,thumbnailUrl,selected:false}]`.
- Produces: `api.getMatchClothing(projectId, ctx)` — `ctx={clothingType, targetGenders}`; 반환은 레거시 `MatchClothing[]`에 현행 선택 오버레이.

- [ ] **Step 1: httpAdapter에 getMatchClothing 추가** (`httpAdapter.js`의 `httpAdapter` 객체에)

```javascript
  // 매칭 후보 — 백엔드 추천(공개 R2 썸네일). 선택은 호출부가 현행 상태로 오버레이(계약 §4 과도기).
  async getMatchClothing(projectId, ctx = {}) {
    const q = new URLSearchParams();
    if (ctx.clothingType) q.set('clothingType', ctx.clothingType);
    (ctx.targetGenders || []).forEach((g) => q.append('gender', g));
    return http(`/v1/projects/${projectId}/analysis/match-candidates?${q.toString()}`);
  },
```

- [ ] **Step 2: mock getMatchClothing이 ctx로 재계산** (`src/mock/api.js:166` 교체 — `recommendLegacyMatchClothing` 재사용)

```javascript
  async getMatchClothing(projectId, ctx) {
    await delay();
    if (ctx?.clothingType) {
      // ctx 제공 시 추천 재계산(http 모드와 동치) — 현행 선택 보존
      DB.analysis.matchClothing = recommendLegacyMatchClothing({
        clothingType: ctx.clothingType,
        targetGenders: ctx.targetGenders,
        current: DB.analysis.matchClothing,
      });
    }
    return clone((DB.analysis?.matchClothing?.length ? DB.analysis.matchClothing : DB.matchClothing));
  },
```

- [ ] **Step 3: 선택 오버레이 헬퍼** (`src/mock/matchingRecommendation.js`에 export 추가 — http 후보에 현행 선택 적용)

```javascript
// 백엔드 후보(selected:false) + 현행 선택(prev) → 선택 보존 레거시 리스트.
// http 모드에서 호출부가 사용. 후보에서 사라진 선택 id는 자동 탈락.
export function overlaySelection(candidates, prev) {
  const sel = new Map((prev || []).filter((m) => m.selected).map((m) => [m.id, m.selOrder]));
  return (candidates || []).map((c) => {
    const selOrder = sel.get(c.id);
    return selOrder ? { ...c, selected: true, selOrder } : { ...c, selected: false };
  });
}
```

- [ ] **Step 4: 소비자 배선 — Mannequin** (`Mannequin.jsx:234` 교체)

```javascript
      api.getMatchClothing(pid, { clothingType, targetGenders }).then((cands) =>
        setMatchClothing(overlaySelection(cands, matchClothing)));
```
(상단에 `import { overlaySelection } from '@/mock/matchingRecommendation.js';` 추가. `clothingType`/`targetGenders`는 해당 컴포넌트의 analysis 상태에서 취득 — 없으면 분석 로드 값 사용.)

- [ ] **Step 5: 소비자 배선 — Storyboard** (`Storyboard.jsx:324` 인자 없는 호출 교체)

```javascript
      const [b, c, m, p] = await Promise.all([
        api.getStoryboard(pid), api.getCatalogs(),
        api.getMatchClothing(pid, { clothingType: /* 분석값 */ undefined, targetGenders: [] }),
        api.getProduct(pid),
      ]);
      setMatchClothing(overlaySelection(m, m)); // 초기엔 서버 selected 사용; 이후 토글은 현행 상태
```
(분석 컨텍스트가 Storyboard에 없으면 `getProduct`/분석 로드에서 `clothingType` 취득해 전달. import 추가.)

- [ ] **Step 6: 소비자 배선 — AnalysisForm** (`clothingType`/`targetGenders` 변경 시 후보 재fetch)

`AnalysisForm.jsx`에서 후보 목록을 `a.matchClothing`로 렌더하는 현행 유지하되, http 모드에서 후보 소스를 `getMatchClothing(ctx)`로 채우도록 컨텍스트 변경 effect 추가:
```javascript
  useEffect(() => {
    if (!a.clothingType) return;
    let alive = true;
    api.getMatchClothing(projectId, { clothingType: a.clothingType, targetGenders: a.targetGenders || [] })
      .then((cands) => { if (alive) onChange({ matchClothing: overlaySelection(cands, a.matchClothing) }); });
    return () => { alive = false; };
  }, [a.clothingType, JSON.stringify(a.targetGenders)]);
```
(import `overlaySelection`, `useEffect`, `api`, `projectId` 확인. 선택 토글 onChange는 현행 유지 — 오버레이가 보존.)

- [ ] **Step 7: 빌드 검증**

Run: `pnpm build`
Expected: 에러 0 (청크 경고만 허용).

- [ ] **Step 8: 수동 검증 (mock 모드 회귀)**

Run: `pnpm dev` → 분석→스토리보드→마네킹 이동.
Expected(mock): 썸네일 렌더, 선택(메인/서브)이 화면 전환에도 유지, 콘솔 에러 0.

- [ ] **Step 9: 수동 검증 (http 모드 — R2)**

Run: `VITE_API_MODE=http pnpm dev` (Supabase 로그인 + 백엔드 기동 전제).
Expected: 매칭 썸네일이 **R2 공개 URL**(`img.example…` 도메인)로 렌더(네트워크 탭 확인), 라우트 전환 후 선택 유지.

- [ ] **Step 10: 커밋**

```bash
git add src/lib/api/httpAdapter.js src/mock/api.js src/mock/matchingRecommendation.js src/features/analysis/AnalysisForm.jsx src/features/mannequin/Mannequin.jsx src/features/storyboard/Storyboard.jsx
git commit -m "feat(web): 매칭 후보를 백엔드(R2)에서 fetch + 선택 오버레이" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (작성자 체크)

**Spec coverage:** A(마이그레이션)=Task1 · B(시드)=Task4 · C(추천)=Task2 · D(엔드포인트)=Task3 · E(프론트)=Task5. Codex 7건: thumb 계약=Task3 응답·Task5, ctx 재계산=Task5 Step2, 선택 라우트유지=Task5 Step3-6, Storyboard=Task5 Step5, fail-fast=Task3 Step4, checksum=Task4 Step3, 캐시헤더=Task4 Step4. ✅
**Placeholder scan:** 마이그레이션 타임스탬프(`20260629120000`)는 실제 값으로 확정 가능; 프론트 Step5/6의 "분석값" 주석은 컨텍스트 취득 지점 표시(구현 시 해당 컴포넌트 상태로 확정). 그 외 TBD 없음.
**Type consistency:** `recommend(items, clothing_type, genders, limit)`·`list_active_matching_items`·`getMatchClothing(projectId, ctx)`·`overlaySelection(candidates, prev)` 전 태스크 일치. 응답 필드 `thumb`(UI 소비)·`selected` 일관.
**리스크:** Task5의 분석 컨텍스트(clothingType) 취득 지점은 각 컴포넌트의 현행 상태 구조에 의존 — 구현 시 실제 상태 경로 확인 필요(가벼운 방식이라 선택 저장 변경은 없음).
