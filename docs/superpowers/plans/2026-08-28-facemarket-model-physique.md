# FaceMarket 모델 체형·키 반영 컷 생성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FaceMarket 실제 모델이 등록 시 키·체형을 고르면, 그 모델로 만드는 착장 컷에 체형·키가 프롬프트로 반영되게 한다(얼굴 보존 불변).

**Architecture:** 대표 이미지(cover) 데이터 흐름을 미러한다 — 위저드 physique 스텝 → enrollment 스테이징 → 등록 완료 시 fm_models 승격. 성별만은 OACX 신원확인에서 자동 세팅. 컷 생성은 라이브 텍스트-템플릿 경로(`render_cut_prompt`)에서 garment `fit_profile`과 동일한 방식으로 model `body_profile`을 별도 파라미터로 스레딩해 `build_fit_profile_block` 옆에 body 블록을 렌더한다. 값 없으면 블록 생략(하위호환), 착장 컷에만 적용.

**Tech Stack:** Python 3.12 / FastAPI / psycopg (async) / Supabase Postgres 마이그레이션 · React (Vite) 프론트 · pytest / node:test.

**Spec:** `docs/superpowers/specs/2026-08-28-facemarket-model-physique-design.md`

## Global Constraints

- **주입 안전:** 프롬프트에 모델/셀러 자유문자열을 절대 보간하지 않는다. 검증된 enum 값 → 고정 영문 문구만 방출(`fit_axes.build_fit_profile_block` 관례). 얼굴 속성은 방출 금지.
- **얼굴 불변:** IDENTITY_REF/FACE_REF 블록·face-fit 게이팅(`wants_face`,`_face_fits`)·라이선스 검증은 건드리지 않는다. physique는 additive 텍스트만.
- **착장 컷 한정:** physique는 worn cut에만. 게이트 = 워커의 `fm_face_injected`(REAL + non-product + identity 2장). `wants_face`와 AND하지 않는다(뒷모습·얼굴가림 컷도 몸은 그 모델).
- **하위호환:** 신규 컬럼 전부 nullable. 값 없는 모델/구 라이선스는 현행 동작 유지(블록 생략). 기존 컷 재생성 없음.
- **성별 자동:** gender는 OACX(`identity_verify`)에서만 세팅. physique 스텝·라우트는 gender를 받지 않는다.
- **소스 단일화:** 버킷/체형 enum·라벨·영문문구·검증·블록빌더는 전부 `server/app/facemarket_physique.py` 한 곳이 소유.
- **마이그레이션:** additive·PG16-safe. CHECK 제약은 `drop constraint if exists`→`add` 패턴(선례 `supabase/migrations/20260824000000_facemarket_identity_first_reorder.sql`).

---

## File Structure

- **Create** `server/app/facemarket_physique.py` — physique 단일 소스: `GENDERS`, `HEIGHT_BUCKETS`(gender별), `BODY_TYPES`(7), 값→{ko_label, en_phrase} 맵, `validate_physique()`, `build_body_profile_block(profile)`.
- **Create** `supabase/migrations/20260828000000_facemarket_model_physique.sql` — fm_models(gender,height_bucket,body_type) + fm_biometric_enrollments(height_bucket,body_type) + CHECK.
- **Modify** `server/app/facemarket.py` — `identity_verify` gender 추출/세팅; `resolve_model_license` SELECT에 physique 추가.
- **Modify** `server/app/facemarket_enrollment.py` — `POST /enrollments/{id}/physique` 라우트; `EnrollmentView` 필드; `_enrollment_view`/`_load_owned_enrollment` physique 로드; finalization 승격.
- **Modify** `server/app/agents/cut_generator.py` — `generate`/`build_prompt`/`render_cut_prompt`에 `body_profile` 스레딩 + `body_block` 렌더.
- **Modify** `server/app/workers/editor_image_job.py`, `server/app/workers/detail_page_job.py` — `fm_face_injected` 게이트서 `body_profile` 구성·전달.
- **Modify** `src/features/model/ModelRegister.jsx` (+ `ModelRegister.module.css`) — physique 스텝.
- **Modify** `src/lib/api/facemarket.js` — `submitPhysique` API.
- **Tests**: `server/tests/test_facemarket_physique.py`(신규), `server/tests/test_facemarket_biometric_enrollment.py`(라우트·승격), `server/tests/test_cut_generator_body_profile.py`(신규, 프롬프트 주입), `tests/frontend/facemarket-biometric-enrollment.test.mjs`(위저드).

---

### Task 1: 마이그레이션 — physique 컬럼

**Files:**
- Create: `supabase/migrations/20260828000000_facemarket_model_physique.sql`

**Interfaces:**
- Produces: `fm_models.gender`, `fm_models.height_bucket`, `fm_models.body_type`, `fm_biometric_enrollments.height_bucket`, `fm_biometric_enrollments.body_type` (모두 nullable text, CHECK 제약).

- [ ] **Step 1: 마이그레이션 작성**

```sql
-- FaceMarket 모델 physique(체형·키). 얼굴만 앵커되는 실모델의 몸을 컷 생성에 반영하기 위한
-- 모델 속성. gender 는 신원확인(identity)에서, height_bucket·body_type 은 physique 스텝에서 승격.
-- Additive · PG16-safe · 전부 nullable(선택 입력).

-- 1) fm_models: gender / height_bucket / body_type
alter table public.fm_models add column if not exists gender text;
alter table public.fm_models add column if not exists height_bucket text;
alter table public.fm_models add column if not exists body_type text;

alter table public.fm_models drop constraint if exists fm_models_gender_check;
alter table public.fm_models add constraint fm_models_gender_check
  check (gender is null or gender in ('male','female'));

alter table public.fm_models drop constraint if exists fm_models_height_bucket_check;
alter table public.fm_models add constraint fm_models_height_bucket_check
  check (height_bucket is null or height_bucket in (
    'm_lt170','m_170_175','m_175_180','m_180_185','m_185_190','m_gte190',
    'f_lt155','f_155_160','f_160_165','f_165_170','f_170_175','f_gte175'
  ));

alter table public.fm_models drop constraint if exists fm_models_body_type_check;
alter table public.fm_models add constraint fm_models_body_type_check
  check (body_type is null or body_type in (
    'delicate','slim','regular','plump','toned','bulk','glamorous'
  ));

-- 2) fm_biometric_enrollments: height_bucket / body_type 스테이징(gender 는 모델 소유)
alter table public.fm_biometric_enrollments add column if not exists height_bucket text;
alter table public.fm_biometric_enrollments add column if not exists body_type text;

alter table public.fm_biometric_enrollments drop constraint if exists fm_enrollments_height_bucket_check;
alter table public.fm_biometric_enrollments add constraint fm_enrollments_height_bucket_check
  check (height_bucket is null or height_bucket in (
    'm_lt170','m_170_175','m_175_180','m_180_185','m_185_190','m_gte190',
    'f_lt155','f_155_160','f_160_165','f_165_170','f_170_175','f_gte175'
  ));

alter table public.fm_biometric_enrollments drop constraint if exists fm_enrollments_body_type_check;
alter table public.fm_biometric_enrollments add constraint fm_enrollments_body_type_check
  check (body_type is null or body_type in (
    'delicate','slim','regular','plump','toned','bulk','glamorous'
  ));
```

- [ ] **Step 2: 마이그레이션 적용 검증(로컬 supabase)**

Run: `cd server && supabase db reset` 이 무거우면, 최소한 SQL 구문 검증 — 로컬 PG 있으면 `psql -f supabase/migrations/20260828000000_facemarket_model_physique.sql` 로 적용, 없으면 `supabase db lint`(가능 시).
Expected: 에러 없이 적용. 재적용(idempotent) 시에도 `if not exists`/`drop if exists`로 무해.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20260828000000_facemarket_model_physique.sql
git commit -m "feat(facemarket): physique 컬럼 마이그레이션 (fm_models·enrollments)"
```

---

### Task 2: physique 단일 소스 모듈 + 검증 + 블록 빌더

**Files:**
- Create: `server/app/facemarket_physique.py`
- Test: `server/tests/test_facemarket_physique.py`

**Interfaces:**
- Produces:
  - `GENDERS: tuple[str,...]` = `("male","female")`
  - `HEIGHT_BUCKETS: dict[str, tuple[str,...]]` — gender→버킷 튜플
  - `BODY_TYPES: tuple[str,...]` — 7종
  - `validate_physique(*, height_bucket: str|None, body_type: str|None, gender: str|None) -> None` — 위반 시 `PhysiqueError(code, message)` raise
  - `build_body_profile_block(profile: Mapping|None) -> str` — profile={"gender","heightBucket","bodyType"} → 영문 프롬프트 블록 or `""`
  - `class PhysiqueError(Exception)` with `.code`, `.message`

- [ ] **Step 1: 실패 테스트 작성**

```python
# server/tests/test_facemarket_physique.py
import pytest
from app.facemarket_physique import (
    validate_physique, build_body_profile_block, PhysiqueError,
    HEIGHT_BUCKETS, BODY_TYPES, GENDERS,
)

def test_vocab_shapes():
    assert GENDERS == ("male", "female")
    assert len(BODY_TYPES) == 7 and "toned" in BODY_TYPES and "glamorous" in BODY_TYPES
    assert HEIGHT_BUCKETS["male"][0] == "m_lt170"
    assert HEIGHT_BUCKETS["female"][-1] == "f_gte175"

def test_validate_accepts_partial_and_none():
    validate_physique(height_bucket=None, body_type=None, gender=None)
    validate_physique(height_bucket=None, body_type="slim", gender=None)

def test_validate_rejects_unknown_body_type():
    with pytest.raises(PhysiqueError) as e:
        validate_physique(height_bucket=None, body_type="hulk", gender=None)
    assert e.value.code == "invalid_physique"

def test_validate_bucket_gender_prefix_mismatch():
    # female 모델에 male 버킷 → 거부
    with pytest.raises(PhysiqueError) as e:
        validate_physique(height_bucket="m_180_185", body_type=None, gender="female")
    assert e.value.code == "invalid_physique"

def test_validate_bucket_requires_gender():
    with pytest.raises(PhysiqueError):
        validate_physique(height_bucket="m_180_185", body_type=None, gender=None)

def test_block_empty_when_nothing():
    assert build_body_profile_block(None) == ""
    assert build_body_profile_block({}) == ""

def test_block_renders_fixed_phrases_no_freetext():
    block = build_body_profile_block(
        {"gender": "male", "heightBucket": "m_180_185", "bodyType": "toned"}
    )
    assert "180" in block and "185" in block
    assert "toned" in block.lower() or "lean" in block.lower()
    assert "SUBJECT BUILD" in block
    # 얼굴 속성 미방출
    assert "face" not in block.lower() or "unchanged" in block.lower()

def test_block_partial_only_body_type():
    block = build_body_profile_block({"bodyType": "glamorous"})
    assert block  # 비어있지 않음
    assert "glamorous" in block.lower() or "curvy" in block.lower()
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_physique.py -q`
Expected: FAIL (`ModuleNotFoundError: app.facemarket_physique`)

- [ ] **Step 3: 모듈 구현**

```python
# server/app/facemarket_physique.py
"""FaceMarket 모델 physique(체형·키) 단일 소스 — enum·라벨·프롬프트 문구·검증·블록 빌더.
값→라벨(ko, UI)·문구(en, 프롬프트) 매핑을 여기서만 소유한다. 프롬프트엔 자유문자열을 절대
방출하지 않는다(검증된 enum→고정 문구만; fit_axes.build_fit_profile_block 관례)."""
from __future__ import annotations
from collections.abc import Mapping

GENDERS: tuple[str, ...] = ("male", "female")

HEIGHT_BUCKETS: dict[str, tuple[str, ...]] = {
    "male": ("m_lt170", "m_170_175", "m_175_180", "m_180_185", "m_185_190", "m_gte190"),
    "female": ("f_lt155", "f_155_160", "f_160_165", "f_165_170", "f_170_175", "f_gte175"),
}

BODY_TYPES: tuple[str, ...] = (
    "delicate", "slim", "regular", "plump", "toned", "bulk", "glamorous",
)

# 값 → (한국어 UI 라벨, 영문 프롬프트 문구)
_HEIGHT_LABELS: dict[str, tuple[str, str]] = {
    "m_lt170": ("170cm 미만", "under 170 cm tall"),
    "m_170_175": ("170–175cm", "approximately 170–175 cm tall"),
    "m_175_180": ("175–180cm", "approximately 175–180 cm tall"),
    "m_180_185": ("180–185cm", "approximately 180–185 cm tall"),
    "m_185_190": ("185–190cm", "approximately 185–190 cm tall"),
    "m_gte190": ("190cm 이상", "190 cm or taller"),
    "f_lt155": ("155cm 미만", "under 155 cm tall"),
    "f_155_160": ("155–160cm", "approximately 155–160 cm tall"),
    "f_160_165": ("160–165cm", "approximately 160–165 cm tall"),
    "f_165_170": ("165–170cm", "approximately 165–170 cm tall"),
    "f_170_175": ("170–175cm", "approximately 170–175 cm tall"),
    "f_gte175": ("175cm 이상", "175 cm or taller"),
}
_BODY_LABELS: dict[str, tuple[str, str]] = {
    "delicate": ("여리여리", "a delicate, slender build"),
    "slim": ("마름", "a slim build"),
    "regular": ("보통", "an average build"),
    "plump": ("통통", "a fuller, soft build"),
    "toned": ("잔잔한 근육", "a lean, lightly toned build"),
    "bulk": ("벌크업", "a muscular, bulked-up build"),
    "glamorous": ("글래머러스", "a curvy, glamorous build"),
}
_GENDER_PHRASE = {"male": "male presentation", "female": "female presentation"}


class PhysiqueError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _bucket_gender(bucket: str) -> str | None:
    if bucket.startswith("m_"):
        return "male"
    if bucket.startswith("f_"):
        return "female"
    return None


def validate_physique(*, height_bucket: str | None, body_type: str | None, gender: str | None) -> None:
    """부분 입력 허용(각 필드 독립). 위반 시 PhysiqueError('invalid_physique')."""
    if gender is not None and gender not in GENDERS:
        raise PhysiqueError("invalid_physique", "성별 값이 올바르지 않습니다.")
    if body_type is not None and body_type not in BODY_TYPES:
        raise PhysiqueError("invalid_physique", "체형 값이 올바르지 않습니다.")
    if height_bucket is not None:
        if height_bucket not in _HEIGHT_LABELS:
            raise PhysiqueError("invalid_physique", "키 구간 값이 올바르지 않습니다.")
        bg = _bucket_gender(height_bucket)
        if gender is None:
            raise PhysiqueError("invalid_physique", "키 구간을 저장하려면 성별이 필요합니다.")
        if bg != gender:
            raise PhysiqueError("invalid_physique", "키 구간이 성별과 일치하지 않습니다.")


def build_body_profile_block(profile: Mapping | None) -> str:
    """profile={"gender","heightBucket","bodyType"} → 영문 프롬프트 블록. 아무것도 없으면 ''.
    자유문자열 미방출 — enum→고정 문구만."""
    if not isinstance(profile, Mapping):
        return ""
    parts: list[str] = []
    height = profile.get("heightBucket")
    if height in _HEIGHT_LABELS:
        parts.append(_HEIGHT_LABELS[height][1])
    body = profile.get("bodyType")
    if body in _BODY_LABELS:
        parts.append(_BODY_LABELS[body][1])
    gender = profile.get("gender")
    if gender in _GENDER_PHRASE:
        parts.append(_GENDER_PHRASE[gender])
    if not parts:
        return ""
    desc = ", ".join(parts)
    return (
        "SUBJECT BUILD (generated body identity; the face is owned separately and "
        "left unchanged): the model has " + desc + ". Keep this build consistent across "
        "cuts; it has no authority over the face."
    )
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_physique.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add server/app/facemarket_physique.py server/tests/test_facemarket_physique.py
git commit -m "feat(facemarket): physique 단일 소스 모듈(vocab·검증·프롬프트 블록)"
```

---

### Task 3: 성별 OACX 자동 세팅 (identity_verify)

**Files:**
- Modify: `server/app/facemarket.py` (`identity_verify`, ~line 258-296: fields 구성 + fm_models insert/update)
- Test: `server/tests/test_facemarket_biometrics.py` (identity_verify 테스트가 있는 파일 — 없으면 `test_facemarket_identity.py` 신규)

**Interfaces:**
- Consumes: OACX `trans` 응답(`_dig`).
- Produces: `fm_models.gender` 가 신원확인 시 세팅됨(CX가 성별 주면). fields JSON 에 `gender` 포함.

- [ ] **Step 1: 실패 테스트 작성** — identity_verify 테스트 픽스처(기존 파일의 CX mock)에 성별 필드를 추가하고, 응답/DB에 gender 가 세팅되는지 검증. 기존 identity 테스트 파일의 CX fake(`_dig` 대상 dict)에 예: `"gender": "1"`(남) 또는 `"sexCd"` 추가. 실제 CX 필드명은 구현 스텝에서 raw 응답 확인 후 확정하되, 테스트는 헬퍼 `_gender_from_trans(trans)` 단위로 검증한다:

```python
# 기존 identity 테스트 파일에 추가 (import 경로는 파일 관례에 맞춤)
from app.facemarket import _gender_from_trans

@pytest.mark.parametrize("raw,expected", [
    ({"gender": "M"}, "male"),
    ({"gender": "F"}, "female"),
    ({"sexCd": "1"}, "male"),
    ({"sexCd": "2"}, "female"),
    ({"gender": "male"}, "male"),
    ({}, None),
    ({"gender": "x"}, None),
])
def test_gender_from_trans(raw, expected):
    assert _gender_from_trans(raw) == expected
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest -k gender_from_trans -q`
Expected: FAIL (`_gender_from_trans` 없음)

- [ ] **Step 3: 구현** — `facemarket.py`에 헬퍼 추가 + fields·insert 배선. CX 성별 코드 관례(1/M=남, 2/F=여)를 정규화.

```python
# facemarket.py — _dig 근처에 헬퍼 추가
def _gender_from_trans(trans) -> str | None:
    raw = _dig(trans, "gender", "sex", "sexCd", "sexCode", "genderCd")
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s in ("m", "male", "1", "남", "남자"):
        return "male"
    if s in ("f", "female", "2", "여", "여자"):
        return "female"
    return None
```

`identity_verify`의 fields(~270)에 `"gender": _gender_from_trans(trans)` 추가. 모델 insert(~291-296)와 update(~286-289)에 `gender` 컬럼 세팅:

```python
gender = _gender_from_trans(trans)
fields = {
    "nameMasked": name_masked,
    "birthYear": str(birth)[:4] if birth else None,
    "vcType": _dig(trans, "vcTypeCodeList"),
    "gender": gender,
}
...
# existing 분기(update):
await cur.execute(
    "update fm_models set status = 'verified', user_id = %s, "
    "gender = coalesce(%s, gender) where id = %s",
    (user_id, gender, model_id),
)
# else 분기(insert):
await cur.execute(
    """insert into fm_models (user_id, display_name, status, ci_hash, gender)
       values (%s, %s, 'verified', %s, %s) returning id""",
    (user_id, name_masked, ci_hash, gender),
)
```

(`coalesce(%s, gender)`: 재인증 시 CX가 성별을 안 주면 기존 값 보존.)

- [ ] **Step 4: 통과 확인 + 회귀**

Run: `cd server && uv run pytest -k "gender_from_trans or identity" tests/ -q`
Expected: PASS. identity_verify 기존 테스트 green 유지(gender 컬럼 추가는 additive).

- [ ] **Step 5: Commit**

```bash
git add server/app/facemarket.py server/tests/
git commit -m "feat(facemarket): 성별을 OACX 신원확인에서 자동 세팅"
```

---

### Task 4: physique 라우트 + EnrollmentView 필드

**Files:**
- Modify: `server/app/facemarket_enrollment.py` (`EnrollmentView` ~193; `_enrollment_view` ~613; `_load_owned_enrollment`; 새 라우트 profile-image ~1108 옆)
- Test: `server/tests/test_facemarket_biometric_enrollment.py`

**Interfaces:**
- Consumes: `app.facemarket_physique.validate_physique`, `PhysiqueError`.
- Produces: `POST /v1/facemarket/enrollments/{id}/physique` (body `{heightBucket?, bodyType?}`, 응답 `EnrollmentView`). `EnrollmentView`에 `height_bucket`,`body_type`,`gender` 추가.

- [ ] **Step 1: 실패 테스트 작성** — 기존 enrollment 테스트의 fake store 패턴을 따라(대표 이미지 업로드 테스트 미러) physique 저장·검증·복원 테스트.

```python
# test_facemarket_biometric_enrollment.py 에 추가
def test_physique_saves_and_returns(client, ...):  # 기존 픽스처 시그니처에 맞춤
    eid = _start_enrollment_through_photos(client, ...)  # 기존 헬퍼 재사용
    r = client.post(f"/v1/facemarket/enrollments/{eid}/physique",
                    json={"heightBucket": "f_165_170", "bodyType": "toned"},
                    headers=_auth(...))
    assert r.status_code == 200
    body = r.json()
    assert body["heightBucket"] == "f_165_170"
    assert body["bodyType"] == "toned"

def test_physique_rejects_gender_mismatch(client, ...):
    # 모델 gender=female 인 등록에 male 버킷 → 400 invalid_physique
    eid = _start_enrollment_through_photos(client, ..., model_gender="female")
    r = client.post(f"/v1/facemarket/enrollments/{eid}/physique",
                    json={"heightBucket": "m_180_185"}, headers=_auth(...))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_physique"
```

(fake store에 `fm_biometric_enrollments`의 height_bucket/body_type + 모델 gender 조회를 지원하도록 확장. 기존 store가 profile_image_r2_key를 다루는 방식을 미러.)

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_biometric_enrollment.py -k physique -q`
Expected: FAIL (라우트 없음 → 404)

- [ ] **Step 3: 구현**

`EnrollmentView`(~193)에 필드 추가:
```python
    height_bucket: str | None = None
    body_type: str | None = None
    gender: str | None = None
```

Body 모델 + 라우트 추가(profile-image 라우트 ~1108 옆):
```python
class PhysiqueBody(CamelModel):
    height_bucket: str | None = None
    body_type: str | None = None

@router.post("/enrollments/{enrollment_id}/physique", response_model=EnrollmentView)
async def set_physique(
    request: Request,
    enrollment_id: str,
    body: PhysiqueBody,
    user_id: str = Depends(require_user),
):
    from .facemarket_physique import validate_physique, PhysiqueError
    enrollment_id = _canonical_enrollment_id(enrollment_id)
    async with get_conn(request) as conn:
        await _assert_account_open(conn, user_id)
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        if row is None:
            raise _err("not_found", "등록을 찾을 수 없습니다.", status=404)
        try:
            validate_physique(
                height_bucket=body.height_bucket, body_type=body.body_type,
                gender=row.get("model_gender"),
            )
        except PhysiqueError as e:
            raise _err(e.code, e.message, status=400)
        async with conn.cursor() as cur:
            await cur.execute(
                "update fm_biometric_enrollments set height_bucket = %s, body_type = %s "
                "where id = %s and user_id = %s",
                (body.height_bucket, body.body_type, enrollment_id, user_id),
            )
        await conn.commit()
        row = await _load_owned_enrollment(conn, enrollment_id, user_id)
        return await _enrollment_view(conn, row)
```

`_load_owned_enrollment` SELECT에 `e.height_bucket`, `e.body_type` + 모델 gender(`m.gender as model_gender`, fm_models 조인이 이미 있으면 컬럼만 추가; 없으면 `left join fm_models m on m.id = e.model_id`)를 포함. `_enrollment_view`(~633 return) 에 매핑 추가:
```python
        height_bucket=row.get("height_bucket"),
        body_type=row.get("body_type"),
        gender=row.get("model_gender"),
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_biometric_enrollment.py -q`
Expected: PASS (physique 테스트 + 기존 green)

- [ ] **Step 5: Commit**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "feat(facemarket): physique 라우트 + EnrollmentView 필드"
```

---

### Task 5: 등록 완료 시 fm_models 승격

**Files:**
- Modify: `server/app/facemarket_enrollment.py` (finalization ~2010, cover 승격 옆)
- Test: `server/tests/test_facemarket_biometric_enrollment.py`

**Interfaces:**
- Consumes: enrollment row의 `height_bucket`/`body_type`.
- Produces: `fm_models.height_bucket`/`body_type` 가 등록 완료 시 채워짐.

- [ ] **Step 1: 실패 테스트 작성** — 등록을 physique 세팅 후 완료까지 진행 → fm_models에 height_bucket/body_type 승격 확인.

```python
def test_physique_promoted_to_model_on_finalize(client, store, ...):
    eid, model_id = _complete_enrollment_with_physique(
        client, store, ..., height_bucket="f_165_170", body_type="toned")
    model = store.model(model_id)  # fake store accessor
    assert model["height_bucket"] == "f_165_170"
    assert model["body_type"] == "toned"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_biometric_enrollment.py -k promoted_to_model -q`
Expected: FAIL

- [ ] **Step 3: 구현** — finalization의 cover 승격 블록(~2007-2013) 바로 뒤에 추가. `row`(enrollment)에 height_bucket/body_type 이 실려야 하므로 finalization이 읽는 enrollment 조회에 두 컬럼 포함(이미 `row.get`로 접근 가능하면 조회 SELECT만 확장).

```python
                # physique(키·체형) 승격 — cover 승격과 같은 트랜잭션. gender 는 identity 단계서 이미 세팅.
                if row.get("height_bucket") or row.get("body_type"):
                    await cur.execute(
                        "update fm_models set height_bucket = coalesce(%s, height_bucket), "
                        "body_type = coalesce(%s, body_type) where id = %s",
                        (row.get("height_bucket"), row.get("body_type"), model_id),
                    )
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_biometric_enrollment.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/facemarket_enrollment.py server/tests/test_facemarket_biometric_enrollment.py
git commit -m "feat(facemarket): 등록 완료 시 physique 를 fm_models 로 승격"
```

---

### Task 6: 컷 생성 로드 seam — resolve_model_license physique 로드

**Files:**
- Modify: `server/app/facemarket.py` (`resolve_model_license` ~1860, SELECT ~1885-1928, `from fm_models m` ~1922)
- Test: `server/tests/test_facemarket_licenses.py` (resolve_model_license 반환에 physique 포함)

**Interfaces:**
- Produces: `resolve_model_license(...)` 반환 dict에 `gender`,`height_bucket`,`body_type` 포함.

- [ ] **Step 1: 실패 테스트 작성** — fake store의 resolve_model_license 대응 쿼리 결과에 physique 3필드가 실리는지. (기존 라이선스 테스트 fake의 `select ... from fm_models` 핸들러 확장.)

```python
def test_resolve_model_license_includes_physique(...):
    # store에 gender/height_bucket/body_type 세팅된 모델 + 라이선스
    row = _call_resolve_model_license(...)  # 얇은 헬퍼 or 엔드포인트 경유
    assert row["gender"] == "male"
    assert row["height_bucket"] == "m_180_185"
    assert row["body_type"] == "toned"
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_facemarket_licenses.py -k physique -q`
Expected: FAIL

- [ ] **Step 3: 구현** — `resolve_model_license`의 SELECT에 `m.gender, m.height_bucket, m.body_type` 추가(이미 `from fm_models m` 조인 존재). fake store의 해당 핸들러도 3필드 반환하도록 확장.

- [ ] **Step 4: 통과 확인**

Run: `cd server && uv run pytest tests/test_facemarket_licenses.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/app/facemarket.py server/tests/test_facemarket_licenses.py
git commit -m "feat(facemarket): resolve_model_license 가 모델 physique 를 싣는다"
```

---

### Task 7: body_profile 스레딩 + 프롬프트 블록 렌더

**Files:**
- Modify: `server/app/agents/cut_generator.py` (`generate` ~1326; `build_prompt` ~1216; `render_cut_prompt` ~613, join ~900-902)
- Modify: `server/app/workers/editor_image_job.py` (`fm_face_injected` ~437-441; generate_kwargs ~600; generate 호출 ~607/650/659/681)
- Modify: `server/app/workers/detail_page_job.py` (generate 호출부 ~321/397/408/433/520)
- Test: `server/tests/test_cut_generator_body_profile.py` (신규)

**Interfaces:**
- Consumes: `resolve_model_license` 반환의 physique(Task 6), `build_body_profile_block`(Task 2).
- Produces: 착장 컷 프롬프트에 SUBJECT BUILD 블록(값 있을 때). `generate/build_prompt/render_cut_prompt`에 `body_profile: dict|None = None` 파라미터.

- [ ] **Step 1: 실패 테스트 작성** — render_cut_prompt에 body_profile 넘기면 프롬프트에 블록이 들어가고, None이면 안 들어감. (얼굴/기존 블록 불변 확인.)

```python
# server/tests/test_cut_generator_body_profile.py
from app.agents import cut_generator

def _spec():
    return {"cutType": "styling", "shot": "full", "faceExposure": "show", "modelId": "m1"}

def test_render_includes_body_block_when_profile_present():
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(), cut_generator.normalize_spec(_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "", has_face=True,
        body_profile={"gender": "male", "heightBucket": "m_180_185", "bodyType": "toned"},
    )
    assert "SUBJECT BUILD" in prompt and "180" in prompt

def test_render_omits_body_block_when_none():
    prompt = cut_generator.render_cut_prompt(
        cut_generator.load_cut_template(), cut_generator.normalize_spec(_spec(), clothing_type="top"),
        {"clothing_type": "top"}, {}, "top", "", has_face=True, body_profile=None,
    )
    assert "SUBJECT BUILD" not in prompt
```

(정확한 render_cut_prompt 인자 순서는 구현 시 파일에서 확인해 맞춘다 — 위는 시그니처 기준.)

- [ ] **Step 2: 실패 확인**

Run: `cd server && uv run pytest tests/test_cut_generator_body_profile.py -q`
Expected: FAIL (`render_cut_prompt() got an unexpected keyword argument 'body_profile'`)

- [ ] **Step 3: 구현 — cut_generator 스레딩**

`render_cut_prompt`(~613) 시그니처에 `body_profile: dict | None = None` 추가. import 추가(파일 상단): `from ..facemarket_physique import build_body_profile_block`. join 지점(~900-902):
```python
    fit_block = build_fit_profile_block(fit_profile)
    body_block = build_body_profile_block(body_profile)
    block = _product_block(product, analysis or {}, include_legacy_fit=fit_profile is None)
    return "\n\n".join(
        part for part in (text, directing_block, fit_block, body_block, block) if part
    )
```

`build_prompt`(~1216) 시그니처에 `body_profile=None` 추가, `render_cut_prompt(...)` 호출(~1255)에 `body_profile=body_profile` 전달.

`generate`(~1326) 시그니처에 `body_profile: dict | None = None` 추가, `build_prompt(...)` 호출(~1367)에 `body_profile=body_profile` 전달.

- [ ] **Step 4: cut_generator 테스트 통과**

Run: `cd server && uv run pytest tests/test_cut_generator_body_profile.py -q`
Expected: PASS

- [ ] **Step 5: 구현 — 워커 배선(editor_image_job)**

`fm_face_injected`(~437-441) 직후:
```python
    body_profile = None
    if fm_face_injected and isinstance(fm_license_row, dict):
        body_profile = {
            "gender": fm_license_row.get("gender"),
            "heightBucket": fm_license_row.get("height_bucket"),
            "bodyType": fm_license_row.get("body_type"),
        }
```
`generate_kwargs`(~600)에 `generate_kwargs["body_profile"] = body_profile`. (모든 generate 호출이 generate_kwargs 를 언패킹하므로 재시도/후보 호출도 자동 포함.)

- [ ] **Step 6: 구현 — 워커 배선(detail_page_job)**

detail_page_job 의 REAL 모델 분기에서 동일하게 `fm_license_row`(또는 real 모델 로드 결과)에서 physique 를 뽑아 각 `cut_generator.generate(...)` 호출(~321/397/408/433/520)에 `body_profile=` 로 전달. worn-cut 게이트는 detail_page_job 의 `model_has_full_body`/real 분기 조건을 따르되, product/ghost 컷엔 전달하지 않는다(값이 있어도 render 는 modelId 없는 spec 이면 무해하지만, 명시적으로 worn 컷에만).

- [ ] **Step 7: 워커 회귀 테스트**

Run: `cd server && uv run pytest tests/test_editor_image.py tests/test_detail_page.py -q`
Expected: PASS (기존 green 유지 — body_profile 은 additive optional)

- [ ] **Step 8: Commit**

```bash
git add server/app/agents/cut_generator.py server/app/workers/editor_image_job.py server/app/workers/detail_page_job.py server/tests/test_cut_generator_body_profile.py
git commit -m "feat(facemarket): 착장 컷 프롬프트에 모델 body_profile 주입"
```

---

### Task 8: 프론트 physique 스텝 + API

**Files:**
- Modify: `src/lib/api/facemarket.js` (`uploadProfileImage` ~113 옆에 `submitPhysique`)
- Modify: `src/features/model/ModelRegister.jsx` (스텝머신: photos→physique→profile; 새 스텝 UI; FLOW_STEPS)
- Modify: `src/features/model/ModelRegister.module.css` (스텝 스타일 — 기존 uploadZone/chip 재사용)
- Test: `tests/frontend/facemarket-biometric-enrollment.test.mjs`

**Interfaces:**
- Consumes: `POST /enrollments/{id}/physique` (Task 4). EnrollmentView.gender(버킷 필터).
- Produces: 위저드 physique 스텝.

- [ ] **Step 1: API 함수 추가**

```javascript
// src/lib/api/facemarket.js — uploadProfileImage 아래
export function submitPhysique({ enrollmentId, heightBucket, bodyType }) {
  return http(
    `/v1/facemarket/enrollments/${encodeURIComponent(enrollmentId)}/physique`,
    { method: 'POST', body: JSON.stringify({ heightBucket, bodyType }) },
  );
}
```
(http 헬퍼의 기존 시그니처에 맞춤 — 파일 상단 http 정의 확인해 body/headers 형태 일치.)

- [ ] **Step 2: 실패 테스트 작성** — 위저드가 physique 스텝을 렌더하고 제출 시 submitPhysique 호출 후 profile 스텝으로 전이. (기존 테스트의 fake api 모듈에 submitPhysique 추가, 훅 순서 회귀 없음 확인.)

```javascript
// facemarket-biometric-enrollment.test.mjs — 기존 패턴대로
test('physique 스텝: 버킷 선택→제출→대표이미지 스텝', async () => {
  // photos 완료 상태로 진입 → physique 렌더 확인 → bodyType 선택 → 제출 → profile 스텝
  // fakeApi.submitPhysique 가 호출됐는지 + 다음 스텝이 'profile' 인지 assert
});
```

- [ ] **Step 3: 실패 확인**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs`
Expected: FAIL

- [ ] **Step 4: 구현 — 스텝머신 + UI**

`finishPhotos`(~321-327): `setStep('profile')` → `setStep('physique')`.
`FLOW_STEPS`(~105)에 physique 항목 삽입(photos 와 profile 사이).
새 스텝 블록 추가(profile 스텝 ~707-733 미러). gender(EnrollmentView.gender)로 `HEIGHT_BUCKETS[gender]` 필터, 없으면 남녀 통합. 체형 7칩. 제출 → `submitPhysique` → `setStep('profile')`. 건너뛰기 → `setStep('profile')`. **훅 순서 보존:** 새 useState(선택값·busy)와 effect 는 컴포넌트 **마지막**에 추가(기존 테스트 위치 인덱스 회귀 방지 — [[facemarket-biometric-shipped]] 관례).
버킷/체형 라벨은 프론트 상수(별도 `src/lib/facemarketPhysique.js` 또는 컴포넌트 내 상수)로 두되, 값(value)은 백엔드 enum과 정확히 일치(`m_180_185` 등).

- [ ] **Step 5: 통과 확인 + 빌드**

Run: `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs && npm run build`
Expected: 테스트 PASS + build 성공

- [ ] **Step 6: Commit**

```bash
git add src/lib/api/facemarket.js src/features/model/ModelRegister.jsx src/features/model/ModelRegister.module.css tests/frontend/facemarket-biometric-enrollment.test.mjs
git commit -m "feat(facemarket): 등록 위저드 체형·키 스텝"
```

---

## 최종 검증 (전 태스크 후)

- [ ] `cd server && uv run pytest tests/ -q` — 전체 서버 green.
- [ ] `npm run build` — 프론트 빌드.
- [ ] `node --test tests/frontend/facemarket-biometric-enrollment.test.mjs` — 위저드 green.
- [ ] 수동 스모크(선택): 로컬 등록 → physique 선택 → 완료 → fm_models 값 확인 → 그 모델로 에디터 컷 생성 → 프롬프트 로그에 SUBJECT BUILD 확인.
