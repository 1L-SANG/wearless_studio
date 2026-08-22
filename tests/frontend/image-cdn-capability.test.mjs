import test from 'node:test';
import assert from 'node:assert/strict';

import { thumbUrlForConfig } from '../../src/lib/imageCdn.js';

const CONFIG = { enabled: true, cdn: 'https://images.wearless.kr' };

test('API asset capabilities bypass Cloudflare image transformations', () => {
  for (const url of [
    '/v1/assets/asset-1/file?e=2',
    '/v1/assets/asset-1/bytes?e=2',
    'https://api.wearless.kr/v1/assets/asset-1/file?e=2',
    'https://api.wearless.kr/v1/assets/asset-1/bytes',
  ]) {
    assert.equal(thumbUrlForConfig(url, 240, undefined, CONFIG), url);
  }
});

test('ordinary public images keep the existing Cloudflare transform', () => {
  const url = 'https://images.wearless.kr/users/u/public-cut.png';

  assert.equal(
    thumbUrlForConfig(url, 240, { quality: 75, fit: 'contain' }, CONFIG),
    'https://images.wearless.kr/cdn-cgi/image/width=240,quality=75,format=auto,fit=contain/' + url,
  );
});
