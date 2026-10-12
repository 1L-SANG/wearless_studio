import test, { before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createServer } from 'vite';
import { ensureSections, normalizeBoard } from '../../src/lib/sections.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { classifyEditorAssetInfo } from '../../src/lib/editorAssetInfo.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { hasSelectableGenerationExamples, selectGenerationExamples } from '../../src/lib/generationExamples.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';
import { isSupportedCutType } from '../../src/lib/storyboardTaxonomy.js';

const oldCut = { id: 'removed', source: 'ai', cutType: 'mirror', shot: 'full',
  sectionRole: 'styling', contentRole: 'realWear', exampleId: 'old-example', taxonomyVersion: 3 };
const supported = { id: 'current', source: 'ai', cutType: 'styling', shot: 'full', direction: 'front',
  sectionRole: 'styling', contentRole: 'coordination', exampleId: 'current-example', taxonomyVersion: 3 };

test('old saved cuts and their dependent cards are ignored before recipe normalization', () => {
  const input = [oldCut, { ...supported, id: 'dependent', similarSourceBlockId: 'removed' }, supported,
    { id: 'mine', source: 'mine', cutType: null, contentRole: 'custom', sectionRole: 'styling', taxonomyVersion: 3 }];
  for (const normalize of [ensureSections, normalizeBoard]) {
    assert.deepEqual(normalize(input).map(block => block.id), ['current', 'mine']);
  }
  assert.equal(input[0].cutType, 'mirror', 'reading does not mutate the saved input');
});

test('credit estimates and failed-cut retry do not schedule removed cut types', () => {
  assert.equal(uniqueGenerationCutCount([null, oldCut, supported]), 1);
  assert.equal(buildFailedCutRetry([oldCut], 'removed'), null);
});

test('old examples cannot be selected through stale gallery options or a direct selection', () => {
  const example = { id: 'old-example', cutType: 'mirror', shot: 'full', gender: 'women',
    applicableClothingTypes: ['top'], variants: ['all'], setOnly: true };
  const options = { cutType: 'mirror', shot: 'full', gender: 'women', clothingType: 'top', appendSetOnly: true };
  assert.equal(hasSelectableGenerationExamples([example], options), false);
  assert.deepEqual(selectGenerationExamples([example], options), []);
  assert.deepEqual(generationExampleSelectionPatch(supported, example), { patch: {}, settingsReset: false });
});

test('old generated assets cannot offer worn-image actions even with stale kind metadata', () => {
  for (const info of [{ cutType: 'mirror' }, { kind: 'worn', recipe: { cutType: 'mirror' } }]) {
    assert.equal(classifyEditorAssetInfo(info), 'unknown');
  }
  assert.equal(classifyEditorAssetInfo({ kind: 'upload', cutType: 'mirror' }), 'upload');
});

test('HTTP refuses explicit removed cut requests before posting or signaling a job', async () => {
  const source = readFileSync(new URL('../../src/lib/api/httpAdapter.js', import.meta.url), 'utf8');
  const method = source.slice(source.indexOf('  async generateImage('), source.lastIndexOf('\n};'));
  let calls = 0, started = 0;
  const adapter = new Function('http', 'pollJob', 'recordClientAction', 'isSupportedCutType',
    `return ({ ${method} });`)(async () => { calls += 1; return { data: {} }; }, () => {}, () => {}, isSupportedCutType);
  for (const request of [{ mode: 'new', cutType: 'mirror' },
    { mode: 'vary', variationMode: 'similar', source: { src: '/old.png', cutType: 'mirror' } }]) {
    await assert.rejects(adapter.generateImage('p', { ...request, onJobStarted: () => { started += 1; } }),
      { code: 'unknown_cut_type', status: 400, requestNotSent: true });
  }
  assert.equal(calls, 0);
  assert.equal(started, 0);
});

let vite, mock, base, DB;
before(async () => {
  vite = await createServer({
    configFile: false, logLevel: 'silent', root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, hmr: false, ws: false },
    optimizeDeps: { noDiscovery: true, include: [] },
  });
  ({ mockAdapter: mock } = await vite.ssrLoadModule('/src/lib/api/mockAdapter.js'));
  ({ api: base } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());
beforeEach(() => {
  DB.account.credits = 200;
  DB.project.status = 'draft';
  DB.storyboard = [];
  DB.editorBlocks = [];
  DB.assets = {};
  DB.wardrobe = { misc: [{ id: 'old', src: '/old.png', cutType: 'mirror', ai: true }] };
});

test('mock rejects old new-image and similar-image requests before jobs, assets or charges', async () => {
  let started = 0;
  for (const request of [{ mode: 'new', cutType: 'mirror' },
    { mode: 'vary', variationMode: 'similar', source: { src: '/old.png' } }]) {
    await assert.rejects(mock.generateImage('p', { ...request, onJobStarted: () => { started += 1; } }),
      { code: 'unknown_cut_type', status: 400 });
  }
  assert.equal(started, 0);
  assert.equal(DB.account.credits, 200);
  assert.equal(DB.wardrobe.misc.length, 1);
});

test('mock detail-page entry points reject an old saved cut before starting generation', async () => {
  DB.storyboard = [oldCut];
  for (const method of ['startDetailPage', 'generateDetailPage']) {
    await assert.rejects(base[method]('p'), { code: 'unknown_cut_type', status: 400 });
    assert.equal(DB.project.status, 'draft');
    assert.equal(DB.account.credits, 200);
    assert.deepEqual(DB.editorBlocks, []);
  }
});
