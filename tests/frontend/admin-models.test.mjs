/* 모델 화면 계약 — 정지에 사유를 강제하는지, 상세가 네 블록을 다 내는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

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

test('본인 중단은 운영 정지로 전환할 수 있고 관리자 정지만 해제한다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  assert.ok(source.includes("model.suspensionSource === 'owner'"), '본인 중단 판정이 없다');
  assert.ok(source.includes('운영 정지로 전환'), '본인 중단에서 관리자 정지로 바꾸는 버튼이 없다');
  assert.ok(source.includes('adminSuspended'), '관리자 정지 전용 해제 분기가 없다');
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

test('fm_models 가 허용하는 다섯 상태 모두 라벨·필터·변형을 갖고, 모르는 값은 원문자열로 낮춘다', () => {
  // fm_models_status_check 는 pending·awaiting_confirm·verified·suspended·reverification_required를
  // 허용한다(facemarket_admin.py MODEL_STATUSES). reverification_required 가 STATUS_LABEL/
  // STATUS_VARIANT 에 없으면 undefined → 빈 배지(내용 없이 verified 와 같은 색)로 렌더되고,
  // STATUS_FILTERS 에 없으면 그 상태만 목록에서 걸러낼 방법이 없다.
  const source = read('src/features/admin/AdminModels.jsx');

  assert.ok(source.includes("awaiting_confirm: '확인 대기'"));
  assert.ok(/STATUS_VARIANT\s*=\s*\{[^}]*awaiting_confirm\s*:/.test(source));
  assert.ok(/STATUS_FILTERS\s*=\s*\[[\s\S]*?value:\s*'awaiting_confirm'[\s\S]*?\];/.test(source));
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

test('계정 칸은 auth 이메일이 없으면 지원서 이메일을 출처 표시와 함께 폴백으로 보여준다', () => {
  // auth.users.email 은 카카오 로그인 이메일 동의가 선택이라 null 일 수 있다
  // (facemarket_admin.py LIST_MODELS_SQL 주석). m.email 이 없을 때 그냥 '-' 로 떨어지면
  // 운영자가 실제로 아는 지원서 이메일(applicationContactEmail, 백엔드가 이제 내려준다)을
  // 보여줄 기회를 버리는 것이고, 값을 보여줄 땐 auth 이메일과 헷갈리지 않게 출처를 밝혀야
  // 한다.
  const source = read('src/features/admin/AdminModels.jsx');
  const idx = source.indexOf('m.email || \'-\'');
  assert.ok(idx === -1, "계정 칸이 여전히 m.email || '-' 로 지원서 이메일 폴백을 무시한다");
  assert.ok(
    source.includes('m.applicationContactEmail'),
    '계정 칸이 applicationContactEmail 폴백을 안 쓴다',
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

test('모델 상세 상단에서 연결 지원서와 프로필을 공용 팝업에 전달해요', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  assert.match(source, /상세히 보기/);
  assert.match(source, /<AdminSubmissionDetails\b[^>]*detail=\{data\}/);
});

test('모델 팝업은 프로필, 라이선스, 동의 이력을 순서대로 모두 렌더해요', async () => {
  const h = await modelComponentHarness({ initialStates: [], api: {},
    entry: '/src/features/admin/AdminSubmissionDetails.jsx', exportName: 'AdminSubmissionDetails' });
  const expand = node => !node || typeof node !== 'object' ? node : Array.isArray(node) ? node.map(expand)
    : typeof node.type === 'function' ? expand(node.type(node.props))
      : { ...node, props: { ...node.props, children: expand(node.props?.children) } };
  const text = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node) ? node.map(text).join(' ')
    : typeof node === 'object' ? text(node.props?.children) : String(node);
  try {
    const tree = expand(h.render({ onClose() {}, detail: {
      application: { applicantName: '연결 지원자', birthdate: '2000-01-02' },
      model: { id: 'PRIVATE-MODEL', displayName: '표시 모델', status: 'verified', gender: 'female', heightBucket: 'f_170_175', bodyType: 'regular' },
      enrollment: { id: 'PRIVATE-ENROLLMENT', status: 'passed', photoCount: 18, bodyType: 'slim_upper' },
      profile: { heightCm: 170.5, weightKg: 52.5, bustCm: 85, waistCm: 60, hipCm: 90,
        bodyType: 'slim', bodyTypeCustom: '직접 적은 체형', gender: 'female', ageRange: '20s', skinTone: '밝은 피부',
        hair: '직접 적은 머리', clothingSize: 'S', hairColor: 'brown', hairLength: 'long', eyeColor: 'brown' },
      licenses: [{ id: 'PRIVATE-LICENSE', status: 'active', allowedUse: ['일반 패션'], validUntil: null,
        createdAt: '2026-09-01T00:00:00Z', vcId: 'vc-public', optLocationCuts: true,
        optLookbookPersonReplace: true, optConsentVersion: 'opt-v1', optConsentedAt: '2026-09-01T00:00:00Z' }],
      consentEvents: [{ biometricVersion: 'bio-v1', termsVersion: 'terms-v1', overseasVersion: 'notice-v1', acceptedAt: '2026-09-01T00:00:00Z' }],
    } }));
    const rendered = text(tree);
    for (const value of ['연결 지원자', '표시 모델', '18장', '85cm', '60cm', '90cm', '170.5cm', '52.5kg',
      '직접 적은 체형', '밝은 피부', '직접 적은 머리', '20대', '170', '갈색', '긴 머리', '일반 패션', 'vc-public',
      '로케이션', '룩북', '철회 시까지', 'bio-v1', 'terms-v1', 'notice-v1', 'opt-v1']) assert.ok(rendered.includes(value), value);
    const positions = ['지원서', '등록·프로필', '라이선스 조건', '동의 기록'].map(label => rendered.indexOf(label));
    assert.ok(positions.every((pos, i) => pos >= 0 && (i === 0 || positions[i - 1] < pos)));
    assert.ok(!JSON.stringify(tree).includes('PRIVATE-'));
    assert.ok(!rendered.includes('f_170_175'));
    for (const [label, expected] of [['키 구간', '170–175cm'], ['등록한 체형', '마름 · 상체 볼륨']]) {
      const row = findTree(tree, node => node.type === 'div' && Array.isArray(node.props.children)
        && node.props.children[0]?.type === 'dt' && text(node.props.children[0]) === label);
      assert.equal(text(row?.props.children[1]), expected);
    }
  } finally { await h.close(); }
});
