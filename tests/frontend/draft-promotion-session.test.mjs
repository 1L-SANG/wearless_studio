import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { createDraftPromotionSession } from '../../src/lib/draftPromotionSession.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

class MapStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

test('a refreshed promotion joins the persisted project and reuses uploaded photo assets', () => {
  const storage = new MapStorage();
  const beforeRefresh = createDraftPromotionSession({ storage });
  beforeRefresh.rememberProject('project-1');
  beforeRefresh.rememberAsset('front-local', {
    assetId: 'asset-1',
    url: 'https://img.test/asset-1.jpg',
  });

  const afterRefresh = createDraftPromotionSession({ storage });
  assert.deepEqual(afterRefresh.read(), {
    projectId: 'project-1',
    assets: {
      'front-local': {
        assetId: 'asset-1',
        url: 'https://img.test/asset-1.jpg',
      },
    },
  });

  const syncSource = read('../../src/lib/draftSync.js');
  assert.match(syncSource, /existing \?\? persisted\.projectId \?\? \(await api\.createProject\(\)\)\.id/);
  assert.match(syncSource, /const cached = draftPromotionSession\.read\(\)\.assets\?\.\[p\.imageId\]/);
});

test('starting a genuinely new flow clears the persisted promotion identity', () => {
  const storage = new MapStorage();
  const session = createDraftPromotionSession({ storage });
  session.rememberProject('project-1');
  session.clear();
  assert.deepEqual(createDraftPromotionSession({ storage }).read(), {});
});
