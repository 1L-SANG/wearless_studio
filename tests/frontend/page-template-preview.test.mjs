import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pageTemplatesCatalog as catalog } from '../../src/lib/pageTemplates/catalog.js';
import { previewScrollTiming, observePreviewMotion } from '../../src/lib/pageTemplates/previewMotion.js';
import { outfitHarness, nodes, textOf } from './helpers/outfitHarness.mjs';

const template = catalog.templates[0];

test('versioned full-page preview files exist at the catalog paths; fallback photos remain available', () => {
  for (const item of catalog.templates) {
    assert.match(item.preview.src, new RegExp(`^/templates/previews/preview-${item.id}-v[1-9]\\d*\\.webp$`));
    const bytes = readFileSync(new URL(`../../public${item.preview.src}`, import.meta.url));
    assert.equal(bytes.toString('ascii', 0, 4), 'RIFF');
    assert.equal(bytes.toString('ascii', 8, 12), 'WEBP');
    assert.ok(bytes.length > 0);
    assert.equal(item.preview.fallbackSrc, `/models/demo/${item.id === 'emo' ? 'cut1' : 'cut2'}.webp`);
    assert.ok(item.preview.description);
  }
});

test('every overflowing preview reaches the bottom in six seconds regardless of image or card height', () => {
  for (const [imageHeight, viewportHeight, distance] of [[201, 200, 1], [1000, 200, 800], [2000, 200, 1800], [4000, 274, 3726], [12000, 274, 11726], [7000, 600, 6400]]) {
    assert.deepEqual(previewScrollTiming(imageHeight, viewportHeight), { distance, duration: 6000 });
  }
  assert.deepEqual(previewScrollTiming(200, 200), { distance: 0, duration: 0 });
  assert.deepEqual(previewScrollTiming(1000, 0), { distance: 0, duration: 0 });
});

function motion({ hovered = true, focused = false, reduced = false, hover = true } = {}) {
  const animations = [], listeners = new Map(); let resize, disconnected = false, imageHeight = 1000;
  const preference = matches => ({ matches,
    addEventListener: (_event, fn) => listeners.set(fn, true), removeEventListener: (_event, fn) => listeners.delete(fn),
  });
  const reducedMedia = preference(reduced), hoverMedia = preference(hover);
  const viewport = { clientHeight: 200 };
  const image = { getBoundingClientRect: () => ({ height: imageHeight }), animate: (frames, options) => {
    const animation = { frames, options, cancelled: false, cancel() { this.cancelled = true; } };
    animations.push(animation); return animation;
  } };
  const cleanup = observePreviewMotion({ viewport, image, hovered, focused,
    media: query => query.includes('reduced-motion') ? reducedMedia : hoverMedia,
    makeObserver: callback => { resize = callback; return { observe() {}, disconnect() { disconnected = true; } }; },
  });
  return { animations, viewport, reducedMedia, hoverMedia, cleanup, listeners,
    resize(height) { imageHeight = height; resize(); },
    refresh() { [...listeners.keys()].forEach(fn => fn()); }, get disconnected() { return disconnected; },
  };
}

test('hover scroll stays linear for six seconds, pauses at the bottom and resets on leave', () => {
  const m = motion();
  assert.deepEqual(m.animations[0].frames, [{ transform: 'translateY(0)' }, { transform: 'translateY(-800px)' }]);
  assert.deepEqual(m.animations[0].options, { duration: 6000, endDelay: 800, easing: 'linear', fill: 'forwards' });
  m.resize(2000);
  assert.equal(m.animations[0].cancelled, true);
  assert.equal(m.animations[1].options.duration, 6000);
  assert.equal(m.animations[1].frames.at(-1).transform, 'translateY(-1800px)');
  m.cleanup();
  assert.equal(m.animations[1].cancelled, true);
  assert.equal(m.listeners.size, 0);
  assert.equal(m.disconnected, true);
});

test('keyboard focus also scrolls; reduced motion, touch-only hover and inactive cards do not', () => {
  for (const [options, count] of [
    [{ hovered: false, focused: true, hover: false }, 1],
    [{ focused: true, reduced: true }, 0],
    [{ hover: false }, 0], [{ hovered: false }, 0],
  ]) {
    const m = motion(options); assert.equal(m.animations.length, count); m.cleanup();
  }
  const m = motion();
  m.reducedMedia.matches = true; m.refresh();
  assert.equal(m.animations[0].cancelled, true);
  assert.equal(m.animations.length, 1);
  m.cleanup();
});

test('radio choice, hover, focus and full-view button remain distinct interactions', async t => {
  const h = await outfitHarness(); t.after(() => h.close());
  const { TemplateOption, TemplatePreviewImage } = await h.load('/src/features/analysis/PageTemplatePicker.jsx');
  const selected = [], previews = [], props = { template, value: template.id, onChange: id => selected.push(id), onPreview: id => previews.push(id) };
  let tree = h.render(TemplateOption, props);
  tree.props.onPointerEnter({ pointerType: 'touch' });
  tree = h.render(TemplateOption, props);
  assert.equal(nodes(tree).find(n => n.type === TemplatePreviewImage).props.hovered, false);
  tree.props.onPointerEnter({ pointerType: 'mouse' });
  tree = h.render(TemplateOption, props);
  assert.equal(nodes(tree).find(n => n.type === TemplatePreviewImage).props.hovered, true);
  tree.props.onPointerLeave();
  tree.props.onFocusCapture({ target: { matches: selector => selector === ':focus-visible' } });
  tree = h.render(TemplateOption, props);
  assert.equal(nodes(tree).find(n => n.type === TemplatePreviewImage).props.focused, true);
  tree.props.onBlurCapture({ currentTarget: { contains: () => false }, relatedTarget: null });
  tree = h.render(TemplateOption, props);
  assert.equal(nodes(tree).find(n => n.type === TemplatePreviewImage).props.focused, false);
  const button = nodes(tree).find(n => n.type === 'button');
  assert.equal(textOf(button), '전체 보기');
  assert.equal(button.props['aria-label'], '감성형 전체 보기');
  const opener = {};
  props.onPreview = (id, trigger) => { previews.push(id); assert.equal(trigger, opener); };
  tree = h.render(TemplateOption, props);
  nodes(tree).find(n => n.type === 'button').props.onClick({ currentTarget: opener });
  assert.deepEqual(previews, ['emo']); assert.deepEqual(selected, []);
  nodes(tree).find(n => n.type === 'input').props.onChange(); assert.deepEqual(selected, ['emo']);
  assert.equal(nodes(tree).find(n => n.type === 'label').props.children.some(child => child?.type === 'button'), false);
});

const css = readFileSync(new URL('../../src/features/analysis/PageTemplatePicker.module.css', import.meta.url), 'utf8');
function declarations(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const body = css.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))?.[1];
  assert.ok(body, `missing CSS rule: ${selector}`);
  return Object.fromEntries(body.split(';').filter(value => value.trim()).map(value => {
    const colon = value.indexOf(':');
    return [value.slice(0, colon).trim(), value.slice(colon + 1).trim()];
  }));
}

test('the full-view button has a token-backed translucent pill over the photo', async t => {
  const h = await outfitHarness(); t.after(() => h.close());
  const { TemplateOption } = await h.load('/src/features/analysis/PageTemplatePicker.jsx');
  const tree = h.render(TemplateOption, { template, value: template.id });
  const button = nodes(tree).find(node => node.type === 'button');
  assert.match(button.props.className, /previewButton/);
  const pill = declarations('.previewButton:global(.btn)');
  assert.equal(pill.background, 'var(--glass-bg)');
  assert.equal(pill.color, 'var(--fg-1)');
  assert.equal(pill['box-shadow'], '0 0 0 1px var(--ring-strong)');
  assert.match(nodes(tree).find(node => node.type === 'div' && node.props.className?.includes('previewActions')).props.className, /modal-actions/);
});

test('the native full-view dialog overrides the margin reset and centers on both viewport axes', () => {
  const dialog = declarations('.previewDialog');
  assert.equal(dialog.position, 'fixed');
  assert.equal(dialog.inset, '0');
  assert.equal(dialog.margin, 'auto');
  assert.equal(dialog.width, 'min(840px, calc(100vw - 32px))');
  assert.equal(dialog['max-height'], 'calc(100dvh - 32px)');
});

test('a missing long image falls back to the existing sample; a loaded image removes the fallback', async t => {
  const h = await outfitHarness(); t.after(() => h.close());
  const { TemplatePreviewImage } = await h.load('/src/features/analysis/PageTemplatePicker.jsx');
  let tree = h.render(TemplatePreviewImage, { template });
  const long = nodes(tree).find(n => n.type === 'img' && n.props.src === template.preview.src);
  assert.ok(long);
  assert.ok(nodes(tree).some(n => n.type === 'img' && n.props.src === template.preview.fallbackSrc));
  long.props.onError(); tree = h.render(TemplatePreviewImage, { template });
  assert.deepEqual(nodes(tree).filter(n => n.type === 'img').map(n => n.props.src), [template.preview.fallbackSrc]);
  h.runtime.slots = [];
  tree = h.render(TemplatePreviewImage, { template, full: true });
  nodes(tree).find(n => n.type === 'img' && n.props.src === template.preview.src).props.onLoad();
  tree = h.render(TemplatePreviewImage, { template, full: true });
  assert.deepEqual(nodes(tree).filter(n => n.type === 'img').map(n => n.props.src), [template.preview.src]);
  assert.equal(nodes(tree).find(n => n.type === 'img').props.alt, '감성형 상세페이지 견본');
});

test('full-view control opens and closes the selected template dialog without changing pageStyle', async t => {
  const h = await outfitHarness(); t.after(() => h.close());
  const { PageTemplatePicker, TemplateOption, TemplatePreviewDialog } = await h.load('/src/features/analysis/PageTemplatePicker.jsx');
  const changes = [], props = { value: 'emo', onChange: id => changes.push(id) };
  let tree = h.render(PageTemplatePicker, props);
  const opener = {};
  nodes(tree).find(n => n.type === TemplateOption && n.props.template.id === 'show').props.onPreview('show', opener);
  tree = h.render(PageTemplatePicker, props);
  const dialog = nodes(tree).find(n => n.type === TemplatePreviewDialog);
  assert.equal(dialog.props.template.id, 'show');
  assert.equal(dialog.props.returnFocusTo, opener);
  assert.deepEqual(changes, []);
  dialog.props.onClose(); tree = h.render(PageTemplatePicker, props);
  assert.equal(nodes(tree).some(n => n.type === TemplatePreviewDialog), false);
});

test('native preview dialog declares its modal role, focuses inside, dismisses and returns to its actual trigger', async t => {
  const oldDocument = globalThis.document;
  let shown = 0, closed = 0, restored = 0, dismissed = 0;
  const opener = { isConnected: true, focus(options) { assert.equal(options.preventScroll, true); restored++; document.activeElement = this; } };
  const closeButton = { focus(options) { assert.equal(options.preventScroll, true); document.activeElement = this; } };
  globalThis.document = { activeElement: {} };
  t.after(() => { globalThis.document = oldDocument; });
  const h = await outfitHarness();
  const { TemplatePreviewDialog, TemplatePreviewImage } = await h.load('/src/features/analysis/PageTemplatePicker.jsx');
  const tree = h.render(TemplatePreviewDialog, { template, returnFocusTo: opener, onClose: () => dismissed++ }, node => {
    node.props.ref.current = { showModal() { shown++; document.activeElement = this; }, close() { closed++; } };
    const ref = nodes(node).find(child => child.type === 'button').props.ref;
    if (ref) ref.current = closeButton;
  });
  assert.equal(tree.type, 'dialog'); assert.equal(shown, 1);
  assert.equal(tree.props.role, 'dialog');
  assert.equal(tree.props['aria-modal'], 'true');
  assert.equal(document.activeElement, closeButton);
  assert.equal(nodes(tree).find(node => node.type === 'h2').props.id, tree.props['aria-labelledby']);
  assert.equal(textOf(nodes(tree).find(node => node.type === 'h2')), '감성형 전체 보기');
  assert.ok(nodes(tree).some(n => n.props.tabIndex === 0 && n.props['aria-label'] === '감성형 견본 상세페이지'));
  assert.equal(nodes(tree).find(n => n.type === TemplatePreviewImage).props.full, true);
  tree.props.onClick({ target: {}, currentTarget: tree.props.ref.current }); assert.equal(dismissed, 0);
  let prevented = false; tree.props.onCancel({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.equal(dismissed, 1);
  nodes(tree).find(n => n.type === 'button').props.onClick(); assert.equal(dismissed, 2);
  const backdrop = {}; tree.props.onClick({ target: backdrop, currentTarget: backdrop }); assert.equal(dismissed, 3);
  await h.close(); assert.equal(closed, 1); assert.equal(restored, 1);
  assert.equal(document.activeElement, opener);
});
