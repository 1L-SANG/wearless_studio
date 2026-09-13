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
