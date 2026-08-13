# 마네킹 소매 기장 축 + 겨드랑이 핫존 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 상의에 `sleeve`(민소매·반팔) 조정 축을 신설하고, 마네킹컷 핫존을 서로 겹치지 않는 자리로 재배치한다.

**Architecture:** 핏 축 카탈로그는 `src/lib/fitAxes.js`(프론트)와 `server/app/agents/fit_axes.py`(서버)에 **수동 미러**된 단일 정본이다. 축을 추가하면 UI 스텝·프로필 정규화·프롬프트가 카탈로그에서 파생되지만, QC 계열 4개 테이블(observable·편집템플릿·비교속성·서열)은 **수동 등록**이라 빠뜨리면 그 축만 조용히 판정에서 빠진다. 핫존은 `fitHotspots.js`(축→점 id)와 `Mannequin.css`(점 id→프레임 % 좌표)로 나뉜다.

**Tech Stack:** React 18 + Vite (pnpm), Node 내장 test runner(`node --test`), Python 3.12 + pytest (`server/.venv`)

## Global Constraints

- 설계 정본: `docs/superpowers/specs/2026-08-13-mannequin-sleeve-hotspot-design.md`. 이 계획과 어긋나면 스펙이 우선이다.
- 브랜치 `feat/mannequin-sleeve-hotspots`. main 직접 커밋 금지.
- 프론트·서버 카탈로그는 **값·라벨·promptEn 문자열까지 완전히 동일**해야 한다. 미러를 검증하는 자동 테스트는 없다.
- 셀러 문자열은 프롬프트에 절대 보간하지 않는다. 카탈로그 고정 문구만 쓴다.
- 축 미선택(`axes.sleeve` 부재) = "사진 그대로". 기본값을 넣지 않는다.
- 핫존 좌표는 `.fit-mine-img` 프레임 기준 %다(이미지 좌표가 아니다). 좁은 쪽 프레임은 `comparing` 상태의 **300×400px**.
- 프론트 테스트: `pnpm test:frontend` 또는 `node --test tests/frontend/<file>.test.mjs`
- 서버 테스트: `cd server && .venv/bin/python -m pytest tests/<file>.py -q`

---

### Task 1: `top.sleeve` 축 신설 + QC 배선

축을 카탈로그에 추가하고, 축을 소비하는 서버 테이블 5곳을 같은 커밋에서 채운다. `server/tests/test_mannequin_fit_qc.py:135`의 커버리지 테스트가 카탈로그를 순회하며 편집 템플릿을 요구하므로, 카탈로그만 추가하면 그 테스트가 즉시 깨진다 — 두 작업은 분리할 수 없다.

이 시점에는 핫존이 없어 **화면에는 아무 변화가 없다**(축 스텝은 생기지만 여는 진입점이 없어 inert). 의도된 중간 상태다.

**Files:**
- Modify: `src/lib/fitAxes.js:35` (`top.length` 블록 뒤)
- Modify: `server/app/agents/fit_axes.py:35` (`top.length` 블록 뒤), `:143` 부근 `AXIS_OBSERVABLES`
- Modify: `server/app/agents/mannequin_fit_qc.py:34` `_EDIT_TEMPLATES`
- Modify: `server/app/agents/mannequin_pairwise_qc.py:17` `_COMPARATIVE`, `:26` `_ORDINAL`
- Modify: `server/app/agents/fit_axis_matrix.py:21` `AXIS_PAIRS`
- Test: `tests/frontend/fit-vocabulary.test.mjs`, `server/tests/test_fit_axis_matrix.py`, `server/tests/test_mannequin_fit_profile.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces: `axesFor('top', gender).sleeve` → `[{value:'sleeveless',label:'민소매',promptEn}, {value:'short',label:'반팔',promptEn}]`. Task 3의 `fitHotspotsFor('top','sleeve')`가 이 축 키를 참조한다.

- [ ] **Step 1: 프론트 어휘 잠금 테스트를 먼저 쓴다**

`tests/frontend/fit-vocabulary.test.mjs` 맨 끝에 추가:

```js
test('상의 소매 기장 축은 민소매·반팔 두 값이고 남녀가 같다', () => {
  const values = (gender) => axesFor('top', gender).sleeve.map(({ value }) => value);
  assert.deepEqual(values('women'), ['sleeveless', 'short']);
  assert.deepEqual(values('men'), ['sleeveless', 'short']);
});

test('소매 기장 축은 fit·length 뒤에 온다 — 카탈로그 순서가 UI 스텝 순서다', () => {
  assert.deepEqual(Object.keys(axesFor('top', 'women')), ['fit', 'length', 'sleeve']);
  assert.deepEqual(Object.keys(axesFor('top', 'men')), ['fit', 'length', 'sleeve']);
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/frontend/fit-vocabulary.test.mjs`
Expected: FAIL — `Cannot read properties of undefined (reading 'map')` (`.sleeve`가 없음)

- [ ] **Step 3: 프론트 카탈로그에 축 추가**

`src/lib/fitAxes.js`에서 `top.length` 블록 닫는 `},` 바로 뒤, `top`이 닫히기 전에 삽입:

```js
    sleeve: {
      women: [
        { value: 'sleeveless', label: '민소매', promptEn: 'a sleeveless version of the same top; if the photographed garment has sleeves, visibly re-tailor only its sleeves by removing them completely and finishing clean armholes at the shoulder points, leaving the neckline, body width and hem length unchanged; if it is already sleeveless, preserve those proportions' },
        { value: 'short', label: '반팔', promptEn: 'a short-sleeve version of the same top; if the photographed garment has long or three-quarter sleeves, visibly re-tailor only its sleeves by shortening them to end around the mid-upper-arm, leaving the neckline, body width and hem length unchanged; if it already satisfies this target, preserve those proportions' },
      ],
      men: [
        { value: 'sleeveless', label: '민소매', promptEn: 'a sleeveless version of the same top; if the photographed garment has sleeves, visibly re-tailor only its sleeves by removing them completely and finishing clean armholes at the shoulder points, leaving the neckline, body width and hem length unchanged; if it is already sleeveless, preserve those proportions' },
        { value: 'short', label: '반팔', promptEn: 'a short-sleeve version of the same top; if the photographed garment has long or three-quarter sleeves, visibly re-tailor only its sleeves by shortening them to end around the mid-upper-arm, leaving the neckline, body width and hem length unchanged; if it already satisfies this target, preserve those proportions' },
      ],
    },
```

- [ ] **Step 4: 통과 확인**

Run: `node --test tests/frontend/fit-vocabulary.test.mjs`
Expected: PASS

- [ ] **Step 5: 서버 어휘 잠금 테스트를 쓴다**

`server/tests/test_mannequin_fit_profile.py` 맨 끝에 추가:

```python
# ---------- 소매 기장 축 ----------

def test_sleeve_axis_mirrors_the_frontend_vocabulary():
    for gender in ("women", "men"):
        entries = FIT_AXES["top"]["sleeve"][gender]
        assert [e["value"] for e in entries] == ["sleeveless", "short"]
        assert [e["label"] for e in entries] == ["민소매", "반팔"]
        assert all(e["promptEn"] for e in entries)
    assert list(FIT_AXES["top"]) == ["fit", "length", "sleeve"]


def test_normalize_keeps_valid_sleeve_and_drops_unknown_values():
    kept = normalize_fit_profile(
        {"category": "top", "gender": "women", "axes": {"fit": "regular", "sleeve": "short"}}
    )
    assert kept["axes"]["sleeve"] == "short"

    dropped = normalize_fit_profile(
        {"category": "top", "gender": "women", "axes": {"fit": "regular", "sleeve": "long"}}
    )
    assert "sleeve" not in dropped["axes"]

    # 소매 축은 상의 전용 — 아우터 프로필에 실려 와도 통과시키지 않는다
    outer = normalize_fit_profile(
        {"category": "outer", "gender": "women", "axes": {"fit": "regular", "sleeve": "short"}}
    )
    assert "sleeve" not in outer["axes"]
```

- [ ] **Step 6: 실패 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_mannequin_fit_profile.py -q -k sleeve`
Expected: FAIL — `KeyError: 'sleeve'`

- [ ] **Step 7: 서버 카탈로그에 축 미러**

`server/app/agents/fit_axes.py`에서 `"top"`의 `"length"` 블록 뒤에 삽입:

```python
        "sleeve": {
            "women": [
                {"value": "sleeveless", "label": "민소매", "promptEn": "a sleeveless version of the same top; if the photographed garment has sleeves, visibly re-tailor only its sleeves by removing them completely and finishing clean armholes at the shoulder points, leaving the neckline, body width and hem length unchanged; if it is already sleeveless, preserve those proportions"},
                {"value": "short", "label": "반팔", "promptEn": "a short-sleeve version of the same top; if the photographed garment has long or three-quarter sleeves, visibly re-tailor only its sleeves by shortening them to end around the mid-upper-arm, leaving the neckline, body width and hem length unchanged; if it already satisfies this target, preserve those proportions"},
            ],
            "men": [
                {"value": "sleeveless", "label": "민소매", "promptEn": "a sleeveless version of the same top; if the photographed garment has sleeves, visibly re-tailor only its sleeves by removing them completely and finishing clean armholes at the shoulder points, leaving the neckline, body width and hem length unchanged; if it is already sleeveless, preserve those proportions"},
                {"value": "short", "label": "반팔", "promptEn": "a short-sleeve version of the same top; if the photographed garment has long or three-quarter sleeves, visibly re-tailor only its sleeves by shortening them to end around the mid-upper-arm, leaving the neckline, body width and hem length unchanged; if it already satisfies this target, preserve those proportions"},
            ],
        },
```

- [ ] **Step 8: 통과 확인 + 무엇이 깨졌는지 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_mannequin_fit_profile.py tests/test_mannequin_fit_qc.py tests/test_fit_axis_matrix.py -q`
Expected: sleeve 테스트는 PASS. 다음 두 개가 FAIL — 이게 안전망이 작동한 증거다:
- `test_edit_instruction_templates_cover_every_axis_family` → `템플릿 누락: top.sleeve`
- `test_all_pairs_women_covers_ten` → `assert 10 == 11`이 아니라, 아직 `AXIS_PAIRS` 미등록이라 개수는 10 그대로 PASS일 수 있다. 실제 실패는 템플릿 쪽 1건이다.

- [ ] **Step 9: observable 2개 등록**

`server/app/agents/fit_axes.py`의 `AXIS_OBSERVABLES`에서 `("top", "length", "long")` 줄 뒤에 삽입:

```python
    ("top", "sleeve", "sleeveless"): "both shoulders bare with clean finished armholes and no sleeve fabric on the upper arms",
    ("top", "sleeve", "short"): "both sleeve hems end on the upper arm above the elbow, with the forearms fully bare",
```

- [ ] **Step 10: 편집 템플릿 등록**

`server/app/agents/mannequin_fit_qc.py`의 `_EDIT_TEMPLATES`에서 `("top", "length")` 항목 뒤에 삽입:

```python
    ("top", "sleeve"): ("Re-tailor only the top's sleeves in this photo until {observable}; "
                        "keep the neckline, body width and hem length unchanged."),
```

- [ ] **Step 11: 비교속성·서열 등록**

`server/app/agents/mannequin_pairwise_qc.py`의 `_COMPARATIVE`에 추가:

```python
    "sleeve": "the sleeves cover MORE of the arm (i.e. they are longer)",
```

같은 파일 `_ORDINAL`에서 `("top", "fit")` 줄 뒤에 추가:

```python
    ("top", "sleeve"): {"sleeveless": 0, "short": 1},
```

- [ ] **Step 12: 반영 측정 매트릭스에 축 등록**

`server/app/agents/fit_axis_matrix.py`의 `AXIS_PAIRS`에서 `("top", "fit"), ("top", "length"),` 줄을 다음으로 교체:

```python
    ("top", "fit"), ("top", "length"), ("top", "sleeve"),
```

같은 파일 20번 줄 주석의 `10쌍`을 `11쌍`으로 고친다.

- [ ] **Step 13: 개수 단언 갱신 + 극단쌍 테스트 추가**

`server/tests/test_fit_axis_matrix.py:60-64`를 다음으로 교체:

```python
def test_all_pairs_women_covers_eleven():
    pairs = FM.all_pairs("women")
    assert len(pairs) == 11                                  # 11개 (카테고리,축)
    keys = {(p["category"], p["axis"]) for p in pairs}
    assert keys == set(FM.AXIS_PAIRS)
    assert all(p["low"] != p["high"] for p in pairs)
```

`:73`의 남성 개수 단언을 교체:

```python
    assert len(pairs) == 7                                   # top3 + pants2 + outer2
```

같은 파일 끝에 추가:

```python
def test_sleeve_extreme_pair_runs_sleeveless_to_short():
    assert FM.extreme_pair("top", "sleeve", "women") == ("sleeveless", "short")
    assert FM.extreme_pair("top", "sleeve", "men") == ("sleeveless", "short")
```

- [ ] **Step 14: 서버 전체 확인**

Run: `cd server && .venv/bin/python -m pytest tests/test_mannequin_fit_profile.py tests/test_mannequin_fit_qc.py tests/test_fit_axis_matrix.py tests/test_mannequin_pairwise_qc.py tests/test_image_qc.py tests/test_mannequin_adjust.py -q`
Expected: 전부 PASS

- [ ] **Step 15: 프론트 전체 확인**

Run: `pnpm test:frontend`
Expected: 전부 PASS (화면은 아직 그대로 — 핫존이 없어 sleeve 스텝은 열리지 않는다)

- [ ] **Step 16: 커밋**

```bash
git add src/lib/fitAxes.js server/app/agents/fit_axes.py \
        server/app/agents/mannequin_fit_qc.py server/app/agents/mannequin_pairwise_qc.py \
        server/app/agents/fit_axis_matrix.py \
        tests/frontend/fit-vocabulary.test.mjs \
        server/tests/test_mannequin_fit_profile.py server/tests/test_fit_axis_matrix.py
git commit -m "feat(fit): add the top sleeve-length axis

Sellers could adjust body fit and hem length but never sleeve length, so a
long-sleeve product could not be shown as short-sleeve or sleeveless.

Register the axis in both catalog mirrors and in the four QC tables that
derive from them, so the axis is judged like every other one instead of
being silently skipped."
```

---

### Task 2: 하의 계열 핫존 좌표 분산 + 최소거리 회귀 테스트

기존 버그를 먼저 고치고 불변식을 도입한다. Task 3에서 점을 하나 더 얹을 때 이 테스트가 자동으로 검증한다.

`outer-hem`(55%,55%)과 매칭 하의의 `pants-cut`(56%,55%)이 사실상 같은 자리라, 아우터 상품 화면에서 아우터 기장 축이 클릭되지 않는다. `outer-hem`만 옮기면 마네킹 몸 밖으로 나가므로, 힙 주변에 몰린 세 점을 해부학적으로 더 맞는 자리로 분산한다.

**Files:**
- Modify: `src/features/mannequin/Mannequin.css:147-151`
- Test: `tests/frontend/mannequin-fit-hotspots.test.mjs`

**Interfaces:**
- Consumes: `fitHotspotsFor(category, axis)` (기존), `axesFor(category, gender)` (Task 1에서 sleeve 추가됨), `matchingFitDefinition(item, gender)` (기존, `src/lib/matchingFit.js`)
- Produces: `.fit-hotspot-<id> { left: N%; top: N%; }` CSS 규칙을 파싱하는 회귀 테스트. Task 3이 추가하는 점도 이 테스트의 대상이 된다.

- [ ] **Step 1: 최소거리 회귀 테스트를 먼저 쓴다**

`tests/frontend/mannequin-fit-hotspots.test.mjs` 상단 import 블록을 다음으로 교체:

```js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { axesFor } from '../../src/lib/fitAxes.js';
import { matchingFitDefinition } from '../../src/lib/matchingFit.js';
import { fitHotspotsFor } from '../../src/features/mannequin/fitHotspots.js';
```

파일 맨 끝에 추가:

```js
// .fit-mine-img 는 예시 패널이 열린 comparing 상태에서 가장 좁다 (Mannequin.css: width 300px, aspect-ratio 3/4).
const FRAME_W = 300;
const FRAME_H = 400;
// .fit-hotspot 히트박스가 44×44px 이라, 중심이 이보다 가까우면 두 버튼이 겹쳐 뒤엣것이 클릭을 가로챈다.
const MIN_GAP_PX = 48;

function hotspotCoords() {
  const css = readFileSync(
    new URL('../../src/features/mannequin/Mannequin.css', import.meta.url),
    'utf8',
  );
  const coords = new Map();
  const rule = /\.fit-hotspot-([a-z-]+)\s*\{\s*left:\s*([\d.]+)%;\s*top:\s*([\d.]+)%;\s*\}/g;
  let match = rule.exec(css);
  while (match !== null) {
    coords.set(match[1], { left: Number(match[2]), top: Number(match[3]) });
    match = rule.exec(css);
  }
  return coords;
}

// 한 화면에 동시에 뜨는 점 = 주상품 축들 + 메인 매칭 의류 축 1개.
// 매칭 가능한 조합은 server/app/services/matching.py 의 보완타입 규칙 미러다:
// top·outer → 하의(pants|skirt), pants·skirt → 상의(top), dress → 매칭 없음.
const SCREEN_COMBOS = [
  ['top', ['pants', 'skirt', null]],
  ['outer', ['pants', 'skirt', null]],
  ['pants', ['top', null]],
  ['skirt', ['top', null]],
  ['dress', [null]],
];

function hotspotIdsOnScreen(category, gender, matchCategory) {
  const ids = Object.keys(axesFor(category, gender))
    .flatMap((axis) => fitHotspotsFor(category, axis).map(({ id }) => id));
  if (matchCategory) {
    const def = matchingFitDefinition({ id: 'match-1', fitCategory: matchCategory }, gender);
    if (def) ids.push(...fitHotspotsFor(def.fitCategory, def.axisKey).map(({ id }) => id));
  }
  return ids;
}

test('한 화면에 함께 뜨는 핫존은 서로 겹치지 않는다', () => {
  const coords = hotspotCoords();
  const failures = [];

  for (const [category, matchCategories] of SCREEN_COMBOS) {
    for (const gender of ['women', 'men']) {
      for (const matchCategory of matchCategories) {
        const label = `${category}/${gender}${matchCategory ? `+${matchCategory}` : ''}`;
        const ids = hotspotIdsOnScreen(category, gender, matchCategory);
        assert.deepEqual([...new Set(ids)], ids, `${label}: 같은 핫존이 두 번 뜬다`);
        ids.forEach((id) => assert.ok(coords.has(id), `${label}: 좌표 없음 .fit-hotspot-${id}`));

        for (let i = 0; i < ids.length; i += 1) {
          for (let j = i + 1; j < ids.length; j += 1) {
            const a = coords.get(ids[i]);
            const b = coords.get(ids[j]);
            const dx = ((a.left - b.left) / 100) * FRAME_W;
            const dy = ((a.top - b.top) / 100) * FRAME_H;
            const gap = Math.hypot(dx, dy);
            if (gap < MIN_GAP_PX) {
              failures.push(`${label}: ${ids[i]} ↔ ${ids[j]} = ${gap.toFixed(1)}px`);
            }
          }
        }
      }
    }
  }

  assert.deepEqual(failures, [], `핫존 간격 ${MIN_GAP_PX}px 미만:\n${failures.join('\n')}`);
});
```

- [ ] **Step 2: 실패 확인 — 기존 버그가 그대로 잡히는지**

Run: `node --test tests/frontend/mannequin-fit-hotspots.test.mjs`
Expected: FAIL. 실패 목록은 정확히 다음 4줄이다:
```
outer/women+pants: outer-hem ↔ pants-cut = 3.0px
outer/women+skirt: outer-hem ↔ skirt-shape = 15.0px
outer/men+pants: outer-hem ↔ pants-cut = 3.0px
outer/men+skirt: outer-hem ↔ skirt-shape = 15.0px
```
다른 줄이 더 나오면 좌표가 이미 손대진 것이니 멈추고 확인한다. Task 1에서 추가한 `top.sleeve`는 아직 핫존이 없어(`fitHotspotsFor('top','sleeve')`가 `[]`) 이 목록에 나타나지 않는다 — 정상이다.

- [ ] **Step 3: 좌표 분산**

`src/features/mannequin/Mannequin.css`에서 세 줄을 교체한다.

바꾸기 전:
```css
.fit-hotspot-outer-hem { left: 55%; top: 55%; }
.fit-hotspot-pants-cut { left: 56%; top: 55%; }
.fit-hotspot-skirt-shape { left: 58%; top: 58%; }
```

바꾼 뒤:
```css
.fit-hotspot-outer-hem { left: 46%; top: 50%; }
.fit-hotspot-pants-cut { left: 60%; top: 62%; }
.fit-hotspot-skirt-shape { left: 62%; top: 60%; }
```

- [ ] **Step 4: 통과 확인**

Run: `node --test tests/frontend/mannequin-fit-hotspots.test.mjs`
Expected: 최소거리 테스트는 PASS. 좌표를 pin 한 기존 테스트가 FAIL — `.fit-hotspot-pants-cut { left: 56%; top: 55%; }` 정규식이 더는 맞지 않는다.

- [ ] **Step 5: pin 테스트 갱신**

같은 파일에서 pants-cut pin 한 줄을 교체:

```js
  assert.match(styles, /\.fit-hotspot-pants-cut \{ left: 60%; top: 62%; \}/);
```

- [ ] **Step 6: 통과 확인**

Run: `pnpm test:frontend`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add src/features/mannequin/Mannequin.css tests/frontend/mannequin-fit-hotspots.test.mjs
git commit -m "fix(mannequin): stop hotspots from covering each other

outer-hem sat at 55%,55% and the matching pants-cut at 56%,55%, so on an
outerwear project the two 44px buttons overlapped almost exactly and the
matching one — rendered last — swallowed the clicks. The outerwear length
axis was unreachable, and the hotspots are its only entry point.

Spread the three hip-area points onto the places they actually describe,
and add a regression test that walks every category/gender/matching
combination and fails when two live hotspots come within 48px."
```

---

### Task 3: 소매 핫존 노출 + 겨드랑이 재배치

축을 화면에 연결한다. Task 2의 최소거리 테스트가 새 점을 자동으로 검증한다.

`top-fit` 라벨을 "몸통·소매 핏" → "몸통 핏"으로 바꾼다. 소매가 독립 축이 된 이상 두 점이 같은 것을 가리키는 이름을 쓰면 안 된다. `outer-fit`은 소매 축이 없으므로 "몸통·소매 핏"을 유지한다.

**Files:**
- Modify: `src/features/mannequin/fitHotspots.js:2-5`
- Modify: `src/features/mannequin/Mannequin.jsx:44`
- Modify: `src/features/mannequin/Mannequin.css:144` (`top-fit`), `:153` 뒤(`top-sleeve` 신규)
- Test: `tests/frontend/mannequin-fit-hotspots.test.mjs`

**Interfaces:**
- Consumes: `axesFor('top', gender).sleeve` (Task 1), 최소거리 테스트 (Task 2)
- Produces: `fitHotspotsFor('top','sleeve')` → `[{ id: 'top-sleeve', label: '소매 기장' }]`

- [ ] **Step 1: 핫존 커버리지 테스트를 먼저 쓴다**

`tests/frontend/mannequin-fit-hotspots.test.mjs`의 첫 테스트에서 `expected` 의 top 줄을 교체:

```js
    top: ['fit', 'length', 'sleeve'],
```

같은 파일 끝에 추가:

```js
test('소매 기장은 자체 핫존을 갖고, 몸통 핏 라벨은 소매를 더는 주장하지 않는다', () => {
  assert.deepEqual(fitHotspotsFor('top', 'sleeve'), [{ id: 'top-sleeve', label: '소매 기장' }]);
  assert.deepEqual(fitHotspotsFor('top', 'fit'), [{ id: 'top-fit', label: '몸통 핏' }]);
  // 아우터는 소매 축이 없어 기존 라벨을 유지한다
  assert.deepEqual(fitHotspotsFor('outer', 'fit'), [{ id: 'outer-fit', label: '몸통·소매 핏' }]);
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test tests/frontend/mannequin-fit-hotspots.test.mjs`
Expected: FAIL — `fitHotspotsFor('top','sleeve')`가 `[]`, `top-fit` 라벨이 `'몸통·소매 핏'`

- [ ] **Step 3: 핫존 카탈로그 수정**

`src/features/mannequin/fitHotspots.js`의 `top` 블록을 교체:

```js
  top: Object.freeze({
    fit: Object.freeze([{ id: 'top-fit', label: '몸통 핏' }]),
    length: Object.freeze([{ id: 'top-hem', label: '상의 밑단' }]),
    sleeve: Object.freeze([{ id: 'top-sleeve', label: '소매 기장' }]),
  }),
```

- [ ] **Step 4: 축 라벨 등록**

`src/features/mannequin/Mannequin.jsx:44`를 교체:

```js
const AXIS_LABELS = { fit: '핏', length: '기장', cut: '핏', silhouette: '실루엣', sleeve: '소매 기장' };
```

이걸 빠뜨리면 예시 패널 제목이 "원하는 sleeve의 예시를 선택해주세요."가 된다 — `stepName`이 라벨 미등록 시 축 키를 그대로 쓴다(`Mannequin.jsx:853`).

- [ ] **Step 5: 좌표 배치**

`src/features/mannequin/Mannequin.css`에서 `top-fit` 줄을 교체하고 `top-sleeve` 줄을 바로 뒤에 추가:

```css
.fit-hotspot-top-fit { left: 46%; top: 27%; }
.fit-hotspot-top-sleeve { left: 64%; top: 27%; }
```

- [ ] **Step 6: 통과 확인**

Run: `node --test tests/frontend/mannequin-fit-hotspots.test.mjs`
Expected: 커버리지·최소거리 테스트는 PASS. `top-fit` 좌표를 pin 한 기존 테스트가 FAIL.

- [ ] **Step 7: pin 테스트 갱신**

같은 파일의 `top-fit` pin 줄을 교체하고 `top-sleeve` pin 을 추가:

```js
  assert.match(styles, /\.fit-hotspot-top-fit \{ left: 46%; top: 27%; \}/);
  assert.match(styles, /\.fit-hotspot-top-sleeve \{ left: 64%; top: 27%; \}/);
```

- [ ] **Step 8: 전체 통과 확인**

Run: `pnpm test:frontend`
Expected: 전부 PASS

- [ ] **Step 9: 실제 마네킹컷 위에서 좌표 보정**

여기까지의 숫자는 기존 좌표에서 역산한 출발값이라, 실제 마네킹 몸의 겨드랑이에 정확히 얹힌다는 보장이 없다. 눈으로 확인한다.

```bash
pnpm dev:mock
```

브라우저로 mock 플로우를 태워 "의류 재현성 높이기" 화면까지 간 뒤, 상의 상품의 마네킹컷 위에서 확인한다.

- `top-fit` 점이 화면 왼쪽 겨드랑이(마네킹의 오른쪽 팔 접합부)에 얹혔는가
- `top-sleeve` 점이 화면 오른쪽 겨드랑이에 얹혔는가
- 두 점 다 배경이 아니라 몸/옷 위에 있는가
- 예시 패널을 열어 폭이 300px로 줄어든 `comparing` 상태에서도 마찬가지인가

어긋나면 `Mannequin.css`의 %를 조정한다. 조정할 때마다 `node --test tests/frontend/mannequin-fit-hotspots.test.mjs`를 돌려 최소거리 불변식이 유지되는지 확인하고, pin 테스트의 정규식과 스펙 §5.2 표를 같은 값으로 맞춘다.

- [ ] **Step 10: 커밋**

```bash
git add src/features/mannequin/fitHotspots.js src/features/mannequin/Mannequin.jsx \
        src/features/mannequin/Mannequin.css tests/frontend/mannequin-fit-hotspots.test.mjs \
        docs/superpowers/specs/2026-08-13-mannequin-sleeve-hotspot-design.md
git commit -m "feat(mannequin): put the sleeve axis on the armpit

The fit dot sat in the middle of the chest, where nothing about it says
what it adjusts, and the new sleeve axis had no way onto the screen at
all. Anchor both to the armpits they describe, and rename the fit label
to 몸통 핏 now that it no longer speaks for the sleeves."
```

---

### Task 4: 계약·UI 문서 갱신

**Files:**
- Modify: `documents/fit_profile_spec.md` §2 축 카탈로그
- Modify: `documents/mannequin_ui_direction.md` §2 컴포넌트 구조, §4 에셋 현황

**Interfaces:**
- Consumes: Task 1~3의 최종 값·좌표·라벨
- Produces: 없음 (문서)

- [ ] **Step 1: 축 카탈로그 문서에 sleeve 행 추가**

`documents/fit_profile_spec.md` §2의 표에서 `top.length` 행 바로 뒤에 한 줄 추가:

```markdown
| top.sleeve | sleeveless/short (민소매/반팔) | 동일 |
```

표 아래 `promptEn은 검증 생성에 쓴 구절 그대로…`로 시작하는 문단 뒤에 다음 문장을 붙인다:

> `top.sleeve`는 상의 전용이다. 축을 고르지 않으면 사진 그대로(긴팔이면 긴팔)이며, 카탈로그에 '긴팔' 값은 없다 — 한 번 저장한 뒤의 복귀는 이전 버전 선택으로만 가능하다.

- [ ] **Step 2: UI 방향서 갱신**

`documents/mannequin_ui_direction.md` §2 컴포넌트 구조의 상의 핫존 설명에 소매 축을 넣고, §4 에셋 현황 문장에 신규 2장을 반영한다. 에셋을 아직 만들지 않았다면 §4에 갭으로 적는다:

> **갭(텍스트 폴백으로 동작, 추가 생성 백로그)**: top sleeve sleeveless/short.

- [ ] **Step 3: 커밋**

```bash
git add documents/fit_profile_spec.md documents/mannequin_ui_direction.md
git commit -m "docs(fit): record the sleeve axis in the contract and UI notes"
```

---

### Task 5: 예시 타일 이미지 2장

**이 태스크는 유료 이미지 생성 API를 호출한다 (조합당 1회, 총 2회). 실행 전에 사용자 승인을 받는다.** 생략해도 UI는 텍스트 타일로 폴백하므로 앞 태스크만으로 기능은 완결이다.

**Files:**
- Modify: `server/scripts/gen_fit_examples.py` (`COMBOS`)
- Create: `public/assets/fit-examples/top-any-sleeve-sleeveless.jpg`, `public/assets/fit-examples/top-any-sleeve-short.jpg`
- Modify: `src/lib/fitExampleImages.js` (`FILES`)
- Test: `tests/frontend/fit-example-files.test.mjs`

**Interfaces:**
- Consumes: Task 1의 값 이름 `sleeveless` · `short` (파일명 규칙 `{category}-{gender|any}-{axis}-{value}.jpg`)
- Produces: 없음

- [ ] **Step 1: 생성 조합 등록**

`server/scripts/gen_fit_examples.py`의 `COMBOS` 리스트에 추가한다. 성별은 `any` = 여성 베이스이며, 옷 계열은 기존 여성 상의 예시와 맞춘다.

```python
    ("top-any-sleeve-sleeveless", "women",
     "a plain ivory jersey top, sleeveless — no sleeves at all, clean finished armholes at the "
     "shoulder points, both shoulders and upper arms bare"),
    ("top-any-sleeve-short", "women",
     "a plain ivory jersey top, short sleeves — the sleeve hems end on the upper arm above the "
     "elbow with the forearms bare"),
```

- [ ] **Step 2: 생성 실행**

```bash
cd server && .venv/bin/python -m scripts.gen_fit_examples --only top-any-sleeve-sleeveless
cd server && .venv/bin/python -m scripts.gen_fit_examples --only top-any-sleeve-short
```

원본 PNG는 `server/ab_out/fit_examples/`에 저장된다.

- [ ] **Step 3: 눈으로 확인**

두 장이 기존 예시 톤(흰 마네킹 + 옅은 회색 스튜디오, 3/4 각도, 상의만 착용)과 맞는지, 소매 차이가 한눈에 읽히는지 본다. 안 맞으면 Step 2를 다시 돌린다.

- [ ] **Step 4: 300×447 jpg로 변환해 배치**

```bash
cd /Users/nojeong-un/devs/wearless_studio
sips -z 447 300 -s format jpeg \
  server/ab_out/fit_examples/top-any-sleeve-sleeveless.png \
  --out public/assets/fit-examples/top-any-sleeve-sleeveless.jpg
sips -z 447 300 -s format jpeg \
  server/ab_out/fit_examples/top-any-sleeve-short.png \
  --out public/assets/fit-examples/top-any-sleeve-short.jpg
```

- [ ] **Step 5: 정합 테스트가 미등록을 잡는지 확인**

Run: `node --test tests/frontend/fit-example-files.test.mjs`
Expected: FAIL — `파일 있는데 미등록`

- [ ] **Step 6: FILES에 등록**

`src/lib/fitExampleImages.js`의 `FILES` Set에서 top-men 줄 뒤에 추가:

```js
  'top-any-sleeve-sleeveless', 'top-any-sleeve-short',
```

- [ ] **Step 7: 예시가 실제로 뜨는지 잠근다**

`tests/frontend/fit-example-files.test.mjs` 끝에 추가:

```js
test('소매 기장 예시는 남녀 모두 any 폴백으로 뜬다', () => {
  for (const v of ['sleeveless', 'short']) {
    assert.ok(fitExampleImage('top', 'women', 'sleeve', v), `top-women sleeve ${v}`);
    assert.ok(fitExampleImage('top', 'men', 'sleeve', v), `top-men sleeve ${v}`);
  }
});
```

- [ ] **Step 8: 통과 확인**

Run: `pnpm test:frontend`
Expected: 전부 PASS

- [ ] **Step 9: 문서의 갭 항목 제거**

`documents/mannequin_ui_direction.md` §4에서 Task 4 Step 2에 적은 sleeve 갭 문장을 지우고, 보유 장수를 36 → 38로 고친다.

- [ ] **Step 10: 커밋**

```bash
git add server/scripts/gen_fit_examples.py public/assets/fit-examples/top-any-sleeve-*.jpg \
        src/lib/fitExampleImages.js tests/frontend/fit-example-files.test.mjs \
        documents/mannequin_ui_direction.md
git commit -m "feat(fit): add sleeve-length example tiles

The sleeve axis fell back to text-only tiles, which is the weakest way to
ask someone to pick a silhouette. Generate the two neutral mannequin
examples with the existing script and register them."
```

---

## 완료 판정

- [ ] `pnpm test:frontend` 전부 PASS
- [ ] `cd server && .venv/bin/python -m pytest -q` 전부 PASS
- [ ] 상의 프로젝트의 마네킹 화면에 점 3개(왼쪽 겨드랑이·오른쪽 겨드랑이·밑단)가 몸 위에 얹혀 보인다
- [ ] 아우터 프로젝트에서 아우터 밑단 점과 매칭 하의 점이 따로 눌린다
- [ ] 소매 점 → 예시 패널 제목이 "원하는 소매 기장의 예시를 선택해주세요."
- [ ] 반팔 선택 → `수정 반영 · 2 크레딧` CTA → 새 버전 마네킹컷의 소매가 실제로 짧아진다