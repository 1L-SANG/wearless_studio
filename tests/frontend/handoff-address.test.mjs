import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { copyHandoffAddress } from '../../src/lib/handoffAddress.js';

const origin = 'https://preview.wearless.test:8443';

test('handoff address copies the origin and shows the success toast', async () => {
  const calls = [];
  const navigator = { clipboard: { async writeText(value) { calls.push(['copy', value]); } } };
  await copyHandoffAddress((message) => calls.push(['toast', message]), { navigator, origin });
  assert.deepEqual(calls, [['copy', origin], ['toast', '주소를 복사했어요']]);
});

for (const failure of ['missing navigator', 'missing clipboard', 'rejected clipboard', 'throwing clipboard']) {
  test(`handoff address never throws and shows the host for ${failure}`, async () => {
    const toasts = [];
    const navigator = failure === 'missing navigator' ? null : {};
    if (failure === 'rejected clipboard') navigator.clipboard = { async writeText() { throw new Error('denied'); } };
    if (failure === 'throwing clipboard') navigator.clipboard = { writeText() { throw new Error('denied'); } };
    await assert.doesNotReject(() => copyHandoffAddress((message) => toasts.push(message), { navigator, origin }));
    assert.deepEqual(toasts, ['PC에서 preview.wearless.test:8443로 접속해 주세요']);
  });
}

test('handoff address defaults to the current window origin and navigator', async (t) => {
  const calls = [];
  for (const [key, value] of Object.entries({
    window: { location: { origin, href: `${origin}/editor/project?preview=1#page` } },
    navigator: { clipboard: { async writeText(value) { calls.push(value); } } },
  })) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { configurable: true, value });
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, key, previous);
      else delete globalThis[key];
    });
  }
  await copyHandoffAddress((message) => calls.push(message));
  assert.deepEqual(calls, [origin, '주소를 복사했어요']);
});

test('handoff address does not propagate toast failures', async () => {
  for (const navigator of [{}, { clipboard: { async writeText() {} } }]) {
    await assert.doesNotReject(() => copyHandoffAddress(() => { throw new Error('toast failed'); }, { navigator, origin }));
    await assert.doesNotReject(() => copyHandoffAddress(async () => { throw new Error('toast rejected'); }, { navigator, origin }));
  }
});

const chipSource = readFileSync(new URL('../../src/features/shell/AddressCopyChip.jsx', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
const { code } = await transformWithEsbuild(chipSource, 'AddressCopyChip.jsx', { jsx: 'transform' });

for (const host of ['ai.wearless.kr', 'localhost:5173']) {
  test(`address chip renders ${host} and calls the copy helper with its toast`, async () => {
    const calls = [];
    const pushToast = () => {};
    const bindings = {
      React, window: { location: { host } }, s: { chip: 'chip', icon: 'icon' },
      useToast: () => ({ push: pushToast }),
      copyHandoffAddress: async (...args) => calls.push(args),
      Icon: ({ name, size }) => React.createElement('svg', { 'data-icon': name, width: size, height: size }),
    };
    const AddressCopyChip = new Function(...Object.keys(bindings), `${code}; return AddressCopyChip;`)(...Object.values(bindings));
    const tree = AddressCopyChip();
    assert.equal(tree.type, 'button');
    assert.equal(tree.props.type, 'button');
    assert.equal(tree.props['aria-label'], '주소 복사');
    const [label, icon] = React.Children.toArray(tree.props.children);
    assert.equal(label.props.children, host);
    assert.equal(icon.props['aria-hidden'], 'true');
    assert.equal(icon.props.children.props.name, 'copy');
    assert.equal(icon.props.children.props.size, 16);
    assert.ok(renderToStaticMarkup(tree).includes(host));
    assert.deepEqual(calls, []);
    await tree.props.onClick();
    assert.deepEqual(calls, [[pushToast]]);
  });
}
