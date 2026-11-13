import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

let vite, api, DB, buildDraft, buildStoryboard;
before(async () => {
  process.env.MOCK_API_LATENCY_SCALE = '0';
  vite = await createServer({ configFile: false, logLevel: 'silent', optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom' });
  ({ api } = await vite.ssrLoadModule('/src/mock/api.js'));
  ({ DB, buildDraft, buildStoryboard } = await vite.ssrLoadModule('/src/mock/db.js'));
});
after(() => vite?.close());

const noComposeMode = project => {
  assert.equal('composeMode' in project, false);
  assert.equal('compose_mode' in project, false);
};

test('mock new and resumed projects and catalogs omit the retired photo quantity setting', async () => {
  noComposeMode(buildDraft().project);
  noComposeMode(await api.createProject());
  for (const step of ['storyboard', 'mannequin', 'generating', 'failed']) {
    noComposeMode(await api.getProject(`in-progress-${step}`));
  }
  assert.equal('composeModes' in await api.getCatalogs(), false);
});

for (const field of ['composeMode', 'compose_mode']) {
  for (const value of ['basic', 'extended', 'simple', null]) {
    test(`mock project PATCH ignores ${field}=${value} and applies remaining settings`, async () => {
      await api.createProject();
      const patched = await api.patchProject(DB.project.id, { [field]: value, copywriting: false });
      noComposeMode(patched);
      noComposeMode(await api.getProject(DB.project.id));
      assert.equal(patched.copywriting, false);
    });
  }
}

test('mock remaining project whitelist still rejects null copywriting and allows clearing mannequin', async () => {
  await api.createProject();
  await assert.rejects(api.patchProject(DB.project.id, { copywriting: null }));
  assert.equal((await api.getProject(DB.project.id)).copywriting, true);
  const patched = await api.patchProject(DB.project.id, { selectedMannequinId: null, status: 'done', adjustCount: 99 });
  assert.equal(patched.selectedMannequinId, null);
  assert.equal(patched.status, 'draft');
  assert.equal(patched.adjustCount, 0);
});

test('mock retired-only project PATCH does not mark the project as changed', async () => {
  await api.createProject();
  DB.project.updatedAt = '2026-10-01T00:00:00Z';
  const before = await api.getProject(DB.project.id);
  assert.deepEqual(await api.patchProject(DB.project.id, { composeMode: 'extended', compose_mode: null }), before);
});

test('mock storyboard builder accepts product colors without a retired mode argument', () => {
  const colors = [{ id: 'base', name: '아이보리', isBase: true }, { id: 'other', name: '네이비' }];
  const storyboard = buildStoryboard(colors, { name: '셔츠', clothingType: 'top', targetGenders: ['women'] });
  assert.ok(storyboard.length > 0);
  assert.ok(storyboard.some(block => block.colorId === 'other'));
  assert.ok(storyboard.filter(block => block.colorId).every(block => ['base', 'other'].includes(block.colorId)));
});
