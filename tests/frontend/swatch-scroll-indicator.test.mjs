import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { getSwatchScrollThumb } from '../../src/features/product-input/swatchScrollIndicator.js';

for (const [label, visible, total, top, expected] of [
  ['비례 크기와 중간 위치', 100, 400, 150, { height: 25, offset: 37.5 }],
  ['최소 높이 24px', 100, 1000, 450, { height: 24, offset: 38 }],
  ['위쪽 탄성 스크롤 제한', 100, 400, -50, { height: 25, offset: 0 }],
  ['아래쪽 탄성 스크롤 제한', 100, 400, 900, { height: 25, offset: 75 }],
  ['짧은 트랙 안에 제한', 20, 100, 80, { height: 20, offset: 0 }],
  ['같은 높이면 숨김', 100, 100, 0, null],
  ['내용이 적으면 숨김', 100, 80, 0, null],
  ['측정 전에는 숨김', 0, 0, 0, null],
]) {
  test(`스와치 표시기 계산: ${label}`, () => {
    assert.deepEqual(getSwatchScrollThumb(visible, total, top), expected);
  });
}

const source = readFileSync(new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url), 'utf8');
const pickerSource = source.slice(source.indexOf('const chipWidth'), source.indexOf('// build file metas'));
const { code } = await transformWithEsbuild(pickerSource, 'ColorSwatchPicker.jsx', { jsx: 'transform' });
const find = (tree, className) => {
  if (!tree || typeof tree !== 'object') return null;
  if (tree.props.className === className) return tree;
  return tree.props.children.flat(Infinity).map((child) => find(child, className)).find(Boolean);
};

function mount(mobile, visible = 100, total = 400) {
  const effects = [], cleanups = [], frames = new Map(), listeners = new Map(), resizeListeners = new Map();
  let resize, disconnected = false, stateWrites = 0, frameId = 0;
  const grid = { clientHeight: visible, scrollHeight: total, scrollTop: 0,
    addEventListener: (event, fn) => listeners.set(event, fn),
    removeEventListener: (event) => listeners.delete(event), children: [] };
  const properties = new Map();
  const track = { hidden: true, style: { setProperty: (key, value) => properties.set(key, value) } };
  const bindings = {
    React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    useState: (value) => [value, () => { stateWrites++; }], useRef: () => ({ current: null }),
    useEffect: (effect) => effects.push(effect), getSwatchScrollThumb, Icon: 'Icon',
    requestAnimationFrame: (fn) => { frames.set(++frameId, fn); return frameId; },
    cancelAnimationFrame: (id) => frames.delete(id),
    ResizeObserver: class {
      constructor(fn) { resize = fn; }
      observe() {}
      disconnect() { disconnected = true; }
    },
    window: { addEventListener: (event, fn) => resizeListeners.set(event, fn),
      removeEventListener: (event) => resizeListeners.delete(event) },
  };
  const Picker = new Function(...Object.keys(bindings), `${code}; return ColorSwatchPicker;`)(...Object.values(bindings));
  const picks = [];
  const tree = Picker({ mobile, swatchColors: [{ id: 'navy', label: '네이비', hex: 'navy' }],
    name: '', value: 'navy', onChange: (id) => picks.push(id), onNameChange() {} });
  const gridNode = find(tree, 'swatch-grid');
  if (gridNode.props.ref) gridNode.props.ref.current = grid;
  const indicator = find(tree, 'swatch-scroll-track');
  if (indicator) indicator.props.ref.current = track;
  for (const effect of effects) cleanups.push(effect());
  const flush = () => { const batch = [...frames.values()]; frames.clear(); batch.forEach((fn) => fn()); };
  flush();
  return { tree, grid, track, indicator, properties, picks, listeners, resizeListeners, frames, flush,
    resize: () => resize(), stateWrites: () => stateWrites,
    cleanup: () => cleanups.forEach((fn) => fn?.()), disconnected: () => disconnected };
}

test('모바일에서만 넘치는 스와치 표시기를 보여준다', () => {
  const overflowing = mount(true);
  assert.equal(overflowing.track.hidden, false);
  assert.equal(overflowing.indicator.props['aria-hidden'], 'true');
  assert.equal(overflowing.properties.get('--swatch-thumb-height'), '25px');
  assert.equal(mount(true, 100, 100).track.hidden, true);
  assert.equal(mount(true, 100, 80).track.hidden, true);
  assert.equal(mount(false).indicator, undefined);
});

test('스크롤은 프레임당 한 번 DOM만 갱신하고 리사이즈 후 넘침을 다시 계산한다', () => {
  const h = mount(true);
  const writes = h.stateWrites();
  h.grid.scrollTop = 150;
  h.listeners.get('scroll')();
  h.listeners.get('scroll')();
  assert.equal(h.frames.size, 1);
  h.flush();
  assert.equal(h.properties.get('--swatch-thumb-offset'), '37.5px');
  assert.equal(h.stateWrites(), writes);
  h.grid.clientHeight = 200;
  h.resize(); h.flush();
  assert.equal(h.properties.get('--swatch-thumb-height'), '100px');
  assert.equal(h.properties.get('--swatch-thumb-offset'), '75px');
  h.grid.clientHeight = 400;
  h.resizeListeners.get('resize')(); h.flush();
  assert.equal(h.track.hidden, true);
  h.grid.clientHeight = 100;
  h.resize(); h.flush();
  assert.equal(h.track.hidden, false);
  h.listeners.get('scroll')();
  h.cleanup();
  assert.equal(h.frames.size, 0);
  assert.equal(h.listeners.size, 0);
  assert.equal(h.resizeListeners.size, 0);
  assert.equal(h.disconnected(), true);
});

test('데스크톱은 기존 그리드 구조와 색상 선택 동작을 유지한다', () => {
  const h = mount(false);
  assert.deepEqual(h.tree.props.children.map((child) => child.props.className), ['color-pick-head', 'swatch-grid']);
  assert.equal(find(h.tree, 'swatch-scroll-area'), undefined);
  assert.equal(h.listeners.size, 0);
  assert.equal(h.resizeListeners.size, 0);
  find(h.tree, 'swatch on').props.onClick();
  assert.deepEqual(h.picks, ['navy']);
});
