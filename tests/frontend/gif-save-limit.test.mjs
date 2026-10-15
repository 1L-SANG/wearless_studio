import test from 'node:test';
import assert from 'node:assert/strict';
import { uploadWardrobeGif } from '../../src/lib/api/gifWardrobe.js';

const result = (bytes) => ({
  blob: new Blob([new Uint8Array(bytes)], { type: 'image/gif' }),
  jpgBlob: new Blob(['poster'], { type: 'image/jpeg' }),
  width: 640, height: 768, frames: 2, delayMs: 1500,
});

test('GIF 저장 상한은 이전 초과 허용 옵션이 있어도 업로드 전에 적용한다', async () => {
  let uploads = 0;
  const upload = async () => {
    uploads++;
    return { assetId: `asset-${uploads}`, url: `/asset-${uploads}` };
  };
  await assert.rejects(uploadWardrobeGif('project', result(5_000_001), upload,
    { allowOversize: true }), /5MB/);
  assert.equal(uploads, 0, '한도를 넘으면 GIF와 poster 어느 쪽도 올리지 않는다');
});

test('정확히 5MB인 GIF는 원본 바이트와 첫 컷을 저장한다', async () => {
  const input = result(5_000_000);
  const uploaded = [];
  const image = await uploadWardrobeGif('project', input, async (project, file) => {
    assert.equal(project, 'project');
    uploaded.push(file);
    return { assetId: `asset-${uploaded.length}`, url: `/asset-${uploaded.length}` };
  });
  assert.equal(uploaded.length, 2);
  assert.equal(uploaded[0].blob, input.blob);
  assert.equal(uploaded[1].blob, input.jpgBlob);
  assert.equal(image.gifMeta.bytes, 5_000_000);
  assert.equal(image.posterSrc, '/asset-2');
});
