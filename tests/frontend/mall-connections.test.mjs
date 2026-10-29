import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';

const Button = ({ children, onClick, disabled, className }) => React.createElement('button', { onClick, disabled, className }, children);
const Icon = () => null;
const connection = { mall: 'zigzag', status: 'connected', shopName: '데일리 니트 스토어', keyHint: '12345678', connectedAt: '2026-10-24T00:00:00Z', permissions: ['상품 조회와 등록'], defaults: {} };
const render = (node) => renderToStaticMarkup(node);
async function load(runtime = hooks(), api = {}) {
  assert.ok(existsSync(new URL('../../src/features/settings/MallConnections.jsx', import.meta.url)), 'mall settings page is missing');
  return componentModule('src/features/settings/MallConnections.jsx', ['MallConnectionRow', 'ZigzagConnection', 'MallActionDialog', 'MallDialog', 'KeyFields'],
    { ...runtime, api, Button, Icon, Modal: ({ children }) => children, styles: {},
      seoulDate: () => '2026.10.24', ExportMenu: () => null, SalesDefaultsCard: () => null });
}

test('secret key input accessible name is exactly Secret Key', async () => {
  const { KeyFields } = await load();
  const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
  for (const rotate of [false, true]) for (const showSecret of [false, true]) {
    const nodes = walk(KeyFields({ rotate, showSecret }));
    const input = nodes.find((node) => node.type === 'input' && node.props.name === 'secretKey');
    const label = nodes.find((node) => node.type === 'label' && node.props.htmlFor === input.props.id);
    assert.equal(render(label).replace(/<[^>]*>/g, '').trim(), rotate ? '새 Secret Key' : 'Secret Key');
    assert.equal(nodes.find((node) => node.type === 'button').props['aria-controls'], input.props.id);
    assert.equal(input.props.type, showSecret ? 'text' : 'password');
  }
});

test('renders five row states', async () => {
  const { MallConnectionRow } = await load();
  for (const [state, text] of [['none', '연결하기'], ['editing', 'Access Key'], ['verifying', '확인 중'], ['connected', '데일리 니트 스토어'], ['failed', '다시 확인']]) {
    const html = render(React.createElement(MallConnectionRow, { state, connection: state === 'connected' ? connection : null, values: {}, error: state === 'failed' ? '인증키가 맞지 않아요' : '' }));
    assert.ok(html.includes(text), state);
    if (state === 'connected') {
      assert.match(html, /인증키 끝자리 12345678/); assert.doesNotMatch(html, /<input/);
    }
    if (state === 'editing') {
      assert.match(html, /spellcheck="false"/); assert.match(html, /autoComplete="off"/);
      assert.match(html, /partners.kakaostyle.com/);
    }
    if (state === 'verifying') assert.match(html, /disabled/);
  }
});

test('connect failure keeps typed values and shows seller copy', async () => {
  const runtime = hooks();
  const sellerCopy = '인증키가 맞지 않아요. 파트너센터에서 다시 복사해 주세요';
  const { ZigzagConnection } = await load(runtime, { connectZigzag: async () => { throw new Error(sellerCopy); } });
  const props = { connection: { status: 'none' }, onChanged() {} };
  let tree = runtime.render(ZigzagConnection, props);
  let row = React.Children.toArray(tree.props.children)[0];
  row.props.onConnect();
  tree = runtime.render(ZigzagConnection, props); row = React.Children.toArray(tree.props.children)[0];
  row.props.onChange({ accessKey: 'typed-fixture-access', secretKey: 'typed-fixture-secret' });
  tree = runtime.render(ZigzagConnection, props); row = React.Children.toArray(tree.props.children)[0];
  await row.props.onSubmit({ preventDefault() {} });
  tree = runtime.render(ZigzagConnection, props); row = React.Children.toArray(tree.props.children)[0];
  assert.equal(row.props.state, 'failed');
  assert.equal(row.props.values.accessKey, 'typed-fixture-access');
  assert.equal(row.props.values.secretKey, 'typed-fixture-secret');
  assert.equal(row.props.error, sellerCopy);
});

test('rotate modal copy', async () => {
  const { MallActionDialog } = await load();
  const html = render(React.createElement(MallActionDialog, { kind: 'rotate', values: {}, anchorRef: {} }));
  assert.match(html, /새 Access Key/); assert.match(html, /새 Secret Key/);
  assert.match(html, /새 인증키로 확인이 끝나야 바뀌어요. 확인이 안 되면 지금 연결은 그대로 남아요/);
  assert.match(html, /확인하고 바꾸기/);
  assert.match(html, /role="dialog"/); assert.match(html, /aria-modal="true"/);
});

test('revoke confirm copy', async () => {
  const { MallActionDialog } = await load();
  const html = render(React.createElement(MallActionDialog, { kind: 'revoke', values: {}, anchorRef: {} }));
  for (const text of ['지그재그 연결을 해제할까요?', '스토어와의 연결이 끊겨요', '등록한 상품과 게시한 이미지는 그대로예요', '기본 설정은 남아요', '취소', '연결 해제']) assert.ok(html.includes(text));
  assert.match(html, /role="alertdialog"/);
});

test('mall dialog keeps focus inside while verifying and after failure', async () => {
  const runtime = hooks();
  const { MallDialog } = await load(runtime);
  let focused = '', busy = false;
  const input = { focus() { focused = 'input'; } };
  const dialogNode = { querySelector: () => busy ? null : input, focus() { focused = 'dialog'; } };
  const anchorRef = { current: { focus() { focused = 'anchor'; } } };
  let tree = runtime.render(MallDialog, { kind: 'rotate', anchorRef, busy });
  tree.props.children.ref.current = dialogNode;
  runtime.flushEffects();
  assert.equal(focused, 'input');
  busy = true; focused = 'body';
  tree = runtime.render(MallDialog, { kind: 'rotate', anchorRef, busy });
  runtime.flushEffects();
  assert.equal(focused, 'dialog'); assert.equal(tree.props.children.props.tabIndex, -1);
  busy = false;
  runtime.render(MallDialog, { kind: 'rotate', anchorRef, busy });
  runtime.flushEffects();
  assert.equal(focused, 'input');
});

test('revoke dialog restores focus when the manage button is removed', async () => {
  const runtime = hooks();
  const { MallDialog } = await load(runtime);
  let restored = false;
  const anchorRef = { current: { focus() {} } };
  const fallbackRef = { current: { querySelector: () => ({ focus() { restored = true; } }) } };
  runtime.render(MallDialog, { kind: 'revoke', anchorRef, fallbackRef });
  const cleanups = runtime.flushEffects();
  anchorRef.current = null;
  for (const cleanup of cleanups) if (typeof cleanup === 'function') cleanup();
  assert.equal(restored, true);
});

async function profile(enabled, isFacemarket) {
  const runtime = hooks();
  let queryEnabled;
  const { ProfileMenu } = await componentModule('src/features/shell/shell.jsx', ['ProfileMenu'], {
    ...runtime, useAuth: () => ({ session: { user: { id: 'local-user' } } }), useNavigate: () => () => {},
    useQuery: (query) => { queryEnabled = query.enabled; return { data: query.enabled ? { disabled: !enabled, items: [] } : undefined }; }, IS_FACEMARKET: isFacemarket,
    api: {}, draftSlot: { configure() {} }, ProfileMenuIcon: Icon, Icon,
  });
  let tree = runtime.render(ProfileMenu);
  assert.equal(queryEnabled, false, 'closed profile menu must not fetch malls');
  React.Children.toArray(tree.props.children)[0].props.onClick();
  tree = runtime.render(ProfileMenu);
  assert.equal(queryEnabled, !isFacemarket);
  const html = render(tree);
  React.Children.toArray(tree.props.children)[0].props.onClick();
  runtime.render(ProfileMenu);
  assert.equal(queryEnabled, false);
  return html;
}

test('profile menu shows 쇼핑몰 연결 above 요금제 관리 only when enabled', async () => {
  const html = await profile(true, false);
  assert.ok(html.includes('쇼핑몰 연결'));
  assert.ok(html.indexOf('쇼핑몰 연결') < html.indexOf('요금제 관리'));
  assert.doesNotMatch(await profile(false, false), /쇼핑몰 연결/);
  assert.doesNotMatch(await profile(true, true), /쇼핑몰 연결/);
});

test('disabled connect api hides profile link and shows export mall items as 준비 중', async () => {
  assert.doesNotMatch(await profile(false, false), /쇼핑몰 연결/);
  const { buildExportMenuItems } = await componentModule('src/features/editor/ExportMenu.jsx', ['buildExportMenuItems'], {});
  const items = buildExportMenuItems({ malls: { disabled: true }, publishEnabled: true, gifCount: 0 });
  assert.deepEqual(items.map((item) => item.id), ['download', 'paste', 'zigzag', 'cafe24', 'ably']);
  for (const item of items.filter((item) => ['zigzag', 'ably'].includes(item.id))) {
    assert.equal(item.disabled, true);
    assert.equal(item.badge, '준비 중');
  }
});
