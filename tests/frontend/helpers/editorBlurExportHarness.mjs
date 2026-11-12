import { toBytesUrl, isAssetBytesUrl } from '../../../src/features/editor/editorAssetBytes.js';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import JSZip from 'jszip';
import { bakeExportBackdrops, rasterizeBackdropImage, rasterizeExportSvg } from '../../../src/features/editor/editorExportRaster.js';
import { hasBackdropBlur } from '../../../src/features/editor/editorImageBlur.js';
import { isGifImage } from '../../../src/lib/gif/gifMedia.js';

const exportSource = readFileSync(new URL('../../../src/features/editor/editorExport.js', import.meta.url), 'utf8');
const publishSource = readFileSync(new URL('../../../src/features/editor/detailPublish.js', import.meta.url), 'utf8');
const executable = source => source.replace(/^export \{.*\} from .*;\r?\n/gm, '').replace(/^import .+;\r?\n/gm, '').replace(/\bexport (?=(?:async )?(?:function|const))/g, '');

// Real preflight, blur, capture ordering, stitching, split encoding and public
// export entrypoints run here. Only DOM, network and codec boundaries are fake.
export function blurExportHarness(t, { omitBake = false } = {}) {
  const events = [], saves = [], allocations = [];
  class Node {
    constructor(tag = 'div') { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.attributes = {}; this.offsetHeight = 10;
      this.classList = { remove() {} }; }
    setAttribute(name, value) { this.attributes[name] = value; }
    getAttribute(name) { return this.attributes[name] || null; }
    removeAttribute(name) { delete this.attributes[name]; }
    appendChild(child) { child.parentElement?.children.splice(child.parentElement.children.indexOf(child), 1); this.children.push(child); child.parentElement = this; return child; }
    replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); }
    remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); }
    click() { saves.push(this.download); }
    cloneNode() {
      const node = new Node(this.tag);
      node.dataset = { ...this.dataset }; node.attributes = { ...this.attributes }; node.style = { ...this.style };
      node.naturalWidth = this.naturalWidth; node.naturalHeight = this.naturalHeight;
      this.children.forEach(child => node.appendChild(child.cloneNode()));
      return node;
    }
    async decode() { events.push('decode'); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
      if (selector === '*') return all;
      if (selector === '[data-editor-blur]') return all.filter(node => 'editorBlur' in node.dataset);
      if (selector === 'img' || selector === 'img[src]') return all.filter(node => node.tag === 'img');
      return [];
    }
  }
  function canvas(width = 0, height = 0) {
    let data;
    const result = { width, height,
      getContext() { return {
        clearRect() {}, fillRect() {},
        drawImage() {
          data = new Uint8ClampedArray(result.width * result.height * 4);
          for (let y = 0; y < result.height; y++) for (let x = 0; x < result.width; x++) {
            data.set([x < result.width / 2 ? 0 : 255, 60, 120, 255], (y * result.width + x) * 4);
          }
        },
        getImageData: () => ({ data }),
        putImageData(image) { data = image.data; events.push('pixel-bake'); },
      }; },
      toDataURL() { assert.ok(data[4 * (Math.floor(result.width / 2) - 1)] > 0, 'export contains spread pixels'); return `data:image/png;base64,BAKED`; },
      toBlob(callback, type) { events.push('encode'); callback(new Blob(['baked output'], { type })); },
    };
    allocations.push(result);
    return result;
  }
  const document = { body: new Node(), createElement: tag => tag === 'canvas' ? canvas() : new Node(tag) };
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  globalThis.document = document;
  t.after(() => oldDocument ? Object.defineProperty(globalThis, 'document', oldDocument) : delete globalThis.document);
  const source = new Node('img'); source.setAttribute('src', '/v1/assets/backdrop/file'); source.naturalWidth = 100; source.naturalHeight = 100;
  const frame = new Node(); frame.dataset = { editorBlur: '3', blurWidth: '20', blurHeight: '10', blurFit: 'cover' };
  frame.appendChild(new Node('svg')); frame.appendChild(source);
  const block = new Node(); block.appendChild(frame);
  const toSvg = async clone => {
    const wrapper = clone.querySelector('[data-editor-blur]');
    assert.equal(wrapper?.dataset.blurBaked, 'true', 'pixels must be baked before SVG capture');
    assert.equal(wrapper.children.length, 1);
    assert.match(wrapper.children[0].getAttribute('src'), /BAKED/);
    events.push('svg'); return 'baked-svg';
  };
  const scope = {
    waitForEditedContrast: async () => {}, document, JSZip, toBytesUrl, isAssetBytesUrl, ASSET_CACHE_VERSION: 1, hasBackdropBlur, isGifImage, bakeExportBackdrops, rasterizeBackdropImage,
    collectExportImageProbes: async clone => {
      assert.equal(clone.querySelector('[data-editor-blur]').dataset.blurBaked, 'true', 'probes must sample baked pixels');
      events.push('probe'); return [];
    },
    rasterizeExportSvg: (svg, options) => rasterizeExportSvg(svg, options, { createCanvas: canvas, loadImage: async () => ({}) }),
    getPageFontEmbedCSS: async () => '', toSvg, recordClientAction() {},
    clientApiFetch: async url => { assert.match(url, /\/bytes\?e=/); events.push('bytes'); return new Response(new Blob(['image']), { headers: { 'content-type': 'image/png' } }); },
    readClientResponse: (response, method) => response[method](), attachResponseError: error => error,
    FileReader: class { readAsDataURL() { this.result = 'data:image/png;base64,SOURCE'; this.onload(); } },
    URL: { createObjectURL: () => 'blob:download', revokeObjectURL() {} },
    setTimeout: () => 1, clearTimeout() {}, notarize: async blob => ({ blob }),
  };
  const code = executable(omitBake ? exportSource.replaceAll('await bakeExportBackdrops(clone, pixelRatio);', '') : exportSource);
  const api = new Function(...Object.keys(scope), `${code}; return { exportBlockPng, exportLongPng, exportBlocksZip,
    exportStorefrontDownload, captureBlockCanvasSlices, renderStorefrontFiles, EXPORT_PRESETS, MAX_SPLIT_HEIGHT, MAX_JPEG_BYTES };`)(...Object.values(scope));
  const publishScope = { ...scope, ...api, publicationFetch() { throw new Error('unconfigured network'); } };
  const publishDetailPage = new Function(...Object.keys(publishScope), `${executable(publishSource)}; return publishDetailPage;`)(...Object.values(publishScope));
  return { api, block, events, saves, allocations, publishDetailPage };
}
