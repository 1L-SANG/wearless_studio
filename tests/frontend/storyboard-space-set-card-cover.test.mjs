import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import * as spaceSetLabels from '../../src/lib/spaceSetDisplayNames.js';

// Exercise the production component without loading the full route and its browser services.
const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
const component = source.slice(source.indexOf('function SpaceSetCard('), source.indexOf('function SpaceSetInspectorHeader('));
const { code } = await transformWithEsbuild(component, 'SpaceSetCard.jsx', { jsx: 'transform' });
const SpaceSetCard = new Function('React', ...Object.keys(spaceSetLabels), `${code}; return SpaceSetCard;`)(React, ...Object.values(spaceSetLabels));
const members = count => Array.from({ length: count }, (_, index) => ({ exampleId: `cut-${index}`, thumb: `/cut-${index}.png`, direction: 'front', shot: 'full' }));

for (const count of [3, 4, 5, 7]) {
  test(`${count} members render one cover and ${Math.min(count - 1, 4)} real additional miniatures`, () => {
    const html = renderToStaticMarkup(React.createElement(SpaceSetCard, { set: { members: members(count) } }));
    assert.equal((html.match(/class="sb-set-cover"/g) || []).length, 1);
    assert.equal((html.match(/class="sb-set-miniature"/g) || []).length, Math.min(count - 1, 4));
    const sources = [...html.matchAll(/src="([^"]+)"/g)].map(match => match[1]);
    assert.deepEqual(sources, members(count).slice(0, 5).map(member => member.thumb));
    assert.equal(new Set(sources).size, sources.length, 'never duplicate the cover to fill miniature slots');
  });
}

test('selection, preview, keyboard focus and static inspector behavior remain intact', () => {
  const shootingSet = { members: members(4) };
  const calls = [];
  const shell = SpaceSetCard({ set: shootingSet, onChoose: set => calls.push(['choose', set]), onPreviewOpen: (...args) => calls.push(['preview', ...args]), onPreviewClose: () => calls.push(['close']) });
  const card = shell.props.children[0];
  assert.equal(shell.type, 'div');
  assert.equal(card.type, 'button');
  assert.equal(shell.props.children[1].props.children, '개별 컷 보기');
  const anchor = {};
  card.props.onFocus({ currentTarget: anchor }); card.props.onBlur(); card.props.onClick({ currentTarget: anchor });
  assert.deepEqual(calls, [['preview', shootingSet, anchor, 0], ['close'], ['choose', shootingSet]]);
  const staticCard = SpaceSetCard({ set: shootingSet, interactive: false, currentCutOrdinal: 3 });
  assert.equal(staticCard.type, 'div'); assert.equal(staticCard.props.onClick, undefined);
  assert.match(renderToStaticMarkup(staticCard), /현재 선택 · 3번째 컷/);
});


test('catalog cards keep short names and cut count; reference detail stays outside the catalog card', () => {
  const id = 'horizon-sequence-figma-s05-v2';
  const html = renderToStaticMarkup(React.createElement(SpaceSetCard, { set: { id, members: members(4) }, selected: true }));
  assert.match(html, /aria-pressed="true"/);
  assert.match(html, /4컷/);
  assert.ok(html.includes(spaceSetLabels.spaceSetDisplayName({ id })));
  assert.doesNotMatch(html, /sb-set-reference-facts|sb-set-description/);
});
