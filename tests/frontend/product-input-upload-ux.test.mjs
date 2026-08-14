import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const productInput = readFileSync(
  new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url),
  'utf8',
);
const analysisForm = readFileSync(
  new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url),
  'utf8',
);
const featureStyles = readFileSync(
  new URL('../../src/styles/features.css', import.meta.url),
  'utf8',
);

test('파일 드롭 문지기는 capture 단계에서 파일 드래그만 막고 바깥 drop을 안내한다', () => {
  assert.match(productInput, /includes\('Files'\)/);
  assert.match(productInput, /addEventListener\('dragover', preventFileNavigation, true\)/);
  assert.match(productInput, /addEventListener\('drop', handleDocumentDrop, true\)/);
  assert.match(productInput, /사진은 점선 칸에 올려주세요/);
  assert.match(productInput, /removeEventListener\('drop', handleDocumentDrop, true\)/);
});

test('비활성 분석 버튼 이유와 선택 상품명 안내가 sticky CTA 안에 있다', () => {
  for (const reason of [
    '앞면 사진이 필요해요',
    '뒷면 사진이 필요해요',
    '앞면·뒷면 사진이 각 1장 필요해요',
  ]) assert.match(productInput, new RegExp(reason));
  assert.match(productInput, /<WizardCTA>[\s\S]*wizard-cta-reason[\s\S]*AI 분석하기[\s\S]*<\/WizardCTA>/);
  assert.match(productInput, /선택 — 비우면 AI가 지어드려요/);
});

test('새 기기 첫 진입 로딩은 빈 카드형 스켈레톤을 노출하지 않는다', () => {
  const loadingBranch = productInput.match(/if \(!product \|\| !catalogs\) return \(([\s\S]*?)\n  \);/)?.[1];
  assert.ok(loadingBranch);
  assert.match(loadingBranch, /aria-busy="true"/);
  assert.doesNotMatch(loadingBranch, /className="surface"|<Skeleton/);
});

test('특징 삭제 버튼은 시각 아이콘을 유지한 44px 터치 영역이며 편집 mousedown과 분리된다', () => {
  assert.match(featureStyles, /\.sp-chip-x[^}]*width: 44px; height: 44px;[^}]*margin: -12px;/);
  assert.match(featureStyles, /\.sp-chipwrap[^}]*gap: 24px;/);
  assert.match(analysisForm, /className="sp-chip-x"[\s\S]*onPointerDown=\{\(e\) => e\.stopPropagation\(\)\}/);
  assert.match(analysisForm, /<Icon name="x" size=\{12\}/);
});

test('상품 사진은 화면용 레지스트리 미리보기와 44px 삭제 영역을 사용한다', () => {
  assert.match(productInput, /createProductPhotoPreviewRegistry/);
  assert.match(productInput, /displayUrl\(image\.id, image\.src\)/);
  assert.match(productInput, /decoding="async"/);
  assert.match(productInput, /aria-label="내가 업로드한 의류 사진 삭제"/);
  assert.match(featureStyles, /\.tile \.rm \{ width: 44px; height: 44px;/);
});
