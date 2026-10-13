import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';

// Render the real toolbar JSX, not a parallel implementation of its controls.
const editor = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const start = editor.indexOf('<div className="crop-bar"');
const end = editor.indexOf('</div>', start) + 6;
const compiled = await transformWithEsbuild(`function Toolbar({crop,onCropRatio,onCropZoom,onCropReset,onCropCommit,onCropCancel}) { return (${editor.slice(start,end)}); }`, 'crop-toolbar.jsx', { jsx: 'transform' });
const Toolbar = new Function('React', 'Icon', `${compiled.code}; return Toolbar;`)(React, () => null);
function nodes(root) {
  if (!React.isValidElement(root)) return [];
  return [root, ...React.Children.toArray(root.props.children).flatMap(nodes)];
}
const draft = { fx: 50, fy: 0, fw: 200, fh: 300, iw: 600, ih: 900, initial: { iw: 400 } };

test('crop ratio choices stay within the three choices shown in the approved mock', () => {
  const tree = Toolbar({ crop: draft });
  assert.deepEqual(nodes(tree).filter(n => n.type === 'option').map(n => n.props.value), ['', '1', '0.8']);
});

test('approved zoom slider shows the current percentage and sends an absolute 200% change correctly', () => {
  const factors = [];
  const tree = Toolbar({ crop: draft, onCropZoom: factor => factors.push(factor) });
  const slider = nodes(tree).find(n => n.type === 'input' && n.props.type === 'range');
  assert.ok(slider, 'the approved toolbar uses a zoom slider');
  assert.equal(slider.props.value, 150);
  slider.props.onChange({ target: { value: '200' } });
  assert.deepEqual(factors, [4 / 3]);
});
