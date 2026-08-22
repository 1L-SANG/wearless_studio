import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ASSET_CACHE_VERSION,
  assetFileUrl,
  rebaseAssetUrls,
  relativizeAssetUrls,
} from '../../src/lib/assetUrl.js';

const AID = '123e4567-e89b-42d3-a456-426614174000';

test('normal client upgrades unversioned and legacy asset capability URLs', () => {
  assert.equal(ASSET_CACHE_VERSION, '2');
  assert.equal(
    rebaseAssetUrls(`/v1/assets/${AID}/file`),
    `/v1/assets/${AID}/file?e=2`,
  );
  assert.equal(
    rebaseAssetUrls(`/v1/assets/${AID}/file?e=1`),
    `/v1/assets/${AID}/file?e=2`,
  );
  assert.equal(
    rebaseAssetUrls(`/v1/assets/${AID}/bytes?download=1&e=1`),
    `/v1/assets/${AID}/bytes?download=1&e=2`,
  );
  assert.equal(assetFileUrl(AID), `/v1/assets/${AID}/file?e=2`);
});

test('persisted API asset URLs retain the current capability version without host pinning', () => {
  assert.equal(
    relativizeAssetUrls(`https://old-api.example/v1/assets/${AID}/file?e=1`),
    `/v1/assets/${AID}/file?e=2`,
  );
});
