import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ASSET_CACHE_VERSION,
  assetFileUrl,
  rebaseAssetUrls,
  relativizeAssetUrls,
  resolveAssetCacheVersion,
  versionAssetCapabilityUrl,
} from '../../src/lib/assetUrl.js';

const AID = '123e4567-e89b-42d3-a456-426614174000';

test('default build stays on legacy capability version until rollout is explicit', () => {
  assert.equal(ASSET_CACHE_VERSION, '1');
  assert.equal(resolveAssetCacheVersion(undefined), '1');
  assert.equal(resolveAssetCacheVersion(''), '1');
  assert.equal(resolveAssetCacheVersion('on'), '1');
  assert.equal(
    rebaseAssetUrls(`/v1/assets/${AID}/file`),
    `/v1/assets/${AID}/file?e=1`,
  );
  assert.equal(
    rebaseAssetUrls(`/v1/assets/${AID}/file?e=2`),
    `/v1/assets/${AID}/file?e=1`,
  );
  assert.equal(
    rebaseAssetUrls(`/v1/assets/${AID}/bytes?download=1&e=2`),
    `/v1/assets/${AID}/bytes?download=1&e=1`,
  );
  assert.equal(assetFileUrl(AID), `/v1/assets/${AID}/file?e=1`);
});

test('only explicit build value 2 enables the new capability version', () => {
  const version = resolveAssetCacheVersion('2');

  assert.equal(version, '2');
  assert.equal(
    versionAssetCapabilityUrl(`/v1/assets/${AID}/file`, version),
    `/v1/assets/${AID}/file?e=2`,
  );
});

test('persisted API asset URLs retain the current capability version without host pinning', () => {
  assert.equal(
    relativizeAssetUrls(`https://old-api.example/v1/assets/${AID}/file?e=1`),
    `/v1/assets/${AID}/file?e=1`,
  );
});
