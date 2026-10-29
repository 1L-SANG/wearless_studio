import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { componentModule, hooks } from './helpers/reactHarness.mjs';

test('A04 registration placeholder performs no save publish or registration request', async () => {
  const runtime = hooks(); let calls = 0, closed = 0;
  const { MallRegisterModal } = await componentModule('src/features/editor/MallRegisterModal.jsx', ['MallRegisterModal'], {
    ...runtime, Modal: ({ children }) => children, styles: {},
  });
  const tree = runtime.render(MallRegisterModal, { onClose: () => closed++, waitForSave: () => calls++, onPublish: () => calls++, onRegistered: () => calls++ });
  const html = renderToStaticMarkup(tree);
  assert.match(html, /쇼핑몰 등록 준비 중/); assert.match(html, /role="dialog" aria-modal="true"/); assert.doesNotMatch(html, /<input|type="submit"/);
  const walk = (node) => [node, ...React.Children.toArray(node?.props?.children).flatMap(walk)];
  walk(tree).find((node) => node.type === 'button').props.onClick();
  assert.equal(closed, 1); assert.equal(calls, 0);
});

test('registration placeholder restores focus and traps Tab on close button', async () => {
  const runtime = hooks(); let focused = 0, restored = 0;
  const { MallRegisterModal } = await componentModule('src/features/editor/MallRegisterModal.jsx', ['MallRegisterModal'], {
    ...runtime, Modal: ({ children }) => children, styles: {},
  });
  const tree = runtime.render(MallRegisterModal, { anchorRef: { current: { focus: () => restored++ } } });
  const dialog = tree.props.children;
  React.Children.toArray(dialog.props.children)[2].props.children.ref.current = { focus: () => focused++ };
  const cleanups = runtime.flushEffects();
  dialog.props.onKeyDown({ key: 'Tab', preventDefault() {} });
  cleanups.forEach((cleanup) => cleanup?.());
  assert.equal(focused, 2); assert.equal(restored, 1);
});
