import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { horizonBackgroundMode } from '../../src/lib/horizonBackground.js';
import { effectiveHorizonBackgroundMode } from '../../src/lib/horizonBackgroundAvailability.js';
import * as labels from '../../src/lib/spaceSetDisplayNames.js';
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const controlSource = read('../../src/features/storyboard/HorizonBackgroundControl.jsx');
const controlBody = controlSource.slice(controlSource.indexOf('export default function')).replace('export default ', '');
const transformed = await transformWithEsbuild(controlBody, 'control.jsx', { jsx: 'transform' });
const Control = new Function('React', 'useState', 'horizonBackgroundMode', 'effectiveHorizonBackgroundMode', `${transformed.code};return HorizonBackgroundControl;`)(React, React.useState, horizonBackgroundMode, effectiveHorizonBackgroundMode);
const block = { id: 'b', cutType: 'horizon', spaceGroupId: 'g', horizonBackgroundMode: 'garment-tone' };
test('unavailable saved tone selects the actual reference background while leaving reference selectable', () => {
  const html = renderToStaticMarkup(React.createElement(Control, { block, memberCount: 4, onChange() {} }));
  assert.equal((html.match(/type="radio"/g) || []).length, 2);
  assert.match(html, /value="garment-tone"/);
  assert.match(html, /data-selected="true"[^>]*>\s*<input[^>]*value="reference"[^>]*checked|data-selected="true"[^>]*>\s*<input[^>]*checked[^>]*value="reference"/);
  assert.match(html, /value="garment-tone"[^>]*disabled|disabled[^>]*value="garment-tone"/);
  assert.match(html, /기존 배경/); assert.match(html, /옷 색에 맞춤/); assert.match(html, /4컷/);
  const available = renderToStaticMarkup(React.createElement(Control, { block, garmentToneAvailable: true, onChange() {} }));
  assert.doesNotMatch(available, /disabled/);
  assert.match(available, /data-selected="true"[^>]*>\s*<input[^>]*value="garment-tone"[^>]*checked|data-selected="true"[^>]*>\s*<input[^>]*checked[^>]*value="garment-tone"/);
});
const storyboard = read('../../src/features/storyboard/Storyboard.jsx');
const headerSource = storyboard.slice(storyboard.indexOf('function SpaceSetInspectorHeader('), storyboard.indexOf('function SpaceSetGallery('));
const headerTransform = await transformWithEsbuild(headerSource, 'header.jsx', { jsx: 'transform' });
const Header = new Function('React', 'horizonBackgroundMode', 'effectiveHorizonBackgroundMode', ...Object.keys(labels), `${headerTransform.code};return SpaceSetInspectorHeader;`)(React, horizonBackgroundMode, effectiveHorizonBackgroundMode, ...Object.values(labels));
test('compact affiliation shows the current cut rather than the set cover', () => {
  const selected = { ...block, thumb: '/selected-cut.png', spaceSetMemberOrder: 5 };
  const set = { id: 'horizon-sequence-figma-s05-v2', members: [{ thumb: '/cover.png' }] };
  const html = renderToStaticMarkup(React.createElement(Header, { set, siblings: [{ id: 'a' }, selected], block: selected, onChangeSet() {} }));
  assert.match(html, /src="\/selected-cut.png"/); assert.doesNotMatch(html, /src="\/cover.png"|sb-set-card/);
  assert.match(html, /2번째 컷/); assert.match(html, /2\/2/); assert.match(html, /세트 설정/); assert.match(html, /세트 공통/);
});

test('individual cut header labels the effective background when a saved request is stale', () => {
  const set = { id: 'horizon-sequence-figma-s05-v2', members: [] };
  const stale = renderToStaticMarkup(React.createElement(Header, { set, siblings: [block], block,
    backgroundAvailability: { available: false }, onChangeSet() {} }));
  const ready = renderToStaticMarkup(React.createElement(Header, { set, siblings: [block], block,
    backgroundAvailability: { available: true }, onChangeSet() {} }));
  assert.match(stale, /배경: 기존 배경/);
  assert.doesNotMatch(stale, /배경: 옷 색에 맞춤/);
  assert.match(ready, /배경: 옷 색에 맞춤/);
});
