/** 마이페이지 → /model/license 진입점.
 *
 * 라우트(<Route path="license">)와 화면(ModelLicense.jsx: listLicenses·revokeLicense·"해지")은
 * 진작 있었는데 들어가는 길이 등록 완료 화면에만 있었다 — 등록을 마치면 증서 전체와 해지로 가는
 * 길이 사실상 끊겼다. 증서를 보는 자리(증서 카드·그 카드를 담은 대화상자)에 길을 붙이고,
 * 상태별로 어떻게 보이는지를 여기서 고정한다.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { loadEarningsHarness, findTree } from './helpers/mypageHarness.mjs';

const root = new URL('../..', import.meta.url).pathname;
const read = path => readFileSync(`${root}${path}`, 'utf8');
const model = { id: 'm1', displayName: '모델', coverImageUrl: null };
const issued = { id: 'l1', status: 'active', vcId: 'proof-1', createdAt: '2026-09-13T00:00:00Z', allowedUse: ['일반 의류'] };
const revoked = { ...issued, status: 'revoked', updatedAt: '2026-09-13T14:28:31Z' };
const text = node => node == null || typeof node === 'boolean' ? ''
  : Array.isArray(node) ? node.map(text).join('')
  : typeof node === 'object' ? text(node.props?.children) : String(node);
// 하네스의 jsx 는 {type, props} 만 만든다 — 자식 컴포넌트는 펼쳐지지 않는다.
// 그래서 링크 자체는 직접 렌더해서 보고, 그 링크가 증서 카드 안에 놓였는지는 원소로 확인한다.
const manageLink = tree => findTree(tree, node => node.type === 'Link' && node.props?.to === '/model/license');
const elementOf = (tree, name) => findTree(tree, node => node.type?.name === name);

async function cert(harness) {
  return harness.server.ssrLoadModule('/src/features/model/mypage/MyPageCertificate.jsx');
}

test('발급된 증서에는 라이선스 관리로 가는 길이 붙어요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const { LicenseManageLink } = await cert(harness);
    const link = manageLink(harness.render(LicenseManageLink, { license: issued }));
    assert.ok(link, '/model/license 로 가는 링크가 있다');
    assert.equal(text(link), '라이선스 관리');
  } finally { await harness.close(); }
});

test('해지한 뒤에도 길은 남고, 문구만 기록 확인으로 바뀌어요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const { LicenseManageLink } = await cert(harness);
    const link = manageLink(harness.render(LicenseManageLink, { license: revoked }));
    assert.ok(link, '해지 뒤에도 증서와 기록은 볼 수 있어야 한다');
    assert.equal(text(link), '해지한 라이선스 보기');
  } finally { await harness.close(); }
});

test('발급 전에는 진입점을 만들지 않아요 — 발급 안내는 등록 진행 카드가 맡아요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const { LicenseManageLink, MyPageCertificate } = await cert(harness);
    for (const license of [null, { id: 'l1', status: 'pending' }, { id: 'l1', status: 'active', vcId: null }]) {
      assert.equal(harness.render(LicenseManageLink, { license }), null, '링크 자체가 렌더되지 않는다');
      // 증서 카드도 이때는 안내 패널로 빠져 링크를 담지 않는다
      assert.equal(elementOf(harness.render(MyPageCertificate, { license, model }), 'LicenseManageLink'), null);
    }
  } finally { await harness.close(); }
});

test('증서 카드가 그 링크를 담고, 라이선스 탭은 그 카드를 담아요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const { MyPageCertificate } = await cert(harness);
    const card = harness.render(MyPageCertificate, { license: issued, model });
    const link = elementOf(card, 'LicenseManageLink');
    assert.ok(link, '증서 카드 안에 진입점이 있다');
    assert.equal(link.props.license, issued);

    const conditions = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageConditions.jsx');
    const tab = harness.render(conditions.MyPageConditions,
      { license: issued, model, onCertificate() {}, onManage() {} });
    assert.ok(elementOf(tab, 'MyPageCertificate'), '라이선스 탭이 같은 카드를 쓴다');
  } finally { await harness.close(); }
});

test('증서 대화상자는 이 증서 카드를 그대로 담아요', () => {
  // ProfileHero "내 증서 보기" → 대화상자 → MyPageCertificate. 카드에 길을 붙였으니 거기도 생긴다.
  const dashboard = read('/src/features/model/mypage/MyPageDashboard.jsx');
  assert.match(dashboard, /MyPageDialog title="내 라이선스 증서"[\s\S]*?<MyPageCertificate/);
});

test('제품 용어는 해지다 — 폐기라고 쓰지 않아요', () => {
  const source = read('/src/features/model/mypage/MyPageCertificate.jsx');
  assert.ok(!/폐기/.test(source.replace(/'폐기'/g, '')), "'폐기'를 사용자 문구로 쓰지 않는다");
  assert.match(source, /해지한 라이선스 보기/);
});

test('라우트 가드와 공개 리다이렉트는 그대로예요', () => {
  const routes = read('/src/apps/facemarket/modelSectionRoutes.jsx');
  assert.match(routes, /<Route path="license" element={<ModelLicense \/>} \/>/);
  assert.match(routes, /RequireOwnedModel/);
  const app = read('/src/apps/facemarket/App.jsx');
  // /license·/licensing 은 공개 경로라 /status 로 보낸다 — 모델 전용 /model/license 와 다른 길이다.
  assert.match(app, /path="license" element={<Navigate to="\/status" replace \/>}/);
  assert.match(app, /path="licensing" element={<Navigate to="\/status" replace \/>}/);
});

for (const [status, modelStatus] of [['vc_pending', 'pending'], ['passed', 'pending'], ['passed', 'awaiting_confirm']]) {
  test(`${status} 상태의 ${modelStatus} 모델은 이미 발급된 증서도 확정 전에는 숨겨요`, async () => {
    const harness = await loadEarningsHarness();
    try {
      const { resolveHubJourney } = await import('../../src/features/model/modelHubState.js');
      const { ActiveDashboard } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageDashboard.jsx');
      const { MyPageConditions } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageConditions.jsx');
      const { ProfileHero, MyPageCertificate } = await cert(harness);
      const ownedModel = { ...model, status: modelStatus };
      const enrollment = { status };
      const journey = resolveHubJourney({ ownedModel, enrollment, license: issued, hasLicense: true });
      harness.runtime.hash = '#license';
      let tree = harness.render(ActiveDashboard, { journey, enrollment, model: ownedModel, license: issued });
      let hero = elementOf(tree, 'ProfileHero');
      assert.equal(hero.props.license, null);
      assert.ok(text(ProfileHero(hero.props)).includes('테스트컷 준비 중'));
      hero.props.onCertificate();
      tree = harness.render(ActiveDashboard, { journey, enrollment, model: ownedModel, license: issued });
      const dialogCard = elementOf(tree, 'MyPageCertificate');
      assert.equal(dialogCard.props.license, null);
      assert.equal(elementOf(MyPageCertificate(dialogCard.props), 'LicenseManageLink'), null);
      const conditions = elementOf(tree, 'MyPageConditions');
      const tab = harness.render(MyPageConditions, conditions.props);
      assert.equal(elementOf(tab, 'MyPageCertificate').props.license, null);
      assert.equal(findTree(tab, node => node.type === 'button' && text(node).startsWith('사용 조건 편집')).props.disabled, true);
      assert.equal(text(MyPageCertificate(elementOf(tab, 'MyPageCertificate').props)).includes('proof-1'), false);
    } finally { await harness.close(); }
  });
}

test('확정 뒤에는 마이페이지의 증서와 관리 링크를 보여줘요', async () => {
  const harness = await loadEarningsHarness();
  try {
    const { ActiveDashboard } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageDashboard.jsx');
    const { ProfileHero } = await cert(harness);
    const tree = harness.render(ActiveDashboard, { journey: { mode: 'active', flag: 'none' }, model: { ...model, status: 'verified', confirmedAt: '2026-09-14T00:00:00Z' }, license: issued });
    const hero = elementOf(tree, 'ProfileHero');
    assert.equal(hero.props.license, issued);
    assert.ok(text(ProfileHero(hero.props)).includes('내 증서 보기'));
  } finally { await harness.close(); }
});

for (const confirmedAt of [null, '2026-09-14T00:00:00Z']) {
  for (const entry of ['상단 카드', '증서 대화상자', '라이선스 탭']) {
    test(`운영 정지 모델의 ${entry}에서 테스트컷 ${confirmedAt ? '확정 뒤 증서를 보여줘요' : '확정 전 증서를 숨겨요'}`, async () => {
      const harness = await loadEarningsHarness();
      try {
        const { resolveHubJourney } = await import('../../src/features/model/modelHubState.js');
        const { ActiveDashboard } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageDashboard.jsx');
        const { MyPageConditions } = await harness.server.ssrLoadModule('/src/features/model/mypage/MyPageConditions.jsx');
        const { ProfileHero, MyPageCertificate, LicenseManageLink } = await cert(harness);
        const ownedModel = { ...model, status: 'suspended', confirmedAt };
        const journey = resolveHubJourney({ ownedModel, license: issued, hasLicense: true });
        assert.equal(journey.mode, 'active');
        assert.equal(journey.flag, 'paused');
        const props = { journey, model: ownedModel, license: issued };
        let tree = harness.render(ActiveDashboard, props);
        const hero = elementOf(tree, 'ProfileHero');

        if (entry === '상단 카드') {
          const rendered = ProfileHero(hero.props);
          assert.equal(hero.props.license, confirmedAt ? issued : null);
          assert.equal(text(rendered).includes('초상 라이선스 증서'), Boolean(confirmedAt));
          assert.equal(text(rendered).includes('내 증서 보기'), Boolean(confirmedAt));
          assert.equal(text(rendered).includes('테스트컷 준비 중'), !confirmedAt);
          return;
        }

        let certificate;
        if (entry === '증서 대화상자') {
          hero.props.onCertificate();
          tree = harness.render(ActiveDashboard, props);
          certificate = elementOf(tree, 'MyPageCertificate');
        } else {
          findTree(tree, node => node.props?.role === 'tab' && text(node) === '라이선스').props.onClick();
          tree = harness.render(ActiveDashboard, props);
          const conditions = elementOf(tree, 'MyPageConditions');
          const tab = harness.render(MyPageConditions, conditions.props);
          certificate = elementOf(tab, 'MyPageCertificate');
          assert.equal(text(findTree(tab, node => node.type === 'button' && node.props?.onClick === conditions.props.onCertificate)),
            confirmedAt ? '내 증서 보기' : '테스트컷 준비 중');
        }

        assert.equal(certificate.props.license, confirmedAt ? issued : null);
        const rendered = MyPageCertificate(certificate.props);
        const link = elementOf(rendered, 'LicenseManageLink');
        assert.equal(text(rendered).includes('proof-1'), Boolean(confirmedAt));
        if (confirmedAt) {
          assert.ok(link);
          assert.ok(manageLink(LicenseManageLink(link.props)));
        } else {
          assert.equal(link, null);
          assert.equal(findTree(rendered, node => node.type === 'Link'), null);
          assert.equal(elementOf(rendered, 'EmptyPanel').props.title, '테스트컷 준비 중');
        }
      } finally { await harness.close(); }
    });
  }
}

test('얼굴 라이선스 페이지는 증서의 계약 역할을 안내하며 유지돼요', async () => {
  const { modelComponentHarness } = await import('./helpers/facemarketHarness.mjs');
  const h = await modelComponentHarness({ entry: '/src/features/model/ModelLicense.jsx', exportName: 'ModelLicense',
    initialStates: ['ready', 'cards', null, [], null, []], api: {} });
  try {
    const tree = h.render();
    assert.ok(text(tree).includes('얼굴 라이선스'));
    assert.ok(text(tree).includes('발급받은 라이선스 증서는 셀러가 모델님을 이용할 때 계약서로서 작용해요.'));
  } finally { await h.close(); }
});
