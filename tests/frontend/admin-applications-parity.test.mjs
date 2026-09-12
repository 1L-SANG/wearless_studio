/* 지원서 화면 이관 — 껍데기만 바꾸고 동작은 그대로인지.

   되돌아가면: 스타일을 갈아엎다가 사진 objectURL 해제나 409 재조회 같은 "안 보이는 동작"이
   함께 사라진다. 그 손실은 화면을 봐서는 모른다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { findTree, modelComponentHarness } from './helpers/facemarketHarness.mjs';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const source = read('src/features/admin/AdminApplications.jsx');

test('CSS 모듈을 버리고 admin-ui 를 쓴다 — ui.jsx 에서는 토스트 훅만 빌린다', () => {
  assert.ok(!source.includes('AdminApplications.module.css'));
  assert.ok(source.includes('@/components/admin-ui/'));
  // ToastProvider 는 AppProviders 에 남아 있고 스타일은 studio 레이어가 준다.
  // 훅 하나를 위해 토스트를 다시 구현하지 않는다. 대신 **시각 컴포넌트는** 가져오지 않는다.
  const uiImport = source.match(/import\s*\{([^}]*)\}\s*from\s*'@\/components\/ui\.jsx';/);
  if (uiImport) {
    const named = uiImport[1].split(',').map((s) => s.trim()).filter(Boolean);
    assert.deepEqual(named, ['useToast'], `ui.jsx 에서 토스트 훅 말고 더 가져온다: ${named}`);
  }
});

test('관리자 API 다섯 개를 그대로 호출한다', () => {
  for (const fn of [
    'adminListApplications', 'adminApproveApplication', 'adminRejectApplication',
    'adminResendEmail', 'adminFetchApplicationPhotoUrl',
  ]) {
    assert.ok(source.includes(fn), `호출이 사라졌다: ${fn}`);
  }
});

test('사진 objectURL 을 계속 해제한다', () => {
  assert.ok(source.includes('URL.revokeObjectURL'), 'objectURL 누수');
});

test('거절은 사유 입력을 요구한다', () => {
  assert.ok(source.includes('reason'), '거절 사유 상태가 사라졌다');
});

test('거절 사유는 여러 줄 textarea 로 받는다 — 한 줄 Input 으로 좁아지면 안 된다', () => {
  // 재-스킨 전엔 <textarea rows={2}> 였다. shadcn 이관 때 <Input>(=<input type="text">)으로
  // 좁혀졌던 적이 있다 — 거절 사유는 지원자에게 메일로 전달되는 프로즈라 한 줄 입력이 아니다.
  // reason 상태만 보는 위 테스트는 이 폭을 못 잡는다(Input 이든 Textarea 든 'reason' 은 있다).
  assert.ok(source.includes('@/components/admin-ui/textarea.jsx'), 'Textarea 컴포넌트를 안 쓴다');
  // 'pending && rejecting &&' 로 정확히 자른다 — 'rejecting &&' 만 찾으면 그 앞줄의
  // '!rejecting &&'(승인/거절 버튼 행)에도 부분 일치해 시작점이 한 블록 위로 밀린다.
  const reasonBlock = source.slice(source.indexOf('pending && rejecting &&'));
  assert.ok(/<Textarea\b/.test(reasonBlock), '거절 사유 입력이 Textarea 가 아니다');
});

const textOf = node => node == null || typeof node === 'boolean' ? '' : Array.isArray(node)
  ? node.map(textOf).join(' ') : typeof node === 'object' ? textOf(node.props?.children) : String(node);
const expand = node => {
  if (!node || typeof node !== 'object') return node;
  if (Array.isArray(node)) return node.map(expand);
  if (typeof node.type === 'function') return expand(node.type(node.props));
  return { ...node, props: { ...node.props, children: expand(node.props?.children) } };
};
const detailsHarness = () => modelComponentHarness({ initialStates: [], api: {},
  entry: '/src/features/admin/AdminSubmissionDetails.jsx', exportName: 'AdminSubmissionDetails' });
const field = (tree, label) => findTree(tree, node => node.type === 'div'
  && Array.isArray(node.props.children) && node.props.children[0]?.type === 'dt'
  && textOf(node.props.children[0]) === label);

test('지원서 카드에서 공용 상세 팝업을 열어요', () => {
  assert.match(source, /상세히 보기/);
  assert.match(source, /onClick=\{\(\) => onDetail\(app\)\}/);
  assert.match(source, /<AdminSubmissionDetails\b/);
});

test('상세 팝업은 지원서 입력 전문과 0, false를 보존하고 내부 식별자를 그리지 않아요', async () => {
  const h = await detailsHarness();
  const application = {
    id: 'PRIVATE-APPLICATION', userId: 'PRIVATE-USER', reviewedBy: 'PRIVATE-REVIEWER',
    status: 'rejected', applicantName: '김지원', birthdate: '2000-01-02', region: '서울', gender: 'female',
    heightCm: 170, weightKg: 52, phone: '010-1234-5678', contactEmail: 'hello@example.com',
    experienceLevel: 'professional', agencyContracted: false, categories: ['패션', '뷰티'],
    portfolioUrl: 'https://example.com/portfolio', snsUrl: 'https://example.com/sns',
    bio: '첫 줄\n' + '긴 자기소개 '.repeat(400) + '\n마지막 줄', photoKinds: [],
    createdAt: '2026-09-11T15:30:00Z', reviewedAt: '2026-09-12T01:00:00Z',
    rejectReason: '다음에 다시 지원해 주세요', identityMismatchCount: 0,
    privacyConsentVersion: 'privacy-v1', privacyConsentedAt: '2026-09-11T15:30:00Z',
    attestations: { photosAreMine: true, privateKey: 'PRIVATE-ATTESTATION' },
    faceImageUri: 'PRIVATE-FACE', r2Key: 'PRIVATE-R2', digest: 'PRIVATE-DIGEST',
  };
  try {
    const tree = expand(h.render({ application, onClose() {} }));
    const expected = { '이름': '김지원', '성별': '여성', '지역': '서울', '키': '170cm', '몸무게': '52kg',
      '이메일': 'hello@example.com', '전화': '010-1234-5678', '경력 수준': '전문', '소속사 계약 여부': '없음',
      '카테고리': '패션, 뷰티', '포트폴리오 URL': application.portfolioUrl, 'SNS URL': application.snsUrl,
      '자기소개': application.bio, '거절 사유': application.rejectReason, '신원 불일치 횟수': '0회',
      '지원 사진': '입력 안 함', '개인정보 동의 버전': 'privacy-v1' };
    for (const [label, value] of Object.entries(expected)) assert.equal(textOf(field(tree, label)?.props.children[1]), value, label);
    assert.match(textOf(field(tree, '생년월일')), /2000-01-02 \(만 \d+세\)/);
    assert.match(textOf(field(tree, '접수일')), /2026-09-12/);
    assert.match(textOf(field(tree, '검토일')), /2026-09-12/);
    assert.ok(!textOf(tree).includes('PRIVATE-'));
    assert.ok(!JSON.stringify(tree).includes('PRIVATE-'));
  } finally { await h.close(); }
});

test('빈 지원서의 표시 항목은 입력 안 함으로 보여요', async () => {
  const h = await detailsHarness();
  try {
    const tree = expand(h.render({ application: {}, onClose() {} }));
    for (const label of ['이름', '생년월일', '성별', '지역', '키', '몸무게', '이메일', '전화', '경력 수준',
      '소속사 계약 여부', '카테고리', '포트폴리오 URL', 'SNS URL', '자기소개', '지원 사진', '접수일',
      '검토일', '거절 사유', '신원 불일치 횟수']) {
      assert.equal(textOf(field(tree, label)?.props.children[1]), '입력 안 함', label);
    }
  } finally { await h.close(); }
});

test('상세 팝업은 Esc, 배경 클릭, 닫기로 닫히고 본문 클릭은 유지돼요', async () => {
  const h = await detailsHarness();
  let closed = 0;
  try {
    const tree = expand(h.render({ application: {}, onClose: () => { closed += 1; } }));
    const dialog = findTree(tree, node => node.type === 'dialog');
    assert.ok(dialog.props['aria-labelledby']);
    let opened = 0; let released = 0;
    h.runtime.refs[0].current = { showModal() { opened += 1; }, close() { released += 1; } };
    const cleanup = h.runtime.effects[0]();
    assert.equal(opened, 1);
    cleanup(); assert.equal(released, 1);
    let prevented = false;
    dialog.props.onKeyDown({ key: 'Enter' }); assert.equal(closed, 0);
    dialog.props.onKeyDown({ key: 'Escape', preventDefault() { prevented = true; } });
    assert.equal(closed, 1); assert.equal(prevented, true);
    const background = {};
    dialog.props.onClick({ target: {}, currentTarget: background }); assert.equal(closed, 1);
    dialog.props.onClick({ target: background, currentTarget: background }); assert.equal(closed, 2);
    findTree(tree, node => node.type === 'button' && textOf(node) === '닫기').props.onClick();
    assert.equal(closed, 3);
  } finally { await h.close(); }
});

test('지원 사진 네 종류는 기존 인증 게이트를 쓰고 닫은 뒤 도착한 URL도 해제해요', async () => {
  const calls = []; const revoked = [];
  const originalRevoke = URL.revokeObjectURL;
  URL.revokeObjectURL = url => revoked.push(url);
  try {
    for (const [kind, label] of [['profile', '프로필'], ['closeup', '클로즈업'], ['waist_up', '상반신'], ['full_length', '전신']]) {
      let resolve;
      const uri = `/v1/facemarket/admin/applications/a1/profile-image?kind=${kind}`;
      const h = await modelComponentHarness({ initialStates: [], api: {
        adminApplicationProfileImage: path => { calls.push(path); return new Promise(done => { resolve = done; }); },
      }, entry: '/src/features/admin/AdminSubmissionDetails.jsx', exportName: 'SubmissionPhoto' });
      try {
        h.render({ application: { photoUris: { [kind]: uri } }, kind, label });
        const cleanup = h.runtime.effects[0]();
        if (kind === 'full_length') cleanup();
        resolve(`blob:${kind}`);
        await new Promise(done => setImmediate(done));
        if (kind !== 'full_length') {
          const tree = h.render({ application: { photoUris: { [kind]: uri } }, kind, label });
          const image = findTree(tree, node => node.type === 'img');
          assert.equal(image.props.src, `blob:${kind}`); assert.equal(image.props.alt, `지원자 ${label} 사진`);
          cleanup();
        }
      } finally { await h.close(); }
    }
    assert.equal(calls.length, 4);
    assert.deepEqual(revoked, ['blob:profile', 'blob:closeup', 'blob:waist_up', 'blob:full_length']);
  } finally { URL.revokeObjectURL = originalRevoke; }
});
