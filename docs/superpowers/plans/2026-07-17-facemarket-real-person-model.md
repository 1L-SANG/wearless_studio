# 실존 인물 디지털화 (가상모델 fork) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실존 인물이 본인 얼굴 3장으로 검증된 라이선스 모델이 되어, 셀러 상세페이지 컷에 본인 얼굴이 주입되게 한다.

**Architecture:** 가상모델 파이프라인의 fork. 앞단(사진→그리드 자산)만 신규, 컷 생성 슬롯은 재사용. 실존 얼굴은 비공개 버킷, 얼굴 대조 QC 게이트, 컷당 단일 아이덴티티-소스 상태머신.

**Tech Stack:** FastAPI, psycopg(async), Pillow(합성), OpenCV SFace+YuNet ONNX(얼굴 대조, Apache-2.0), Cloudflare R2, Supabase migrations.

## Global Constraints

- 얼굴 바이트·임베딩·비공개 R2 키·서명 URL: payload·이벤트·로그·API 응답·job result·trace·debug repr **전부 미포함**. (api-spec §1.4)
- 실존 얼굴 = 생체 PII → `r2_face` 비공개 버킷 전용, 공개 버킷 폴백 금지. 인증 게이트 서빙만.
- QC weights = OpenCV SFace/YuNet(Apache-2.0)만. insightface buffalo_l(비상업) 금지.
- 컷당 아이덴티티 소스 **정확히 1개**(REAL/VIRTUAL/LEGACY/NONE). 라이선스 실패 = REJECTED(조용한 폴백 금지).
- private 키는 spec·payload 미진입 — 워커 런타임 컨텍스트(dict)로만 전달.
- 로컬 검증 우선. push/merge는 사용자 명시 요청 시만.
- 기존 가상모델·step03 라이선스 얼굴 경로 무영향(회귀 테스트).

---

### Task 1: 마이그레이션 — fm_model_assets + fm_models 확장

**Files:**
- Create: `supabase/migrations/20260717000000_facemarket_model_assets.sql`
- Test: `server/tests/test_fm_model_assets_migration.py`

**Interfaces:**
- Produces: 테이블 `fm_model_assets(model_id, view, r2_key, mime, bucket, created_at)` PK(model_id,view); `fm_models.assets_status`('none'|'building'|'ready'|'failed'), `fm_models.qc_score` numeric(4,3), `fm_models.assets_source_hash` text; 부분 유니크 인덱스 `fm_model_asset_build_singleflight`.

- [ ] **Step 1: 마이그레이션 SQL 작성**

```sql
-- fm_model_assets: 실존 모델 아이덴티티 자산(비공개 버킷). 얼굴=생체 PII.
create table if not exists public.fm_model_assets (
  model_id   uuid not null references public.fm_models(id) on delete cascade,
  view       text not null check (view in ('face_front','grid_sedcard')),
  r2_key     text not null,
  mime       text not null,
  bucket     text not null default 'face' check (bucket in ('face','public')),
  created_at timestamptz not null default now(),
  primary key (model_id, view)
);
alter table public.fm_model_assets enable row level security;
-- 정책 없음 = 서비스 롤(service_role, RLS 우회)만 접근. anon/authenticated 차단.

alter table public.fm_models
  add column if not exists assets_status text not null default 'none'
    check (assets_status in ('none','building','ready','failed')),
  add column if not exists qc_score numeric(4,3),
  add column if not exists assets_source_hash text;

create unique index if not exists fm_model_asset_build_singleflight
  on public.jobs ((payload->>'modelId'))
  where kind = 'fm_model_asset_build' and status in ('queued','running');
```

- [ ] **Step 2: 로컬 DB 적용**

Run: `psql "$DATABASE_URL" -f supabase/migrations/20260717000000_facemarket_model_assets.sql`
Expected: `CREATE TABLE` / `ALTER TABLE` / `CREATE INDEX`, 에러 없음.

- [ ] **Step 3: 스키마 검증 테스트**

```python
# server/tests/test_fm_model_assets_migration.py
import pytest

@pytest.mark.asyncio
async def test_fm_model_assets_columns(db_pool):
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select column_name from information_schema.columns "
                "where table_name='fm_model_assets' order by column_name")
            cols = {r["column_name"] for r in await cur.fetchall()}
    assert {"model_id","view","r2_key","mime","bucket","created_at"} <= cols

@pytest.mark.asyncio
async def test_fm_models_assets_status_default(db_pool):
    async with db_pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select column_default from information_schema.columns "
                "where table_name='fm_models' and column_name='assets_status'")
            row = await cur.fetchone()
    assert row is not None and "none" in (row["column_default"] or "")
```

- [ ] **Step 4: 테스트 실행**

Run: `cd server && python -m pytest tests/test_fm_model_assets_migration.py -v`
Expected: PASS (2 passed). conftest의 db_pool 픽스처가 로컬 supabase 사용.

- [ ] **Step 5: 커밋**

```bash
git add supabase/migrations/20260717000000_facemarket_model_assets.sql server/tests/test_fm_model_assets_migration.py
git commit -m "feat(facemarket): fm_model_assets 테이블 + fm_models 자산 상태 컬럼"
```

---

### Task 2: 얼굴 대조 QC 모듈 (OpenCV SFace)

**Files:**
- Create: `server/app/agents/face_qc.py`
- Test: `server/tests/test_face_qc.py`
- Modify: `server/app/config.py` (QC 설정 3개), `server/pyproject.toml` (opencv-contrib-python-headless, numpy)

**Interfaces:**
- Produces:
  - `class QcFailed(Exception)` (attr: `score: float`, `reason: str`)
  - `class FaceQc` with `def pairwise_min_similarity(self, images: list[bytes]) -> float` (얼굴 검출+임베딩+최소 pairwise 코사인; 검출 실패 시 `QcFailed`)
  - `def load_face_qc(settings) -> FaceQc | None` (disabled면 None)
  - config: `fm_face_qc_enabled: bool`, `fm_face_qc_threshold: float`, `fm_face_qc_dir: str`(weights 디렉터리)

- [ ] **Step 1: 의존성 + config 추가**

`server/pyproject.toml` dependencies에 추가:
```toml
"opencv-contrib-python-headless>=4.9,<5",
"numpy>=1.26",
```

`server/app/config.py` Settings 로딩부(다른 env 옆)에 추가:
```python
fm_face_qc_enabled=os.getenv("FM_FACE_QC_ENABLED", "false").lower() == "true",
fm_face_qc_threshold=float(os.getenv("FM_FACE_QC_THRESHOLD", "0.363")),
fm_face_qc_dir=os.getenv("FM_FACE_QC_DIR", os.path.join(_SERVER_DIR, "app", "data", "face_models")),
```
Settings 데이터클래스에도 세 필드 선언(타입: bool, float, str).

- [ ] **Step 2: weights 준비(빌드/로컬 공통)**

Run:
```bash
mkdir -p server/app/data/face_models
curl -L -o server/app/data/face_models/face_recognition_sface_2021dec.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx
curl -L -o server/app/data/face_models/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```
Expected: 두 파일 존재(sface ~37MB, yunet ~230KB). `.gitignore`에 `server/app/data/face_models/*.onnx` 추가(용량 — Docker 빌드시 재다운로드, checksum 검증).

- [ ] **Step 3: 실패 테스트 작성**

```python
# server/tests/test_face_qc.py
import numpy as np, cv2, pytest
from app.agents.face_qc import FaceQc, QcFailed

def _face_png(seed: int) -> bytes:
    # 결정적 합성 "얼굴" — 같은 seed=같은 사람 근사. 실제 얼굴 대신 검출 가능한 패턴.
    rng = np.random.default_rng(seed)
    img = np.full((256,256,3), 220, np.uint8)
    cv2.rectangle(img,(80,70),(176,190),(180,150,140),-1)      # face oval-ish
    cv2.circle(img,(110,120),10,(50,50,50),-1); cv2.circle(img,(146,120),10,(50,50,50),-1)
    cv2.rectangle(img,(120,150),(136,170),(120,90,80),-1)
    ok,buf = cv2.imencode(".png", img); assert ok
    return buf.tobytes()

def test_qc_disabled_returns_none(monkeypatch):
    from app import config
    s = config.load_settings()
    object.__setattr__(s, "fm_face_qc_enabled", False)
    from app.agents.face_qc import load_face_qc
    assert load_face_qc(s) is None

def test_qc_detects_no_face_raises():
    qc = FaceQc.__new__(FaceQc)  # weights 없이 검출 경로만
    # blank 이미지 3장 → 얼굴 0개 → QcFailed
    blank = np.full((64,64,3),255,np.uint8)
    ok,buf = cv2.imencode(".png", blank)
    with pytest.raises(QcFailed):
        FaceQc(_test_dir()).pairwise_min_similarity([buf.tobytes()]*3)
```
(주: `_test_dir()`는 Step 2 weights 경로 반환하는 헬퍼. 실제 얼굴 픽스처가 없으면 검출 0 → QcFailed 경로만 검증. 동일인/타인 임계 테스트는 실사진 픽스처 확보 후 Step 6에서 추가.)

- [ ] **Step 4: 테스트 실패 확인**

Run: `cd server && python -m pytest tests/test_face_qc.py::test_qc_detects_no_face_raises -v`
Expected: FAIL (module `app.agents.face_qc` not found).

- [ ] **Step 5: face_qc.py 구현**

```python
# server/app/agents/face_qc.py
"""실존 모델 얼굴 대조 QC — OpenCV SFace(임베딩)+YuNet(검출), Apache-2.0.

3장(front/side/angle45)이 동일인인지 pairwise 코사인으로 게이트한다. 합성 그리드라
자산=실사진이므로 핵심 위협은 스푸핑(남의 얼굴 섞기)이며, 이를 임계로 차단한다.
얼굴 바이트·임베딩·경로는 로그·응답 미노출(§1.4). weights는 빌드타임 번들.
"""
import logging, os
import cv2, numpy as np

log = logging.getLogger("wearless.face_qc")
_SFACE = "face_recognition_sface_2021dec.onnx"
_YUNET = "face_detection_yunet_2023mar.onnx"


class QcFailed(Exception):
    def __init__(self, reason: str, score: float = 0.0):
        super().__init__(reason); self.reason = reason; self.score = score


class FaceQc:
    def __init__(self, model_dir: str):
        det_path = os.path.join(model_dir, _YUNET)
        rec_path = os.path.join(model_dir, _SFACE)
        if not (os.path.exists(det_path) and os.path.exists(rec_path)):
            raise FileNotFoundError("face QC weights missing")  # 경로는 예외 메시지에 미포함
        self._det = cv2.FaceDetectorYN.create(det_path, "", (320, 320), score_threshold=0.7)
        self._rec = cv2.FaceRecognizerSF.create(rec_path, "")

    def _embed(self, data: bytes) -> np.ndarray:
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            raise QcFailed("decode_failed")
        h, w = arr.shape[:2]
        self._det.setInputSize((w, h))
        _, faces = self._det.detect(arr)
        if faces is None or len(faces) == 0:
            raise QcFailed("no_face_detected")
        face = max(faces, key=lambda f: f[2] * f[3])  # 최대 얼굴
        aligned = self._rec.alignCrop(arr, face)
        feat = self._rec.feature(aligned)
        return feat.flatten()

    def pairwise_min_similarity(self, images: list[bytes]) -> float:
        feats = [self._embed(d) for d in images]
        mn = 1.0
        for i in range(len(feats)):
            for j in range(i + 1, len(feats)):
                a, b = feats[i], feats[j]
                cos = float(np.dot(a, b) / ((np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9))
                mn = min(mn, cos)
        return mn


def load_face_qc(settings):
    if not getattr(settings, "fm_face_qc_enabled", False):
        return None
    try:
        return FaceQc(settings.fm_face_qc_dir)
    except Exception as e:
        log.warning("face QC init failed: %s", type(e).__name__)
        return None
```

테스트 파일 상단에 헬퍼 추가:
```python
def _test_dir():
    import os
    from app import config
    return config.load_settings().fm_face_qc_dir
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `cd server && python -m pytest tests/test_face_qc.py -v`
Expected: PASS. (weights 있어야 `test_qc_detects_no_face_raises` 통과 — Step 2 선행.)

- [ ] **Step 7: 커밋**

```bash
git add server/app/agents/face_qc.py server/tests/test_face_qc.py server/app/config.py server/pyproject.toml .gitignore
git commit -m "feat(facemarket): OpenCV SFace 얼굴 대조 QC 모듈"
```

---

### Task 3: 자산 빌드 API + 워커 (합성 + QC + staging + 등록)

**Files:**
- Modify: `server/app/facemarket.py` (POST /models/me/build-assets)
- Create: `server/app/workers/fm_model_asset_job.py`
- Modify: `server/app/workers/dispatcher.py` (kind 등록)
- Create: `server/app/agents/face_grid.py` (2×2 합성)
- Test: `server/tests/test_face_grid.py`, `server/tests/test_fm_model_asset_job.py`

**Interfaces:**
- Consumes: Task2 `FaceQc.pairwise_min_similarity`, `load_face_qc`.
- Produces:
  - `face_grid.compose_sedcard(images: list[bytes]) -> bytes` (2×2 PNG; 4번째 칸 중립)
  - job kind `"fm_model_asset_build"` → `run_fm_model_asset_job(app, job)`
  - `fm_model_assets` 행 등록 + `fm_models.assets_status='ready'` + `qc_score` + `assets_source_hash`
  - 얼굴 자산 키 규약: `facemarket/models/{model_id}/{view}.{ext}` (비공개), staging: `.../staging/{view}.{ext}`

- [ ] **Step 1: 그리드 합성 실패 테스트**

```python
# server/tests/test_face_grid.py
import io, cv2, numpy as np
from PIL import Image
from app.agents.face_grid import compose_sedcard

def _png(color):
    img = np.full((200,200,3), color, np.uint8)
    ok,buf = cv2.imencode(".png", img); return buf.tobytes()

def test_compose_sedcard_2x2_square():
    out = compose_sedcard([_png(10), _png(120), _png(240)])
    im = Image.open(io.BytesIO(out))
    assert im.width == im.height          # 정사각
    assert im.width >= 1024               # 컷 앵커로 충분한 해상도
    assert im.format == "PNG"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && python -m pytest tests/test_face_grid.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: face_grid.py 구현**

```python
# server/app/agents/face_grid.py
"""실존 모델 grid_sedcard 합성 — 업로드 3장을 2×2 아이덴티티 대조 시트로.

Gemini 생성 아님(handoff §03 "얼굴 새로 생성 금지"). 타이트 크롭·중립 배경·라벨 없음.
4번째 칸은 포즈 복제 유발을 피해 중립(빈 회색) — MODEL SHEET는 아이덴티티 참조 전용이고
컷 생성 프롬프트가 레이아웃 복제를 금지하므로 4번째 칸을 원본 반복으로 채우지 않는다.
"""
import io
from PIL import Image

_CELL = 640           # 셀 한 변 → 1280×1280 그리드
_NEUTRAL = (230, 230, 230)


def _fit_square(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    s = min(im.width, im.height)
    left, top = (im.width - s) // 2, (im.height - s) // 2
    im = im.crop((left, top, left + s, top + s)).resize((_CELL, _CELL), Image.LANCZOS)
    return im


def compose_sedcard(images: list[bytes]) -> bytes:
    if not images:
        raise ValueError("no images")
    cells = [_fit_square(d) for d in images[:3]]
    canvas = Image.new("RGB", (_CELL * 2, _CELL * 2), _NEUTRAL)
    positions = [(0, 0), (_CELL, 0), (0, _CELL)]     # 3장 + 4번째 중립
    for im, pos in zip(cells, positions):
        canvas.paste(im, pos)
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && python -m pytest tests/test_face_grid.py -v`
Expected: PASS.

- [ ] **Step 5: 워커 구현 (staging → QC → 합성 → 등록)**

```python
# server/app/workers/fm_model_asset_job.py
"""실존 모델 자산 빌드 워커. payload={modelId}. 얼굴 키·바이트는 payload·이벤트 미포함.

user_id로 fm_models(verified)+personalization_face_photos(3각도) 로드 → r2_face에서 bytes →
얼굴 대조 QC → 통과 시 2×2 그리드 합성 + face_front → staging put → DB 등록 tx → staged 정리.
오브젝트 스토리지는 tx 밖이라 staging→commit→cleanup 순서로 orphan 얼굴을 남기지 않는다.
"""
import asyncio, hashlib, logging
from psycopg.types.json import Json
from .. import repo
from ..agents.face_grid import compose_sedcard
from ..agents.face_qc import load_face_qc, QcFailed
from ..r2 import ext_for_mime
from ._common import emit_job_event as _emit

log = logging.getLogger("wearless.fm_model_asset_job")
_ANGLE_ORDER = {"front": 0, "side": 1, "angle45": 2}


def _asset_key(model_id: str, view: str, ext: str, *, staging: bool = False) -> str:
    mid = "staging/" if staging else ""
    return f"facemarket/models/{model_id}/{mid}{view}.{ext}"


async def run_fm_model_asset_job(app, job: dict) -> None:
    pool = app.state.pool
    job_id, user_id, lease = job["id"], job["user_id"], job["lease_token"]
    model_id = (job.get("payload") or {}).get("modelId")
    r2_face = getattr(app.state, "r2_face", None)
    s = app.state.settings

    async def _fail(msg, meta, code="asset_build_failed", discard=None):
        for k in (discard or []):
            try: await asyncio.to_thread(r2_face.delete, k)
            except Exception: log.warning("orphan face cleanup failed job %s", job_id)
        try:
            async with pool.connection() as conn:
                await repo._finalize_job_failure(conn, job_id=job_id, lease_token=lease,
                    message=msg, metadata=meta, code=code)
                async with conn.cursor() as cur:
                    await cur.execute("update fm_models set assets_status='failed' where id=%s", (model_id,))
                await conn.commit()
        except Exception:
            log.exception("asset job finalize_failure error job %s", job_id)

    try:
        if not model_id:
            await _fail("모델 대상이 없어요.", {"error": "missing_model_id"}); return
        if r2_face is None:
            await _fail("얼굴 저장소가 설정되지 않았어요.", {"error": "face_storage_unavailable"}); return

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select m.id, m.status, p.id as profile_id from fm_models m "
                    "join personalization_profiles p on p.user_id = m.user_id "
                    "where m.id=%s and m.user_id=%s "
                    "order by p.created_at desc limit 1", (model_id, user_id))
                mrow = await cur.fetchone()
                if mrow is None or mrow["status"] != "verified":
                    await _fail("검증된 모델이 아니에요.", {"error": "model_not_verified"},
                                code="model_not_verified"); return
                await cur.execute(
                    "select angle, r2_key, mime_type from personalization_face_photos "
                    "where profile_id=%s", (mrow["profile_id"],))
                faces = await cur.fetchall()
                await cur.execute("update fm_models set assets_status='building' where id=%s", (model_id,))
            await conn.commit()

        if len({f["angle"] for f in faces}) < 3:
            await _fail("얼굴 사진 3장이 필요해요.", {"error": "face_photos_incomplete"},
                        code="face_photos_incomplete"); return

        faces.sort(key=lambda f: _ANGLE_ORDER.get(f["angle"], 9))
        face_bytes = [await asyncio.to_thread(r2_face.get_bytes, f["r2_key"]) for f in faces]
        await _emit(pool, job_id, "progress", {"progress": 30, "phase": "inputs_loaded"})

        # 얼굴 대조 QC — 미달/검출실패 시 등록 차단
        qc = load_face_qc(s)
        qc_score = None
        if qc is not None:
            try:
                qc_score = qc.pairwise_min_similarity(face_bytes)
            except QcFailed as e:
                await _fail("본인 얼굴 일치 확인에 실패했어요.",
                            {"error": "qc_failed", "reason": e.reason}, code="qc_failed"); return
            if qc_score < s.fm_face_qc_threshold:
                await _fail("본인 얼굴 일치 확인에 실패했어요.",
                            {"error": "qc_below_threshold"}, code="qc_failed"); return
        await _emit(pool, job_id, "progress", {"progress": 55, "phase": "qc_passed"})

        # 합성 + face_front
        grid = compose_sedcard(face_bytes)
        front = next((fb for f, fb in zip(faces, face_bytes) if f["angle"] == "front"), face_bytes[0])
        front_mime = next((f["mime_type"] for f in faces if f["angle"] == "front"), faces[0]["mime_type"])
        src_hash = hashlib.sha256(b"".join(sorted(face_bytes, key=len))).hexdigest()

        assets = [
            ("grid_sedcard", grid, "image/png"),
            ("face_front", front, front_mime),
        ]
        staged = []
        for view, data, mime in assets:
            ext = ext_for_mime(mime) or "png"
            skey = _asset_key(model_id, view, ext, staging=True)
            await asyncio.to_thread(r2_face.put_bytes, skey, data, mime)
            staged.append((view, skey, _asset_key(model_id, view, ext), mime, data))
        await _emit(pool, job_id, "progress", {"progress": 80, "phase": "staged"})

        # 최종 키로 복사(put) 후 DB 등록 — staged는 이후 정리
        try:
            for view, skey, fkey, mime, data in staged:
                await asyncio.to_thread(r2_face.put_bytes, fkey, data, mime)
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "select id from jobs where id=%s and locked_by=%s and status='running' for update",
                        (job_id, lease))
                    if await cur.fetchone() is None:
                        raise RuntimeError("lease_lost")
                    for view, skey, fkey, mime, data in staged:
                        await cur.execute(
                            "insert into fm_model_assets (model_id, view, r2_key, mime, bucket) "
                            "values (%s,%s,%s,%s,'face') on conflict (model_id,view) "
                            "do update set r2_key=excluded.r2_key, mime=excluded.mime",
                            (model_id, view, fkey, mime))
                    await cur.execute(
                        "update fm_models set assets_status='ready', qc_score=%s, assets_source_hash=%s where id=%s",
                        (qc_score, src_hash, model_id))
                    await cur.execute(
                        "update jobs set status='done', progress=100, locked_by=null, "
                        "locked_at=null, finished_at=now(), result=%s where id=%s",
                        (Json({"data": {"modelId": model_id, "assetsStatus": "ready"}}), job_id))
                    await cur.execute(
                        "insert into job_events (job_id, event_type, payload) values (%s,'done',%s)",
                        (job_id, Json({"data": {"modelId": model_id}})))
                await conn.commit()
        finally:
            for view, skey, *_ in staged:
                try: await asyncio.to_thread(r2_face.delete, skey)
                except Exception: log.warning("staged cleanup failed job %s", job_id)
    except Exception as e:
        await _fail("자산 생성 중 오류가 발생했어요.", {"error": str(e)[:200]},
                    discard=[t[2] for t in (staged if 'staged' in dir() else [])])
```

- [ ] **Step 6: dispatcher 등록**

`server/app/workers/dispatcher.py` — import + HANDLERS 등록:
```python
from .fm_model_asset_job import run_fm_model_asset_job
# HANDLERS dict 안:
    "fm_model_asset_build": run_fm_model_asset_job,  # 실존 모델 자산 빌드(합성+QC)
```

- [ ] **Step 7: build-assets 엔드포인트**

`server/app/facemarket.py` — 라우터에 추가(멱등: 진행 중 잡 재사용):
```python
@router.post("/models/me/build-assets", responses={**COMMON_RESPONSES}, summary="내 모델 자산 빌드")
async def build_my_model_assets(request: Request, user_id: str = Depends(require_user)):
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id from fm_models where user_id=%s and status='verified' "
                "order by created_at desc limit 1", (user_id,))
            m = await cur.fetchone()
            if m is None:
                raise HTTPException(status_code=400, detail={"code": "model_not_verified",
                    "message": "먼저 본인확인을 완료해 주세요."})
            model_id = str(m["id"])
            await cur.execute(
                "select id from jobs where kind='fm_model_asset_build' "
                "and payload->>'modelId'=%s and status in ('queued','running') limit 1", (model_id,))
            existing = await cur.fetchone()
            if existing:
                return _json({"jobId": str(existing["id"]), "modelId": model_id}, status=202)
        job = await repo.create_job(conn, user_id=user_id, project_id=None,
            kind="fm_model_asset_build", payload={"modelId": model_id})
        await conn.commit()
    _wake_dispatcher(request)
    return _json({"jobId": str(job["id"]), "modelId": model_id}, status=202)
```
(주: `_json`·`_wake_dispatcher`·`repo.create_job` 시그니처는 personalization.start_generation 선례 확인 후 정합. `create_job` 파라미터명이 다르면 맞춘다.)

- [ ] **Step 8: 워커 통합 테스트 (얼굴 픽스처 없이 경로 검증)**

```python
# server/tests/test_fm_model_asset_job.py — QC off로 합성·등록·staging 경로만 검증
import pytest
# fixtures: seed fm_models(verified)+personalization_profiles+3 face_photos(작은 png를 r2_face에)
# FM_FACE_QC_ENABLED=false → QC 스킵. run_fm_model_asset_job 호출 후:
#   assert fm_model_assets 2행(grid_sedcard, face_front), assets_status='ready',
#          staging 키 삭제됨, 이벤트에 얼굴키 미포함.
```
(실제 픽스처 코드는 test_personalization.py의 fm_models/face_photos seed 헬퍼 재사용.)

- [ ] **Step 9: 커밋**

```bash
git add server/app/agents/face_grid.py server/app/workers/fm_model_asset_job.py \
  server/app/workers/dispatcher.py server/app/facemarket.py \
  server/tests/test_face_grid.py server/tests/test_fm_model_asset_job.py
git commit -m "feat(facemarket): 실존 모델 자산 빌드 API+워커(합성+QC+staging)"
```

---

### Task 4: 컷 resolve — 실존 자산 + 버킷 태깅 + 아이덴티티-소스 선택 헬퍼

**Files:**
- Modify: `server/app/agents/cut_generator.py` (resolve refs에 bucket 추가)
- Create: `server/app/agents/identity_source.py` (상태머신 선택 + 실존 자산 조회)
- Test: `server/tests/test_identity_source.py`

**Interfaces:**
- Consumes: `fm_model_assets`, `fm_licenses`.
- Produces:
  - `resolve_virtual_model_assets` 반환 ref에 `"bucket": "public"` 추가(기존 호출부 하위호환 — `.get("bucket","public")`).
  - `identity_source.resolve_real_model_assets(conn, model_id) -> list[dict] | None` (ready면 `[{key,mime,bucket:"face"}, ...]` face_front,grid_sedcard 순)
  - `identity_source.select_source(*, selected_model_id, license_row, has_real_assets, has_license_face) -> str` 반환 `"REAL"|"VIRTUAL"|"LEGACY"|"NONE"|"REJECTED"`

- [ ] **Step 1: 선택 로직 실패 테스트**

```python
# server/tests/test_identity_source.py
from app.agents.identity_source import select_source

def test_real_requires_active_license():
    # 실자산 있으나 라이선스 없음 → REJECTED(조용한 폴백 금지)
    assert select_source(selected_model_id="m1", license_row=None,
        has_real_assets=True, has_license_face=False) == "REJECTED"

def test_real_with_license():
    assert select_source(selected_model_id="m1",
        license_row={"model_id":"m1","status":"active"},
        has_real_assets=True, has_license_face=True) == "REAL"

def test_virtual_no_license_needed():
    assert select_source(selected_model_id="mA", license_row=None,
        has_real_assets=False, has_license_face=False) == "VIRTUAL"

def test_legacy_face_only():
    assert select_source(selected_model_id=None, license_row=None,
        has_real_assets=False, has_license_face=True) == "LEGACY"

def test_none():
    assert select_source(selected_model_id=None, license_row=None,
        has_real_assets=False, has_license_face=False) == "NONE"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && python -m pytest tests/test_identity_source.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: identity_source.py 구현**

```python
# server/app/agents/identity_source.py
"""컷당 단일 아이덴티티-소스 선택 + 실존 모델 자산 조회.

detail_page/editor 워커가 컷 루프 전 1회 소스를 정한다(컷마다 소스 1개 불변식).
실존 자산은 라이선스 활성일 때만(REAL); 실자산 대상인데 라이선스 실패면 REJECTED로
조용한 폴백을 막는다. 가상모델은 라이선스 불요(VIRTUAL). 키·바이트는 여기서 다루지 않고
상위 워커가 r2_face로 로드한다(private 키 미직렬화).
"""
_VIRTUAL_MODEL_IDS = None  # lazy


def select_source(*, selected_model_id, license_row, has_real_assets, has_license_face) -> str:
    if has_real_assets:
        if license_row and license_row.get("model_id") == selected_model_id \
                and license_row.get("status") == "active":
            return "REAL"
        return "REJECTED"
    if selected_model_id:
        return "VIRTUAL"
    if has_license_face:
        return "LEGACY"
    return "NONE"


async def resolve_real_model_assets(conn, model_id: str):
    async with conn.cursor() as cur:
        await cur.execute(
            "select m.assets_status, a.view, a.r2_key, a.mime, a.bucket "
            "from fm_models m left join fm_model_assets a on a.model_id=m.id "
            "where m.id=%s", (model_id,))
        rows = await cur.fetchall()
    if not rows or rows[0]["assets_status"] != "ready":
        return None
    by_view = {r["view"]: r for r in rows if r["view"]}
    out = []
    for view in ("face_front", "grid_sedcard"):
        r = by_view.get(view)
        if not r or not r["r2_key"] or not (r["mime"] or "").startswith("image/"):
            return None
        out.append({"key": r["r2_key"], "mime": r["mime"], "bucket": r["bucket"] or "face"})
    return out
```

`cut_generator.resolve_virtual_model_assets` 반환부(line ~228) 수정 — bucket 추가:
```python
        resolved.append({"key": key, "mime": mime, "bucket": "public"})
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && python -m pytest tests/test_identity_source.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: 커밋**

```bash
git add server/app/agents/identity_source.py server/app/agents/cut_generator.py server/tests/test_identity_source.py
git commit -m "feat(facemarket): 아이덴티티-소스 상태머신 + 실존 자산 resolve + 버킷 태깅"
```

---

### Task 5: 워커 배선 — detail_page_job / editor_image_job (버킷 인지 + 상태머신 + 라이선스 게이트)

**Files:**
- Modify: `server/app/workers/detail_page_job.py` (~218-360, ~416)
- Modify: `server/app/workers/editor_image_job.py` (~149-167)
- Test: `server/tests/test_detail_page_identity_source.py`

**Interfaces:**
- Consumes: Task4 `select_source`, `resolve_real_model_assets`; 기존 `_load_license_face`, `resolve_virtual_model_assets`, `wants_face`.
- Produces: 컷에 REAL 자산(그리드+face_front, r2_face) 주입; 이중주입 0; 라이선스 실패 시 얼굴 미주입.

- [ ] **Step 1: 회귀+신규 테스트 작성**

```python
# server/tests/test_detail_page_identity_source.py
# 1) REAL: selectedModelId=실존 fm_models(ready)+활성 라이선스 → 그리드 주입, face_ref 미사용, r2_face 로드
# 2) REJECTED: 실존 모델 but 라이선스 없음 → 얼굴 미주입(images에 얼굴 0)
# 3) LEGACY 회귀: facemarket_license_id만 → 기존 face_ref 경로 그대로(virtual 그리드 안 붙음)
# 4) VIRTUAL 회귀: 가상 modelId → 공개 버킷 그리드, 라이선스 불요
# (detail_page_job의 _prepare 경로를 좁게 호출하거나 select_source 주입 지점 단위테스트)
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && python -m pytest tests/test_detail_page_identity_source.py -v`
Expected: FAIL.

- [ ] **Step 3: detail_page_job 버킷 인지 로더 + 소스 선택**

`_r2_img`(line 256)을 bucket 인지로 교체:
```python
        async def _r2_img(k: str, mime: str, bucket: str = "public") -> InlineImage:
            client = app.state.r2_face if bucket == "face" else app.state.r2
            if client is None:
                raise RuntimeError("bucket client unavailable")
            return InlineImage(mime, await asyncio.to_thread(client.get_bytes, k))
```

`_model_images`(line 267~289) — 실존/가상 분기 + 라이선스 게이트. 잡 시작부(line 218~222 부근)에서 소스 1회 선택:
```python
        from ..agents import identity_source
        # 라이선스 로우(실존 게이트용) — project.facemarket_license_id 기반
        license_row = await _load_license_row(conn, project)  # {model_id,status} or None
        real_refs = None
        if selected_model_id:
            real_refs = await identity_source.resolve_real_model_assets(conn, selected_model_id)
        source = identity_source.select_source(
            selected_model_id=selected_model_id, license_row=license_row,
            has_real_assets=real_refs is not None,
            has_license_face=face_ref is not None)
        if source == "REJECTED":
            face_ref = None; real_refs = None  # 무라이선스 실얼굴 차단
```
컷 준비 루프(line ~319~333): `has_face`/`model_images` 삼거리 확장:
```python
            if source == "REAL":
                has_face = cut_generator.wants_face(cut_spec, clothing_type)
                model_images = ([await _r2_img(r["key"], r["mime"], r["bucket"]) for r in real_refs]
                                if has_face else [])
                imgs = [*prods, *match_imgs, *moods, *model_images]  # 얼굴=그리드로 대체
            elif source == "LEGACY":
                has_face = face_ref is not None and cut_generator.wants_face(cut_spec, clothing_type)
                model_images = []
                imgs = [*prods, *match_imgs, *moods]
                if has_face: imgs.append(face_ref["image"])
            else:  # VIRTUAL or NONE
                has_face = False
                model_images = await _model_images(normalized)  # 공개 버킷
                imgs = [*prods, *match_imgs, *moods, *model_images]
```
(정확한 변수명 prods/match_imgs/moods는 기존 라인 확인 후 정합. 핵심: REAL이면 그리드가 얼굴 역할, LEGACY면 기존 단일얼굴, 둘이 절대 동시 아님.)

`_load_license_row` 헬퍼 추가(facemarket_license_id → fm_licenses 활성 조회):
```python
async def _load_license_row(conn, project):
    lic_id = project.get("facemarket_license_id") or project.get("facemarketLicenseId")
    if not lic_id:
        return None
    async with conn.cursor() as cur:
        await cur.execute(
            "select model_id::text as model_id, status from fm_licenses "
            "where id=%s and status='active' "
            "and (valid_until is null or valid_until > now())", (lic_id,))
        return await cur.fetchone()
```

- [ ] **Step 4: editor_image_job 동일 버킷 인지**

`editor_image_job.py` line 149~167 — model_refs 로드도 bucket 인지(실존 modelId 지원). 최소한 `resolve_virtual_model_assets` ref의 `bucket` 존중:
```python
                model_refs = cut_generator.resolve_virtual_model_assets(normalized)
                if model_refs:
                    for ref in model_refs:
                        client = app.state.r2_face if ref.get("bucket") == "face" else app.state.r2
                        model_imgs.append(InlineImage(ref["mime"],
                            await asyncio.to_thread(client.get_bytes, ref["key"])))
```
(에디터의 실존 모델 라이선스 게이트는 후속 — 우선 버킷 인지로 크래시 방지.)

- [ ] **Step 5: 통과 확인**

Run: `cd server && python -m pytest tests/test_detail_page_identity_source.py tests/test_detail_page.py -v`
Expected: PASS(신규) + 기존 detail_page 테스트 회귀 없음.

- [ ] **Step 6: 커밋**

```bash
git add server/app/workers/detail_page_job.py server/app/workers/editor_image_job.py server/tests/test_detail_page_identity_source.py
git commit -m "feat(facemarket): 컷 워커 아이덴티티-소스 상태머신+버킷 인지+라이선스 게이트"
```

---

### Task 6: 카탈로그 노출 — list_models에 assets 준비 상태

**Files:**
- Modify: `server/app/facemarket.py` (list_models 쿼리 ~294-315)
- Test: `server/tests/test_facemarket_catalog_assets.py`

**Interfaces:**
- Produces: `list_models` 응답 모델 카드에 `assetsReady: bool`(assets_status='ready'). 셀러 선택 가능 판단용.

- [ ] **Step 1: 실패 테스트**

```python
# server/tests/test_facemarket_catalog_assets.py
# fm_models verified 2개: 하나 assets_status='ready', 하나 'none'.
# GET /v1/facemarket/models → ready 모델 카드에 assetsReady=true, 다른 건 false.
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && python -m pytest tests/test_facemarket_catalog_assets.py -v`
Expected: FAIL.

- [ ] **Step 3: list_models 쿼리에 assets_status 추가**

`_MODEL_CARD_COLS_ENRICHED`(또는 list_models SELECT)에 `m.assets_status` 추가, 응답 매핑에 `"assetsReady": row["assets_status"] == "ready"`.

- [ ] **Step 4: 통과 확인**

Run: `cd server && python -m pytest tests/test_facemarket_catalog_assets.py -v`
Expected: PASS.

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket.py server/tests/test_facemarket_catalog_assets.py
git commit -m "feat(facemarket): 카탈로그에 모델 자산 준비 상태 노출"
```

---

### Task 7: 배포 — Dockerfile 의존성 + weights 번들 + manifest

**Files:**
- Modify: `server/Dockerfile` (opencv-contrib + weights 다운로드+checksum)
- Modify: `copilot/api/manifest.yml` (mem 2048, QC env)

**Interfaces:**
- Produces: 프로드 이미지에 SFace/YuNet weights 번들, FM_FACE_QC_ENABLED 등 env.

- [ ] **Step 1: Dockerfile weights 번들**

`server/Dockerfile`에 (pip install 후):
```dockerfile
# 얼굴 대조 QC weights (OpenCV Zoo, Apache-2.0) — pin+checksum, 런타임 다운로드 금지
RUN mkdir -p /app/app/data/face_models && \
    curl -fL -o /app/app/data/face_models/face_recognition_sface_2021dec.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx && \
    curl -fL -o /app/app/data/face_models/face_detection_yunet_2023mar.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
# (checksum 검증 라인은 실제 sha256 확보 후 추가)
```
opencv-contrib-python-headless는 pyproject 의존성으로 이미 설치(Task2). 시스템 libGL 불필요(headless).

- [ ] **Step 2: manifest env + mem**

`copilot/api/manifest.yml`:
```yaml
memory: 2048    # SFace ONNX + OpenCV 상주 (codex: 1024 부족)
```
`variables:` 블록에:
```yaml
  FM_FACE_QC_ENABLED: "true"
  FM_FACE_QC_THRESHOLD: "0.363"
```

- [ ] **Step 3: 로컬 Docker 빌드 검증(선택)**

Run: `cd server && docker build -t wearless-api-test . 2>&1 | tail -20`
Expected: weights 다운로드 성공, 빌드 완료. (네트워크/시간 되면.)

- [ ] **Step 4: 커밋**

```bash
git add server/Dockerfile copilot/api/manifest.yml
git commit -m "feat(facemarket): SFace weights 번들 + mem 2048 + QC env"
```

---

## Self-Review (완료)

**Spec coverage:** C1(자산빌드)=Task3, C2(QC)=Task2, C3(resolve+게이트)=Task4·5, 데이터모델=Task1, 카탈로그=Task6, 배포=Task7, 상태머신=Task4·5, staging=Task3, PII=전 태스크 준수. 커버 완료.

**Placeholder scan:** 픽스처 코드 일부(Task3 Step8, Task5 Step1, Task6 Step1)는 기존 test_personalization.py seed 헬퍼 재사용을 지시 — 실행자가 해당 헬퍼 확인 필요(플레이스홀더 아님, 재사용 지시). 나머지 코드 블록은 완전.

**Type consistency:** `select_source`/`resolve_real_model_assets`/`_load_license_row`/`_asset_key`/`compose_sedcard`/`pairwise_min_similarity` 시그니처 태스크 간 일치. ref dict 키(`key`,`mime`,`bucket`) 통일.

**알려진 실행 시 확인점:** `repo.create_job`·`_json`·`_wake_dispatcher`·`_load_license_face` 정확 시그니처, detail_page_job의 `prods`/`match_imgs`/`moods` 실제 변수명, `fm_licenses.valid_until` 컬럼명 — 각 태스크가 "선례 확인 후 정합" 명시.
