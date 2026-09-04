/* 모델 화면 계약 — 정지에 사유를 강제하는지, 상세가 네 블록을 다 내는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 모델 함수 네 개가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminListModels', 'adminModelDetail', 'adminSuspendModel', 'adminUnsuspendModel']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
});

test('사유가 비면 정지 버튼이 비활성이다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  assert.ok(/disabled=\{[^}]*!reason\.trim\(\)/.test(source), '빈 사유로 정지가 눌린다');
});

test('상세는 라이선스·정산·생체등록을 모두 보여준다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  for (const label of ['라이선스', '정산', '생체등록']) {
    assert.ok(source.includes(label), `상세 블록 누락: ${label}`);
  }
});

test('모델 행은 키보드만으로도 열 수 있다', () => {
  // TableRow 는 props 를 그대로 <tr> 로 흘려보낸다 — onClick 만 있으면 마우스가
  // 없는 관리자는 이 행을 절대 못 연다.
  const source = read('src/features/admin/AdminModels.jsx');
  const rowStart = source.indexOf('{items.map((m) =>');
  assert.ok(rowStart !== -1, '모델 행 렌더 블록을 못 찾았다');
  const rowEnd = source.indexOf('</TableRow>', rowStart);
  const rowBlock = source.slice(rowStart, rowEnd);

  assert.ok(/tabIndex=\{0\}/.test(rowBlock), '행에 tabIndex={0} 이 없다 — 포커스가 안 간다');
  assert.ok(/role=/.test(rowBlock), '행에 role 이 없다');
  assert.ok(
    /onKeyDown=\{[\s\S]*?e\.key === 'Enter'[\s\S]*?e\.key === ' '[\s\S]*?\}\}/.test(rowBlock)
      || /onKeyDown=\{[\s\S]*?e\.key === ' '[\s\S]*?e\.key === 'Enter'[\s\S]*?\}\}/.test(rowBlock),
    '행에 Enter·Space 를 둘 다 받는 onKeyDown 이 없다',
  );
  assert.ok(/focus-visible:ring/.test(rowBlock), '포커스된 행이 눈에 안 보인다 — 시각 포커스 스타일이 없다');
});

test('모델 목록 조회 실패는 빈 배열이 아니라 에러 상태로 남는다', () => {
  // 실패를 빈 배열로 떨어뜨리면 "모델이 없어요" 와 "요청이 실패했어요" 가 화면에서
  // 구분이 안 된다.
  const source = read('src/features/admin/AdminModels.jsx');
  const idx = source.indexOf('adminListModels({');
  assert.ok(idx !== -1, 'adminListModels 호출을 못 찾았다');
  const loadBlock = source.slice(idx, idx + 250);
  assert.ok(!/\.catch\(\(\)\s*=>\s*setItems\(\[\]\)\)/.test(loadBlock), '목록 조회 실패가 빈 배열로 위장된다');
  assert.ok(/setListError/.test(loadBlock), '목록 조회 실패를 담을 에러 상태 세터가 없다');
});

test('fm_models 가 허용하는 네 상태 모두 라벨·필터·변형을 갖고, 모르는 값은 원문자열로 낮춘다', () => {
  // fm_models_status_check 는 pending·verified·suspended·reverification_required 넷을
  // 허용한다(facemarket_admin.py MODEL_STATUSES). reverification_required 가 STATUS_LABEL/
  // STATUS_VARIANT 에 없으면 undefined → 빈 배지(내용 없이 verified 와 같은 색)로 렌더되고,
  // STATUS_FILTERS 에 없으면 그 상태만 목록에서 걸러낼 방법이 없다.
  const source = read('src/features/admin/AdminModels.jsx');

  assert.ok(
    source.includes("reverification_required: '재검증 필요'"),
    "STATUS_LABEL 에 reverification_required 라벨이 없다 — ModelHub.jsx 의 라벨('재검증 필요')과 맞춰야 한다",
  );
  assert.ok(
    /STATUS_VARIANT\s*=\s*\{[^}]*reverification_required\s*:/.test(source),
    'STATUS_VARIANT 에 reverification_required 가 없다 — 빈 배지가 verified 와 같은 색으로 보인다',
  );
  assert.ok(
    /STATUS_FILTERS\s*=\s*\[[\s\S]*?value:\s*'reverification_required'[\s\S]*?\];/.test(source),
    'STATUS_FILTERS 에 reverification_required 칩이 없다 — 재검증 필요 모델을 목록에서 걸러낼 수 없다',
  );
  // fm_models_status_check 에 다섯 번째 값이 늘어나도, 라벨 조회가 undefined 를 그대로
  // 배지에 넘기는 대신 원문자열로 낮춰야 한다 — 안 보이는 빈 배지보다 못생긴 원문자열이 낫다.
  assert.ok(
    /STATUS_LABEL\[[^\]]+\]\s*\|\|\s*[a-zA-Z.]+/.test(source),
    'STATUS_LABEL 조회에 || 폴백이 없다 — 다음에 상태가 하나 더 늘면 다시 빈 배지가 나온다',
  );
});

test('상세 패널은 실패해도 카드 틀을 그대로 그리고, 다시 시도를 준다', () => {
  // 예전엔 실패해도 data 가 계속 null 이라 패널 전체가 <Skeleton> 하나로 영원히 멈췄다
  // (카드 틀조차 없이) — 여기서는 에러 분기가 실제로 Card 로 감싸져 있고 다시 시도
  // 버튼을 갖는지 확인한다.
  const source = read('src/features/admin/AdminModels.jsx');
  assert.ok(source.includes('detailError'), '상세 패널에 에러 상태가 없다');
  const errStart = source.indexOf('if (detailError)');
  const dataStart = source.indexOf('if (!data)');
  assert.ok(errStart !== -1 && dataStart !== -1 && errStart < dataStart, '상세 패널의 에러/로딩 분기 순서를 못 찾았다');
  const errorBranch = source.slice(errStart, dataStart);
  assert.ok(/<Card>/.test(errorBranch), '에러 상태가 패널 틀(Card) 없이 그려진다');
  assert.ok(/onClick=\{load\}/.test(errorBranch), '에러 상태에 다시 시도가 없다');
});
