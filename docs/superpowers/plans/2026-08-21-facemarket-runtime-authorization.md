# FaceMarket Runtime Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실물 FaceMarket 모델 사용 요청을 12개 고정 브랜드 분류, 현재 생체 enrollment 자산, 유효한 Holder VC로 승인하고 API와 worker 양쪽에서 실패 시 생성·크레딧 차감·정산·실물 대체를 모두 차단한다.

**Architecture:** 생체 등록 계획의 runtime schema와 mandatory VC 계획의 signed Holder client/기존 `verify_license`를 선행 계약으로 소비한다. 이 계획은 새 migration이나 Holder 통신 계층을 만들지 않고, 기존 `resolve_model_license`가 현재 license/model/enrollment/asset evidence를 한 행으로 읽도록 확장하고 기존 `verify_license`에 model/category/current-evidence 판정만 합친다. API는 크레딧 예약 전에 그 공통 함수를 호출하고 검증된 model/license/category를 job payload에 snapshot하며, detail/editor worker는 mandatory VC 계획이 만든 동일 recheck 지점을 snapshot-pinned 판정으로 확장해 요청 이후 revoke·purge·Holder 장애 race를 막는다. 카탈로그 썸네일은 얼굴 저장소를 전혀 읽지 않고 서버 내장 비생체 SVG placeholder만 반환한다.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3, PostgreSQL JSONB, httpx, React 18, Node test runner, pytest

**Spec:** `docs/superpowers/specs/2026-08-21-facemarket-biometric-runtime-hardening-design.md`

## Global Constraints

- 실제 송금·지급은 구현하지 않는다. 기존 온체인 정산은 성공한 생성의 record-only 감사 기록으로만 유지한다.
- 새 범용 policy/ABAC 계층을 만들지 않는다. 공유 판정은 기존 `resolve_model_license`와 `verify_license`만 확장한다.
- 실물 모델은 UUID, 기존 가상 모델은 비UUID라는 현재 계약을 유지한다. `None` 판정은 가상 모델에만 허용하고 UUID 실물 모델의 누락 상태는 거절한다.
- 브랜드 사용 분류는 아래 12개 문자열만 허용하며 공백 정리 뒤 exact match한다.
  - 허용 분류: `일반 여성 의류`, `남성 의류`, `캐주얼·스트릿`, `스포츠·애슬레저`, `뷰티·화장품`, `액세서리·잡화`
  - 금지 분류: `속옷·란제리`, `수영복·비키니`, `성인용품`, `주류·담배`, `의료·성형`, `정치·종교`
- 금지 분류가 항상 우선한다. `allowed_use`가 비어 있거나 선택 분류가 없으면 거절한다.
- `clothingType`, `category`, `subCategory`로 `brandUseCategory`를 추정하지 않는다.
- API는 job 생성과 credit 예약 전에 로컬 상태와 mandatory signed Holder VC를 검증한다. Holder URL/secret 누락은 선행 startup invariant가 서버 기동을 거절하고, runtime timeout·연결 실패·5xx·해석 불가는 503, VC 누락·invalid·revoked는 409다.
- worker는 job snapshot과 현재 DB/Holder 상태를 다시 검증한다. 실패는 기존 `finalize_detail_page_failure` 또는 `finalize_editor_image_failure`로 예약 credit을 환불하고 성공 결과·R2 결과·정산을 만들지 않는다.
- 실물 모델을 가상 모델이나 얼굴 없는 결과로 대체하지 않는다. 가상 모델 ID의 기존 생성 흐름은 변경하지 않는다.
- 카탈로그는 검증되지 않은 `cover_image_url`을 표시하지 않는다. `/models/{id}/thumbnail`은 `face_front`, `fm_model_assets.r2_key`, `r2_face`를 읽지 않는다.
- 모든 thumbnail 응답은 `Cache-Control: no-store, private`를 유지한다.
- category와 job snapshot은 기존 `analyses.payload`와 `jobs.payload` JSONB에 저장한다. 이 계획은 migration을 하나도 추가하지 않는다.
- 선행 `docs/superpowers/plans/2026-08-21-facemarket-biometric-enrollment.md`의 runtime schema를 그대로 소비한다: `fm_models.current_enrollment_id`, `fm_licenses.enrollment_id`, `fm_model_assets.source_enrollment_id`, `fm_model_assets.evidence_version`, `fm_biometric_enrollments.status='passed'`, model/license 상태 `reverification_required`. 이 계획에서 같은 column/status/migration을 다시 정의하지 않는다.
- 선행 `docs/superpowers/plans/2026-08-21-facemarket-mandatory-vc-cutover.md` Task 3-5를 그대로 소비한다: `server/app/holder_client.py`, `fm_vc_required`, `opendid_holder_hmac_secret`, signed `holder_client.post`, mandatory `verify_license`, worker-time VC recheck와 기존 failure finalizer. 이 계획은 unsigned `httpx` 호출, 두 번째 Holder client, 두 번째 VC verifier, 두 번째 worker recheck를 만들지 않는다.
- 실행 순서는 biometric runtime schema 적용 → mandatory VC Task 1-5 완료 → 이 계획이다. 외부 OACX/liveness 등록, VC issue의 `pending -> active`, durable revoke queue, production startup/HMAC 설정, 기존 모델 freeze/purge는 각 선행 계획이 소유하고 이 계획은 그 결과 상태를 소비해 생성만 fail-closed한다.
- 새 dependency를 추가하지 않는다.

---

## File Structure

- Create: `src/lib/brandUseCategories.js` — 프런트 전체가 재사용하는 12개 표시 상수.
- Modify: `src/features/model/ModelLicense.jsx` — 라이선스 허용/금지 칩을 공유 상수로 표시.
- Modify: `src/features/analysis/AnalysisForm.jsx` — 실물 모델 선택 시 프로젝트 `brandUseCategory`를 명시적으로 선택·저장.
- Modify: `src/lib/api/shapes.js` — 분석 기본 shape에 `brandUseCategory: null` 추가.
- Modify: `src/lib/types.js` — Analysis/NewCutRequest 계약에 `brandUseCategory` 문서화.
- Modify: `src/features/editor/Editor.jsx` — 저장된 분류를 editor `mode:'new'` 요청에 전달.
- Modify: `server/app/facemarket.py` — 서버 고정 분류, license 입력 제한, 확장 resolver/verifier, placeholder thumbnail.
- Modify: `server/app/routes.py` — analysis 저장 검증, detail/editor pre-credit gate, 서버 생성 job snapshot.
- Modify: `server/app/workers/detail_page_job.py` — snapshot 기반 worker 재검증, 환불, 실물 fallback 제거.
- Modify: `server/app/workers/editor_image_job.py` — snapshot 기반 worker 재검증과 환불.
- Create: `tests/frontend/facemarket-brand-use-category.test.mjs` — 고정 분류·분석 UI·editor payload 회귀.
- Modify: `server/tests/test_facemarket_licenses.py` — license 닫힌 분류와 placeholder thumbnail HTTP 계약.
- Modify: `server/tests/test_facemarket_seller_loop.py` — 공통 resolver/verifier의 모든 fail-closed arm.
- Modify: `server/tests/test_routes.py` — analysis 저장과 editor pre-credit 순서.
- Modify: `server/tests/test_detail_page.py` — detail pre-credit gate, snapshot, worker refund race.
- Modify: `server/tests/test_detail_page_identity_source.py` — 실물 거절 시 가상 모델 fallback 금지.
- Modify: `server/tests/test_cut_input_authority.py` — editor worker 재검증·환불·결과 없음.
- Modify: `server/tests/test_facemarket_identity.py` — 카탈로그가 검증되지 않은 cover를 노출하지 않는 계약.

---

### Task 1: Fix the 12 Brand-Use Categories at Both Trust Boundaries

**Files:**

- Create: `src/lib/brandUseCategories.js`
- Modify: `src/features/model/ModelLicense.jsx:38-56`
- Modify: `server/app/facemarket.py:420-470`
- Modify: `server/app/facemarket.py:536-650`
- Modify: `server/app/routes.py:865-899`
- Modify: `server/tests/test_facemarket_licenses.py`
- Modify: `server/tests/test_routes.py`
- Create: `tests/frontend/facemarket-brand-use-category.test.mjs`

**Interfaces:**

- Consumes: 기존 multipart `allowed_use[]`, `forbidden_use[]`; 기존 `analyses.payload` JSONB.
- Produces: `ALLOWED_BRAND_USE_CATEGORIES`, `FORBIDDEN_BRAND_USE_CATEGORIES`, `BRAND_USE_CATEGORIES`; 프런트의 동일한 세 배열; 저장 가능한 `analysis.brandUseCategory: string | null`.

- [ ] **Step 1: Write failing backend tests for the closed license values**

`server/tests/test_facemarket_licenses.py`에서 기존 자유 문자열 `광고`, `성인`, `상세페이지` 픽스처를 고정 분류로 바꾸고 다음 거절 테스트를 추가한다.

```python
def test_create_license_rejects_unknown_allowed_use(client, make_token):
    response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"allowed_use": "광고", "forbidden_use": "정치·종교"},
        headers=_auth(make_token),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"


def test_create_license_rejects_unknown_forbidden_use(client, make_token):
    response = client.post(
        "/v1/facemarket/licenses",
        files={"face": _png()},
        data={"allowed_use": "일반 여성 의류", "forbidden_use": "성인"},
        headers=_auth(make_token),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_use_category"
```

- [ ] **Step 2: Run the license tests and confirm the free strings still pass**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_licenses.py -k 'unknown_allowed_use or unknown_forbidden_use'
```

Expected: FAIL because `_clean_uses()` currently truncates and accepts both unknown strings.

- [ ] **Step 3: Add server-owned category constants and strict list cleaning**

Add next to `MAX_USE_ITEMS` in `server/app/facemarket.py`:

```python
ALLOWED_BRAND_USE_CATEGORIES = (
    "일반 여성 의류",
    "남성 의류",
    "캐주얼·스트릿",
    "스포츠·애슬레저",
    "뷰티·화장품",
    "액세서리·잡화",
)
FORBIDDEN_BRAND_USE_CATEGORIES = (
    "속옷·란제리",
    "수영복·비키니",
    "성인용품",
    "주류·담배",
    "의료·성형",
    "정치·종교",
)
BRAND_USE_CATEGORIES = frozenset(
    (*ALLOWED_BRAND_USE_CATEGORIES, *FORBIDDEN_BRAND_USE_CATEGORIES)
)
```

Replace `_clean_uses` with an exact allowed-set argument:

```python
def _clean_uses(items: list[str], accepted: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    accepted_set = frozenset(accepted)
    for raw in items or []:
        value = (raw or "").strip()
        if not value:
            continue
        if value not in accepted_set:
            raise _err("invalid_use_category", "정해진 브랜드 유형만 선택할 수 있어요.")
        if value not in out:
            out.append(value)
    return out
```

Update `create_license`:

```python
allowed = _clean_uses(allowed_use, ALLOWED_BRAND_USE_CATEGORIES)
forbidden = _clean_uses(forbidden_use, FORBIDDEN_BRAND_USE_CATEGORIES)
```

Delete `MAX_USE_ITEMS` and `MAX_USE_LEN`; the closed sets make truncation and count caps unnecessary.

- [ ] **Step 4: Run focused license tests**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_licenses.py
```

Expected: PASS after every positive fixture uses one of the fixed strings.

- [ ] **Step 5: Write failing analysis-storage tests**

Add to `server/tests/test_routes.py`:

```python
def test_save_analysis_normalizes_known_brand_use_category(
    client, make_token, monkeypatch,
):
    seen = {}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_save_analysis(conn, project_id, analysis):
        seen.update(analysis)
        return {"project_id": project_id, "payload": analysis}

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    response = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"brandUseCategory": "  일반 여성 의류  "},
    )
    assert response.status_code == 200
    assert seen["brandUseCategory"] == "일반 여성 의류"


def test_save_analysis_rejects_unknown_brand_use_category_before_storage(
    client, make_token, monkeypatch,
):
    calls = {"save": 0}

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_product(conn, project_id):
        return {"clothingType": "top"}

    async def fake_save_analysis(conn, project_id, analysis):
        calls["save"] += 1

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_product", fake_get_product)
    monkeypatch.setattr(routes.repo, "save_analysis", fake_save_analysis)
    patch_route_db(monkeypatch, routes)

    response = client.patch(
        "/v1/projects/p1/analysis",
        headers=_auth(make_token),
        json={"brandUseCategory": "의류"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_brand_use_category"
    assert calls["save"] == 0
```

- [ ] **Step 6: Run the analysis tests and confirm no validation exists**

Run:

```bash
cd server
uv run pytest -q tests/test_routes.py -k brand_use_category
```

Expected: the normalization assertion fails and the unknown value reaches `repo.save_analysis`.

- [ ] **Step 7: Validate `brandUseCategory` before `repo.save_analysis`**

In `server/app/routes.py:save_analysis`, after current fit/photo normalization and before opening the connection, add:

```python
    if "brandUseCategory" in analysis:
        category = str(analysis.get("brandUseCategory") or "").strip()
        if category not in facemarket.BRAND_USE_CATEGORIES:
            raise _bad_request(
                "invalid_brand_use_category",
                "정해진 브랜드 사용 분류를 선택해 주세요.",
            )
        analysis = {**analysis, "brandUseCategory": category}
```

Missing values remain valid for virtual-model projects; real-model generation becomes the mandatory gate in Task 3.

- [ ] **Step 8: Add the shared frontend constants**

Create `src/lib/brandUseCategories.js`:

```javascript
export const ALLOWED_BRAND_USE_CATEGORIES = Object.freeze([
  '일반 여성 의류',
  '남성 의류',
  '캐주얼·스트릿',
  '스포츠·애슬레저',
  '뷰티·화장품',
  '액세서리·잡화',
]);

export const FORBIDDEN_BRAND_USE_CATEGORIES = Object.freeze([
  '속옷·란제리',
  '수영복·비키니',
  '성인용품',
  '주류·담배',
  '의료·성형',
  '정치·종교',
]);

export const BRAND_USE_CATEGORIES = Object.freeze([
  ...ALLOWED_BRAND_USE_CATEGORIES,
  ...FORBIDDEN_BRAND_USE_CATEGORIES,
]);
```

In `src/features/model/ModelLicense.jsx`, import the first two arrays and remove the local `ALLOWED_PRESETS`/`FORBIDDEN_PRESETS` definitions. Keep the existing Chips markup, replacing the option names with the imports.

- [ ] **Step 9: Add a frontend contract test for the exact set and shared license import**

Create `tests/frontend/facemarket-brand-use-category.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  ALLOWED_BRAND_USE_CATEGORIES,
  BRAND_USE_CATEGORIES,
  FORBIDDEN_BRAND_USE_CATEGORIES,
} from '../../src/lib/brandUseCategories.js';

const licenseSource = readFileSync(
  new URL('../../src/features/model/ModelLicense.jsx', import.meta.url),
  'utf8',
);

test('brand-use categories are the approved fixed twelve', () => {
  assert.deepEqual(ALLOWED_BRAND_USE_CATEGORIES, [
    '일반 여성 의류', '남성 의류', '캐주얼·스트릿',
    '스포츠·애슬레저', '뷰티·화장품', '액세서리·잡화',
  ]);
  assert.deepEqual(FORBIDDEN_BRAND_USE_CATEGORIES, [
    '속옷·란제리', '수영복·비키니', '성인용품',
    '주류·담배', '의료·성형', '정치·종교',
  ]);
  assert.equal(BRAND_USE_CATEGORIES.length, 12);
  assert.equal(new Set(BRAND_USE_CATEGORIES).size, 12);
});

test('the license screen imports the shared values instead of owning another list', () => {
  assert.match(licenseSource, /from ["']@\/lib\/brandUseCategories\.js["']/);
  assert.doesNotMatch(licenseSource, /const ALLOWED_PRESETS/);
  assert.doesNotMatch(licenseSource, /const FORBIDDEN_PRESETS/);
});
```

- [ ] **Step 10: Run category tests**

Run:

```bash
npm run test:frontend -- --test-name-pattern='brand-use|license screen'
cd server
uv run pytest -q tests/test_facemarket_licenses.py tests/test_routes.py -k 'use_category or facemarket'
```

Expected: PASS.

- [ ] **Step 11: Commit the closed category boundary**

```bash
git add \
  src/lib/brandUseCategories.js \
  src/features/model/ModelLicense.jsx \
  server/app/facemarket.py \
  server/app/routes.py \
  server/tests/test_facemarket_licenses.py \
  server/tests/test_routes.py \
  tests/frontend/facemarket-brand-use-category.test.mjs
git commit \
  -m "Reject ambiguous brand-use authorization at the server boundary" \
  -m "Constraint: FaceMarket accepts only the approved twelve brand categories." \
  -m "Rejected: Free-form license tags | legacy strings cannot be authorized deterministically." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: Keep forbidden-use precedence in every runtime gate." \
  -m "Tested: FaceMarket license and analysis route tests; frontend category contract." \
  -m "Not-tested: Live Holder integration."
```

---

### Task 2: Persist an Explicit Category for Real-Model Projects

**Files:**

- Modify: `src/features/analysis/AnalysisForm.jsx:573-830`
- Modify: `src/features/analysis/AnalysisForm.jsx:1170-1280`
- Modify: `src/lib/api/shapes.js:284-306`
- Modify: `src/lib/types.js:108-126`
- Modify: `src/features/editor/Editor.jsx:2178-2193`
- Modify: `tests/frontend/facemarket-brand-use-category.test.mjs`

**Interfaces:**

- Consumes: `BRAND_USE_CATEGORIES`; existing `AnalysisForm.onChange`; existing `api.saveAnalysis` full-payload merge; existing `analysis.selectedModelId`.
- Produces: `analysis.brandUseCategory: string | null`; editor `NewCutRequest.brandUseCategory` copied from persisted analysis.

- [ ] **Step 1: Add failing source-contract tests for analysis selection and editor payload**

Append to `tests/frontend/facemarket-brand-use-category.test.mjs`:

```javascript
const analysisSource = readFileSync(
  new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url),
  'utf8',
);
const editorSource = readFileSync(
  new URL('../../src/features/editor/Editor.jsx', import.meta.url),
  'utf8',
);
const shapesSource = readFileSync(
  new URL('../../src/lib/api/shapes.js', import.meta.url),
  'utf8',
);

test('real-model analysis displays and saves one explicit brand-use category', () => {
  assert.match(analysisSource, /BRAND_USE_CATEGORIES/);
  assert.match(analysisSource, /value=\{a\.brandUseCategory\}/);
  assert.match(analysisSource, /onChange\(\{ brandUseCategory: value \}\)/);
  assert.match(analysisSource, /실제 모델을 사용할 브랜드 유형을 선택해 주세요/);
});

test('analysis shape and editor new-cut request preserve the category', () => {
  assert.match(shapesSource, /brandUseCategory: null/);
  assert.match(editorSource, /brandUseCategory: analysis\?\.brandUseCategory/);
});
```

- [ ] **Step 2: Run the frontend test and confirm the field is absent**

Run:

```bash
node --test tests/frontend/facemarket-brand-use-category.test.mjs
```

Expected: FAIL on the AnalysisForm, shapes, and Editor assertions.

- [ ] **Step 3: Add the field to the analysis shape and type contract**

In `src/lib/api/shapes.js:defaultAnalysisShape` add beside `selectedModelId`:

```javascript
brandUseCategory: null,
```

In `src/lib/types.js` add to `Analysis` and `NewCutRequest` documentation:

```javascript
@property {string|null} brandUseCategory  approved fixed category; required for a real model
```

- [ ] **Step 4: Render the explicit selector only for a selected real model**

In `AnalysisForm.jsx`, import `BRAND_USE_CATEGORIES` and `isRealModelSelection`:

```javascript
import { BRAND_USE_CATEGORIES } from '@/lib/brandUseCategories.js';
import { isRealModelSelection, resolveSelectedModelId } from './modelSelection.js';
```

Immediately below the real-model grid, render:

```jsx
{isRealModelSelection(a.selectedModelId) && (
  <div className="fm-use-category">
    <div className="sec-title">사용 브랜드 유형</div>
    <div className="sec-sub">이 모델을 사용할 브랜드 유형을 하나 선택해 주세요.</div>
    <Chips
      options={BRAND_USE_CATEGORIES}
      value={a.brandUseCategory}
      onChange={(value) => onChange({ brandUseCategory: value })}
    />
  </div>
)}
```

Do not select a default category; this value authorizes a use and must be an explicit seller choice.

- [ ] **Step 5: Block analysis confirmation when a real model has no category**

At the start of `confirmAnalysis`, before `setConfirming(true)`, add:

```javascript
if (isRealModelSelection(a.selectedModelId) && !a.brandUseCategory) {
  toast.push('실제 모델을 사용할 브랜드 유형을 선택해 주세요.', {
    icon: 'alertCircle',
  });
  return;
}
```

Virtual models remain unchanged.

- [ ] **Step 6: Copy the persisted category into editor new-cut requests**

In `Editor.jsx:generateImage`, make the server request body authoritative over the panel request:

```javascript
const { data: img, credits } = await api.generateImage(projectId, {
  mode: 'new',
  ...req,
  colorId: group,
  brandUseCategory: analysis?.brandUseCategory,
});
```

The category comes after `...req` so a caller cannot replace the persisted project value.

- [ ] **Step 7: Run focused frontend tests**

Run:

```bash
node --test \
  tests/frontend/facemarket-brand-use-category.test.mjs \
  tests/frontend/analysis-model-selection.test.mjs \
  tests/frontend/editor-ai-panel.test.mjs
```

Expected: PASS.

- [ ] **Step 8: Commit the explicit project category**

```bash
git add \
  src/features/analysis/AnalysisForm.jsx \
  src/lib/api/shapes.js \
  src/lib/types.js \
  src/features/editor/Editor.jsx \
  tests/frontend/facemarket-brand-use-category.test.mjs
git commit \
  -m "Require sellers to name the authorized real-model use" \
  -m "Constraint: Product clothing fields cannot stand in for a brand-use category." \
  -m "Rejected: Automatic category inference | it could authorize a use the seller never selected." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: Keep the real-model category explicit and project-scoped." \
  -m "Tested: Analysis model-selection and editor payload frontend tests." \
  -m "Not-tested: Browser visual regression."
```

---

### Task 3: Make `resolve_model_license` and `verify_license` the Mandatory Shared Gate

**Files:**

- Modify: `server/app/facemarket.py:1438-1560`
- Modify: `server/tests/test_facemarket_seller_loop.py:20-170`
- Modify: `server/tests/test_facemarket_seller_loop.py:381-390`

**Interfaces:**

- Consumes from biometric enrollment: `fm_models.current_enrollment_id`, `fm_licenses.enrollment_id`, `fm_model_assets.source_enrollment_id`, `fm_model_assets.evidence_version`, `fm_biometric_enrollments.status='passed'`.
- Consumes from mandatory VC cutover: `server/app/holder_client.py`, required Holder settings, and the existing mandatory `verify_license(app, license_row) -> None` local status/expiry plus signed Holder verification body. Extend that function in place; do not replace or duplicate its Holder arm.
- Produces:

```python
async def resolve_model_license(
    conn,
    model_id: str | None,
    *,
    license_id: str | None = None,
) -> dict | None

async def verify_license(
    app,
    license_row: dict | None,
    *,
    model_id: str | None,
    brand_use_category: str | None,
) -> None
```

- `resolve_model_license` returns `None` without DB access only for empty/nonUUID virtual IDs.
- UUID lookup returns a joined row even when the model has no active license; missing model remains `None` and `verify_license` maps it to 409.
- The row contains `id`, `model_id`, `status`, `license_valid_until`, `unit_price`, `vc_id`, `allowed_use`, `forbidden_use`, `model_status`, `assets_status`, `current_enrollment_id`, `license_enrollment_id`, `enrollment_status`, `has_face_front`, `has_grid_sedcard`, `assets_current_evidence`.

- [ ] **Step 1: Extend the mandatory verifier matrix with category and current-evidence cases**

Start from the valid signed-Holder fixtures created by mandatory VC cutover Task 4. Do not recreate Holder transport tests in this plan. Extend its reusable valid row:

```python
def _valid_gate_row(**overrides):
    row = {
        "id": "lic-1",
        "model_id": "11111111-1111-1111-1111-111111111111",
        "status": "active",
        "license_valid_until": FUTURE,
        "vc_id": "vc-1",
        "allowed_use": ["일반 여성 의류"],
        "forbidden_use": ["정치·종교"],
        "model_status": "verified",
        "assets_status": "ready",
        "current_enrollment_id": "enr-1",
        "license_enrollment_id": "enr-1",
        "enrollment_status": "passed",
        "has_face_front": True,
        "has_grid_sedcard": True,
        "assets_current_evidence": True,
    }
    row.update(overrides)
    return row
```

Add these tests:

```python
def _verify(app, row, category="일반 여성 의류"):
    return asyncio.run(facemarket.verify_license(
        app,
        row,
        model_id="11111111-1111-1111-1111-111111111111",
        brand_use_category=category,
    ))


def test_verify_forbidden_wins_even_when_also_allowed():
    row = _valid_gate_row(
        allowed_use=["정치·종교"],
        forbidden_use=["정치·종교"],
    )
    with pytest.raises(facemarket.HTTPException) as error:
        _verify(_app("http://holder"), row, "정치·종교")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "license_use_forbidden"


@pytest.mark.parametrize("category", [None, "", "의류"])
def test_verify_missing_or_unknown_category_is_rejected(category):
    with pytest.raises(facemarket.HTTPException) as error:
        _verify(_app("http://holder"), _valid_gate_row(), category)
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "brand_use_category_required"


def test_verify_empty_allowed_use_is_rejected():
    with pytest.raises(facemarket.HTTPException) as error:
        _verify(_app("http://holder"), _valid_gate_row(allowed_use=[]))
    assert error.value.detail["code"] == "license_use_not_allowed"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("model_status", "reverification_required", "model_unavailable"),
        ("status", "reverification_required", "license_inactive"),
        ("assets_status", "building", "model_assets_unavailable"),
        ("enrollment_status", "processing", "model_enrollment_unavailable"),
        ("license_enrollment_id", "enr-old", "model_enrollment_unavailable"),
        ("has_face_front", False, "model_assets_unavailable"),
        ("has_grid_sedcard", False, "model_assets_unavailable"),
        ("assets_current_evidence", False, "model_assets_unavailable"),
    ],
)
def test_verify_rejects_every_non_current_runtime_state(field, value, code):
    with pytest.raises(facemarket.HTTPException) as error:
        _verify(_app("http://holder"), _valid_gate_row(**{field: value}))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == code
```

Keep the mandatory plan's VC-missing, Holder-unreachable/non-200/malformed/invalid/revoked, inactive, and expired tests. Update their calls only to pass `model_id` and `brand_use_category`; their expected 409/503 mapping must remain unchanged.

- [ ] **Step 2: Run verifier tests and confirm permissive arms fail**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_seller_loop.py -k verify
```

Expected: FAIL because the current signature lacks category/model arguments and current enrollment/evidence checks.

- [ ] **Step 3: Write resolver tests for joined current state and pinned license**

Extend the test cursor to record SQL and parameters, then add:

```python
def test_resolve_virtual_model_still_skips_db():
    conn = object()
    assert asyncio.run(facemarket.resolve_model_license(conn, "mA")) is None


def test_resolve_real_model_uses_joined_runtime_state():
    model_id = "11111111-1111-1111-1111-111111111111"
    conn = _Conn({"by_model": {model_id: _valid_gate_row()}, "by_id": {}})
    row = asyncio.run(facemarket.resolve_model_license(conn, model_id))
    assert row["current_enrollment_id"] == "enr-1"
    assert row["license_enrollment_id"] == "enr-1"
    assert row["has_face_front"] is True
    assert row["has_grid_sedcard"] is True
    assert row["assets_current_evidence"] is True


def test_resolve_worker_snapshot_pins_the_license_id():
    model_id = "11111111-1111-1111-1111-111111111111"
    locked = _valid_gate_row(id="lic-snapshot")
    conn = _Conn({"by_model": {}, "by_id": {"lic-snapshot": locked}})
    row = asyncio.run(facemarket.resolve_model_license(
        conn, model_id, license_id="lic-snapshot"
    ))
    assert row["id"] == "lic-snapshot"
    assert row["model_id"] == model_id
```

Update `_Cur.execute` so `license_id` queries return `by_id`, model queries return `by_model`, and assert both query shapes mention `fm_biometric_enrollments`, `current_enrollment_id`, `source_enrollment_id`, `evidence_version`, and both required views.

- [ ] **Step 4: Run resolver tests and confirm the signature/query is incomplete**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_seller_loop.py -k resolve
```

Expected: FAIL because `license_id` is unsupported and current enrollment/asset fields are not selected.

- [ ] **Step 5: Expand `_LICENSE_VERIFY_COLS` to one joined runtime row**

In `server/app/facemarket.py`, replace the single-table column fragment with aliases valid for `fm_licenses l join fm_models m`:

```python
_LICENSE_VERIFY_COLS = (
    "l.id::text as id, l.model_id::text as model_id, l.status, "
    "l.license_valid_until, l.unit_price, l.vc_id, l.allowed_use, l.forbidden_use, "
    "m.status as model_status, m.assets_status, "
    "m.current_enrollment_id::text as current_enrollment_id, "
    "l.enrollment_id::text as license_enrollment_id, "
    "e.status as enrollment_status, "
    "exists (select 1 from fm_model_assets a "
    "        where a.model_id = m.id and a.view = 'face_front' "
    "          and a.source_enrollment_id = m.current_enrollment_id) as has_face_front, "
    "exists (select 1 from fm_model_assets a "
    "        where a.model_id = m.id and a.view = 'grid_sedcard' "
    "          and a.source_enrollment_id = m.current_enrollment_id) as has_grid_sedcard, "
    "exists (select 1 from fm_model_assets face "
    "        join fm_model_assets grid on grid.model_id = face.model_id "
    "         and grid.view = 'grid_sedcard' "
    "         and grid.source_enrollment_id = face.source_enrollment_id "
    "         and grid.evidence_version = face.evidence_version "
    "        where face.model_id = m.id and face.view = 'face_front' "
    "          and face.source_enrollment_id = m.current_enrollment_id "
    "          and nullif(face.evidence_version, '') is not null) "
    "as assets_current_evidence"
)
```

- [ ] **Step 6: Extend `resolve_model_license` without adding another policy layer**

Replace the function with two explicit query branches:

```python
async def resolve_model_license(
    conn,
    model_id,
    *,
    license_id: str | None = None,
) -> dict | None:
    if model_id:
        try:
            uuid.UUID(str(model_id))
        except (ValueError, TypeError):
            return None
    elif not license_id:
        return None

    if license_id:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""select {_LICENSE_VERIFY_COLS}
                    from fm_licenses l
                    join fm_models m on m.id = l.model_id
                    left join fm_biometric_enrollments e
                      on e.id = m.current_enrollment_id
                    where l.id = %s
                      and (%s is null or m.id = %s)""",
                (str(license_id), str(model_id) if model_id else None,
                 str(model_id) if model_id else None),
            )
            return await cur.fetchone()

    async with conn.cursor() as cur:
        await cur.execute(
            f"""select {_LICENSE_VERIFY_COLS}
                from fm_models m
                left join lateral (
                    select * from fm_licenses candidate
                    where candidate.model_id = m.id
                    order by (candidate.status = 'active') desc,
                             candidate.created_at desc
                    limit 1
                ) l on true
                left join fm_biometric_enrollments e
                  on e.id = m.current_enrollment_id
                where m.id = %s""",
            (str(model_id),),
        )
        return await cur.fetchone()
```

Alias the lateral subquery as `l` exactly as above so `_LICENSE_VERIFY_COLS` remains shared. A model with no license still returns its model/enrollment fields and `id=None`; `verify_license` rejects it.

Update `resolve_project_license`:

```python
async def resolve_project_license(conn, project: dict, analysis: dict) -> dict | None:
    locked = (project or {}).get("facemarket_license_id")
    selected = (analysis or {}).get("selectedModelId")
    if locked:
        return await resolve_model_license(conn, selected, license_id=str(locked))
    return await resolve_model_license(conn, selected)
```

- [ ] **Step 7: Extend the existing mandatory `verify_license` before its signed Holder arm**

Change the signature, keep the mandatory plan's existing active/revoked/expiry checks, then insert the exact model/category/current-evidence checks below before its existing `required = bool(...)` line:

```python
async def verify_license(
    app,
    license_row: dict | None,
    *,
    model_id: str | None,
    brand_use_category: str | None,
) -> None:
    try:
        uuid.UUID(str(model_id))
    except (ValueError, TypeError):
        return

    if license_row is None or not license_row.get("model_id"):
        raise _err("model_unavailable", "선택한 실제 모델을 사용할 수 없습니다.", 409)
    if str(license_row["model_id"]) != str(model_id):
        raise _err("model_unavailable", "선택한 실제 모델을 사용할 수 없습니다.", 409)
    if license_row.get("model_status") != "verified":
        raise _err("model_unavailable", "선택한 실제 모델을 사용할 수 없습니다.", 409)

    status = license_row.get("status")
    if status == "revoked":
        raise _err("license_revoked", "이 모델의 얼굴 라이선스가 해지되었습니다.", 409)
    if status != "active":
        raise _err("license_inactive", "활성화된 얼굴 라이선스가 아닙니다.", 409)
    if _is_expired(license_row):
        raise _err("license_expired", "얼굴 라이선스 사용 기간이 만료되었습니다.", 409)

    category = str(brand_use_category or "").strip()
    if category not in BRAND_USE_CATEGORIES:
        raise _err(
            "brand_use_category_required",
            "실제 모델을 사용할 브랜드 유형을 선택해 주세요.",
            409,
        )
    forbidden = set(license_row.get("forbidden_use") or [])
    allowed = set(license_row.get("allowed_use") or [])
    if category in forbidden:
        raise _err("license_use_forbidden", "이 브랜드 유형에는 모델을 사용할 수 없습니다.", 409)
    if not allowed or category not in allowed:
        raise _err("license_use_not_allowed", "라이선스의 허용 조건과 맞지 않습니다.", 409)

    if license_row.get("assets_status") != "ready":
        raise _err("model_assets_unavailable", "검증된 모델 자산을 사용할 수 없습니다.", 409)
    enrollment_id = license_row.get("current_enrollment_id")
    if not enrollment_id:
        raise _err("model_enrollment_unavailable", "모델 재인증이 필요합니다.", 409)
    if str(license_row.get("license_enrollment_id")) != str(enrollment_id):
        raise _err("model_enrollment_unavailable", "모델 재인증이 필요합니다.", 409)
    if license_row.get("enrollment_status") != "passed":
        raise _err("model_enrollment_unavailable", "모델 재인증이 필요합니다.", 409)
    if not license_row.get("has_face_front") or not license_row.get("has_grid_sedcard"):
        raise _err("model_assets_unavailable", "검증된 모델 자산을 사용할 수 없습니다.", 409)
    if license_row.get("assets_current_evidence") is not True:
        raise _err("model_assets_unavailable", "검증된 모델 자산을 사용할 수 없습니다.", 409)
```

Immediately after this insertion, retain mandatory VC cutover Task 4's existing `required`, URL/secret/VC checks and signed `holder_client.post(..., path="/holder/vc/verify")` block byte-for-byte. Do not add direct `client.post`, another timeout constant, another response mapper, or new Holder logging. The signed shared arm remains the sole owner of missing/invalid/revoked 409 and transport/non-200/malformed 503 behavior.

- [ ] **Step 8: Run the common-gate test file**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_seller_loop.py
```

Expected: PASS.

- [ ] **Step 9: Commit the mandatory shared gate**

```bash
git add server/app/facemarket.py server/tests/test_facemarket_seller_loop.py
git commit \
  -m "Refuse real-model generation without current authorization" \
  -m "Constraint: Holder VC, enrollment, assets, and use category must all be current." \
  -m "Rejected: A second Holder verifier | the signed mandatory VC gate is already authoritative." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: Route and worker callers must use this same verifier." \
  -m "Tested: Resolver/category/current-evidence matrix plus the mandatory VC regression matrix." \
  -m "Not-tested: Live Holder latency and network partition."
```

---

### Task 4: Gate Both Request Routes Before Job Creation and Credit Reservation

**Files:**

- Modify: `server/app/routes.py:2671-2755`
- Modify: `server/app/routes.py:2762-2820`
- Modify: `server/tests/test_routes.py`
- Modify: `server/tests/test_detail_page.py:1-145`

**Interfaces:**

- Consumes: Task 3 `resolve_model_license` and `verify_license`; persisted `analysis.brandUseCategory`.
- Produces: server-owned job payload snapshot:

```json
{
  "brandUseCategory": "일반 여성 의류",
  "_facemarket": {
    "modelId": "11111111-1111-1111-1111-111111111111",
    "licenseId": "license-uuid"
  }
}
```

- `_facemarket` is overwritten by the server and never trusted from request JSON.
- Detail snapshots the current `analysis.selectedModelId`; editor snapshots `body.modelId`.

- [ ] **Step 1: Write failing editor route ordering and snapshot tests**

Add to `server/tests/test_routes.py`:

```python
def test_editor_real_model_gate_runs_before_job_and_credit(
    client, make_token, monkeypatch,
):
    calls = {"job": 0, "credit": 0}
    model_id = "11111111-1111-1111-1111-111111111111"

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_resolve(conn, selected_model_id, *, license_id=None):
        assert selected_model_id == model_id
        return {"id": "lic-1", "model_id": model_id}

    async def fake_verify(app, row, *, model_id, brand_use_category):
        assert brand_use_category is None
        raise routes.facemarket._err(
            "brand_use_category_required", "브랜드 유형을 선택해 주세요.", 409
        )

    async def fake_create_job(conn, **kwargs):
        calls["job"] += 1

    async def fake_reserve(conn, user_id, amount):
        calls["credit"] += 1

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={"mode": "new", "cutType": "styling", "modelId": model_id},
    )
    assert response.status_code == 409
    assert calls == {"job": 0, "credit": 0}


def test_editor_job_payload_overwrites_client_facemarket_snapshot(
    client, make_token, monkeypatch,
):
    seen = {}
    model_id = "11111111-1111-1111-1111-111111111111"

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_resolve(conn, selected_model_id, *, license_id=None):
        return {"id": "lic-server", "model_id": model_id}

    async def fake_verify(app, row, *, model_id, brand_use_category):
        assert brand_use_category == "일반 여성 의류"

    async def fake_create_job(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-editor"}, True

    async def fake_reserve(conn, user_id, amount):
        return 10

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/editor:generate-image",
        headers=_auth(make_token),
        json={
            "mode": "new",
            "cutType": "styling",
            "modelId": model_id,
            "brandUseCategory": "일반 여성 의류",
            "_facemarket": {"modelId": "attacker", "licenseId": "attacker"},
        },
    )
    assert response.status_code == 202
    assert seen["payload"]["_facemarket"] == {
        "modelId": model_id,
        "licenseId": "lic-server",
    }
```

- [ ] **Step 2: Write failing detail route ordering and snapshot tests**

Add to `server/tests/test_detail_page.py`:

```python
def test_detail_real_model_gate_runs_before_cached_result_job_and_credit(
    client, make_token, monkeypatch,
):
    calls = {"blocks": 0, "job": 0, "credit": 0}
    model_id = "11111111-1111-1111-1111-111111111111"

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_analysis(conn, project_id):
        return {"selectedModelId": model_id}

    async def fake_resolve(conn, project, analysis):
        return {"id": "lic-1", "model_id": model_id}

    async def fake_verify(app, row, *, model_id, brand_use_category):
        assert brand_use_category is None
        raise routes.facemarket._err(
            "brand_use_category_required", "브랜드 유형을 선택해 주세요.", 409
        )

    async def fake_blocks(conn, project_id):
        calls["blocks"] += 1
        return [{"id": "old-result"}]

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_blocks)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )
    assert response.status_code == 409
    assert calls == {"blocks": 0, "job": 0, "credit": 0}


def test_detail_job_snapshots_model_license_and_category(
    client, make_token, monkeypatch,
):
    seen = {}
    model_id = "11111111-1111-1111-1111-111111111111"

    async def fake_get_project(conn, user_id, project_id):
        return {"id": project_id}

    async def fake_get_analysis(conn, project_id):
        return {
            "selectedModelId": model_id,
            "brandUseCategory": "일반 여성 의류",
        }

    async def fake_resolve(conn, project, analysis):
        return {"id": "lic-1", "model_id": model_id}

    async def fake_verify(app, row, *, model_id, brand_use_category):
        assert brand_use_category == "일반 여성 의류"

    async def fake_set_project_license(conn, project_id, license_id):
        return None

    async def fake_blocks(conn, project_id):
        return []

    async def fake_storyboard(conn, project_id):
        return [{"id": "b1", "source": "ai"}]

    async def fake_product(conn, project_id):
        return {"clothing_type": "top"}

    async def fake_create_job(conn, **kwargs):
        seen.update(kwargs)
        return {"id": "job-detail"}, True

    async def fake_reserve(conn, user_id, amount):
        return 10

    monkeypatch.setattr(routes.repo, "get_project", fake_get_project)
    monkeypatch.setattr(routes.repo, "get_analysis", fake_get_analysis)
    monkeypatch.setattr(routes.facemarket, "resolve_project_license", fake_resolve)
    monkeypatch.setattr(routes.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(routes.facemarket, "set_project_license", fake_set_project_license)
    monkeypatch.setattr(routes.repo, "get_editor_blocks", fake_blocks)
    monkeypatch.setattr(routes.repo, "get_storyboard", fake_storyboard)
    monkeypatch.setattr(routes.repo, "get_product", fake_product)
    monkeypatch.setattr(routes.repo, "create_job", fake_create_job)
    monkeypatch.setattr(routes.repo, "reserve_credits", fake_reserve)
    patch_route_db(monkeypatch, routes)

    response = client.post(
        "/v1/projects/p1/detail-page:generate",
        headers=auth_headers(make_token),
    )
    assert response.status_code == 202
    assert seen["payload"] == {
        "mode": "generate",
        "brandUseCategory": "일반 여성 의류",
        "_facemarket": {"modelId": model_id, "licenseId": "lic-1"},
    }
```

- [ ] **Step 3: Run route tests and confirm snapshot/order failures**

Run:

```bash
cd server
uv run pytest -q tests/test_routes.py tests/test_detail_page.py \
  -k 'facemarket_snapshot or real_model_gate or brand_use'
```

Expected: FAIL because the new verifier signature is unused and job payloads have no server snapshot.

- [ ] **Step 4: Gate and snapshot editor requests**

In `generate_editor_image`, copy and sanitize the payload before `repo.create_job`:

```python
        job_payload = dict(body or {})
        job_payload.pop("_facemarket", None)
        if s.facemarket_enabled and job_payload.get("mode") == "new":
            model_id = job_payload.get("modelId") or job_payload.get("model_id")
            license_row = await facemarket.resolve_model_license(conn, model_id)
            await facemarket.verify_license(
                request.app,
                license_row,
                model_id=model_id,
                brand_use_category=job_payload.get("brandUseCategory"),
            )
            if license_row is not None:
                job_payload["_facemarket"] = {
                    "modelId": str(license_row["model_id"]),
                    "licenseId": str(license_row["id"]),
                }
        job, created = await repo.create_job(
            conn,
            user_id=user_id,
            project_id=project_id,
            kind="editor_image",
            payload=job_payload,
            idempotency_key=scoped_key,
            credits_reserved=cost,
            metadata={"creditCostVersion": s.credit_cost_version},
        )
```

Keep `repo.reserve_credits` after this block.

- [ ] **Step 5: Gate and snapshot detail requests**

In `generate_detail_page`, retain gate-before-cache ordering and pass category/model into the verifier:

```python
        analysis = await repo.get_analysis(conn, project_id) or {}
        fm_snapshot = None
        if s.facemarket_enabled:
            license_row = await facemarket.resolve_project_license(conn, project, analysis)
            selected_model_id = analysis.get("selectedModelId")
            await facemarket.verify_license(
                request.app,
                license_row,
                model_id=selected_model_id,
                brand_use_category=analysis.get("brandUseCategory"),
            )
            if license_row is not None:
                await facemarket.set_project_license(conn, project_id, license_row["id"])
                await conn.commit()
                fm_snapshot = {
                    "modelId": str(license_row["model_id"]),
                    "licenseId": str(license_row["id"]),
                }
```

Build the job payload after storyboard cost calculation:

```python
        job_payload = {"mode": "generate"}
        if fm_snapshot is not None:
            job_payload.update({
                "brandUseCategory": analysis["brandUseCategory"],
                "_facemarket": fm_snapshot,
            })
        job, created = await repo.create_job(
            conn,
            user_id=user_id,
            project_id=project_id,
            kind="detail_page",
            payload=job_payload,
            idempotency_key=scoped_key,
            credits_reserved=cost,
            metadata={
                "creditCostVersion": s.credit_cost_version,
                "perCutCost": s.credit_cost_storyboard_per_cut,
                "aiCount": ai_count,
            },
        )
```

Because `verify_license` returns immediately for virtual IDs, existing virtual-model detail requests still reach the current cache/job path.

- [ ] **Step 6: Run complete route tests**

Run:

```bash
cd server
uv run pytest -q tests/test_routes.py tests/test_detail_page.py tests/test_facemarket_seller_loop.py
```

Expected: PASS.

- [ ] **Step 7: Commit the pre-credit route gate**

```bash
git add server/app/routes.py server/tests/test_routes.py server/tests/test_detail_page.py
git commit \
  -m "Stop unauthorized real-model work before scarce resources are reserved" \
  -m "Constraint: Authorization must precede cached-result access, job creation, and credit reservation." \
  -m "Rejected: Worker-only checks | they would reserve credits and enqueue known-invalid work." \
  -m "Confidence: high" \
  -m "Scope-risk: moderate" \
  -m "Directive: Treat the server-written _facemarket payload as the only worker snapshot." \
  -m "Tested: Detail and editor route order plus snapshot ownership." \
  -m "Not-tested: Concurrent Holder revoke during the API request."
```

---

### Task 5: Recheck the Snapshot in Both Workers and Refund Without Fallback

**Files:**

- Modify: `server/app/workers/detail_page_job.py:815-930`
- Modify: `server/app/workers/detail_page_job.py:1020-1190`
- Modify: `server/app/workers/detail_page_job.py:1288-1320`
- Modify: `server/app/workers/editor_image_job.py:45-90`
- Modify: `server/app/workers/editor_image_job.py:280-360`
- Modify: `server/tests/test_detail_page.py`
- Modify: `server/tests/test_detail_page_identity_source.py`
- Modify: `server/tests/test_cut_input_authority.py:650-760`

**Interfaces:**

- Consumes: Task 4 `job.payload._facemarket` and `job.payload.brandUseCategory`; Task 3 resolver/verifier; mandatory VC cutover Task 5's existing detail/editor worker-time `verify_license` call and failure finalizers.
- Extension boundary: replace each worker's existing unpinned license lookup plus verifier call with one snapshot-pinned lookup plus the same verifier call. Do not add a second verifier invocation or a second denial/finalization path.
- Produces: current-state worker decision before generation; full reserved-credit release on denial; zero generated asset/result/settlement on denial.

- [ ] **Step 1: Write a failing detail-worker revoke-race test**

Add to `server/tests/test_detail_page.py`:

```python
def test_detail_worker_rechecks_snapshot_and_refunds_revoked_license(monkeypatch):
    captured = {"generated": 0, "success": 0, "settlement": 0}
    model_id = "11111111-1111-1111-1111-111111111111"

    async def fake_resolve(conn, selected_model_id, *, license_id=None):
        assert selected_model_id == model_id
        assert license_id == "lic-1"
        return {"id": "lic-1", "model_id": model_id, "status": "revoked"}

    async def fake_verify(app, row, *, model_id, brand_use_category):
        raise dpj.facemarket._err("license_revoked", "해지된 라이선스입니다.", 409)

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"ok": True}

    async def fake_success(conn, **kwargs):
        captured["success"] += 1

    async def fake_generate(*args, **kwargs):
        captured["generated"] += 1

    async def fake_settlement(*args, **kwargs):
        captured["settlement"] += 1

    monkeypatch.setattr(dpj.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(dpj.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_failure", fake_failure)
    monkeypatch.setattr(dpj.repo, "finalize_detail_page_success", fake_success)
    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(dpj.facemarket, "record_license_settlement", fake_settlement)

    job = worker_job({
        "mode": "generate",
        "brandUseCategory": "일반 여성 의류",
        "_facemarket": {"modelId": model_id, "licenseId": "lic-1"},
    }, credits_reserved=3)
    asyncio.run(dpj.run_detail_page_job(_app(_settings(facemarket_enabled=True)), job))

    assert captured["failure"]["reserved"] == 3
    assert captured["failure"]["metadata"]["error"] == "license_revoked"
    assert captured["generated"] == 0
    assert captured["success"] == 0
    assert captured["settlement"] == 0
```

Use the file's existing `_app`, `_settings`, and `worker_job` helpers; give the fake pool a connection whose cursor is not used because `resolve_model_license` is patched.

- [ ] **Step 2: Invert the existing detail fallback expectation**

In `server/tests/test_detail_page_identity_source.py`, replace `test_rejected_falls_back_to_virtual_model_pack_for_consistency` with:

```python
def test_rejected_real_model_never_falls_back_to_virtual(monkeypatch):
    captured = {"generations": 0}
    face_r2 = _FaceR2()
    _patch(monkeypatch, captured)
    app = _app(_asset_rows(), _license_meta(model_id="other-model"), face_r2)

    async def fake_generate(*args, **kwargs):
        captured["generations"] += 1

    monkeypatch.setattr(dpj.cut_generator, "generate", fake_generate)
    asyncio.run(dpj.run_detail_page_job(app, worker_job(credits_reserved=1)))

    assert captured["generations"] == 0
    assert face_r2.gets == []
```

Update the shared fixture so the job includes a valid `_facemarket` snapshot and patch the common gate to pass until the explicit license/model mismatch is exercised.

- [ ] **Step 3: Write a failing editor-worker Holder-race test**

Add to `server/tests/test_cut_input_authority.py`:

```python
def test_editor_worker_rechecks_snapshot_and_refunds_holder_outage(monkeypatch):
    captured = {"generated": 0, "success": 0, "settlement": 0}
    model_id = "11111111-1111-1111-1111-111111111111"

    async def fake_resolve(conn, selected_model_id, *, license_id=None):
        assert selected_model_id == model_id
        assert license_id == "lic-1"
        return {"id": "lic-1", "model_id": model_id}

    async def fake_verify(app, row, *, model_id, brand_use_category):
        raise eij.facemarket._err("holder_unavailable", "Holder unavailable", 503)

    async def fake_failure(conn, **kwargs):
        captured["failure"] = kwargs
        return {"ok": True}

    async def fake_success(conn, **kwargs):
        captured["success"] += 1

    async def fake_generate(*args, **kwargs):
        captured["generated"] += 1

    async def fake_settlement(*args, **kwargs):
        captured["settlement"] += 1

    monkeypatch.setattr(eij.facemarket, "resolve_model_license", fake_resolve)
    monkeypatch.setattr(eij.facemarket, "verify_license", fake_verify)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_failure", fake_failure)
    monkeypatch.setattr(eij.repo, "finalize_editor_image_success", fake_success)
    monkeypatch.setattr(eij.cut_generator, "generate", fake_generate)
    monkeypatch.setattr(eij.facemarket, "record_license_settlement", fake_settlement)

    job = worker_job({
        "mode": "new",
        "cutType": "styling",
        "modelId": model_id,
        "brandUseCategory": "일반 여성 의류",
        "_facemarket": {"modelId": model_id, "licenseId": "lic-1"},
    }, credits_reserved=2)
    asyncio.run(eij.run_editor_image_job(
        fake_worker_app(
            make_settings(facemarket_enabled=True, gemini_api_key="x", r2_bucket="b"),
            r2=_TrackingR2(),
        ),
        job,
    ))

    assert captured["failure"]["reserved"] == 2
    assert captured["failure"]["metadata"]["error"] == "holder_unavailable"
    assert captured["generated"] == 0
    assert captured["success"] == 0
    assert captured["settlement"] == 0
```

- [ ] **Step 4: Run worker tests and confirm both paths currently bypass the shared verifier**

Run:

```bash
cd server
uv run pytest -q \
  tests/test_detail_page.py \
  tests/test_detail_page_identity_source.py \
  tests/test_cut_input_authority.py \
  -k 'snapshot or holder_outage or never_falls_back'
```

Expected: FAIL because the mandatory VC recheck is not pinned to the server snapshot/category and the detail rejected-source branch can still enter virtual fallback.

- [ ] **Step 5: Reuse the mandatory worker denial/finalization path**

Locate the `try/except facemarket.HTTPException` or outer failure path added by mandatory VC cutover Task 5. Keep that single path and make its metadata preserve the shared error code:

```python
    detail = error.detail if isinstance(error.detail, dict) else {}
    code = detail.get("code") or "facemarket_denied"
    await _fail(
        detail.get("message") or "모델 사용 조건을 확인할 수 없어요.",
        {"error": code},
        code=code,
    )
```

If editor `_fail` has no `code` keyword, use its existing two-argument form with the same metadata; do not change either repository finalizer interface:

```python
await _fail(
    detail.get("message") or "모델 사용 조건을 확인할 수 없어요.",
    {"error": code},
)
```

- [ ] **Step 6: Pin the existing detail worker recheck to the server snapshot**

Import the existing module at the top:

```python
from .. import facemarket, repo
```

At the existing mandatory VC recheck before `_load_license_face`, real asset reads, and provider generation, replace its resolver input with:

```python
        payload = job.get("payload") or {}
        fm_snapshot = payload.get("_facemarket")
        fm_license_row = None
        if fm_snapshot is not None:
            snapshot_model_id = fm_snapshot.get("modelId")
            async with pool.connection() as conn:
                fm_license_row = await facemarket.resolve_model_license(
                    conn,
                    snapshot_model_id,
                    license_id=fm_snapshot.get("licenseId"),
                )
            try:
                await facemarket.verify_license(
                    app,
                    fm_license_row,
                    model_id=snapshot_model_id,
                    brand_use_category=payload.get("brandUseCategory"),
                )
            except facemarket.HTTPException as error:
                detail = error.detail if isinstance(error.detail, dict) else {}
                code = detail.get("code") or "facemarket_denied"
                await _fail(
                    detail.get("message") or "모델 사용 조건을 확인할 수 없어요.",
                    {"error": code},
                    code=code,
                )
                return
```

After loading analysis, use the snapshot model as the runtime authority:

```python
            selected_model_id = (
                fm_snapshot.get("modelId")
                if fm_snapshot is not None
                else analysis.get("selectedModelId") or analysis.get("selected_model_id")
            )
```

If the selected ID is a UUID and `fm_snapshot` is absent, call `verify_license(app, None, model_id=selected_model_id, brand_use_category=None)` and let the outer exception become a failed/refunded legacy real-model job. NonUUID virtual jobs remain unchanged.

- [ ] **Step 7: Remove detail real-model fallback paths**

Use the already verified `fm_license_row` instead of `_load_license_row` for the `REAL` source. If `identity_source.select_source(...)` returns `REJECTED`, raise:

```python
raise RuntimeError("facemarket_real_model_rejected")
```

Remove `REAL` and `REJECTED` from the virtual fallback registry branch:

```python
if fallback_model_id and source == "VIRTUAL":
```

In `_real_model_images`, replace the current exception-to-empty-list downgrade:

```python
except Exception as exc:
    raise RuntimeError("facemarket_real_assets_unavailable") from exc
```

The outer worker exception handler calls `_fail`, refunds the reservation, and never reaches `finalize_detail_page_success` or settlement.

- [ ] **Step 8: Pin the existing editor worker recheck before resolving real assets**

In the `mode == "new"` branch, replace the mandatory VC plan's existing resolver/verifier call with the snapshot-pinned form below before `identity_source.resolve_real_model_assets`:

```python
            selected_model_id = normalized.get("modelId") or normalized.get("model_id")
            fm_snapshot = payload.get("_facemarket")
            if fm_snapshot is not None:
                selected_model_id = fm_snapshot.get("modelId")
                async with pool.connection() as conn:
                    fm_license_row = await facemarket.resolve_model_license(
                        conn,
                        selected_model_id,
                        license_id=fm_snapshot.get("licenseId"),
                    )
                try:
                    await facemarket.verify_license(
                        app,
                        fm_license_row,
                        model_id=selected_model_id,
                        brand_use_category=payload.get("brandUseCategory"),
                    )
                except facemarket.HTTPException as error:
                    detail = error.detail if isinstance(error.detail, dict) else {}
                    await _fail(
                        detail.get("message") or "모델 사용 조건을 확인할 수 없어요.",
                        {"error": detail.get("code") or "facemarket_denied"},
                    )
                    return
```

Then resolve real assets in a new short DB connection and reuse `fm_license_row`; delete the mandatory plan's former unpinned `resolve_model_license` call rather than keeping both. If a UUID selected model has no snapshot, call `verify_license` with `None` so a legacy/spoofed real request fails and refunds.

Keep the existing editor behavior that R2 failure for `fm_source == "REAL"` calls `_fail`; it already prevents success and settlement.

- [ ] **Step 9: Run all worker authorization tests**

Run:

```bash
cd server
uv run pytest -q \
  tests/test_detail_page.py \
  tests/test_detail_page_identity_source.py \
  tests/test_detail_page_license_face.py \
  tests/test_cut_input_authority.py \
  tests/test_editor_image.py
```

Expected: PASS.

- [ ] **Step 10: Commit the race-safe worker recheck**

```bash
git add \
  server/app/workers/detail_page_job.py \
  server/app/workers/editor_image_job.py \
  server/tests/test_detail_page.py \
  server/tests/test_detail_page_identity_source.py \
  server/tests/test_cut_input_authority.py
git commit \
  -m "Refund real-model work when authorization changes in the queue" \
  -m "Constraint: A request-time pass cannot authorize a later worker after revoke or outage." \
  -m "Rejected: Virtual or faceless fallback | it violates the seller's selected-model intent." \
  -m "Confidence: high" \
  -m "Scope-risk: broad" \
  -m "Directive: Keep worker denial before generation, R2 writes, success finalization, and settlement." \
  -m "Tested: Detail/editor revoke and Holder races with full credit release." \
  -m "Not-tested: Live queue cancellation during process termination."
```

---

### Task 6: Make Every Catalog Thumbnail a Non-Biometric Placeholder

**Files:**

- Modify: `server/app/facemarket.py:299-340`
- Modify: `server/app/facemarket.py:738-780`
- Modify: `server/tests/test_facemarket_identity.py:200-225`
- Modify: `server/tests/test_facemarket_licenses.py`

**Interfaces:**

- Consumes: authenticated model ID; local model/license/enrollment eligibility only.
- Produces: `image/svg+xml` static placeholder with `Cache-Control: no-store, private`; catalog `coverImageUrl=null`; no face storage call.

- [ ] **Step 1: Write a failing catalog test that rejects unverified cover URLs**

In `server/tests/test_facemarket_identity.py:test_catalog_lists_verified_without_pii`, add:

```python
assert card["coverImageUrl"] is None
```

Extend its fake catalog row with a non-null `cover_image_url` so the assertion proves SQL does not forward stored legacy covers.

- [ ] **Step 2: Write a failing thumbnail test with an exploding face store**

Add to `server/tests/test_facemarket_licenses.py` using the existing authenticated app fixture:

```python
def test_model_thumbnail_returns_placeholder_without_reading_face_front(
    client, make_token, monkeypatch,
):
    class ExplodingFaceStore:
        def get_bytes(self, key):
            raise AssertionError("thumbnail must not read the face bucket")

    client.app.state.r2_face = ExplodingFaceStore()
    model_id = "11111111-1111-1111-1111-111111111111"

    response = client.get(
        f"/v1/facemarket/models/{model_id}/thumbnail",
        headers=_auth(make_token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "no-store, private"
    assert b"<svg" in response.content
```

Make the fixture cursor return an eligible local row for a query containing `from fm_models m`, and raise if SQL contains `fm_model_assets`, `face_front`, or `r2_key`.

Add a freeze test:

```python
def test_model_thumbnail_is_404_after_reverification_freeze(
    client, make_token, model_store,
):
    model_store["status"] = "reverification_required"
    response = client.get(
        f"/v1/facemarket/models/{model_store['id']}/thumbnail",
        headers=_auth(make_token),
    )
    assert response.status_code == 404
```

- [ ] **Step 3: Run thumbnail tests and confirm `face_front` is read**

Run:

```bash
cd server
uv run pytest -q \
  tests/test_facemarket_identity.py \
  tests/test_facemarket_licenses.py \
  -k 'catalog_lists_verified or model_thumbnail'
```

Expected: FAIL because catalog forwards cover URLs and the route queries `fm_model_assets` then calls `r2_face.get_bytes`.

- [ ] **Step 4: Force placeholder-only fields in the catalog projection**

Change `_MODEL_CARD_COLS_ENRICHED` so it never returns legacy cover bytes and always gives eligible catalog rows the authenticated placeholder route:

```python
_MODEL_CARD_COLS_ENRICHED = (
    "m.id::text as id, m.display_name, m.status, "
    "null::text as cover_image_url, m.created_at, "
    "l.id::text as license_id, l.unit_price, l.vc_id, "
    "(l.id is not null) as has_active_license, "
    "(m.assets_status = 'ready') as assets_ready, "
    "('/v1/facemarket/models/' || m.id::text || '/thumbnail') as face_thumb_uri"
)
```

Keep owner-only `my_models` unchanged; its owner management face is governed by `/licenses/{id}/face`, not the seller catalog thumbnail.

- [ ] **Step 5: Replace the thumbnail body with a static SVG response**

Add a module constant near `_EXT_TO_MIME`:

```python
_MODEL_PLACEHOLDER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400"><rect width="400" height="400" fill="#efeef0"/><circle cx="200" cy="142" r="58" fill="#aaa8b2"/><path d="M92 352c12-82 55-124 108-124s96 42 108 124" fill="#aaa8b2"/></svg>"""
```

Replace `get_model_thumbnail` storage logic with a local eligibility query:

```python
    try:
        uuid.UUID(str(model_id))
    except ValueError:
        raise _err("not_found", "찾을 수 없습니다.", 404)

    async with get_conn(request) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """select 1 as eligible
                   from fm_models m
                   join fm_biometric_enrollments e
                     on e.id = m.current_enrollment_id and e.status = 'passed'
                   where m.id = %s
                     and m.status = 'verified'
                     and m.assets_status = 'ready'
                     and exists (
                         select 1 from fm_licenses l
                         where l.model_id = m.id
                           and l.enrollment_id = m.current_enrollment_id
                           and l.status = 'active'
                           and l.license_valid_until > now()
                           and l.vc_id is not null
                     )""",
                (model_id,),
            )
            eligible = await cur.fetchone()
    if eligible is None:
        raise _err("not_found", "찾을 수 없습니다.", 404)
    return Response(
        content=_MODEL_PLACEHOLDER_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store, private"},
    )
```

Delete `_r2_face(request)` from this route. Do not query `fm_model_assets`; the SVG has no relationship to stored biometric assets.

- [ ] **Step 6: Run FaceMarket catalog and thumbnail tests**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_identity.py tests/test_facemarket_licenses.py
```

Expected: PASS.

- [ ] **Step 7: Commit placeholder-only thumbnails**

```bash
git add \
  server/app/facemarket.py \
  server/tests/test_facemarket_identity.py \
  server/tests/test_facemarket_licenses.py
git commit \
  -m "Keep catalog browsing separate from biometric face assets" \
  -m "Constraint: No verified non-biometric cover asset exists yet." \
  -m "Rejected: Reusing face_front as a thumbnail | authenticated catalog access still exposes a biometric original." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: Only a separately verified non-biometric cover may replace this placeholder." \
  -m "Tested: Catalog cover suppression, zero face-store reads, and freeze 404." \
  -m "Not-tested: Pixel-level browser rendering across themes."
```

---

### Task 7: Run the Authorization Regression Gate

**Files:**

- Verify only: all files changed in Tasks 1-6.

**Interfaces:**

- Consumes: completed Tasks 1-6, applied biometric runtime schema, and mandatory VC cutover Tasks 1-5 including the signed Holder client/verifier and worker recheck.
- Produces: fresh evidence that category, API ordering, worker refund, virtual-model regression, and thumbnail isolation all pass together.

- [ ] **Step 1: Run the focused backend authorization suite**

Run:

```bash
cd server
uv run pytest -q \
  tests/test_facemarket_seller_loop.py \
  tests/test_holder_client.py \
  tests/test_facemarket_mandatory_vc.py \
  tests/test_facemarket_vc_revocation.py \
  tests/test_facemarket_licenses.py \
  tests/test_facemarket_identity.py \
  tests/test_routes.py \
  tests/test_detail_page.py \
  tests/test_detail_page_identity_source.py \
  tests/test_detail_page_license_face.py \
  tests/test_cut_input_authority.py \
  tests/test_editor_image.py
```

Expected: PASS.

- [ ] **Step 2: Run every FaceMarket backend regression**

Run:

```bash
cd server
uv run pytest -q tests/test_facemarket_*.py tests/test_fm_model_asset_job.py
```

Expected: PASS.

- [ ] **Step 3: Run the full backend suite**

Run:

```bash
cd server
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 4: Run frontend tests and production build**

Run:

```bash
npm run test:frontend
npm run build
```

Expected: all Node tests PASS and Vite build exits 0.

- [ ] **Step 5: Check the final diff**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` prints nothing; status lists only the planned implementation and test files.

- [ ] **Step 6: Commit any verification-only fixture corrections**

Skip this commit when Step 1-5 require no changes. If a test fixture required a contract-only correction, commit only that correction:

```bash
git add server/tests tests/frontend
git commit \
  -m "Keep authorization fixtures aligned with the fail-closed contract" \
  -m "Constraint: Legacy free-form categories and best-effort Holder assumptions are no longer valid fixtures." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: Do not restore real-model fallback expectations." \
  -m "Tested: Full backend, frontend, and production build." \
  -m "Not-tested: Stage Holder and mobile browser E2E."
```

---

## Release Stop Conditions

- Stop before deployment if the biometric runtime schema migration is not applied.
- Stop before enabling real-model enqueue if production `OPENDID_HOLDER_URL` and Holder authentication are not configured by the deployment plan.
- Stop if any API failure test observes `create_job` or `reserve_credits` after denial.
- Stop if any worker denial test observes image generation, R2 output, success finalization, or settlement.
- Stop if a UUID real-model request can complete without `_facemarket` snapshot, current enrollment, both private assets, valid VC, and allowed category.
- Stop if catalog or thumbnail code reads `face_front`, `r2_key`, or `r2_face`.
- Stop if virtual model IDs fail existing detail/editor regression tests.
