import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { getPageFontEmbedCSS } from '../../src/features/editor/editorExportFonts.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
import { resolvePageTokens } from '../../src/lib/pageTemplates/tokens.js';
import { cssWeight } from '../../src/features/editor/fontWeights.js';
import { isEmptyTextElement } from '../../src/features/editor/editorGeometry.js';
import { isSpeechBubbleElement } from '../../src/features/editor/editorBubbleFit.js';
import { bakeExportBackdrops, collectExportImageProbes } from '../../src/features/editor/editorExportRaster.js';

const cssUrl = new URL('../../src/styles/tokens.css', import.meta.url);
const tokensCss = readFileSync(cssUrl, 'utf8');
const fontUrl = new URL('../../src/fonts/PretendardVariable.woff2', import.meta.url);
const fontBytes = readFileSync(fontUrl);
const bodyFamily = tokensCss.match(/--font-body:\s*([^;]+);/)[1];
const fontFaces = [...tokensCss.matchAll(/@font-face\s*\{([^}]+)\}/g)].map(([cssText, body]) => {
  const fields = Object.fromEntries([...body.matchAll(/([\w-]+):\s*([^;]+);/g)].map(([, name, value]) => [name, value.trim()]));
  return { cssText, fields };
});

// Execute the editor's real JSX. Hooks stay inert because no browser/effects are
// available; font mapping, weight resolution and the returned style are real.
const editorSource = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
const componentSource = editorSource.slice(editorSource.indexOf('function CanvasElement('), editorSource.indexOf('\nconst IMAGE_IMPORT_COPY'));
const fontMapSource = editorSource.match(/^const FONT_MAP = .*;$/m)[0];
const compiled = await transformWithEsbuild(`${fontMapSource}\n${componentSource}`, 'CanvasElement.jsx', { jsx: 'transform' });
const bindings = { React, useEditedTemplateContrast: el => el, useTitlePlatePreview: () => () => null, useRef: current => ({ current }), useState: initial => [initial, () => {}],
  useCallback: fn => fn, useLayoutEffect() {}, isEmptyTextElement, isSpeechBubbleElement, cssWeight,
  hasTemplateContrastLines: () => false };
const CanvasElement = new Function(...Object.keys(bindings), `${compiled.code}; return CanvasElement;`)(...Object.values(bindings));
const reactText = el => CanvasElement({ el, selected: false, editing: false, preview: false, scale: 1 });

const desc = '단추가 더해진 여밈으로\n차분한 옷차림을 완성해요';
function partDescription(designId, templateId = 'show', fontStyle = 'template') {
  const key = `${templateId}.points`;
  const pageStyle = { templateId, fontStyle, colorMode: 'mono' };
  const blocks = assemblePage({
    storyboard: [
      { id: 'lead', taxonomyVersion: 4, source: 'text', pageSectionKey: key, pageSectionKind: 'points', pageDesignId: designId },
      { id: 'detail', taxonomyVersion: 4, source: 'ai', pageSectionKey: key, pageSectionKind: 'points', pageDesignId: designId,
        slotKey: 'points.detail1', cutType: 'product', shot: 'detail', contentRole: 'detail', detailTargetId: 'button' },
    ],
    cutResults: [{ blockId: 'detail', imageUrl: 'offline:detail', width: 1000, height: 1500 }],
    copyResults: [], product: {}, pageStyle,
    analysis: { detailRecommendations: { candidates: [{ id: 'button', label: '단추' }] },
      partDescriptionCopy: [{ point: '단추', desc }] },
  });
  return blocks.flatMap(block => block.elements).find(el => el.copyRole === 'items.0.desc');
}

for (const templateId of ['emo', 'show']) for (const fontStyle of Object.keys(pageTemplatesCatalog.fontStyles)) {
  test(`WOC11 real editor text uses Pretendard light only for plain descriptions: ${templateId}/${fontStyle}`, () => {
    const body = resolvePageTokens({ templateId, fontStyle }).fonts.body;
    assert.deepEqual([body.font, body.weight], ['Pretendard', 400], 'page font choices must leave the existing body face intact');
    for (const [design, weight, size] of [['PT-TEXT-PHOTO-01', 300, 37], ['PT-PHOTO-CARD-01', 400, 35]]) {
      const element = partDescription(design, templateId, fontStyle);
      assert.deepEqual([element.style.font, element.style.weight, element.style.size], ['Pretendard', weight, size]);
      assert.equal(cssWeight(element.style.font, element.style.weight), weight);
      const markup = renderToStaticMarkup(reactText(element));
      assert.match(markup, new RegExp(`font-weight:${weight}(?:;|")`));
      assert.match(markup, new RegExp(`font-size:${size}px(?:;|")`));
      assert.match(markup, /font-family:var\(--font-body\)/);
      assert.match(markup, /white-space:pre-wrap/);
      assert.ok(markup.includes(desc), 'the explicit two-line sentence survives editor rendering');
    }
  });
}

test('WOC11 both Pretendard CSS aliases declare light through bold using the bundled WOFF2', () => {
  assert.equal(fontBytes.subarray(0, 4).toString('ascii'), 'wOF2');
  assert.equal(fontBytes.readUInt32BE(8), fontBytes.length, 'WOFF2 header length matches the bundled file');
  for (const family of ['"Pretendard Variable"', '"Pretendard"']) {
    const face = fontFaces.find(row => row.fields['font-family'] === family);
    assert.ok(face, `${family} alias must resolve to a declared font face`);
    const bounds = face.fields['font-weight'].split(/\s+/).map(Number);
    assert.deepEqual(bounds, [100, 900]);
    assert.ok(bounds[0] <= 300 && bounds[1] >= 400);
    const source = face.fields.src.match(/url\(["']?([^"')]+)["']?\)/)[1];
    assert.equal(new URL(source, cssUrl).href, fontUrl.href);
    assert.match(face.fields.src, /format\("woff2"\)/);
  }
});

test('WOC11 download clone carries 300/400 and actual bundled font bytes to the SVG boundary', async t => {
  const source = readFileSync(new URL('../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
  const prepare = source.slice(source.indexOf('async function prepareClone('), source.indexOf('\nexport function flattenOnWhite'));
  const capture = source.slice(source.indexOf('async function captureBlockCanvas('), source.indexOf('\nexport async function captureBlockCanvasSlices'));
  const entry = source.slice(source.indexOf('export async function exportBlockPng('), source.indexOf('// ② 전체 블록')).replace(/^export /, '');
  const rendered = [partDescription('PT-TEXT-PHOTO-01'), partDescription('PT-PHOTO-CARD-01')].map(reactText);
  const fetched = [], saves = [];
  let svgBoundary, rasterBoundary, removed = false;

  class Element {
    constructor(style = {}, text = '') {
      this.style = { ...style }; this.text = text; this.children = []; this.dataset = { exportHeight: '160' };
      this.classList = { remove() {} }; this.offsetHeight = 160; this.ownerDocument = document;
    }
    appendChild(node) { this.children.push(node); return node; }
    cloneNode(deep) {
      assert.equal(deep, true);
      const clone = new Element(this.style, this.text);
      clone.children = this.children.map(child => child.cloneNode(true));
      return clone;
    }
    querySelectorAll(selector) { return selector === '.el' ? this.children : []; }
    getBoundingClientRect() { return { x: 0, y: 0, left: 0, top: 0, width: 1000, height: this.offsetHeight }; }
    remove() { removed = true; }
  }
  const sheet = { href: cssUrl.href, cssRules: [] };
  const document = {
    defaultView: { getComputedStyle: () => ({ fontFamily: bodyFamily }) },
    styleSheets: [sheet], body: { appendChild() {} },
    createElement: tag => tag === 'canvas' ? { width: 0, height: 0, getContext: () => ({}) } : new Element(),
    // html-to-image resolves relative CSS URLs through this DOM boundary.
    implementation: { createHTMLDocument() {
      const base = { href: cssUrl.href };
      return { head: { appendChild() {} }, body: { appendChild() {} }, createElement(tag) {
        if (tag === 'base') return base;
        let href;
        return { set href(value) { href = new URL(value, base.href).href; }, get href() { return href; } };
      } };
    } },
  };
  sheet.cssRules = fontFaces.map(({ cssText, fields }) => ({ type: 5, cssText, parentStyleSheet: sheet,
    style: { fontFamily: fields['font-family'], getPropertyValue: key => fields[key] || '' } }));
  const globals = {
    document, HTMLElement: Element, CSSRule: { FONT_FACE_RULE: 5, IMPORT_RULE: 3 },
    getComputedStyle: () => ({ fontFamily: bodyFamily }),
    // Only the exact bundled font may be read. The native network fetch is never called.
    fetch: async url => {
      assert.equal(url, fontUrl.href);
      fetched.push(url);
      return new Response(new Blob([readFileSync(new URL(url))], { type: 'font/woff2' }), { status: 200 });
    },
    FileReader: class {
      readAsDataURL(blob) {
        blob.arrayBuffer().then(bytes => {
          this.result = `data:${blob.type};base64,${Buffer.from(bytes).toString('base64')}`;
          this.onloadend();
        }, error => this.onerror(error));
      }
    },
  };
  for (const [name, value] of Object.entries(globals)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
    t.after(() => previous ? Object.defineProperty(globalThis, name, previous) : delete globalThis[name]);
  }
  const block = new Element();
  rendered.forEach(node => block.appendChild(new Element(node.props.style, node.props.children)));
  const scope = {
    document, EXPORT_WIDTH: 1000, PIXEL_RATIO: 2, CHROME_SELECTOR: '.editor-chrome', getPageFontEmbedCSS,
    replaceGifPosters() {}, preflightImages: async () => ({ softFailed: 0 }), bakeExportBackdrops, collectExportImageProbes,
    toSvg: async (clone, options) => {
      svgBoundary = { clone, options };
      assert.notEqual(clone, block);
      assert.deepEqual(clone.children.map(child => child.style.fontWeight), [300, 400]);
      assert.deepEqual(clone.children.map(child => child.style.fontSize), [37, 35]);
      assert.ok(clone.children.every(child => child.text === desc));
      assert.match(options.fontEmbedCSS, /font-family:\s*"Pretendard Variable"/);
      assert.match(options.fontEmbedCSS, /font-weight:\s*100 900/);
      const embedded = options.fontEmbedCSS.match(/base64,([^"')\s]+)/)[1];
      assert.deepEqual(Buffer.from(embedded, 'base64'), fontBytes);
      assert.doesNotMatch(options.fontEmbedCSS, /url\(["']?(?:https?:|\.\.)/);
      return 'offline-font-svg-boundary';
    },
    rasterizeExportSvg: async (svg, options) => { rasterBoundary = { svg, options }; return { width: options.width, height: options.height }; },
    recordClientAction() {}, canvasToBlob: async () => new Blob(['offline-raster-boundary']), releaseExportCanvas() {},
    exportFileName: () => 'font-check.png', packageBlockDownload: async blob => ({ blob }), saveBlob: (blob, name) => saves.push({ blob, name }),
  };
  const exportBlockPng = new Function(...Object.keys(scope), `${prepare}\n${capture}\n${entry}\nreturn exportBlockPng;`)(...Object.values(scope));
  const result = await exportBlockPng(block, 'font check', 0);
  assert.equal(result.softFailed, 0);
  assert.deepEqual(fetched, [fontUrl.href]);
  assert.equal(svgBoundary.options.width, 1000);
  assert.equal(rasterBoundary.svg, 'offline-font-svg-boundary');
  assert.deepEqual([rasterBoundary.options.width, rasterBoundary.options.height], [2000, 320]);
  assert.deepEqual(block.children.map(child => child.style.fontWeight), [300, 400]);
  assert.equal(saves.length, 1);
  assert.equal(removed, true);
});
