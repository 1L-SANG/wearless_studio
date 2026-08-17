/* 분석이 **셀러가 방금 올린 사진을 갈아치우지 못하게** 한다 (2026-08-17 사고의 진짜 원인).

   비로그인 입력·분석 구간은 서버 projectId 가 없어서 상품·분석 저장이 mock 어댑터의 메모리
   싱글톤으로 간다(api/index.js PUBLIC_INPUT). 그런데

     ① 분석 화면의 색상 편집은 `{colors}` 를 그 싱글톤에 저장한다 (mock saveAnalysis 의
        Object.assign(DB.analysis, rest))
     ② http 모드의 "새 제작"은 그 싱글톤을 비우지 않았다 (useAppStore.beginProject)
     ③ 다음 공개 분석은 **싱글톤에 병합된 값**을 응답으로 돌려준다 (analyzePublicDraft)
     ④ 응답의 colors 는 productPatch 로 갈라져 새 상품 뒤에 spread 된다 (submit)

   결과: 두 번째 제작에서 방금 올린 Front/Back 이 **직전 제작의 colors 로 교체**된다. 사고
   당시 실패한 프로젝트의 분석 이름이 직전 옷("골지 배색 레이어드 절개 반팔티")이었던 것도
   같은 경로다. 셀러에게는 아무 경고도 없다.

   여기서 고정하는 계약:
     - 분석 **응답**은 사진(colors)을 절대 바꾸지 못한다. AG-01 은 colors 를 산출하지 않는다;
       그 필드는 셀러 편집으로만 온다.
     - 공개 분석의 반환값은 이번 원격 결과에서 나온다 — 로컬 저장소에서 가져오는 것은 mock
       추천기가 채운 매칭 후보뿐이다.
     - 새 제작은 게스트 로컬 저장소를 비운다(http 모드 포함).
*/

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { analysisResponseProductPatch } from '../../src/features/product-input/saveRouting.js';
import { analyzePublicDraft } from '../../src/lib/api/publicAnalysis.js';
import { withUploadedSrcs } from '../../src/lib/draftPromotionProduct.js';
import { restoreDraftProduct } from '../../src/features/product-input/draftProductRestore.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const PLACEHOLDER = 'data:image/svg+xml;utf8,%3Csvg%3E%3C%2Fsvg%3E';

// ── 분석 응답은 사진을 건드리지 못한다 ────────────────────────────────────────

test('분석 응답에서 유도한 상품 패치에는 colors 가 없다', () => {
  const response = {
    suggestedName: '반팔 티셔츠',
    clothingType: 'top',
    measurements: [{ key: 'chest' }],
    measurementsUnknown: false,
    // 이전 제작의 잔여값 — 이게 새 상품에 spread 되면 방금 올린 사진이 사라진다.
    colors: [{ id: 'col1', name: '퍼플', images: [{ id: 'old', src: PLACEHOLDER }] }],
    fit: 'regular',
  };
  const patch = analysisResponseProductPatch(response);
  assert.equal('colors' in patch, false, '응답이 사진을 갈아치우면 안 된다');
  assert.equal(patch.clothingType, 'top');
  assert.deepEqual(patch.measurements, [{ key: 'chest' }]);
  assert.equal(patch.measurementsUnknown, false);
  assert.equal('fit' in patch, false, 'fit 은 분석 소유 필드다');
});

test('submit 과 진행 중 분석 복구 둘 다 그 패치를 쓴다', () => {
  const src = read('../../src/features/product-input/ProductInput.jsx');
  const imports = src.slice(0, src.indexOf('draftSlot.configure'));
  assert.match(imports, /\banalysisResponseProductPatch\b/,
    '호출하는 가드를 ProductInput 이 실제로 import 해야 한다');
  assert.equal(
    (src.match(/analysisResponseProductPatch\(/g) || []).length, 2,
    '두 분석 경로(최초 submit·새로고침 복구)가 같은 가드를 지나야 한다',
  );
  assert.equal(src.includes('splitAnalysisEditPatch(a).productPatch'), false,
    '가드 없는 옛 경로가 남아 있으면 안 된다');
});

// ── 공개 분석 반환값은 이번 결과에서 나온다 ──────────────────────────────────

test('공개 분석은 로컬 저장소의 잔여 필드를 응답에 섞지 않는다', async () => {
  const remote = {
    async publicAnalyze() {
      return { suggestedName: '새 상품', clothingType: 'top', matchClothing: [{ id: 'fresh' }] };
    },
  };
  const local = {
    async saveAnalysis(_projectId, analysis) {
      // mock 싱글톤: 이전 제작의 colors·이름이 남아 있고, 추천기가 후보를 채워 넣는다.
      return {
        ...analysis,
        suggestedName: '직전 제작의 옷',
        colors: [{ id: 'col1', images: [{ id: 'old', src: PLACEHOLDER }] }],
        matchClothing: [{ id: 'fresh', selected: true }],
      };
    },
  };
  const out = await analyzePublicDraft({ colors: [] }, {}, { remote, local });
  assert.equal(out.suggestedName, '새 상품', '이번 분석 결과가 이겨야 한다');
  assert.equal('colors' in out, false, '이전 제작의 사진이 응답에 실리면 안 된다');
  // 매칭 후보는 로컬 추천기가 채운 값이 정본이다(그게 이 저장 호출의 목적).
  assert.deepEqual(out.matchClothing, [{ id: 'fresh', selected: true }]);
});

test('로컬 저장이 실패하거나 비어도 원격 결과는 그대로 쓴다', async () => {
  const remote = { async publicAnalyze() { return { suggestedName: 'A', matchClothing: [{ id: 'm' }] }; } };
  const out = await analyzePublicDraft({}, {}, {
    remote, local: { async saveAnalysis() { return null; } },
  });
  assert.equal(out.suggestedName, 'A');
  assert.deepEqual(out.matchClothing, [{ id: 'm' }]);
});

// ── 새 제작은 게스트 로컬 저장소를 비운다 ────────────────────────────────────

test('새 제작은 http 모드에서도 게스트 로컬 저장소를 초기화한다', () => {
  const store = read('../../src/store/useAppStore.js');
  assert.match(store, /await api\.resetInputDraft\(\)/);
  assert.equal(/if \(mode !== 'http'\) await api\.resetInputDraft\(\)/.test(store), false,
    'http 모드에서 건너뛰면 직전 제작의 분석이 다음 분석에 섞인다');
  const api = read('../../src/lib/api/index.js');
  assert.match(api, /CLIENT_ONLY = \[[^\]]*'resetInputDraft'/,
    'http 모드에서 부를 수 있어야 한다(미구현 가드가 throw 한다)');
});

// ── 복원: 저장된 mime 도 본다 ────────────────────────────────────────────────

test('업로드할 수 없는 mime 으로 저장된 사진은 복원하지 않는다', () => {
  // 배포 전 옛 번들이 저장한 draft — data: 플레이스홀더가 blob 으로 담겨 있다.
  const draft = {
    product: {
      colors: [{
        id: 'col1',
        images: [
          { id: 'svg', slot: 'Front', src: PLACEHOLDER },
          { id: 'real', slot: 'Back', src: 'blob:old' },
        ],
      }],
    },
    photos: [
      { imageId: 'svg', blob: 'SVG', mime: 'image/svg+xml' },
      { imageId: 'real', blob: 'JPEG', mime: 'image/jpeg' },
    ],
  };
  const restored = restoreDraftProduct(draft, { createObjectUrl: () => 'blob:fresh' });
  assert.deepEqual(restored.colors[0].images.map((im) => im.id), ['real']);
});

test('src 없이 asset id 만 있는 사진은 복원에서도 유지된다', () => {
  const draft = {
    product: { colors: [{ id: 'col1', images: [{ id: 'asset-1', slot: 'Front' }] }] },
    photos: [],
  };
  const restored = restoreDraftProduct(draft, { createObjectUrl: () => 'blob:x' });
  assert.deepEqual(restored.colors[0].images, [{ id: 'asset-1', slot: 'Front' }],
    '계약상 유효한 서버 자산 참조를 지우면 사진이 사라진다');
});

// ── 승격: 올리지 못한 사진은 상품에도 남기지 않는다 ──────────────────────────

test('업로드되지 않은 로컬 사진은 서버 상품에 저장하지 않는다', () => {
  const product = {
    colors: [{
      id: 'col1',
      images: [
        { id: 'uploaded', src: 'blob:a' },
        { id: 'skipped', src: PLACEHOLDER },
        { id: 'not-uploaded', src: 'blob:b' },
        { id: 'already-on-server', src: 'https://api.wearless.kr/v1/assets/x/file' },
        { id: 'asset-only' },
      ],
    }],
  };
  const out = withUploadedSrcs(product, { uploaded: { assetId: 'A1', url: '/v1/assets/A1/file' } });
  assert.deepEqual(
    out.colors[0].images.map((im) => im.id),
    ['A1', 'already-on-server', 'asset-only'],
    '올라가지 못한 blob/데모 사진이 서버 상품에 남으면 생성이 그 id 를 asset 으로 찾다 실패한다',
  );
});
