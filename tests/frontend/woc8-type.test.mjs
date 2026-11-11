import test from 'node:test';
import assert from 'node:assert/strict';
import { pageTemplatesCatalog as catalog, getTemplate, validateCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import { defaultPageStyle, selectFontStyle, validPageStyle, analysisPageStyle, selectTemplateStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { loadStoryboardComponent, nodes, textOf, hookHarness } from './helpers/storyboardComponents.mjs';

const loadPicker = async (source = catalog) => {
  const h = hookHarness();
  const Carousel = await loadStoryboardComponent('StoryboardStyleCarousel.jsx', 'StoryboardStyleCarousel', {
    ...h.hooks, useLayoutEffect: h.hooks.useEffect, Icon: 'icon',
  });
  const Picker = await loadStoryboardComponent('StoryboardThemePicker.jsx', 'StoryboardThemePicker', {
    pageTemplatesCatalog: source, getTemplate: id => getTemplate(id, source),
    resolvePageTokens: style => resolvePageTokens(style, null, source), StoryboardStyleCarousel: Carousel,
  });
  return props => h.render(Carousel, Picker(props).props);
};
const find = (tree, name) => {
  const node = nodes(tree).find(candidate => candidate.props?.['aria-label'] === name);
  assert.ok(node, `${name} is rendered`);
  return node;
};
const specimen = tree => nodes(tree).find(node => node.props?.['data-font-style']);
const keyEvent = (key, within = true) => ({ key, target: {}, currentTarget: { contains: () => within },
  prevented: false, preventDefault() { this.prevented = true; } });

test('WOC8 a removed stored font falls back without replacing the selected template or tone', () => {
  const styles = catalog.fontStyles;
  const stored = { ...defaultPageStyle(), templateId: 'show', colorMode: 'point', fontStyle: 'serif', updatedAt: '2026-11-11T12:00:00Z' };
  const before = structuredClone(stored);
  try {
    catalog.fontStyles = Object.fromEntries(Object.entries(styles).filter(([key]) => key !== 'serif'));
    const normalized = analysisPageStyle(stored);
    assert.equal(normalized.templateId, 'show'); assert.equal(normalized.colorMode, 'point');
    assert.equal(normalized.fontStyle, catalog.defaults.fontStyle); assert.equal(normalized.updatedAt, stored.updatedAt);
    assert.ok(validPageStyle(normalized)); assert.deepEqual(stored, before);
    assert.equal(selectFontStyle(stored, 'boldGothic').colorMode, 'point');
    assert.equal(selectFontStyle(stored, 'boldGothic').templateId, 'show');
  } finally { catalog.fontStyles = styles; }
});

test('WOC8 template selection follows the catalog default when the template font option is removed', () => {
  const styles = catalog.fontStyles, defaults = catalog.defaults;
  const stored = { ...defaultPageStyle(), colorMode: 'point' };
  try {
    catalog.fontStyles = Object.fromEntries(Object.entries(styles).filter(([key]) => key !== 'template'));
    catalog.defaults = { ...defaults, fontStyle: 'boldGothic' };
    assert.equal(validateCatalog(catalog), catalog);
    const next = selectTemplateStyle('show', '2026-11-11T12:00:00Z', stored);
    assert.equal(next.fontStyle, 'boldGothic'); assert.equal(next.colorMode, 'point');
    assert.equal(next.templateId, 'show'); assert.equal(validPageStyle(next), true);
  } finally { catalog.fontStyles = styles; catalog.defaults = defaults; }
});

test('WOC8 one current two-line specimen has catalog role typography for every template and style', async () => {
  const Picker = await loadPicker();
  for (const templateId of ['emo', 'show']) for (const value of Object.keys(catalog.fontStyles)) {
    const tree = Picker({ templateId, value });
    const sample = specimen(tree);
    assert.ok(sample, 'the current value is exposed on its specimen');
    assert.equal(nodes(tree).filter(node => node.props?.['data-font-style']).length, 1);
    assert.equal(sample.props['data-font-style'], value);
    const label = value === 'template' ? templateId === 'show' ? '기본' : '다정'
      : value === 'plainGothic' ? catalog.fontStyleLabels.plainGothic : value === 'serif' ? '매거진' : '볼드';
    assert.equal(sample.props.title, label);
    assert.equal(sample.props['aria-label'], label);
    const lines = nodes(sample).filter(node => node.type === 'text');
    assert.equal(lines.length, 2);
    for (const [index, role] of ['labelEn', 'titleKo'].entries()) {
      const font = resolvePageTokens({ templateId, fontStyle: value }).fonts[role];
      assert.deepEqual(Object.fromEntries(['fontFamily', 'fontWeight', 'fontStyle', 'letterSpacing', 'textAnchor', 'x'].map(key => [key, lines[index].props[key]])), {
        fontFamily: font.font, fontWeight: font.weight, fontStyle: font.italic ? 'italic' : 'normal',
        letterSpacing: `${font.trackingEm}em`, textAnchor: 'middle', x: '50%',
      });
      const word = index ? '웨어리스' : 'wearless';
      assert.equal(textOf(lines[index]), font.uppercase ? word.toUpperCase() : word);
    }
    assert.deepEqual(nodes(tree).filter(node => node.type === 'button').map(node => node.props['aria-label']),
      ['이전 글자 스타일', '다음 글자 스타일']);
  }
});

test('WOC8 actual arrows wrap the catalog order and each click changes the controlled value immediately', async () => {
  const Picker = await loadPicker();
  let value = 'template';
  const chosen = [];
  const render = () => Picker({ value, onChange: next => { chosen.push(next); value = next; } });
  for (let i = 0; i < 4; i++) find(render(), '다음 글자 스타일').props.onClick();
  for (let i = 0; i < 2; i++) find(render(), '이전 글자 스타일').props.onClick();
  assert.deepEqual(chosen, ['plainGothic', 'serif', 'boldGothic', 'template', 'boldGothic', 'serif']);
  assert.equal(specimen(render()).props['data-font-style'], 'serif');
});

test('WOC8 arrow keys change style only inside the focused group and disabled controls stay inert', async () => {
  const Picker = await loadPicker();
  const chosen = [];
  const group = find(Picker({ value: 'template', onChange: value => chosen.push(value) }), '글자 스타일');
  assert.equal(typeof group.props.onKeyDown, 'function');
  for (const [key, within, expected] of [['ArrowLeft', true, true], ['ArrowRight', true, true], ['ArrowDown', true, false], ['ArrowRight', false, false]]) {
    const event = keyEvent(key, within);
    group.props.onKeyDown(event);
    assert.equal(event.prevented, expected);
  }
  assert.deepEqual(chosen, ['boldGothic', 'template']);
  const disabledTree = Picker({ value: 'template', disabled: true, onChange: value => chosen.push(value) });
  for (const button of nodes(disabledTree).filter(node => node.type === 'button')) {
    assert.equal(button.props.disabled, true);
    button.props.onClick();
  }
  const disabledEvent = keyEvent('ArrowRight');
  find(disabledTree, '글자 스타일').props.onKeyDown(disabledEvent);
  assert.equal(disabledEvent.prevented, false);
  assert.deepEqual(chosen, ['boldGothic', 'template']);
});

test('WOC8 catalog additions, removal and ordering drive arrow choices, display-only dots and fallback labels', async () => {
  const source = structuredClone(catalog);
  source.fontStyles = { future: { labelEn: { font: 'Roboto Mono', weight: 500, italic: true, uppercase: false, trackingEm: .12 } }, template: source.fontStyles.template };
  const Picker = await loadPicker(source);
  let value = 'future';
  const render = () => Picker({ value, onChange: next => { value = next; } });
  assert.equal(specimen(render()).props.title, 'future');
  const dots = nodes(render()).filter(node => node.props?.['data-font-style-dot']);
  assert.deepEqual(dots.map(node => node.props['data-font-style-dot']), ['future', 'template']);
  assert.deepEqual(dots.map(node => node.props['data-active']), [true, false]);
  for (const dot of dots) {
    assert.equal(dot.type, 'span');
    assert.equal(dot.props.onClick, undefined);
    assert.equal(dot.props.tabIndex, undefined);
  }
  find(render(), '이전 글자 스타일').props.onClick();
  assert.equal(value, 'template');
  find(render(), '다음 글자 스타일').props.onClick();
  assert.equal(value, 'future');
  source.fontStyleLabels = { future: '새 서체' };
  assert.equal(specimen(render()).props.title, '새 서체');
  assert.equal(specimen(render()).props['aria-label'], '새 서체');
  source.fontStyles = { future: source.fontStyles.future };
  for (const button of nodes(render()).filter(node => node.type === 'button')) {
    assert.equal(button.props.disabled, true);
    button.props.onClick();
  }
  assert.equal(value, 'future');
});

test('WOC8 catalog accepts added or removed style IDs and requires the default to remain present', () => {
  const source = structuredClone(catalog);
  source.fontStyles.future = { labelEn: { font: 'Roboto Mono', weight: 500 }, titleKo: null };
  delete source.fontStyles.serif;
  source.defaults.fontStyle = 'future';
  assert.equal(validateCatalog(source), source);
  source.fontStyles.future.labelEn.font = 'Unsupported Font';
  assert.throws(() => validateCatalog(source), /font style/);
  delete source.fontStyles.future;
  assert.throws(() => validateCatalog(source), /default page style/);
});

test('WOC8 saving accepts catalog additions and rejects removed, inherited and malformed style IDs', t => {
  const original = catalog.fontStyles;
  t.after(() => { catalog.fontStyles = original; });
  catalog.fontStyles = { ...original, future: { labelEn: null, titleKo: null } };
  delete catalog.fontStyles.serif;
  const current = defaultPageStyle();
  const next = selectFontStyle(current, 'future', '2026-11-11T01:00:00Z');
  assert.equal(next.fontStyle, 'future');
  assert.equal(validPageStyle(next), true);
  for (const value of ['serif', 'toString', 'unknown', null, [], {}]) {
    assert.equal(validPageStyle({ ...current, fontStyle: value }), false);
    assert.throws(() => selectFontStyle(current, value), /invalid_page_style/);
  }
});
