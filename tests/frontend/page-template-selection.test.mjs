import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { defaultPageStyle, selectTemplateStyle } from '../../src/lib/pageTemplates/pageStyle.js';

let vite, mock, db, sync;
before(async () => {
  vite = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom', logLevel: 'silent' });
  ({ api: mock } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB: db } = await vite.ssrLoadModule('/src/mock/db.js'));
  sync = await vite.ssrLoadModule('/src/lib/draftSync.js');
});
after(async () => vite?.close());

test('guest style is merged into the draft and the next project reads the last choice', async () => {
  const pageStyle = selectTemplateStyle('show');
  const saved = await mock.saveAnalysis(null, { pageStyle });
  assert.deepEqual(saved.pageStyle, pageStyle);
  await mock.resetInputDraft();
  assert.deepEqual(await mock.getLastPageStyle(), pageStyle);
});

test('mock locks all style fields once a mannequin job exists and accepts the identical value', async () => {
  const prior = { mannequins: db.mannequins, jobs: db.project.latestJobs, style: db.analysis.pageStyle };
  const pageStyle = selectTemplateStyle('show');
  db.analysis.pageStyle = pageStyle;
  db.project.latestJobs = { ...db.project.latestJobs, mannequin: { id: 'started', status: 'error' } };
  try {
    for (const patch of [{ templateId: 'emo' }, { fontStyle: 'serif' }, { colorMode: 'point' }, { updatedAt: '2026-10-29T00:00:00Z' }]) {
      await assert.rejects(mock.saveAnalysis(db.project.id, { pageStyle: { ...pageStyle, ...patch } }), { code: 'page_style_locked', status: 409 });
    }
    assert.deepEqual((await mock.saveAnalysis(db.project.id, { pageStyle })).pageStyle, pageStyle);
  } finally { db.mannequins = prior.mannequins; db.project.latestJobs = prior.jobs; db.analysis.pageStyle = prior.style; }
});

test('draft promotion sends the whole analysis including pageStyle before the next step', async () => {
  const pageStyle = selectTemplateStyle('emo');
  const apiModule = await vite.ssrLoadModule('/src/lib/api/index.js');
  const client = apiModule.api, original = {};
  const sent = [];
  const mocks = {
    createProject: async () => ({ id: 'promoted-style' }), uploadProductPhotos: async (_id, product) => product,
    saveProduct: async () => {}, saveAnalysis: async (_id, a) => { sent.push(a); return a; },
    patchProject: async () => {}, getCustomMatchDraft: () => null, clearCustomMatchDraft: async () => {},
  };
  for (const [name, value] of Object.entries(mocks)) { original[name] = client[name]; client[name] = value; }
  try {
    await sync.promoteDraftToProject({ product: { name: '니트', clothingType: 'top', colors: [] },
      photos: [], analysis: { pageStyle }, composeMode: 'basic' });
    assert.deepEqual(sent[0].pageStyle, pageStyle);
  } finally { for (const [name, value] of Object.entries(original)) client[name] = value; }
});

test('PC draft restoration retains the chosen template with analysis', async () => {
  const pageStyle = selectTemplateStyle('show');
  await mock.restoreInputDraft({ name: '휴대폰에서 입력한 옷', colors: [] }, { pageStyle });
  assert.deepEqual((await mock.getAnalysis(null)).pageStyle, pageStyle);
  assert.equal(defaultPageStyle().fontStyle, 'template');
  assert.equal(defaultPageStyle().colorMode, 'mono');
});
