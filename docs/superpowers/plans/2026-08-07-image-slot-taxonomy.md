# 입력 이미지 슬롯 개편 구현 계획 (Fit 삭제 · BackDetail 신설 · 앞뒤 필수 · 방향 인지 디테일 컷)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 업로드 슬롯을 `Front/Back/Detail/BackDetail`(앞면·뒷면·앞면 디테일·뒷면 디테일)로 바꾸고, 디테일 컷 첨부를 컷 방향에 맞춰 선택하며(반대 방향 금지 + 같은 방향 원본 폴백), 앞면·뒷면을 필수로 만든다.

**Architecture:** 정본 스펙은 `docs/superpowers/specs/2026-08-07-image-slot-taxonomy-design.md`. 토큰 `Detail` 값은 재사용(=앞면 디테일, DB 마이그레이션 0건), `BackDetail` 신설, `Fit` 완전 삭제. 디테일 컷 이미지 선택은 `cut_generator.detail_reference_images` 단일 초크포인트(호출 3곳: cut_generator:1075 · detail_page_job:634 · editor_image_job:178)에 `direction` 파라미터를 추가해 해결한다. 프롬프트의 방향 전달은 기존 `[[DIR:front_product]]/[[DIR:back_product]]`가 이미 담당하므로, 디테일 샷 섹션은 정밀/구조 2모드만 분기한다.

**Tech Stack:** React(JSX)+Vite 프론트 · FastAPI/Python 서버 · pytest · node --test

## Global Constraints

- 저장 계약(blocks: list)과 콘티보드 블록 필드 계약은 건드리지 않는다 (CLAUDE.md).
- React 훅은 로딩 early-return 위에, 훅 개수 불변 (CLAUDE.md — 위반 시 화이트스크린).
- 커밋 메시지: Conventional 접두사 + 비개발자용 쉬운 한국어 요약 (CLAUDE.md).
- 매니페스트 라벨은 **고정 문자열 룩업만** — 셀러 텍스트를 절대 삽입하지 않는다(인젝션 방지, pl1_analysis_agent_spec 원칙).
- 서버 검증: `cd server && .venv/bin/pytest -q` 전부 통과. 프론트 검증: `npx vite build` + `pnpm test:frontend`.
- `_SLOT_ORDER`의 기존 3종(Front=0, Back=1, Detail=2) 순서 불변 — BackDetail=3만 말미 추가 (기존 상품 재현성).
- DB 마이그레이션 없음. 기존 `Detail` 88장은 자동으로 앞면 디테일.

---

### Task 1: 서버 슬롯 상수 — `_SLOT_ORDER` · 라벨 맵 4곳 (BackDetail 추가, Fit 삭제)

**Files:**
- Modify: `server/app/agents/mannequin.py:10-11` (`_SLOT_ORDER`), `:83-84` (docstring)
- Modify: `server/app/agents/cut_generator.py:756-762` (`_SLOT_LABEL`)
- Modify: `server/app/agents/feature_extractor.py:76-82` (`_SLOT_LABEL`)
- Modify: `server/app/workers/mannequin_job.py:71-77` (`_SLOT_LABEL`)
- Modify: `server/app/workers/analyze_job.py:68` · `server/app/workers/mannequin_job.py:1117` (주석 `Front/Back/Detail/Fit` → `Front/Back/Detail/BackDetail`)
- Test: `server/tests/test_cuts.py`, `server/tests/test_feature_extractor.py` (기존 파일에 케이스 추가)

**Interfaces:**
- Produces: `mannequin._SLOT_ORDER == {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3}` — Task 2·3이 정렬 기준으로 소비. 각 `_SLOT_LABEL`에 `"BackDetail"` 키 존재, `"Fit"` 키 부재.

- [ ] **Step 1: 실패 테스트 작성** — `server/tests/test_cuts.py`에 추가:

```python
def test_slot_order_backdetail_last_no_fit():
    from app.agents.mannequin import _SLOT_ORDER
    assert _SLOT_ORDER == {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3}


def test_slot_labels_have_backdetail_and_no_fit():
    from app.agents import cut_generator, feature_extractor
    from app.workers import mannequin_job
    for labels in (cut_generator._SLOT_LABEL, feature_extractor._SLOT_LABEL,
                   mannequin_job._SLOT_LABEL):
        assert "BackDetail" in labels
        assert "Fit" not in labels
        # 뒷면 전용 못박기 — 스펙 §6 (앞면 배치 금지 문구)
    assert "never place it on the front" in cut_generator._SLOT_LABEL["BackDetail"]
```

- [ ] **Step 2: 실패 확인** — Run: `cd server && .venv/bin/pytest tests/test_cuts.py -q -k backdetail`. Expected: FAIL (`KeyError`/assert).

- [ ] **Step 3: 구현** — 각 파일에서 `"Fit"` 항목 삭제, `"BackDetail"` 추가:

`mannequin.py`:
```python
_SLOT_ORDER = {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3}
```
docstring(`:84`): `slot ∈ Front/Back/Detail/BackDetail. Front·Back 필수는 입력 검증에서 거른다(나머지는 선택).`

`cut_generator.py` `_SLOT_LABEL`:
```python
_SLOT_LABEL = {
    "Front": "PRODUCT — front view of the garment",
    "Back": "PRODUCT — back view of the garment",
    "Detail": ("PRODUCT — front-side detail close-up of the garment (texture, stitching, "
               "print; may also show fabric or trims whose location is not side-specific)"),
    "BackDetail": ("PRODUCT — back-side detail close-up of the garment (back neck, back "
                   "yoke, back pocket). This detail exists on the back side only — "
                   "never place it on the front"),
}
```

`feature_extractor.py` `_SLOT_LABEL`:
```python
_SLOT_LABEL = {
    "Front": "front view",
    "Back": "back view",
    "Detail": ("front-side DETAIL close-up — inspect this one hardest "
               "(texture, stitching, trims, prints)"),
    "BackDetail": ("back-side DETAIL close-up — a back-only feature (back neck, yoke, "
                   "back pocket); never attribute it to the front"),
}
```
`:133` docstring의 `slots(Front/Back/Detail/Fit)` → `slots(Front/Back/Detail/BackDetail)`.

`mannequin_job.py` `_SLOT_LABEL`:
```python
_SLOT_LABEL = {
    "Front": "front view of the garment",
    "Back": "back view of the garment",
    "Detail": "front-side detail close-up of the garment (texture, stitching, trims, print)",
    "BackDetail": ("back-side detail close-up of the garment (a back-only feature — "
                   "back neck, yoke, back pocket)"),
}
```

- [ ] **Step 4: 통과 확인** — Run: `cd server && .venv/bin/pytest tests/test_cuts.py tests/test_feature_extractor.py tests/test_mannequin_series_qc_wiring.py -q`. Expected: PASS. `grep -rn '"Fit"' server/app/`로 잔재 0건 확인(단, `fitProfile`·`baseFit` 등 무관 식별자는 제외 — slot 문자열 `"Fit"`만).

- [ ] **Step 5: Commit** — `git add server/ && git commit -m "feat(server): 사진 칸에 '뒷면 디테일' 추가, 안 쓰던 '착용' 칸 정리"`

---

### Task 2: `detail_reference_images` 방향 인지 (같은 방향만 빌리기 + 원본 폴백)

**Files:**
- Modify: `server/app/agents/cut_generator.py:848-` (`detail_reference_images`)
- Test: `server/tests/test_cut_generator.py` (기존 detail_reference_images 테스트군 옆)

**Interfaces:**
- Consumes: Task 1의 `_SLOT_ORDER`(`_color_image_pairs` 정렬).
- Produces: `detail_reference_images(product: dict, color_id, direction: str = "front") -> tuple[list[tuple[str, str]], dict | None]` — Task 3·4가 호출. `direction ∈ {"front","back"}`(그 외 값은 front 취급). 반환 의미 불변: `(첨부 (slot, asset_id) 목록, 색전환 메타 또는 None)`.

- [ ] **Step 1: 실패 테스트 작성** — `server/tests/test_cut_generator.py`에 추가 (스펙 §5 매트릭스):

```python
def _colors_product(colors):
    return {"colors": colors}


def test_detail_refs_back_direction_prefers_same_color_backdetail():
    p = _colors_product([{"id": "base", "isBase": True, "images": [
        {"slot": "Front", "id": "f1"}, {"slot": "Back", "id": "b1"},
        {"slot": "Detail", "id": "d1"}, {"slot": "BackDetail", "id": "bd1"},
    ]}])
    images, transfer = cut.detail_reference_images(p, None, direction="back")
    assert ("BackDetail", "bd1") in images
    assert transfer is None


def test_detail_refs_back_direction_borrows_backdetail_from_other_color_only():
    # 목표색엔 BackDetail 없음 → 타색 BackDetail을 색전환으로 빌린다. 타색 Detail(앞면)은 금지.
    p = _colors_product([
        {"id": "base", "isBase": True, "images": [
            {"slot": "Front", "id": "bf"}, {"slot": "Detail", "id": "bd-front"},
            {"slot": "BackDetail", "id": "bd-back"}]},
        {"id": "red", "images": [{"slot": "Front", "id": "rf"}, {"slot": "Back", "id": "rb"}]},
    ])
    images, transfer = cut.detail_reference_images(p, "red", direction="back")
    assert ("BackDetail", "bd-back") in images
    assert ("Detail", "bd-front") not in images
    assert transfer is not None            # 타색 근거 → 색전환 메타 필수


def test_detail_refs_back_direction_falls_back_to_originals_when_no_backdetail():
    # 어느 색에도 BackDetail 없음 → 목표색 원본만 반환(구조 확대 모드는 렌더 단계 판정).
    # 반대 방향(Detail)은 절대 첨부하지 않는다 — 스펙 §5 금지열.
    p = _colors_product([{"id": "base", "isBase": True, "images": [
        {"slot": "Front", "id": "f1"}, {"slot": "Back", "id": "b1"},
        {"slot": "Detail", "id": "d1"}]}])
    images, transfer = cut.detail_reference_images(p, None, direction="back")
    assert ("Detail", "d1") not in [(s, i) for s, i in images if s in ("Detail", "BackDetail")] or True
    assert all(s != "Detail" or i != "d1" for s, i in images) is False or True
    # 명시적으로: BackDetail 항목이 없고, transfer 없음
    assert not any(s == "BackDetail" for s, _ in images)
    assert transfer is None


def test_detail_refs_front_direction_never_borrows_backdetail():
    p = _colors_product([
        {"id": "base", "isBase": True, "images": [
            {"slot": "Front", "id": "f1"}, {"slot": "BackDetail", "id": "bd1"}]},
    ])
    images, transfer = cut.detail_reference_images(p, None, direction="front")
    # 앞면 방향에 앞면 디테일이 없다 → 빌릴 것도(타색 없음) 없음 → 원본 폴백, transfer 없음
    assert transfer is None


def test_detail_refs_default_direction_is_front_backward_compat():
    # 기존 호출부(양수인자)와 동일 동작 — direction 미지정 = front
    p = _colors_product([{"id": "base", "isBase": True,
                          "images": [{"slot": "Detail", "id": "d1"}]}])
    images, _ = cut.detail_reference_images(p, None)
    assert ("Detail", "d1") in images
```

주의: 위 세 번째 테스트의 뒤엉킨 두 줄(`or True`)은 작성 금지 — 아래 최종형만 쓴다:

```python
def test_detail_refs_back_direction_falls_back_to_originals_when_no_backdetail():
    p = _colors_product([{"id": "base", "isBase": True, "images": [
        {"slot": "Front", "id": "f1"}, {"slot": "Back", "id": "b1"},
        {"slot": "Detail", "id": "d1"}]}])
    images, transfer = cut.detail_reference_images(p, None, direction="back")
    assert not any(s == "BackDetail" for s, _ in images)
    assert transfer is None
    # 목표색 이미지 목록은 그대로 유지된다 (Front·Back·Detail 전 슬롯 — 첨부 순서 계약 불변)
    assert ("Back", "b1") in images
```

- [ ] **Step 2: 실패 확인** — Run: `cd server && .venv/bin/pytest tests/test_cut_generator.py -q -k detail_refs`. Expected: FAIL (`TypeError: unexpected keyword 'direction'`).

- [ ] **Step 3: 구현** — `detail_reference_images` 개정. 현행 로직에서 `"Detail"` 리터럴을 방향별 슬롯 변수로 치환:

```python
def detail_reference_images(
    product: dict, color_id, direction: str = "front",
) -> tuple[list[tuple[str, str]], dict | None]:
    """디테일 컷 첨부 목록 — 컷 방향의 디테일 슬롯만 근거로 쓴다 (2026-08-07 스펙 §5).

    우선순위: 목표색 같은 방향 디테일 → 타색 같은 방향 디테일(색전환 메타 동반) →
    목표색 원본만(구조 확대 모드 — 렌더 단계가 매니페스트로 판정). 반대 방향 디테일은
    어느 단계에서도 첨부하지 않는다(백넥 자수를 앞가슴에 그리는 사고 차단).
    color_id가 None일 때만 기준색 폴백, 실존하지 않는 색상은 invalid_color 실패 — 기존 계약 유지.
    """
    detail_slot = "BackDetail" if direction == "back" else "Detail"
    colors = product.get("colors") or []
    target_color = _color_by_id(colors, color_id)
    if color_id is not None and target_color is None:
        raise ValueError("invalid_color")
    target_images = _color_image_pairs(target_color)
    if any(slot == detail_slot for slot, _asset_id in target_images):
        return target_images, None

    base = _base_color(colors)
    candidates = ([base] if base is not None else []) + [c for c in colors if c is not base]
    reference_color = next(
        (c for c in candidates
         if any(slot == detail_slot for slot, _aid in _color_image_pairs(c))),
        None,
    )
    if reference_color is None:
        return target_images, None

    reference_details = [
        pair for pair in _color_image_pairs(reference_color) if pair[0] == detail_slot
    ]
    # (이하 _color_prompt_meta·transfer dict 조립은 현행 코드 그대로)
```

- [ ] **Step 4: 통과 확인** — Run: `cd server && .venv/bin/pytest tests/test_cut_generator.py tests/test_cuts.py -q`. Expected: PASS (기존 front 테스트 포함 전부).

- [ ] **Step 5: Commit** — `git commit -m "feat(server): 뒷면 디테일 그림은 뒷면 사진만 근거로 쓴다 (앞뒤 뒤바뀜 사고 차단)"`

---

### Task 3: 렌더 게이트 2모드 — 정밀(`SHOT:detail`) / 구조 확대(`SHOT:detail_zoom`)

**Files:**
- Modify: `server/app/agents/cut_generator.py:485-490` (매니페스트 게이트), `${shotLine}` 렌더의 shot_key 결정부, `:1074-1076` (`build_prompt`의 `detail_reference_images` 호출에 direction 전달)
- Modify: `server/prompts/cut_generate_v1.txt:114-116` (`[[SHOT:detail]]` 개정 + `[[SHOT:detail_zoom]]` 신설)
- Test: `server/tests/test_cuts.py` (기존 `:917-922` detail 게이트 테스트군 옆)

**Interfaces:**
- Consumes: Task 1 `_SLOT_LABEL`, Task 2 `detail_reference_images(..., direction=)`.
- Produces: `render_cut_prompt`는 product+detail 컷에서 매니페스트 내용으로 모드를 판정한다 — 방향 디테일 라벨 존재→`SHOT:detail`, 없고 같은 방향 원본 라벨 존재→`SHOT:detail_zoom`, 둘 다 없으면 `ValueError("detail_reference_required")`. Task 4의 워커들이 이 게이트에 의존.

- [ ] **Step 1: 실패 테스트 작성** — `server/tests/test_cuts.py`에 추가:

```python
def _detail_spec(direction="front"):
    return {"cutType": "product", "shot": "detail", "direction": direction}


def test_detail_cut_back_uses_backdetail_label_gate():
    # 매니페스트에 BackDetail 라벨이 있으면 정밀 모드 문구가 실린다
    manifest = f"1. {cut._SLOT_LABEL['Back']}\n2. {cut._SLOT_LABEL['BackDetail']}"
    p = cut.render_cut_prompt(cut.load_cut_template(), cut.normalize_spec(_detail_spec("back")),
                              {"colors": []}, {}, "top", manifest, False)
    assert "detail close-up reference" in p          # SHOT:detail 정밀 모드
    assert "structural element" not in p             # 구조 모드 아님


def test_detail_cut_back_falls_to_zoom_mode_with_back_original_only():
    manifest = f"1. {cut._SLOT_LABEL['Front']}\n2. {cut._SLOT_LABEL['Back']}"
    p = cut.render_cut_prompt(cut.load_cut_template(), cut.normalize_spec(_detail_spec("back")),
                              {"colors": []}, {}, "top", manifest, False)
    assert "structural element" in p                 # SHOT:detail_zoom
    assert "do NOT invent" in p


def test_detail_cut_back_fails_without_back_side_evidence():
    # 앞면 디테일만 있어도 뒷면 컷 근거가 아니다 — 스펙 §5 금지열
    manifest = f"1. {cut._SLOT_LABEL['Front']}\n2. {cut._SLOT_LABEL['Detail']}"
    with pytest.raises(ValueError, match="detail_reference_required"):
        cut.render_cut_prompt(cut.load_cut_template(), cut.normalize_spec(_detail_spec("back")),
                              {"colors": []}, {}, "top", manifest, False)


def test_detail_cut_front_zoom_mode_with_front_original_only():
    manifest = f"1. {cut._SLOT_LABEL['Front']}"
    p = cut.render_cut_prompt(cut.load_cut_template(), cut.normalize_spec(_detail_spec("front")),
                              {"colors": []}, {}, "top", manifest, False)
    assert "structural element" in p
```

주: `render_cut_prompt` 실제 시그니처(키워드 인자 `authority_plan_line` 등)는 기존 테스트 `test_cuts.py:917` 호출 형태를 그대로 따라 맞춘다.

- [ ] **Step 2: 실패 확인** — Run: `cd server && .venv/bin/pytest tests/test_cuts.py -q -k "detail_cut"`. Expected: FAIL.

- [ ] **Step 3: 구현**

(a) `cut_generate_v1.txt` — `[[SHOT:detail]]` 교체 + `[[SHOT:detail_zoom]]` 신설 (DETAIL_COLOR_TRANSFER 섹션 위):

```text
[[SHOT:detail]]a tight product-only close-up of ONE clearly visible detail from the attached
PRODUCT detail close-up reference (fabric surface, stitching, pocket, or real closure).
Reproduce only what that reference visibly proves; never invent lining, hardware, seams,
texture, or hidden construction
[[SHOT:detail_zoom]]a tight product-only close-up of ONE structural element clearly visible in
the attached full-garment PRODUCT reference for the requested side (collar, placket buttons,
pocket, hem, or a visible seam line). Zoom into that element only. At this reference resolution
fine fabric weave, embroidery threads and small prints cannot be verified — do NOT invent or
sharpen them; pick an element whose shape the reference clearly proves
```

(b) `cut_generator.py` 게이트(현행 `:485-490`) 교체 — `render_cut_prompt` 내부, `shot_key` 결정 전에:

```python
    detail_mode_zoom = False
    if cut == "product" and shot == "detail":
        detail_label = _SLOT_LABEL["BackDetail" if spec["direction"] == "back" else "Detail"]
        original_label = _SLOT_LABEL["Back" if spec["direction"] == "back" else "Front"]
        if detail_label in image_manifest:
            pass                                   # 정밀 모드 (SHOT:detail)
        elif original_label in image_manifest:
            detail_mode_zoom = True                # 구조 확대 모드 (SHOT:detail_zoom)
        else:
            # 방향 근거가 통째로 없다 = 이미지 로드 실패 급 — 컷만 실패시킨다 (스펙 §5)
            raise ValueError("detail_reference_required")
```

`${shotLine}` 렌더부에서 `need(f"SHOT:{shot_key}")`의 shot_key를 `"detail_zoom" if detail_mode_zoom else shot_key`로 분기.

(c) `build_prompt` `:1075` — `detail_reference_images(product, spec["colorId"], direction=spec["direction"])`.

- [ ] **Step 4: 통과 확인** — Run: `cd server && .venv/bin/pytest tests/test_cuts.py tests/test_cut_generator.py -q`. Expected: PASS. 기존 `:917` 테스트(라벨 없음→실패)는 매니페스트에 Front 라벨도 없는 픽스처면 그대로 통과 — 깨지면 의미(§5 게이트 축소)에 맞게 픽스처만 갱신.

- [ ] **Step 5: Commit** — `git commit -m "feat(server): 디테일 사진이 없어도 앞뒤 원본에서 보이는 부분을 확대해 디테일 그림을 만든다"`

---

### Task 4: 워커 배선 — editor_image_job · detail_page_job 방향 전달

**Files:**
- Modify: `server/app/workers/editor_image_job.py:148` (direction 추출), `:170-175` (사전 게이트), `:178-179` (direction 전달)
- Modify: `server/app/workers/detail_page_job.py:476-484` (`_detail_passthrough` 방향 매칭), `:488-489` (asset_key에 방향 포함), `:634` (direction 전달)
- Test: `server/tests/test_editor_image.py`, `server/tests/test_detail_page.py`

**Interfaces:**
- Consumes: Task 2 `detail_reference_images(..., direction=)`, Task 3 게이트.
- Produces: 없음 (말단 배선).

- [ ] **Step 1: 실패 테스트 작성** — 기존 테스트 패턴을 따른다 (`test_editor_image.py:748` 게이트 테스트, `test_detail_page.py:1319` 원본 통과 테스트가 본보기):

```python
# test_editor_image.py — 뒷면 디테일 블록은 BackDetail을 첨부한다
# (기존 detail 첨부 테스트를 복제해 payload에 "direction": "back"을 넣고,
#  픽스처 색상에 {"slot": "BackDetail", "id": "bd1"}을 추가한 뒤
#  첨부 assets에 bd1이 포함되고 d1(Detail)이 근거로 쓰이지 않음을 단언)

# test_detail_page.py — 미세 패턴 원본 통과의 방향 매칭
# (기존 :1319 패턴 복제: fine_pattern 상황에서 direction="back" 디테일 블록은
#  {"slot": "BackDetail"} 자산만 통과시키고 {"slot": "Detail"} 자산은 통과 대상이 아님)
```

구체 픽스처·mock 형태는 각 파일의 인접 테스트(위 줄번호)를 그대로 복제해 slot·direction만 바꾼다 — 이 두 파일의 테스트는 리포지토리 mock 구성이 길어 기존 헬퍼 재사용이 정확성·가독성 모두 낫다.

- [ ] **Step 2: 실패 확인** — Run: `cd server && .venv/bin/pytest tests/test_editor_image.py tests/test_detail_page.py -q -k back`. Expected: FAIL.

- [ ] **Step 3: 구현**

(a) `editor_image_job.py` — `:148` 근처 `is_detail` 옆에:
```python
            detail_direction = "back" if new_payload.get("direction") == "back" else "front"
```
`:170` 사전 게이트를 방향 인지로 (원본 폴백이 생겼으므로 "방향 근거 전무"일 때만 실패):
```python
            _detail_slot = "BackDetail" if detail_direction == "back" else "Detail"
            _original_slot = "Back" if detail_direction == "back" else "Front"
            if is_detail and not any(
                asset.get("slot") in (_detail_slot, _original_slot) for asset in assets
            ):
                await _fail(
                    "디테일 참고 사진을 찾을 수 없어 디테일샷을 만들 수 없어요.",
                    {"error": "detail_reference_required", "colorId": requested_color_id},
                )
                return
```
`:178` 호출: `cut_generator.detail_reference_images(product, requested_color_id, direction=detail_direction)`.

(b) `detail_page_job.py` — 블록 방향 헬퍼 추가(모듈 내 `_is_detail` 옆):
```python
def _detail_direction(block: dict) -> str:
    return "back" if block.get("direction") == "back" else "front"
```
`_detail_passthrough`(`:476`): `asset.get("slot") == "Detail"` → 방향 매칭:
```python
                _slot = "BackDetail" if _detail_direction(block) == "back" else "Detail"
                for asset in color_assets.get(asset_key, []):
                    if asset.get("slot") == _slot:
                        return asset
```
캐시 키(`:488`): `asset_key = (ckey, _is_detail(b))` → `asset_key = (ckey, _is_detail(b), _detail_direction(b) if _is_detail(b) else None)` — `detail_color_transfers`·`color_assets` 딕셔너리 키로 함께 쓰이므로 한 곳에서 바꾸면 전파된다. `:634` 호출: `cut_generator.detail_reference_images(product, ckey, direction=_detail_direction(b))` (블록 `b`가 스코프에 있는 형태로 — 실제 루프 변수명 확인 후 맞춤).

- [ ] **Step 4: 통과 확인** — Run: `cd server && .venv/bin/pytest -q`. Expected: 전체 PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(server): 상세페이지·재생성 경로도 디테일 그림의 앞뒤 근거를 맞춰 쓴다"`

---

### Task 5: 프론트 계약 — AngleSlot · 카탈로그 · 시드

**Files:**
- Modify: `src/lib/types.js:45`, `:77` (주석)
- Modify: `src/mock/db.js:96-97`, `:394-402` (시드)
- Test: 없음(상수 정의) — Task 7 빌드로 검증

**Interfaces:**
- Produces: `AngleSlot = { FRONT: 'Front', BACK: 'Back', DETAIL_FRONT: 'Detail', DETAIL_BACK: 'BackDetail' }` — Task 6이 소비. `catalogs.angleSlots = ['Front','Back','Detail','BackDetail']`, `angleLabels = { Front:'앞면', Back:'뒷면', Detail:'앞면 디테일', BackDetail:'뒷면 디테일' }`.

- [ ] **Step 1: 구현**

`types.js:45`:
```js
export const AngleSlot = Object.freeze({ FRONT: 'Front', BACK: 'Back', DETAIL_FRONT: 'Detail', DETAIL_BACK: 'BackDetail' });
```

`mock/db.js:96-97`:
```js
  angleSlots: ['Front', 'Back', 'Detail', 'BackDetail'],
  angleLabels: { Front: '앞면', Back: '뒷면', Detail: '앞면 디테일', BackDetail: '뒷면 디테일' },
```
`:397` 시드: `{ id: uid('img'), slot: 'Fit', ... }` → `{ id: uid('img'), slot: 'BackDetail', label: 'BackDetail', src: P.photo('c1bd', 'styling', 300, 400) }`.
`:402` 추가 색상 시드는 현행 유지(Front 고정).

- [ ] **Step 2: 검증** — Run: `npx vite build && grep -rn "'Fit'" src/ --include='*.js' --include='*.jsx' | grep -v objectFit | grep -v "Fit\b.*slim"`. Expected: 빌드 성공, slot 'Fit' 잔재 0건 (`Fit`·`AdjustFit` 등 무관 상수는 제외).

- [ ] **Step 3: Commit** — `git commit -m "feat(web): 사진 칸을 앞면·뒷면·앞면 디테일·뒷면 디테일 넷으로 정리"`

---

### Task 6: 입력 화면 — 앞뒤 필수 게이트 (기준 색상 기준) + 문구

**Files:**
- Modify: `src/features/product-input/ProductInput.jsx:198` (필수 별표), `:371-372` (초안 복원 판정), `:403-405` (게이트), `:477` `:534` (안내문)
- Test: `tests/frontend/product-input-pending-tiles.test.mjs` 인접에 신규 파일은 만들지 않는다 — 게이트가 컴포넌트 인라인이므로 검증은 빌드+스모크 (아래 Step 3)

**Interfaces:**
- Consumes: Task 5의 `catalogs.angleSlots`(2×2 우물은 기존 `slot-wells` 렌더가 배열 순서대로 그리므로 UI 구조 변경 없음).

- [ ] **Step 1: 구현**

`:403-405` 게이트 — 기준 색상 기준(스펙 §4, 현행 "아무 색"에서 의도적 강화):
```js
  const baseColor = product.colors.find((c) => c.isBase) || product.colors[0];
  const hasFront = !!baseColor?.images.some((im) => im.slot === 'Front');
  const hasBack = !!baseColor?.images.some((im) => im.slot === 'Back');
  const canDone = hasFront && hasBack && phase === 'input' && !authLoading;
```

`:198` 필수 별표: `{s === 'Front' && <span className="req-star">*</span>}` → `{(s === 'Front' || s === 'Back') && <span className="req-star">*</span>}`

`:477` 안내문: `각도별로 한 장 이상 올리면 더 정확한 상세페이지가 만들어져요. 앞면은 필수예요.` → `각도별로 한 장 이상 올리면 더 정확한 상세페이지가 만들어져요. 앞면·뒷면은 필수예요 — 뒷면이 없으면 뒷모습 컷을 만들 수 없어요.`

`:534` 힌트: `{!hasFront && ...앞면 이미지를 1장 이상...}` → 
```jsx
{!(hasFront && hasBack) && <p className="hint" style={{ textAlign: 'right', marginTop: 8 }}>앞면·뒷면 이미지를 각 1장 이상 올리면 입력을 완료할 수 있어요.</p>}
```
(다음 줄 `hasFront && authLoading`도 `hasFront && hasBack && authLoading`으로.)

`:371` 초안 복원: `restoredHasFront` → 
```js
        const restoredHasRequired = (draft.photos || []).some((p) => p.slot === 'Front')
          && (draft.photos || []).some((p) => p.slot === 'Back');
```
`:372`의 사용처도 `restoredHasRequired`로.

- [ ] **Step 2: 검증** — Run: `npx vite build`. Expected: 성공. 훅 개수·순서 불변 확인(추가한 것은 파생 상수뿐, 훅 아님).

- [ ] **Step 3: 스모크** — mock 모드 헤드리스 크롬으로 입력 페이지 진입: 4칸 라벨(앞면/뒷면/앞면 디테일/뒷면 디테일)과 앞면·뒷면 별표 표시, 앞면만 올린 상태에서 CTA 비활성 문구 확인. 스크린샷 1장.

- [ ] **Step 4: Commit** — `git commit -m "feat(web): 앞면·뒷면 사진을 필수로 — 뒷모습 결과가 안정된다"`

---

### Task 7: 콘티보드 — 디테일 컷 상시 제공 + 기본 구성 방향 인지

**Files:**
- Modify: `src/lib/storyboardTaxonomy.js:104-110` (requiresDetailImage 삭제), `:156-165` (필터 단순화), `:250-253` (강등 삭제), `:347-349` (`hasDetailSource` 정리)
- Modify: `src/lib/api/shapes.js:122-123`, `:174-183`, `:203-205` (기본 구성)
- Modify: `src/features/storyboard/Storyboard.jsx:1098`, `src/features/editor/Editor.jsx:443` (호출부 정리)
- Test: `tests/frontend/storyboard-entry-placement.test.mjs` (defaultStoryboard 검증 케이스 추가)

**Interfaces:**
- Consumes: Task 5 `AngleSlot`.
- Produces: `contentTemplatesForSection(sectionRole)` — `hasDetailImage` 옵션 제거(디테일 역할 항상 포함). `defaultStoryboard`는 디테일 사진 유무와 무관하게 디테일 블록 포함.

- [ ] **Step 1: 실패 테스트 작성** — `tests/frontend/storyboard-entry-placement.test.mjs`에 추가:

```js
test('기본 콘티는 디테일 사진이 없어도 디테일 컷을 포함한다', () => {
  const colors = [{ id: 'col1', isBase: true, images: [
    { slot: 'Front', id: 'f1' }, { slot: 'Back', id: 'b1' },
  ] }];
  const basic = defaultStoryboard(colors, 'basic', { clothingType: 'top' });
  assert.ok(basic.some((b) => b.cutType === 'product' && b.shot === 'detail'));
  const extended = defaultStoryboard(colors, 'extended', { clothingType: 'top' });
  assert.ok(extended.some((b) => b.cutType === 'product' && b.shot === 'detail'));
});

test('디테일 블록의 색상은 그 방향 디테일 보유 색을 우선한다', () => {
  const colors = [
    { id: 'col1', isBase: true, images: [{ slot: 'Front', id: 'f1' }, { slot: 'Back', id: 'b1' }] },
    { id: 'col2', images: [{ slot: 'Detail', id: 'd2' }] },
  ];
  const blocks = defaultStoryboard(colors, 'basic', { clothingType: 'top' });
  const detail = blocks.find((b) => b.shot === 'detail');
  assert.equal(detail.colorId, 'col2');
});
```

- [ ] **Step 2: 실패 확인** — Run: `pnpm test:frontend`. Expected: 신규 2건 FAIL.

- [ ] **Step 3: 구현**

(a) `storyboardTaxonomy.js` — DETAIL 템플릿(`:104-110`)에서 `requiresDetailImage: true,` 줄과 설명문 갱신(`'업로드한 디테일 사진이 있으면 그대로, 없으면 앞뒤 사진에서 보이는 부분을 확대해요.'`). `contentTemplatesForSection`·`allAiContentTemplates`에서 `hasDetailImage` 파라미터와 `requiresDetailImage` 필터 삭제(시그니처의 옵션 객체는 `allAiContentTemplates({ includeHero })`만 유지). `normalizedRecipePatch`의 `hasDetailImage === false → PRODUCT_OVERVIEW` 강등(`:250-253`)과 옵션 삭제. `hasDetailSource`(`:347`)는 앞면 디테일 존재 판정으로 유지하되 소비처가 사라지면 export 삭제.

(b) `shapes.js` — `:122-123`:
```js
  const detailColor = list.find((color) => (color.images || []).some((image) => image.slot === 'Detail'))?.id || base;
```
는 유지(기본 구성 디테일 블록은 front 방향 — 뒷면 디테일 기본 포함은 스펙 §10 비범위). `:174-183`의 `if (hasDetail) {...} else {...}` 분기를 디테일 블록 상시 포함으로 단순화:
```js
    blocks.push(
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor),
      sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor),
    );
```
`:203-205`(basic 모드)도 동일하게 삼항 제거:
```js
    blocks.push(sb(SECTION_ROLES.PRODUCT, CONTENT_ROLES.DETAIL, 'product', 'front', 'detail', detailColor));
```
`hasDetail` 변수·`hasDetailSource` import가 미사용이 되면 제거.

(c) 호출부 — `Storyboard.jsx:1098`의 `hasDetailImage` 산출과 `contentTemplatesForSection(...,{hasDetailImage})` 전달 인자 제거(주변 사용처 grep로 전파 확인), `Editor.jsx:443`의 `setHasDetailImage(hasDetailSource(p))`와 해당 state가 템플릿 필터에만 쓰였다면 함께 제거 — **주의: state 제거 시 훅 개수가 변하므로, `useState(hasDetailImage)`를 지우는 대신 다른 훅 구조 변화가 없는지 확인하고, 불안하면 state는 두고 소비만 끊는다(훅 개수 불변 원칙 우선).**

- [ ] **Step 4: 통과 확인** — Run: `pnpm test:frontend && npx vite build`. Expected: 전부 PASS + 빌드 성공. 기존 `isDefaultStoryboardForMode` 관련 테스트가 블록 수 변화로 깨지면 새 기본 구성에 맞게 기대값 갱신(의미 변화는 스펙 §5 승인 사항).

- [ ] **Step 5: Commit** — `git commit -m "feat(web): 디테일 사진이 없어도 콘티보드에 디테일 컷이 항상 나온다"`

---

### Task 8: 문서 동기화 + 전체 검증

**Files:**
- Modify: `documents/common_data_contract.md:83`, `:375`
- Modify: `documents/PRD.md:145` 부근
- Modify: `documents/pl1_analysis_agent_spec.md:114`, `:116`, `:127`
- Test: 전체 스위트

- [ ] **Step 1: 문서 갱신**

`common_data_contract.md:375`:
```markdown
| AngleSlot | `Front` `Back` `Detail` `BackDetail` | 앞면/뒷면/앞면 디테일/뒷면 디테일 | `Detail`=앞면 디테일(값 재사용, 2026-08-07 개편) · `Fit` 폐기(사용 실적 0) |
```
`:83` 주석의 `추가 색상은 'Front' 고정`은 유지.

`PRD.md:145` 부근: 업로드 각도 `Front / Back / Detail / BackDetail`, 화면 표기 `앞면 / 뒷면 / 앞면 디테일 / 뒷면 디테일`, 요건에 "앞면·뒷면 각 1장 필수" 반영.

`pl1_analysis_agent_spec.md:114` `slot 순서(Front→Back→Detail→Fit)` → `(Front→Back→Detail→BackDetail)`. `:116` 화이트리스트 서술 `AngleSlot 4종` 구성 갱신(Front/Back/Detail/BackDetail). `:127` 라벨 룩업 표를 Task 1의 feature_extractor 최종 문구로 교체.

- [ ] **Step 2: 전체 검증**

```bash
cd server && .venv/bin/pytest -q          # 전부 통과
cd .. && npx vite build && pnpm test:frontend
grep -rn "slot.*'Fit'\|\"Fit\"" src/ server/app/ server/prompts/ | grep -v ab_out   # 잔재 0건
```

- [ ] **Step 3: Commit** — `git commit -m "docs: 사진 칸 개편(앞면·뒷면·앞뒤 디테일)을 계약 문서에 반영"`

---

## Self-Review 결과 (작성 시 수행)

- 스펙 §3(토큰·정렬)→Task 1·5, §4(입력 화면)→Task 6, §5(우선순위 매트릭스·게이트·콘티 완화)→Task 2·3·4·7, §6(라벨)→Task 1·3, §7(변경 지점)→전 Task, §8(무마이그레이션)→Task 없음(설계상 0건), §9 안내문→Task 6. `repo.py:118`은 Front 우선 정렬로 변경 불요(스펙 §7 검증만) — Task 8 grep에 포함.
- 타입 일관성: `detail_reference_images(product, color_id, direction="front")` 시그니처가 Task 2(정의)·3(build_prompt)·4(워커 2곳)에서 동일.
- 실행 순서: Task 1→2→3→4는 의존 순서, Task 5→6→7은 프론트 순서. 서버(1–4)와 프론트(5–7)는 병렬 가능, Task 8은 최후.
