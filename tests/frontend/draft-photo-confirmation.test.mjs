import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { confirmDraftPhotos, photoCollectionFailure } from '../../src/lib/draftPhotoConfirmation.js';

const product = {
  colors: [
    { id: 'other', images: [{ id: 'other-front', slot: 'Front' }] },
    {
      id: 'base', isBase: true,
      images: [
        { id: 'detail-1', slot: 'Detail' },
        { id: 'detail-2', slot: 'Detail' },
        { id: 'detail-3', slot: 'Detail' },
        { id: 'front', slot: 'Front' },
        { id: 'back', slot: 'Back' },
      ],
    },
  ],
};
const ids = (value) => value.colors.flatMap((color) => color.images.map((image) => image.id));
const failureFor = (...failedIds) => ({ failed: failedIds.length, failedIds });

function harness(results) {
  let current = structuredClone(product);
  const events = [];
  const failures = [];
  const saved = [];
  const promoted = [];
  return {
    events, failures, saved, promoted,
    get product() { return current; },
    async run(omitIds = []) {
      return confirmDraftPhotos({
        product: current,
        omitIds,
        onProductChange(next) {
          events.push('state');
          current = next;
        },
        async save(next) {
          events.push('save');
          saved.push(structuredClone(next));
          return results.shift();
        },
        onFailure(failure) {
          events.push('modal');
          failures.push(failure);
        },
        async confirm() {
          events.push('draftSlot.flush');
          events.push('draftSlot.remove');
          events.push('promotion');
          promoted.push(structuredClone(current));
        },
      });
    },
  };
}

for (const id of ['front', 'back', 'detail-1', 'detail-2']) {
  test(`failed analysis source ${id} hides omission and blocks all confirmation work`, async () => {
    const flow = harness([failureFor(id)]);
    await flow.run();
    assert.deepEqual(flow.events, ['save', 'modal']);
    assert.equal(flow.failures[0].canOmit, false);
    assert.deepEqual(flow.product, product);
    assert.deepEqual(flow.promoted, []);
  });
}

test('a mix of source and non-source failures cannot be omitted', () => {
  assert.equal(photoCollectionFailure(product, failureFor('front', 'other-front')).canOmit, false);
});

test('fallback base color uses the same source selection when no isBase is set', () => {
  const fallback = { colors: [{ images: [{ id: 'first', slot: 'Front' }] }, ...product.colors.map((color) => ({ ...color, isBase: false }))] };
  assert.equal(photoCollectionFailure(fallback, failureFor('first')).canOmit, false);
  assert.equal(photoCollectionFailure(fallback, failureFor('front')).canOmit, true);
});

test('failed non-source photos allow omission but still block until explicit consent', async () => {
  const flow = harness([failureFor('detail-3', 'other-front')]);
  await flow.run();
  assert.equal(flow.failures[0].canOmit, true);
  assert.deepEqual(flow.events, ['save', 'modal']);
  assert.deepEqual(flow.product, product, 'closing the modal without another action changes no photos');
  assert.deepEqual(flow.promoted, []);
});

test('consent removes exactly the failed photos from state and saved draft before promotion', async () => {
  const flow = harness([failureFor('detail-3', 'other-front'), failureFor()]);
  await flow.run();
  await flow.run(flow.failures[0].failedIds);
  assert.deepEqual(flow.events, ['save', 'modal', 'state', 'save', 'draftSlot.flush', 'draftSlot.remove', 'promotion']);
  const remaining = ['detail-1', 'detail-2', 'front', 'back'];
  assert.deepEqual(ids(flow.product), remaining);
  assert.deepEqual(ids(flow.saved[1]), remaining);
  assert.deepEqual(ids(flow.promoted[0]), remaining);
  assert.equal(ids(product).length, 6, 'the input snapshot is not mutated');
});

test('retry repeats collection and remains blocked while any photo fails', async () => {
  const flow = harness([failureFor('front'), failureFor('front'), failureFor()]);
  await flow.run();
  await flow.run();
  assert.deepEqual(flow.events, ['save', 'modal', 'save', 'modal']);
  await flow.run();
  assert.deepEqual(flow.events, ['save', 'modal', 'save', 'modal', 'save', 'draftSlot.flush', 'draftSlot.remove', 'promotion']);
  assert.deepEqual(flow.product, product);
});

test('new failures after consent block again before slot flush, removal or promotion', async () => {
  const flow = harness([failureFor('detail-3'), failureFor('front')]);
  await flow.run();
  await flow.run(flow.failures[0].failedIds);
  assert.deepEqual(flow.events, ['save', 'modal', 'state', 'save', 'modal']);
  assert.equal(flow.failures[1].canOmit, false);
  assert.deepEqual(flow.promoted, []);
});

test('stale consent cannot remove a photo that is now an analysis source', async () => {
  const flow = harness([]);
  await flow.run(['front']);
  assert.deepEqual(flow.events, ['modal']);
  assert.equal(flow.failures[0].canOmit, false);
  assert.deepEqual(flow.product, product);
});

test('collection exceptions never enter confirmation', async () => {
  let confirmed = false;
  await assert.rejects(confirmDraftPhotos({
    product,
    save: async () => { throw new Error('IndexedDB unavailable'); },
    onFailure: () => assert.fail('storage errors use the existing error handler'),
    confirm: () => { confirmed = true; },
  }), /IndexedDB unavailable/);
  assert.equal(confirmed, false);
});

test('ProductInput uses the tested gate for same-device, guest, and logged-in confirmation', () => {
  const source = readFileSync(new URL('../../src/features/product-input/ProductInput.jsx', import.meta.url), 'utf8');
  const confirm = source.slice(source.indexOf('const goToStoryboard = async'), source.indexOf('const queueAnalysisPatch'));
  assert.match(confirm, /await confirmDraftPhotos\(/);
  assert.ok(confirm.indexOf('confirm: async () =>') < confirm.indexOf('if (session || isMockMode)'));
  assert.ok(confirm.indexOf('confirm: async () =>') < confirm.indexOf('await draftSlot.flush()'));
  assert.ok(confirm.indexOf('await draftSlot.flush()') < confirm.indexOf('await draftSlot.remove()'));
  assert.ok(confirm.indexOf('await draftSlot.remove()') < confirm.indexOf('promoteDraftToProject(draft'));
  assert.match(source, /photoFailure\.canOmit && \([\s\S]*?이 사진을 빼고 계속/);
  assert.match(source, /Modal onClose=\{\(\) => setPhotoFailure\(null\)\}/);
  assert.doesNotMatch(source, /장은 저장되지 않았어요/);
});

test('only all-404 failures are removed photos, with the same analysis-source omit rule', () => {
  for (const [failedIds, statuses, removed, canOmit] of [
    [['front'], [404], true, false],
    [['detail-3', 'other-front'], [404, 404], true, true],
    [['detail-3', 'other-front'], [404, 503], false, true],
    [['detail-3'], [undefined], false, true],
  ]) {
    const failure = photoCollectionFailure(product, { failed: failedIds.length, failedIds,
      failures: failedIds.map((imageId, i) => ({ imageId, status: statuses[i] })) });
    assert.equal(failure.removed, removed);
    assert.equal(failure.canOmit, canOmit);
  }
});
