# 신분증 가이드 촬영 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 신분증을 파일로 고르는 대신 폰 카메라로 가이드에 맞춰 찍게 하고, 주민등록번호를 규격 좌표로 자동 마스킹한 뒤 서버가 그 마스킹을 검증한다.

**Architecture:** 신분증은 규격이 고정(ISO/IEC 7810 ID-1, 86×54mm)이라, 카드가 화면 가이드를 채우도록 찍으면 주민등록번호의 픽셀 좌표가 계산으로 나온다. 순수 모듈 두 개(규격 좌표 · 프레임 판정)를 먼저 만들어 테스트로 굳히고, 그 위에 카메라 컴포넌트를 올린다. 서버는 기존 OpenCV 로 "그 자리가 실제로 덮였는지"를 검사하되 **shadow 로 먼저** 켠다.

**Tech Stack:** React 19 + Vite (프런트, **새 의존성 0**), FastAPI + OpenCV(서버, 기존 `opencv-contrib-python-headless`), Node 내장 테스트 러너

**Spec:** `docs/superpowers/specs/2026-09-14-id-capture-guided-mobile-design.md`

## Global Constraints

- **작업 위치**: worktree `~/devs/wearless_studio-id-capture`. 메인 트리는 건드리지 않는다.
- **프런트 의존성을 늘리지 않는다.** OpenCV.js·Tesseract.js 금지 — 현재 25개를 유지한다(스펙 §3-3).
- **원본 프레임은 브라우저를 떠나지 않는다.** 서버로 가는 건 항상 마스킹을 태운 캔버스 blob 뿐이다. v1 최종 리뷰가 경로별로 확인한 속성이고, 이 재설계에서도 유지한다.
- **기존 `mid` 경로 동작 불변.** 모바일 게이트·카메라·마스킹 검증 전부 `simple_auth` 에만 적용된다.
- **모바일 판별에 User-Agent 를 쓰지 않는다**(스펙 §5). 프런트는 안내·비활성만, 서버는 기하 검증만 한다.
- **서버 마스킹 검증은 shadow 로 시작한다.** `FM_ID_MASK_VERIFY=off|shadow|enforce`, 기본 `shadow`. v1 에서 SFace 임계를 캘리브 없이 넣었다가 오탈락으로 고생한 선례가 있다(스펙 §7).
- **자동 셔터는 편의이지 관문이 아니다.** 수동 셔터를 항상 노출한다(스펙 §6, v1 리뷰 I7 교훈).
- **v1 범위 유지**: 주민등록증(`rrc`)만. `ID_DOCUMENT_TYPES` 를 넓히지 않는다.
- **테스트 명령**
  - 프런트: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend`
  - 빌드: `cd ~/devs/wearless_studio-id-capture && pnpm build`
  - 백엔드: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/ -q --ignore=tests/test_personalization.py`
- **커밋 메시지**: Conventional Commits 접두어, 한국어 본문, 말미에 정확히:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
  ```

---

## File Structure

**신규**

| 파일 | 책임 |
| --- | --- |
| `src/features/model/idCardGeometry.js` | ISO ID-1 규격 상수, 가이드 비율, 주민번호 상대좌표, 가이드↔자연픽셀 변환. 순수 함수만 |
| `src/features/model/idCardDetector.js` | 프레임 한 장이 "찍을 만한가" 판정(채움·기울기·흔들림). 순수 함수, ImageData 유사 입력 |
| `src/features/model/IdCameraCapture.jsx` | `getUserMedia` + 가이드 오버레이 + 자동/수동 셔터 + 폴백 |
| `server/app/facemarket_id_mask_verify.py` | 업로드 이미지의 주민번호 영역이 단색으로 덮였는지 검사(cv2) |
| `tests/frontend/id-card-geometry.test.mjs` | Task 1 |
| `tests/frontend/id-card-detector.test.mjs` | Task 2 |
| `tests/frontend/id-camera-capture.test.mjs` | Task 3 |
| `server/tests/test_facemarket_id_mask_verify.py` | Task 7 |

**수정**

| 파일 | 변경 |
| --- | --- |
| `src/features/model/IdDocumentStep.jsx` | 카메라 우선, 수동 마스킹은 비상구로 강등 |
| `src/features/model/idDocumentMasking.js` | 가이드 기준 자동 마스크 좌표 추가(기존 드래그 경로는 보존) |
| `src/features/model/IdentityMethodStep.jsx` | 데스크톱에서 간편인증 비활성 + QR |
| `src/features/model/ModelRegister.jsx` | 스텝 순서(인증→촬영) 배선 |
| `src/features/model/biometricEnrollment.js` | 상태→스텝 매핑은 그대로, 순서만 바뀜 |
| `server/app/facemarket_enrollment.py` | `simple_auth` 시작 상태를 `identity_pending` 로, 인증 성공 후 `id_capture_pending` 로 |
| `server/app/facemarket_id_document.py` | 업로드 시 마스킹 검증 호출 |
| `server/app/config.py` | `FM_ID_MASK_VERIFY` |
| `src/features/admin/AdminEnrollmentReview.jsx` | 인증 신원을 카드 옆에, `수동 마스킹` 배지 |
| `copilot/api/manifest.yml` | 새 플래그 기본값 |

---

## Task 1: 카드 규격 좌표 모듈

**Files:**
- Create: `src/features/model/idCardGeometry.js`
- Test: `tests/frontend/id-card-geometry.test.mjs`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `CARD_ASPECT: number` — 85.6/53.98 ≈ 1.5858
  - `RRN_REGION: { xr, yr, wr, hr }` — 카드 기준 주민번호 영역 비율(0~1)
  - `guideRectInFrame(frameW, frameH, fill = 0.86) -> { x, y, w, h }` — 프레임 안에서 카드 비율을 유지한 가이드 사각형(픽셀)
  - `rrnRectInFrame(frameW, frameH, fill?) -> { x, y, w, h }` — 그 가이드 안 주민번호 영역(픽셀)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/frontend/id-card-geometry.test.mjs`:

```js
/* 신분증 규격 좌표. 카드가 가이드를 채우도록 찍게 하면 주민등록번호 위치가
   계산으로 나온다 — 사용자가 박스를 끌 필요가 없고, 서버가 그 자리를 검사할 수 있다.
   이 모듈이 틀리면 마스크가 엉뚱한 데 찍히므로(=주민번호 노출) 좌표를 테스트로 굳힌다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { CARD_ASPECT, RRN_REGION, guideRectInFrame, rrnRectInFrame } from '../../src/features/model/idCardGeometry.js';

test('카드 비율은 ISO/IEC 7810 ID-1 (85.6 × 53.98mm)', () => {
  assert.ok(Math.abs(CARD_ASPECT - 85.6 / 53.98) < 1e-9);
});

test('가이드는 프레임 안에 들어가고 카드 비율을 지킨다', () => {
  for (const [w, h] of [[1080, 1920], [1920, 1080], [800, 800]]) {
    const g = guideRectInFrame(w, h);
    assert.ok(g.x >= 0 && g.y >= 0, '프레임 밖으로 나가면 안 된다');
    assert.ok(g.x + g.w <= w && g.y + g.h <= h);
    assert.ok(Math.abs(g.w / g.h - CARD_ASPECT) < 0.01, `비율이 카드와 달라졌다: ${g.w}/${g.h}`);
  }
});

test('주민번호 영역은 가이드 안에 완전히 들어간다', () => {
  const g = guideRectInFrame(1080, 1920);
  const r = rrnRectInFrame(1080, 1920);
  assert.ok(r.x >= g.x && r.y >= g.y, '가이드 왼쪽/위를 벗어났다');
  assert.ok(r.x + r.w <= g.x + g.w && r.y + r.h <= g.y + g.h, '가이드 오른쪽/아래를 벗어났다');
});

test('주민번호 영역 비율은 카드 아래쪽 번호 줄을 덮는다', () => {
  // 주민등록증의 번호는 이름 아래 한 줄이다. 가로로 넉넉히, 세로로는 그 줄만.
  assert.ok(RRN_REGION.yr > 0.4 && RRN_REGION.yr < 0.8, '세로 위치가 카드 중하단이어야 한다');
  assert.ok(RRN_REGION.wr > 0.4, '번호 전체를 덮을 만큼 가로가 넓어야 한다');
  assert.ok(RRN_REGION.hr > 0.08 && RRN_REGION.hr < 0.3, '한 줄 높이여야 한다');
});

test('fill 을 줄이면 가이드가 작아지고 주민번호 영역도 같이 줄어든다', () => {
  const big = rrnRectInFrame(1080, 1920, 0.9);
  const small = rrnRectInFrame(1080, 1920, 0.6);
  assert.ok(small.w < big.w && small.h < big.h);
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: fail 5 (모듈 없음)

- [ ] **Step 3: 구현**

`src/features/model/idCardGeometry.js`:

```js
/* 신분증 규격 좌표.

   주민등록증은 ISO/IEC 7810 ID-1(85.6 × 53.98mm)이고 주민등록번호 줄의 위치도
   카드 안에서 고정이다. 그래서 카드가 화면 가이드를 채우도록 찍게 하면 번호의
   픽셀 좌표가 계산으로 나온다 — 사용자가 검은 박스를 끌 필요가 없고, 서버가
   "그 자리가 실제로 덮였는지" 검사할 수 있다(설계 §2).

   이 좌표가 틀리면 마스크가 엉뚱한 데 찍혀 주민번호가 그대로 올라간다.
   그래서 상수는 여기 한 곳에만 두고 테스트로 굳힌다. 카드 규격이 개정되면
   여기만 고친다. */

export const CARD_ASPECT = 85.6 / 53.98;   // ≈ 1.5858

/* 카드 기준 주민등록번호 영역(0~1 비율). 가로는 번호 전체를 놓치지 않게 넉넉히,
   세로는 그 한 줄만. v1 의 드래그 기본값(DEFAULT_MASK_RATIOS.rrc)을 출발점으로
   삼되, 가이드 촬영에서는 카드가 프레임을 정확히 채우므로 더 좁게 잡아도 안전하다. */
export const RRN_REGION = Object.freeze({ xr: 0.06, yr: 0.58, wr: 0.62, hr: 0.14 });

/* 프레임 안에서 카드 비율을 유지하는 가장 큰 사각형을 잡고, fill 만큼 줄여 여백을 둔다.
   여백이 필요한 이유: 카드 모서리가 프레임에 딱 붙으면 사용자가 맞추기 어렵고,
   경계 검출도 프레임 가장자리와 카드 경계를 구분하기 힘들어진다. */
export function guideRectInFrame(frameW, frameH, fill = 0.86) {
  const byWidth = frameW / CARD_ASPECT <= frameH;
  const w = (byWidth ? frameW : frameH * CARD_ASPECT) * fill;
  const h = w / CARD_ASPECT;
  return {
    x: Math.round((frameW - w) / 2),
    y: Math.round((frameH - h) / 2),
    w: Math.round(w),
    h: Math.round(h),
  };
}

export function rrnRectInFrame(frameW, frameH, fill) {
  const g = guideRectInFrame(frameW, frameH, fill);
  return {
    x: Math.round(g.x + RRN_REGION.xr * g.w),
    y: Math.round(g.y + RRN_REGION.yr * g.h),
    w: Math.round(RRN_REGION.wr * g.w),
    h: Math.round(RRN_REGION.hr * g.h),
  };
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: fail 0

- [ ] **Step 5: mutation 으로 테스트가 무는지 확인**

`RRN_REGION.yr` 을 `0.58` → `0.20`(카드 위쪽, 번호가 없는 자리)으로 바꾸고 테스트 실행 → 실패해야 한다. 되돌린다. 관찰 결과를 보고에 적는다.

- [ ] **Step 6: 커밋**

```bash
git add src/features/model/idCardGeometry.js tests/frontend/id-card-geometry.test.mjs
git commit -m "$(cat <<'EOF'
feat(facemarket): 신분증 규격 좌표 모듈

카드는 ISO/IEC 7810 ID-1 로 규격이 고정이고 주민등록번호 줄 위치도 고정이다.
카드가 가이드를 채우도록 찍게 하면 번호의 픽셀 좌표가 계산으로 나온다 —
사용자가 박스를 끌 필요가 없고 서버가 그 자리를 검사할 수 있다.

좌표가 틀리면 마스크가 엉뚱한 데 찍혀 주민번호가 그대로 올라가므로, 상수를
한 곳에 모으고 테스트로 굳힌다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 2: 자동 셔터 판정기

**Files:**
- Create: `src/features/model/idCardDetector.js`
- Test: `tests/frontend/id-card-detector.test.mjs`

**Interfaces:**
- Consumes: Task 1 의 `guideRectInFrame`
- Produces:
  - `scoreFrame(gray, width, height, opts?) -> { fill, tilt, sharp, ok }` — `gray` 는 `Uint8ClampedArray`(1채널). `ok` 는 세 조건 동시 충족
  - `createShutterGate({ needed = 5 }) -> { push(score) -> boolean, reset() }` — 연속 `needed` 프레임 `ok` 면 `push` 가 true(=촬영)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/frontend/id-card-detector.test.mjs`:

```js
/* 자동 셔터 판정. "카드가 가이드에 잘 맞았는가"를 프레임 한 장에서 판정하고,
   연속 N프레임 유지될 때만 찍는다 — 손이 지나가는 순간 찍히면 안 된다.

   판정은 순수 함수라 합성 픽셀로 테스트한다. 실제 카메라 없이도 회귀를 잡는다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { scoreFrame, createShutterGate } from '../../src/features/model/idCardDetector.js';
import { guideRectInFrame } from '../../src/features/model/idCardGeometry.js';

const W = 160, H = 120;

/* 가이드 자리에 밝은 사각형(=카드)을 그린 합성 프레임. inset 을 키우면 카드가 작아진다. */
function frameWithCard({ inset = 0, tiltShift = 0 } = {}) {
  const gray = new Uint8ClampedArray(W * H).fill(30);   // 어두운 배경
  const g = guideRectInFrame(W, H);
  for (let y = g.y + inset; y < g.y + g.h - inset; y++) {
    const shift = Math.round(tiltShift * (y - g.y));
    for (let x = g.x + inset + shift; x < g.x + g.w - inset + shift; x++) {
      if (x >= 0 && x < W && y >= 0 && y < H) gray[y * W + x] = 220;
    }
  }
  return gray;
}

test('가이드를 채운 반듯한 카드는 ok', () => {
  const s = scoreFrame(frameWithCard(), W, H);
  assert.equal(s.ok, true, `fill=${s.fill} tilt=${s.tilt} sharp=${s.sharp}`);
});

test('카드가 너무 작으면 ok 아님 (fill 부족)', () => {
  const s = scoreFrame(frameWithCard({ inset: 18 }), W, H);
  assert.equal(s.ok, false);
});

test('빈 프레임은 ok 아님', () => {
  const s = scoreFrame(new Uint8ClampedArray(W * H).fill(30), W, H);
  assert.equal(s.ok, false);
});

test('기울어진 카드는 ok 아님', () => {
  const s = scoreFrame(frameWithCard({ tiltShift: 0.35 }), W, H);
  assert.equal(s.ok, false);
});

test('게이트는 연속 N프레임을 요구한다 — 한 프레임 튀어도 안 찍는다', () => {
  const gate = createShutterGate({ needed: 3 });
  const good = { ok: true }, bad = { ok: false };
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(bad), false, '중간에 끊기면 다시 세야 한다');
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(good), false);
  assert.equal(gate.push(good), true, '연속 3프레임이면 찍는다');
});

test('reset 하면 처음부터 다시 센다', () => {
  const gate = createShutterGate({ needed: 2 });
  gate.push({ ok: true });
  gate.reset();
  assert.equal(gate.push({ ok: true }), false);
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: 모듈 없음으로 실패

- [ ] **Step 3: 구현**

`src/features/model/idCardDetector.js`:

```js
/* 자동 셔터 판정 — 라이브러리 없이 캔버스 그레이스케일만으로.

   OpenCV.js(~8MB)를 끌어오지 않는다(설계 §3-3). 필요한 건 세 가지뿐이고,
   전부 가이드 테두리 안쪽 밴드의 밝기/경계만 보면 판정된다:
     · fill  — 카드가 가이드를 충분히 채웠나 (밝은 화소 비율)
     · tilt  — 반듯한가 (위/아래 경계의 x 위치 차이)
     · sharp — 흔들리지 않았나 (경계의 기울기 세기)

   자동 셔터는 편의이지 관문이 아니다 — 이게 안 잡혀도 사용자는 수동으로 찍을 수
   있어야 한다(설계 §6). 그래서 임계는 "확실할 때만 찍는" 쪽으로 보수적으로 둔다. */
import { guideRectInFrame } from './idCardGeometry.js';

const DEFAULTS = { fillMin: 0.75, tiltMax: 0.06, sharpMin: 12 };

/* 한 행에서 밝은 구간의 시작·끝 x 를 찾는다. 카드가 배경보다 밝다는 전제 —
   어두운 배경(책상) 위 신분증이 일반적이다. 반대 경우는 자동이 안 잡히고
   수동 셔터로 간다(그래서 수동이 항상 열려 있어야 한다). */
function brightSpan(gray, width, y, x0, x1, threshold) {
  let first = -1, last = -1;
  for (let x = x0; x < x1; x++) {
    if (gray[y * width + x] >= threshold) { if (first < 0) first = x; last = x; }
  }
  return first < 0 ? null : { first, last };
}

export function scoreFrame(gray, width, height, opts = {}) {
  const { fillMin, tiltMax, sharpMin } = { ...DEFAULTS, ...opts };
  const g = guideRectInFrame(width, height);

  // 임계는 가이드 안 밝기 분포에서 잡는다(조명이 달라도 따라간다).
  let min = 255, max = 0, sum = 0, n = 0;
  for (let y = g.y; y < g.y + g.h; y++) {
    for (let x = g.x; x < g.x + g.w; x++) {
      const v = gray[y * width + x];
      if (v < min) min = v;
      if (v > max) max = v;
      sum += v; n++;
    }
  }
  const threshold = (min + max) / 2;
  const contrast = max - min;

  let bright = 0;
  for (let y = g.y; y < g.y + g.h; y++) {
    for (let x = g.x; x < g.x + g.w; x++) if (gray[y * width + x] >= threshold) bright++;
  }
  const fill = n ? bright / n : 0;

  // 위/아래 경계의 x 시작점이 얼마나 어긋나는지 = 기울기
  const top = brightSpan(gray, width, g.y + Math.round(g.h * 0.1), g.x, g.x + g.w, threshold);
  const bottom = brightSpan(gray, width, g.y + Math.round(g.h * 0.9), g.x, g.x + g.w, threshold);
  const tilt = top && bottom ? Math.abs(top.first - bottom.first) / g.w : 1;

  const sharp = contrast;
  const ok = fill >= fillMin && tilt <= tiltMax && sharp >= sharpMin;
  return { fill, tilt, sharp, ok };
}

/* 연속 N프레임 ok 여야 찍는다. 손이 지나가거나 순간적으로 맞은 프레임 하나로
   찍히면 흐린 사진이 남는다. */
export function createShutterGate({ needed = 5 } = {}) {
  let streak = 0;
  return {
    push(score) {
      streak = score?.ok ? streak + 1 : 0;
      return streak >= needed;
    },
    reset() { streak = 0; },
  };
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: fail 0

임계값(`DEFAULTS`)이 합성 프레임에서 안 맞으면 **테스트의 합성 프레임이 실제와 얼마나 닮았는지 먼저 의심하고**, 임계를 테스트에 맞추지 말 것. 임계를 바꿔야 한다면 왜 바꾸는지 주석과 보고에 남긴다.

- [ ] **Step 5: mutation 확인**

`createShutterGate` 의 `streak = score?.ok ? streak + 1 : 0` 을 `streak + 1`(항상 증가)로 바꾸면 "중간에 끊기면 다시 센다" 테스트가 실패해야 한다. 되돌린다.

- [ ] **Step 6: 커밋**

```bash
git add src/features/model/idCardDetector.js tests/frontend/id-card-detector.test.mjs
git commit -m "$(cat <<'EOF'
feat(facemarket): 자동 셔터 판정기 — 라이브러리 없이

카드가 가이드에 잘 맞았는지(채움·기울기·선명도)를 프레임 한 장에서 판정하고,
연속 N프레임 유지될 때만 찍는다 — 손이 지나가는 순간 찍히면 안 된다.

OpenCV.js(~8MB)를 끌어오지 않는다. 가이드 안쪽 밝기 분포만 보면 세 조건이
다 나오고, 임계도 분포에서 잡아 조명 변화를 따라간다.

자동 셔터는 편의이지 관문이 아니다 — 안 잡혀도 수동으로 찍을 수 있어야 하므로
임계는 "확실할 때만"으로 보수적으로 둔다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 3: 카메라 촬영 컴포넌트

**Files:**
- Create: `src/features/model/IdCameraCapture.jsx`
- Test: `tests/frontend/id-camera-capture.test.mjs`
- Modify: `src/features/model/ModelRegister.module.css` (가이드 오버레이 스타일)

**Interfaces:**
- Consumes: Task 1 `guideRectInFrame`, Task 2 `scoreFrame`/`createShutterGate`
- Produces: `<IdCameraCapture onCaptured={(blob) => void} onUnavailable={(reason) => void} busy={bool} />`
  - `onCaptured` 는 **마스킹 전** JPEG blob 과 촬영 당시 프레임 크기를 준다: `{ blob, width, height }`
  - `onUnavailable` 은 카메라를 못 쓸 때(권한 거부·미지원) 호출 — 부모가 파일 선택 폴백으로 내려준다

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/frontend/id-camera-capture.test.mjs`:

```js
/* 카메라 촬영 컴포넌트. 렌더 하네스(facemarket-id-capture.test.mjs 의 stepHarness 와
   같은 방식)로 트리를 그려 확인한다 — 실제 카메라 없이 분기만 본다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
  fileURLToPath(new URL('../../src/features/model/IdCameraCapture.jsx', import.meta.url)), 'utf8');

test('후면 카메라를 요청한다', () => {
  assert.match(source, /facingMode/, '후면 카메라를 지정해야 신분증을 찍는다');
  assert.match(source, /environment/);
});

test('수동 셔터가 항상 있다 — 자동은 편의이지 관문이 아니다', () => {
  assert.match(source, /수동|직접 찍기|촬영/, '수동 촬영 버튼이 있어야 한다');
  assert.ok(!/disabled=\{[^}]*autoReady/.test(source),
    '자동 판정이 수동 셔터를 막으면 자동이 안 잡히는 사용자가 갇힌다');
});

test('카메라를 못 쓰면 부모에게 알린다 (폴백 경로)', () => {
  assert.match(source, /onUnavailable/, '권한 거부·미지원 시 파일 선택으로 내려갈 수 있어야 한다');
});

test('촬영 결과는 JPEG blob 이다 — 원본 프레임을 넘기지 않는다', () => {
  assert.match(source, /toBlob\(/, '캔버스에서 blob 을 뽑아야 한다');
  assert.match(source, /image\/jpeg/);
  assert.ok(!/onCaptured\(\s*(?:stream|video|frame)\b/.test(source),
    '원본 스트림/비디오 엘리먼트를 그대로 넘기면 안 된다');
});

test('스트림을 반드시 정리한다', () => {
  assert.match(source, /getTracks\(\)[\s\S]{0,80}stop\(\)/,
    '언마운트에서 트랙을 멈추지 않으면 카메라 표시등이 계속 켜져 있다');
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: 파일 없음으로 실패

- [ ] **Step 3: 구현**

`src/features/model/IdCameraCapture.jsx` 의 뼈대:

```jsx
/* 신분증 가이드 촬영.

   파일 선택을 카메라로 바꾸는 이유는 둘이다. 첫째, 맥 사진앱 HEIC 처럼 브라우저가
   못 그리는 포맷이 구조적으로 사라진다(우리가 canvas.toBlob 으로 만든다). 둘째,
   카드가 가이드를 채우면 주민등록번호 위치가 규격으로 계산돼 사용자가 박스를
   끌 필요가 없어진다(설계 §2).

   자동 셔터는 편의이지 관문이 아니다 — 조명·배경에 따라 안 잡힐 수 있으므로
   수동 셔터를 항상 노출하고, 15초가 지나면 그렇게 안내한다(설계 §6). */
import { useCallback, useEffect, useRef, useState } from 'react';
import { guideRectInFrame } from './idCardGeometry.js';
import { createShutterGate, scoreFrame } from './idCardDetector.js';
import s from './ModelRegister.module.css';

const SAMPLE_WIDTH = 320;     // 판정용 다운스케일 — 전체 해상도로 돌릴 이유가 없다
const HINT_AFTER_MS = 15000;

export default function IdCameraCapture({ onCaptured, onUnavailable, busy }) {
  const videoRef = useRef(null);
  const sampleRef = useRef(null);   // 판정용 작은 캔버스
  const shotRef = useRef(null);     // 촬영용 전체 해상도 캔버스
  const streamRef = useRef(null);
  const gateRef = useRef(createShutterGate({ needed: 5 }));
  const [ready, setReady] = useState(false);
  const [showManualHint, setShowManualHint] = useState(false);

  // 카메라 시작 + 언마운트에서 트랙 정지(안 하면 카메라 표시등이 계속 켜져 있다)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 } },
          audio: false,
        });
        if (cancelled) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        if (videoRef.current) { videoRef.current.srcObject = stream; await videoRef.current.play(); }
        setReady(true);
      } catch (error) {
        onUnavailable?.(error?.name === 'NotAllowedError' ? 'permission' : 'unsupported');
      }
    })();
    return () => {
      cancelled = true;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, [onUnavailable]);

  const capture = useCallback(() => { /* 전체 해상도 캔버스에 그리고 toBlob('image/jpeg') → onCaptured({blob,width,height}) */ }, [onCaptured]);

  // ~10fps 판정 루프: video → 작은 캔버스 → 그레이스케일 → scoreFrame → gate
  useEffect(() => { /* setInterval 100ms, ready 일 때만, gate.push(score) 가 true 면 capture() */ }, [ready, capture]);

  useEffect(() => {
    const timer = setTimeout(() => setShowManualHint(true), HINT_AFTER_MS);
    return () => clearTimeout(timer);
  }, []);

  return (
    <div className={s.idCameraStage}>
      <video ref={videoRef} playsInline muted className={s.idCameraVideo} />
      <div className={s.idCameraGuide} aria-hidden="true" />
      <canvas ref={sampleRef} width={SAMPLE_WIDTH} hidden />
      <canvas ref={shotRef} hidden />
      <p className={s.idCameraHint} role="status">
        신분증을 네모 안에 맞춰 주세요. 잘 맞으면 자동으로 찍혀요.
      </p>
      <button type="button" className={s.primary} disabled={busy} onClick={capture}>
        직접 찍기
      </button>
      {showManualHint && <p className={s.description}>잘 안 잡히면 “직접 찍기”를 눌러도 괜찮아요.</p>}
    </div>
  );
}
```

`ModelRegister.module.css` 에 `.idCameraStage`(position: relative), `.idCameraVideo`(width:100%; display:block), `.idCameraGuide`(absolute, `aspect-ratio: 1.5858`, 흰 테두리 2px dashed, 중앙 정렬), `.idCameraHint` 를 추가한다.

- [ ] **Step 4: 테스트 통과 확인 + 빌드**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)" && pnpm build 2>&1 | tail -2`
Expected: fail 0, 빌드 성공

- [ ] **Step 5: 커밋**

```bash
git add src/features/model/IdCameraCapture.jsx src/features/model/ModelRegister.module.css tests/frontend/id-camera-capture.test.mjs
git commit -m "$(cat <<'EOF'
feat(facemarket): 신분증 가이드 촬영 컴포넌트

후면 카메라에 카드 비율 가이드를 겹치고, 잘 맞으면 자동으로 찍는다.
판정은 320px 로 다운스케일해 ~10fps 로만 돌린다.

파일 선택을 대체하는 이유 둘: HEIC 처럼 브라우저가 못 그리는 포맷이 구조적으로
사라지고(우리가 canvas.toBlob 으로 만든다), 카드가 가이드를 채우면 주민등록번호
위치가 규격으로 계산돼 사용자가 박스를 끌 필요가 없어진다.

자동 셔터는 편의이지 관문이 아니다 — 수동 셔터를 항상 노출하고 15초 뒤 안내한다.
언마운트에서 트랙을 멈춘다(안 그러면 카메라 표시등이 계속 켜져 있다).

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 4: 가이드 기준 자동 마스킹

**Files:**
- Modify: `src/features/model/idDocumentMasking.js`
- Test: `tests/frontend/id-document-masking.test.mjs` (기존 파일에 추가)

**Interfaces:**
- Consumes: Task 1 `rrnRectInFrame`
- Produces: `burnGuideMask(canvas, source, width, height) -> Promise<Blob>` — 촬영 프레임을 그리고 규격 좌표의 주민번호 영역을 덮은 JPEG blob

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/frontend/id-document-masking.test.mjs` 에 추가:

```js
import { burnGuideMask } from '../../src/features/model/idDocumentMasking.js';
import { rrnRectInFrame } from '../../src/features/model/idCardGeometry.js';

test('burnGuideMask: 규격 좌표 자리에 덮고, 그린 뒤에 덮는다', async () => {
  const calls = [];
  const ctx = {
    drawImage: (...args) => calls.push({ op: 'drawImage', args }),
    fillRect: (...args) => calls.push({ op: 'fillRect', args }),
    set fillStyle(v) { calls.push({ op: 'fillStyle', value: v }); },
    get fillStyle() { return '#111'; },
  };
  const canvas = { width: 0, height: 0, getContext: () => ctx, toBlob: (cb) => cb({ type: 'image/jpeg' }) };

  const blob = await burnGuideMask(canvas, { nodeName: 'VIDEO' }, 1920, 1080);
  assert.equal(blob.type, 'image/jpeg');

  const ops = calls.filter((c) => c.op === 'drawImage' || c.op === 'fillRect').map((c) => c.op);
  assert.deepEqual(ops, ['drawImage', 'fillRect'],
    'fillRect 가 drawImage 보다 먼저면 원본이 마스크 위에 다시 그려져 주민번호가 살아난다');

  const expected = rrnRectInFrame(1920, 1080);
  const [x, y, w, h] = calls.find((c) => c.op === 'fillRect').args;
  assert.deepEqual({ x, y, w, h }, expected, '규격 좌표와 어긋나면 번호가 안 가려진다');
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: `burnGuideMask` 없음으로 실패

- [ ] **Step 3: 구현**

`src/features/model/idDocumentMasking.js` 에 추가(기존 `buildMaskedBlob` 은 **그대로 둔다** — 수동 마스킹 비상구가 쓴다):

```js
import { rrnRectInFrame } from './idCardGeometry.js';

/* 가이드 촬영용 자동 마스킹. 카드가 가이드를 채운 상태로 찍혔으므로 주민등록번호
   위치가 규격으로 계산된다 — 사용자가 박스를 끌지 않는다.

   drawImage → fillRect 순서가 뒤집히면 원본이 마스크 위에 다시 그려져 번호가
   살아난다. 순서를 테스트로 고정한다(v1 에서 그 순서를 못 잡는 테스트가 있었다). */
export function burnGuideMask(canvas, source, width, height, quality = 0.92) {
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(source, 0, 0, width, height);
  const r = rrnRectInFrame(width, height);
  ctx.fillStyle = '#111';
  ctx.fillRect(r.x, r.y, r.w, r.h);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: fail 0

- [ ] **Step 5: mutation 확인**

`ctx.fillRect` 를 `ctx.drawImage` 앞으로 옮기면 순서 단언이 실패해야 한다. 되돌린다.

- [ ] **Step 6: 커밋**

```bash
git add src/features/model/idDocumentMasking.js tests/frontend/id-document-masking.test.mjs
git commit -m "$(cat <<'EOF'
feat(facemarket): 가이드 기준 자동 마스킹

카드가 가이드를 채운 상태로 찍히므로 주민등록번호 위치가 규격으로 계산된다 —
사용자가 검은 박스를 끌 필요가 없다. v1 의 수동 드래그(buildMaskedBlob)는
비상구로 남겨 둔다.

drawImage → fillRect 순서를 테스트로 고정한다. 뒤집히면 원본이 마스크 위에
다시 그려져 번호가 살아나는데, v1 에는 그 순서를 못 잡는 테스트가 있었다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 5: 서버 마스킹 검증 (shadow)

**Files:**
- Create: `server/app/facemarket_id_mask_verify.py`
- Create: `server/tests/test_facemarket_id_mask_verify.py`
- Modify: `server/app/config.py`, `server/app/facemarket_id_document.py`

**Interfaces:**
- Consumes: 없음(cv2 는 기존 의존성)
- Produces:
  - `RRN_REGION: dict` — 프런트 `idCardGeometry.RRN_REGION` 과 **같은 값**
  - `mask_is_applied(image_bytes) -> tuple[bool, dict]` — `(덮였는가, 근거 지표)`
  - `settings.fm_id_mask_verify: str` — `off | shadow | enforce`, 기본 `shadow`

- [ ] **Step 1: 실패하는 테스트 작성**

`server/tests/test_facemarket_id_mask_verify.py`:

```python
"""업로드된 신분증 사진의 주민등록번호 자리가 실제로 덮였는지 검사.

카드가 가이드를 채우도록 찍히므로 그 자리는 규격으로 계산된다. v1 스펙 §7.2 가
인정했던 한계("서버는 마스킹을 검증할 수 없다")를 정상 경로에서 없애는 검사다.

임계는 캘리브 전이므로 shadow 로 먼저 켠다 — v1 에서 SFace 임계를 캘리브 없이
넣었다가 오탈락으로 고생한 선례가 있다.
"""
import cv2
import numpy as np
import pytest

from app.facemarket_id_mask_verify import RRN_REGION, mask_is_applied


def _card(masked: bool) -> bytes:
    """가이드를 채운 카드 한 장. masked=True 면 주민번호 자리를 단색으로 덮는다."""
    img = np.full((540, 856, 3), 200, np.uint8)          # 밝은 카드
    img[::7, :] = 120                                     # 텍스트처럼 보이는 줄무늬
    if masked:
        x = int(RRN_REGION["xr"] * 856); y = int(RRN_REGION["yr"] * 540)
        w = int(RRN_REGION["wr"] * 856); h = int(RRN_REGION["hr"] * 540)
        img[y:y + h, x:x + w] = 17                        # 단색으로 덮음
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_masked_card_passes():
    applied, metrics = mask_is_applied(_card(masked=True))
    assert applied is True, metrics


def test_unmasked_card_fails():
    applied, metrics = mask_is_applied(_card(masked=False))
    assert applied is False, metrics


def test_metrics_explain_the_verdict():
    _, metrics = mask_is_applied(_card(masked=True))
    assert "stddev" in metrics and "mean" in metrics, "판정 근거가 없으면 캘리브를 못 한다"


def test_unreadable_bytes_do_not_crash():
    applied, metrics = mask_is_applied(b"not an image")
    assert applied is False
    assert metrics.get("reason") == "decode_failed"


def test_region_matches_frontend():
    """프런트 idCardGeometry.RRN_REGION 과 같은 값이어야 한다 — 어긋나면
    클라가 덮은 자리와 서버가 보는 자리가 달라져 정상 사진이 거부된다."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/features/model/idCardGeometry.js").read_text()
    for key, value in RRN_REGION.items():
        assert f"{key}: {value}" in js, f"{key} 가 프런트와 다르다"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_id_mask_verify.py -q`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`server/app/facemarket_id_mask_verify.py`:

```python
"""신분증 사진의 주민등록번호 자리가 실제로 덮였는지 검사.

가이드 촬영이면 카드가 프레임을 채우므로 번호 자리는 규격 비율로 계산된다.
덮였다면 그 영역은 **단색**이다 — 표준편차가 거의 0 이고 평균이 어둡다.

이 검사가 v1 스펙 §7.2 의 한계("서버는 마스킹을 검증할 수 없다")를 정상 경로에서
없앤다. 다만 임계는 캘리브 전이므로 shadow 로 먼저 켠다(설계 §7).

⚠️ RRN_REGION 은 프런트 `src/features/model/idCardGeometry.js` 의 같은 이름 상수와
값이 일치해야 한다. 어긋나면 클라가 덮은 자리와 서버가 보는 자리가 달라져 정상
사진이 거부된다 — 테스트가 두 값을 대조한다.
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger("wearless.fm_id_mask")

RRN_REGION = {"xr": 0.06, "yr": 0.58, "wr": 0.62, "hr": 0.14}

# 단색으로 덮였다면 표준편차가 거의 0 이다. 임계는 캘리브 전 출발값이다.
MAX_STDDEV = 12.0
MAX_MEAN = 90.0     # 어두운 색으로 덮는다(#111)


def mask_is_applied(image_bytes: bytes) -> tuple[bool, dict]:
    try:
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:
        image = None
    if image is None:
        return False, {"reason": "decode_failed"}

    h, w = image.shape[:2]
    x0 = int(RRN_REGION["xr"] * w); y0 = int(RRN_REGION["yr"] * h)
    x1 = min(w, x0 + int(RRN_REGION["wr"] * w))
    y1 = min(h, y0 + int(RRN_REGION["hr"] * h))
    if x1 <= x0 or y1 <= y0:
        return False, {"reason": "region_empty"}

    region = image[y0:y1, x0:x1]
    mean = float(region.mean())
    stddev = float(region.std())
    applied = stddev <= MAX_STDDEV and mean <= MAX_MEAN
    return applied, {"mean": round(mean, 2), "stddev": round(stddev, 2)}
```

`server/app/config.py` 에 필드와 파서 추가:

```python
    # 신분증 마스킹 기하 검증. off=검사 안 함 / shadow=판정만 기록 / enforce=미달 거부.
    # 임계 캘리브 전에는 shadow — v1 에서 SFace 임계를 캘리브 없이 넣었다가 오탈락으로
    # 고생했다. job_events/로그로 분포를 본 뒤 enforce 로 올린다.
    fm_id_mask_verify: str = "shadow"  # off | shadow | enforce
```
```python
        fm_id_mask_verify=_flag("FM_ID_MASK_VERIFY", "shadow", {"off", "shadow", "enforce"}),
```

`server/app/facemarket_id_document.py` 의 업로드 경로에서 얼굴 크롭 검사 **뒤에** 호출하고, `enforce` 일 때만 거부한다. 판정은 항상 로그로 남긴다(캘리브 근거).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/ -q --ignore=tests/test_personalization.py 2>&1 | tail -3`
Expected: fail 0

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_id_mask_verify.py server/app/config.py server/app/facemarket_id_document.py server/tests/test_facemarket_id_mask_verify.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 서버가 신분증 마스킹을 검증한다 (shadow)

가이드 촬영이면 카드가 프레임을 채우므로 주민등록번호 자리가 규격으로 계산된다.
덮였다면 그 영역은 단색이다 — 표준편차가 거의 0 이고 평균이 어둡다.

이 검사가 v1 스펙 §7.2 의 한계("서버는 마스킹 여부를 검증할 수 없다")를 정상
경로에서 없앤다. 새 의존성은 없다(cv2 는 SFace/YuNet 용으로 이미 있다).

임계는 캘리브 전이라 shadow 로 시작한다. v1 에서 SFace 임계를 캘리브 없이 넣었다가
오탈락으로 고생한 선례가 있다 — 분포를 보고 enforce 로 올린다.

RRN_REGION 은 프런트 idCardGeometry.js 와 값이 같아야 하고, 테스트가 두 파일을
대조한다. 어긋나면 클라가 덮은 자리와 서버가 보는 자리가 달라진다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 6: 순서 뒤집기 (간편인증 → 신분증 촬영)

**Files:**
- Modify: `server/app/facemarket_enrollment.py` (`create_enrollment`, `verify_enrollment_identity`)
- Test: `server/tests/test_facemarket_identity_method.py` (기존 파일에 추가)

**Interfaces:**
- Consumes: 기존 상태값 `identity_pending`, `id_capture_pending`
- Produces: 없음(상태 전이 순서만 변경)

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_simple_auth_now_starts_at_identity_pending(enrollment_client_factory):
    """v2: 두 경로가 같은 자리에서 시작한다. 간편인증이 먼저고 촬영이 나중이다 —
    촬영 시점에 인증사가 검증한 이름·생년월일·CI 를 이미 쥐고 있어야, 카드가
    '정보를 읽는 대상'이 아니라 '그 사람 것인가'를 확인하는 물증이 된다."""
    client, store, _ = enrollment_client_factory(fm_identity_methods=("mid", "simple_auth"))
    response = client.post("/v1/facemarket/enrollments", json={
        "deviceId": "d" * 40,
        "biometricConsent": {"accepted": True, "documentVersion": "2026-09-v1"},
        "identityMethod": "simple_auth",
    })
    assert response.status_code == 201
    assert response.json()["status"] == "identity_pending"


def test_simple_auth_identity_success_moves_to_id_capture(enrollment_client_factory, monkeypatch):
    client, store, _ = enrollment_client_factory(
        fm_identity_methods=("mid", "simple_auth"),
        fm_oacx_simple_auth_contract="simple-auth-v1",
    )
    store.add_enrollment(status="identity_pending", identity_method="simple_auth")

    async def fake_fetch(base_url, token):
        return {"ci": "CI-1", "name": "홍길동", "birthday": "19900101", "txId": "t1"}
    monkeypatch.setattr(cx_identity, "fetch_trans", fake_fetch)

    response = client.post(f"/v1/facemarket/enrollments/{store.latest_id}/identity", json={"token": "tok"})
    assert response.status_code == 200
    assert response.json()["status"] == "id_capture_pending", "간편인증 뒤에 촬영이 온다"


def test_mid_identity_success_still_goes_to_photos(enrollment_client_factory, monkeypatch):
    """mid 경로는 그대로 — 촬영 단계가 없다."""
    client, store, _ = enrollment_client_factory(fm_identity_methods=("mid",))
    store.add_enrollment(status="identity_pending", identity_method="mid")

    async def fake_fetch(base_url, token):
        return {"ci": "CI-2", "name": "김철수", "birth": "19900101", "txId": "t2"}
    monkeypatch.setattr(cx_identity, "fetch_trans", fake_fetch)

    response = client.post(f"/v1/facemarket/enrollments/{store.latest_id}/identity", json={"token": "tok"})
    assert response.json()["status"] == "photos_pending"
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/test_facemarket_identity_method.py -q`
Expected: 첫 테스트가 `id_capture_pending` 을 받아 실패

- [ ] **Step 3: 구현**

`create_enrollment`: `initial_status` 를 메서드와 무관하게 `"identity_pending"` 으로 바꾼다.

`verify_enrollment_identity` 의 상태 전이 UPDATE 에서 다음 상태를 메서드로 가른다:

```python
next_status = "id_capture_pending" if method == "simple_auth" else "photos_pending"
```

`facemarket_id_document.py` 의 업로드 전이는 `id_capture_pending → photos_pending` 으로 바꾼다(기존에는 `→ identity_pending`).

- [ ] **Step 4: 테스트 통과 + 회귀 확인**

Run: `cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/ -q --ignore=tests/test_personalization.py 2>&1 | tail -3`
Expected: fail 0

- [ ] **Step 5: 커밋**

```bash
git add server/app/facemarket_enrollment.py server/app/facemarket_id_document.py server/tests/test_facemarket_identity_method.py
git commit -m "$(cat <<'EOF'
feat(facemarket): 간편인증 다음에 신분증을 찍는다 (순서 뒤집기)

촬영 시점에 인증사가 검증한 이름·생년월일·CI 를 이미 쥐고 있게 된다. 그래야
카드가 "정보를 읽는 대상"이 아니라 "방금 인증된 그 사람 것인가"를 확인하는
물증이 되고, 관리자 심사가 명확해진다.

상태 머신도 단순해진다 — 두 경로가 같은 자리(identity_pending)에서 시작하고
간편인증만 그 뒤에 id_capture_pending 을 거친다. 상태값 자체는 그대로라
마이그레이션이 없다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 7: IdDocumentStep 재구성 (카메라 우선 · 수동 비상구)

**Files:**
- Modify: `src/features/model/IdDocumentStep.jsx`
- Test: `tests/frontend/facemarket-id-capture.test.mjs` (기존 파일에 추가)

**Interfaces:**
- Consumes: Task 3 `IdCameraCapture`, Task 4 `burnGuideMask`, 기존 `buildMaskedBlob`(수동)
- Produces: 없음(부모 계약 `{ enrollmentId, onUploaded, onError, onStale }` 유지)

- [ ] **Step 1: 실패하는 테스트 작성**

```js
test('기본은 카메라 촬영이다', () => {
  const source = readFileSync(fileURLToPath(new URL('../../src/features/model/IdDocumentStep.jsx', import.meta.url)), 'utf8');
  assert.match(source, /IdCameraCapture/, '카메라가 기본 경로여야 한다');
  assert.match(source, /burnGuideMask/, '가이드 기준 자동 마스킹을 써야 한다');
});

test('카메라를 못 쓰면 파일 선택으로 내려간다', () => {
  const source = readFileSync(fileURLToPath(new URL('../../src/features/model/IdDocumentStep.jsx', import.meta.url)), 'utf8');
  assert.match(source, /onUnavailable/, '권한 거부·미지원 시 폴백이 있어야 한다');
  assert.match(source, /image\/jpeg,image\/png,image\/webp|ACCEPT_ATTR/, '폴백도 형식을 좁혀야 한다');
});

test('기하 검증 실패가 반복되면 수동 마스킹으로 내려준다', () => {
  const source = readFileSync(fileURLToPath(new URL('../../src/features/model/IdDocumentStep.jsx', import.meta.url)), 'utf8');
  assert.match(source, /MANUAL_MASK_AFTER|manualFallback/, '연속 실패 횟수로 비상구를 열어야 한다');
  assert.match(source, /buildMaskedBlob/, '수동 마스킹 경로(v1)를 버리지 않는다');
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`
Expected: 3건 실패

- [ ] **Step 3: 구현**

`IdDocumentStep.jsx` 를 다음 모드 머신으로 재구성한다(상수 `MANUAL_MASK_AFTER = 3`):

| 모드 | 화면 | 진입 조건 |
| --- | --- | --- |
| `camera` | `<IdCameraCapture>` | 기본 |
| `file` | 기존 파일 선택 + 미리보기 | `onUnavailable` |
| `manual` | 기존 드래그 마스킹 UI | 업로드가 `id_mask_not_applied` 로 `MANUAL_MASK_AFTER` 회 연속 거부 |

`camera` 에서 촬영하면 `burnGuideMask` 로 마스킹해 바로 업로드한다(미리보기·확인 체크 없음 — 자동이라 확인할 게 없다). `file`·`manual` 은 v1 흐름(확인 체크 포함)을 그대로 쓴다.

- [ ] **Step 4: 테스트 통과 + 빌드**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)" && pnpm build 2>&1 | tail -2`

- [ ] **Step 5: 커밋**

```bash
git add src/features/model/IdDocumentStep.jsx tests/frontend/facemarket-id-capture.test.mjs
git commit -m "$(cat <<'EOF'
feat(facemarket): 신분증 촬영을 카메라 우선으로 재구성

기본은 가이드 촬영이고, 마스킹은 규격 좌표로 자동이라 확인 체크가 필요 없다.
카메라를 못 쓰면(권한 거부·미지원) 파일 선택으로, 기하 검증이 연속 3회 실패하면
v1 의 수동 드래그 마스킹으로 내려준다.

비상구를 세 겹 두는 이유: v1 리뷰에서 409 가 사용자를 촬영 단계에 가두는
문제(I7)를 이미 한 번 겪었다. 자동은 편의이지 관문이 아니다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 8: 모바일 게이트 + QR

**Files:**
- Modify: `src/features/model/IdentityMethodStep.jsx`, `src/features/model/identityMethodConfig.js`
- Test: `tests/frontend/facemarket-id-capture.test.mjs` (기존 파일에 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `isMobileLike() -> boolean` (in `identityMethodConfig.js`)

- [ ] **Step 1: 실패하는 테스트 작성**

```js
test('데스크톱에서는 간편인증 버튼을 막고 이유를 보여준다', () => {
  const source = readFileSync(fileURLToPath(new URL('../../src/features/model/IdentityMethodStep.jsx', import.meta.url)), 'utf8');
  assert.match(source, /휴대폰|폰에서/, '왜 막혔는지 사용자가 알아야 한다');
});

test('모바일 판별에 User-Agent 를 쓰지 않는다', () => {
  const config = readFileSync(fileURLToPath(new URL('../../src/features/model/identityMethodConfig.js', import.meta.url)), 'utf8');
  assert.ok(!/userAgent/i.test(config),
    'UA 는 위조가 쉽고 오판이 잦다 — 입력 기기 특성(pointer/hover)으로 본다');
  assert.match(config, /matchMedia|pointer/, '포인터 특성으로 판별해야 한다');
});
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | grep -E "^ℹ (pass|fail)"`

- [ ] **Step 3: 구현**

`identityMethodConfig.js` 에 추가:

```js
/* 모바일 판별. User-Agent 는 쓰지 않는다 — 위조가 쉽고 데스크톱 브라우저의
   모바일 모드·태블릿에서 오판이 잦다. 우리가 실제로 필요한 건 "카메라로 신분증을
   찍을 수 있는 기기인가"이고, 그건 입력 방식(거친 포인터 = 손가락)이 더 잘 말해준다.

   이건 안내용 힌트일 뿐이다 — 진짜 통제는 서버의 기하 검증이다(설계 §5). */
export function isMobileLike() {
  if (typeof window === 'undefined' || !window.matchMedia) return true;   // SSR·미지원은 막지 않는다
  return window.matchMedia('(pointer: coarse)').matches;
}
```

`IdentityMethodStep.jsx` 에서 `!isMobileLike()` 이면 간편인증 버튼을 비활성화하고 힌트를 *"간편인증은 휴대폰에서 진행해 주세요. 폰에서 같은 계정으로 접속하면 여기서부터 이어져요."* 로 바꾼다. QR 은 현재 주소를 담은 `<img>` 를 외부 라이브러리 없이 넣기 어려우므로 **v1 에서는 문구 안내만** 하고, QR 은 별도 작업으로 남긴다(의존성 0 제약).

- [ ] **Step 4: 테스트 통과 + 빌드**

- [ ] **Step 5: 커밋**

```bash
git add src/features/model/IdentityMethodStep.jsx src/features/model/identityMethodConfig.js tests/frontend/facemarket-id-capture.test.mjs
git commit -m "$(cat <<'EOF'
feat(facemarket): 간편인증을 휴대폰에서만 시작하게 안내한다

간편인증 승인은 어차피 폰 앱으로 온다. PC 로 하면 두 기기를 오가고, 신분증
촬영 때문에 또 폰으로 넘어가야 한다 — 처음부터 폰이면 한 기기에서 끝난다.

판별에 User-Agent 를 쓰지 않는다. 위조가 쉽고 오판이 잦다. 우리가 필요한 건
"카메라로 신분증을 찍을 수 있는 기기인가"이고 포인터 특성이 더 잘 말해준다.
그리고 이건 안내용 힌트일 뿐이다 — 진짜 통제는 서버의 기하 검증이다.

이어받기는 기존 GET /enrollments/current 가 이미 한다. 폰에서 같은 계정으로
들어오면 등록이 그대로 이어지므로 토큰을 URL 에 싣는 핸드오프를 만들지 않는다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## Task 9: 관리자 카드 배치 + 수동 마스킹 배지

**Files:**
- Modify: `src/features/admin/AdminEnrollmentReview.jsx`, `server/app/facemarket_admin_review.py`
- Test: `tests/frontend/admin-enrollment-review.test.mjs`, `server/tests/test_facemarket_admin_review.py`

**Interfaces:**
- Consumes: Task 7 의 수동 마스킹 사용 여부
- Produces: 카드 응답에 `maskMode: "auto" | "manual"`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_review_card_exposes_mask_mode(admin_client):
    client, store = admin_client(is_admin=True)
    store.add_enrollment(status="review_pending", review_status="pending",
                         identity_method="simple_auth", mask_mode="manual")
    card = client.get(f"/v1/facemarket/admin/enrollments/{store.latest_id}").json()
    assert card["maskMode"] == "manual", "수동 마스킹 건은 관리자가 더 꼼꼼히 봐야 한다"
```

```js
test('수동 마스킹 건에 배지가 붙는다', () => {
  const source = readFileSync(fileURLToPath(new URL('../../src/features/admin/AdminEnrollmentReview.jsx', import.meta.url)), 'utf8');
  assert.match(source, /maskMode/, '기하 검증을 못 거친 건을 구분해야 한다');
  assert.match(source, /수동 마스킹/);
});

test('인증된 신원이 카드 사진 옆에 온다', () => {
  const source = readFileSync(fileURLToPath(new URL('../../src/features/admin/AdminEnrollmentReview.jsx', import.meta.url)), 'utf8');
  assert.match(source, /idDocumentWithIdentity|identityBeside/, '대조가 한눈에 되게 붙여 놔야 한다');
});
```

- [ ] **Step 2~5**: 위 테스트를 통과시키는 최소 구현 → 확인 → 커밋. DB 는 `fm_biometric_enrollments.mask_mode text` 컬럼을 추가하는 additive 마이그레이션 한 장(`supabase/migrations/<timestamp>_facemarket_id_mask_mode.sql`)이 필요하다. **타임스탬프는 만들기 전에 `ls supabase/migrations/ | tail -3` 으로 충돌을 확인한다** — v1 에서 두 번 충돌했다.

---

## Task 10: 플래그 배선 + 전체 회귀

**Files:**
- Modify: `copilot/api/manifest.yml`, `.env.example`, `docs/ARCHITECTURE.md`

- [ ] **Step 1: manifest 에 플래그 추가**

```yaml
  # 신분증 마스킹 기하 검증. off=검사 안 함 / shadow=판정만 기록 / enforce=미달 거부.
  # 임계 캘리브 전이라 shadow 로 시작한다 — 로그의 mean/stddev 분포를 보고 올린다.
  FM_ID_MASK_VERIFY: "shadow"
```

- [ ] **Step 2: 전체 회귀 (게이트)**

```
cd ~/devs/wearless_studio-id-capture/server && ~/devs/wearless_studio/server/.venv/bin/python -m pytest tests/ -q --ignore=tests/test_personalization.py 2>&1 | tail -3
cd ~/devs/wearless_studio-id-capture && pnpm test:frontend 2>&1 | tail -8
cd ~/devs/wearless_studio-id-capture && pnpm build 2>&1 | tail -3
```
세 개 모두 실패 0 이어야 한다.

- [ ] **Step 3: mid 경로 불변 확인 후 보고**

플래그 기본값에서 mid 등록을 코드로 따라가, 이 플랜 이전과 달라진 게 있으면 **아무리 작아도** 이름을 댄다. 이 브랜치가 기대는 유일한 주장이다.

- [ ] **Step 4: 커밋**

```bash
git add copilot/api/manifest.yml .env.example docs/ARCHITECTURE.md
git commit -m "$(cat <<'EOF'
chore(facemarket): 마스킹 기하 검증 플래그 (shadow)

임계 캘리브 전이라 shadow 로 시작한다. 로그의 mean/stddev 분포를 보고
enforce 로 올린다 — v1 에서 SFace 임계를 캘리브 없이 넣었다가 오탈락으로
고생한 선례가 있다.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_012y1B2hq8oER1RAey5TC8Cp
EOF
)"
```

---

## 계획 자체 점검 결과

**스펙 커버리지**: §3 결정 1→Task 8, 결정 2→Task 6, 결정 3→Task 2·3, 결정 4(OCR 제외)→비범위 유지, 결정 5→Task 7. §4 흐름→Task 6. §5 게이트→Task 8. §6 촬영→Task 3. §7 마스킹·검증→Task 4·5. §8 관리자→Task 9. §9 비범위 준수. §10 위험→shadow(Task 5·10), 비상구(Task 7).

**타입 일관성**: `RRN_REGION` 이 프런트(Task 1)와 서버(Task 5) 양쪽에 있고 Task 5 의 테스트가 두 파일을 대조한다. `guideRectInFrame` 은 Task 1 이 만들고 Task 2·3 이 쓴다. `burnGuideMask` 는 Task 4 가 만들고 Task 7 이 쓴다.

**알려진 축소**: QR 코드는 의존성 0 제약 때문에 Task 8 에서 문구 안내로 대체했다. 필요하면 별도 작업으로 뺀다.
