import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { ratioEditorCrop } from '../../src/features/editor/editorCrop.js';

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

for (const { label, height } of [{ label: '3:4', height: 266.6666666666667 }, { label: '16:9', height: 112.5 }]) {
  test(`choosing ${label} crops to that frame ratio without stretching the original image`, () => {
    let result;
    const tree = Toolbar({ crop: { ...draft, ox: 0, oy: 0 }, onCropRatio: ratio => {
      result = ratioEditorCrop({ ...draft, ox: 0, oy: 0 }, ratio);
    } });
    const select = nodes(tree).find(n => n.type === 'select');
    const option = nodes(tree).find(n => n.type === 'option' && n.props.children === label);
    assert.ok(option, `${label} can be selected`);
    select.props.onChange({ target: { value: option.props.value } });
    assert.equal(result.fw, 200);
    assert.ok(Math.abs(result.fh - height) < 0.001);
    assert.equal(result.iw, 600);
    assert.equal(result.ih, 900);
  });
}

test('approved zoom slider shows the current percentage and sends an absolute 200% change correctly', () => {
  const factors = [];
  const tree = Toolbar({ crop: draft, onCropZoom: factor => factors.push(factor) });
  const slider = nodes(tree).find(n => n.type === 'input' && n.props.type === 'range');
  assert.ok(slider, 'the approved toolbar uses a zoom slider');
  assert.equal(slider.props.value, 150);
  slider.props.onChange({ target: { value: '200' } });
  assert.deepEqual(factors, [4 / 3]);
});
