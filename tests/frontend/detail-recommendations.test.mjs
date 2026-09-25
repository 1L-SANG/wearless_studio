import test from 'node:test';
import assert from 'node:assert/strict';
import { defaultStoryboard, isDefaultStoryboardForMode } from '../../src/lib/api/shapes.js';
import { generationExampleSelectionPatch } from '../../src/lib/storyboardExampleSelection.js';
import { uniqueGenerationCutCount } from '../../src/lib/generationCutCount.js';
import { detailTargetPresentation, detailTargetPatch, preserveDetailTargetBinding, recommendedDetailTargets } from '../../src/lib/detailRecommendations.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import { buildFailedCutRetry } from '../../src/features/editor/failedCutRetry.js';

const colors = [{ id: 'base', isBase: true, images: [{ slot: 'Front', src: '/my-front.jpg' }, { slot: 'Back', src: '/my-back.jpg' }] }];
const candidate = (id, extra = {}) => ({ id, kind: 'construction', direction: 'front', sourceSlot: 'Front', sourceIndex: 0, sourceSha256: 'a'.repeat(64), region: { x: 0.1, y: 0.1, w: 0.6, h: 0.6 }, photoUse: 'standalone', visibility: 'clear', rank: 1, label: id, reason: '상품의 구조를 보여줘요', informationGroup: id, featurePoints: [], ...extra });
const context = (candidates, extra = {}) => ({ projectId: 'detail-test', clothingType: 'top', detailRecommendations: { version: 1, status: 'ready', candidates }, ...extra });
const details = (board) => board.filter((b) => b.shot === 'detail');

test('automatic details never fill a quota and cap basic at 2 / extended at 3', () => {
  for (let n = 0; n <= 5; n++) {
    const ctx = context(Array.from({ length: n }, (_, i) => candidate(`t${i}`, { rank: i + 1 })));
    for (const mode of ['basic', 'extended']) {
      const blocks = details(defaultStoryboard(colors, mode, ctx));
      assert.equal(blocks.length, Math.min(n, mode === 'basic' ? 2 : 3));
      assert.ok(blocks.every((b) => b.detailTargetId && b.detailTargetOrigin === 'auto'));
    }
  }
  assert.equal(details(defaultStoryboard(colors, 'basic')).length, 0);
  assert.equal(details(defaultStoryboard(colors, 'extended', context([candidate('x')], { detailRecommendations: { version: 1, status: 'unavailable', candidates: [candidate('x')] } }))).length, 0);
});

test('only clear standalone distinct information is seeded; seller highlights rank, not multiply', () => {
  const ctx = context([
    candidate('context', { photoUse: 'context' }), candidate('unclear', { visibility: 'uncertain' }),
    candidate('neck', { informationGroup: 'neck' }), candidate('duplicate', { informationGroup: 'neck' }),
    candidate('fabric', { rank: 9, kind: 'fabric', featurePoints: ['골지 짜임'] }),
    candidate('back', { rank: 3, direction: 'back', sourceSlot: 'Back', sourceIndex: 1 }),
  ], { sellingPoints: ['고급스러운 골지 짜임', '부드러움', '데일리', '편안함', '예쁜 색상'] });
  assert.deepEqual(details(defaultStoryboard(colors, 'basic', ctx)).map((b) => b.detailTargetId), ['fabric', 'neck']);
  assert.equal(details(defaultStoryboard(colors, 'extended', ctx))[2].direction, 'back');
});

test('five reworded highlights rank existing garment facts without padding or creating regions', () => {
  const targets = [
    candidate('hem', { rank: 1, label: '밑단 마감' }),
    candidate('zip', { rank: 8, label: '앞섶 집업 구조' }),
    candidate('ribbon', { rank: 9, label: '가슴 장식', featurePoints: ['가슴 레이스 밴드 및 리본 장식'] }),
    candidate('pocket', { rank: 10, label: '사이드 포켓' }),
    candidate('fabric', { rank: 2, label: '부드러운 원단', kind: 'fabric', photoUse: 'unsupported' }),
  ];
  const snapshot = structuredClone(targets);
  const recommendations = context(targets).detailRecommendations;
  const highlights = ['편리한 지퍼 여밈', '가슴 레이스 리본', '실용적인 주머니', '부드러운 원단', '화사한 색감'];
  const basic = recommendedDetailTargets(recommendations, 'basic', highlights);
  const extended = recommendedDetailTargets(recommendations, 'extended', highlights);
  assert.deepEqual(basic.map((target) => target.id), ['zip', 'ribbon']);
  assert.deepEqual(extended.map((target) => target.id), ['zip', 'ribbon', 'pocket']);
  assert.deepEqual(targets, snapshot);
  assert.ok(extended.every((target) => targets.includes(target)));
  assert.equal(extended.some((target) => target.id === 'fabric'), false);
});

test('lexical emphasis compares stated facts, not broad candidate kind or shared generic neckline', () => {
  const recommendations = context([
    candidate('hem', { rank: 1, label: '밑단 마감' }),
    candidate('collar', { rank: 2, kind: 'neckline', label: '셔츠 카라 넥라인' }),
    candidate('zip', { rank: 3, kind: 'closure', label: '집업 여밈' }),
    candidate('ribneck', { rank: 9, kind: 'neckline', label: '목선', featurePoints: ['리브 조직의 라운드넥'] }),
    candidate('buttons', { rank: 10, kind: 'closure', label: '버튼 여밈 구조' }),
  ]).detailRecommendations;
  assert.deepEqual(recommendedDetailTargets(recommendations, 'basic', ['탄탄한 골지 넥라인', '섬세한 단추 디테일']).map((target) => target.id), ['ribneck', 'buttons']);
  assert.deepEqual(recommendedDetailTargets(recommendations, 'basic', ['카라가 돋보이는 디자인']).map((target) => target.id), ['collar', 'hem']);
  const sleeves = context([
    candidate('rib', { rank: 1, label: '리브 원단' }),
    candidate('sleeve', { rank: 9, label: '소매 마감' }),
  ]).detailRecommendations;
  assert.equal(recommendedDetailTargets(sleeves, 'basic', ['롱 슬리브'])[0].id, 'sleeve');
});

test('matching decoration or fabric cannot promote context, uncertain or duplicate information', () => {
  const recommendations = context([
    candidate('fabric', { kind: 'fabric', label: '골지 짜임', photoUse: 'context' }),
    candidate('uncertain', { label: '지퍼 여밈', visibility: 'uncertain' }),
    candidate('zip', { rank: 5, label: '집업 구조', informationGroup: 'closure' }),
    candidate('same-zip', { rank: 6, label: '지퍼 디테일', informationGroup: 'closure' }),
  ]).detailRecommendations;
  assert.deepEqual(recommendedDetailTargets(recommendations, 'extended', ['골지 소재', '지퍼 여밈', '리브 원단', '부드러운 촉감', '편리한 집업']).map((target) => target.id), ['zip']);
});

test('mode fingerprint protects edited targets, manually added cuts and deleted cuts', () => {
  const ctx = context([candidate('one'), candidate('two'), candidate('three')]);
  const board = defaultStoryboard(colors, 'basic', ctx);
  assert.equal(isDefaultStoryboardForMode(board, colors, 'basic', ctx), true);
  const first = board.findIndex((b) => b.shot === 'detail');
  for (const patch of [{ detailTargetId: 'three' }, { detailTargetOrigin: 'user' }]) {
    assert.equal(isDefaultStoryboardForMode(board.map((b, i) => i === first ? { ...b, ...patch } : b), colors, 'basic', ctx), false);
  }
  assert.equal(isDefaultStoryboardForMode(board.filter((_, i) => i !== first), colors, 'basic', ctx), false);
  assert.equal(isDefaultStoryboardForMode([...board, { ...board[first], id: 'manual', detailTargetOrigin: 'user' }], colors, 'basic', ctx), false);
});

test('photographic example changes cannot change bound target direction or selected color', () => {
  const block = { cutType: 'product', shot: 'detail', detailTargetId: 'back', detailTargetOrigin: 'user', direction: 'back', colorId: 'selected', exampleId: 'old' };
  const changed = { ...block, ...generationExampleSelectionPatch(block, { id: 'new', direction: 'front' }, { defaultColorId: 'base' }).patch };
  assert.equal(changed.direction, 'back');
  assert.equal(changed.colorId, 'selected');
  assert.equal(changed.detailTargetId, 'back');
});

test('different targets are distinct billable image contracts', () => {
  const base = { source: 'ai', cutType: 'product', shot: 'detail', direction: 'front' };
  assert.equal(uniqueGenerationCutCount([{ ...base, detailTargetId: 'a' }, { ...base, detailTargetId: 'b' }, { ...base, detailTargetId: 'a' }]), 2);
});

test('changing source color/direction clears the target; subject choice can bind a new direction', () => {
  const block = { source: 'ai', cutType: 'product', shot: 'detail', colorId: 'base', direction: 'back', detailTargetId: 'back', detailTargetOrigin: 'auto' };
  for (const changes of [{ colorId: 'red' }, { direction: 'front' }, { shot: 'ghost' }, { source: 'mine' }]) {
    assert.equal(preserveDetailTargetBinding(block, changes).detailTargetId, null);
  }
  assert.deepEqual(preserveDetailTargetBinding(block, { colorId: 'base' }), { colorId: 'base' });
  assert.deepEqual(preserveDetailTargetBinding(block, detailTargetPatch(candidate('front'))), detailTargetPatch(candidate('front')));
});

test('whole seller source and its subject label remain consistent across cards and retries', () => {
  const product = { colors: [{ id: 'base', isBase: true, images: [
    { slot: 'Detail', src: '/first-detail.jpg' }, { slot: 'Front', src: '/front.jpg' },
    { slot: 'Detail', src: '/second-detail.jpg' }, { slot: 'Back', src: '/back.jpg' },
  ] }] };
  const target = candidate('cuff', { sourceSlot: 'Detail', sourceIndex: 3, label: '소매 짜임' });
  const ctx = context([target]);
  const block = { id: 'b', source: 'ai', cutType: 'product', shot: 'detail', ...detailTargetPatch(target), exampleId: 'unrelated', thumb: '/example.jpg' };
  assert.equal(detailTargetPresentation(block, product, ctx.detailRecommendations).src, '/front.jpg');
  const withoutOverview = { colors: [{ ...product.colors[0], images: product.colors[0].images.map((image) => image.slot === 'Front' ? { ...image, src: null } : image) }] };
  assert.equal(detailTargetPresentation(block, withoutOverview, ctx.detailRecommendations).src, '/second-detail.jpg');
  const retry = buildFailedCutRetry([block], 'b', ctx, product);
  assert.equal(retry.request.detailTargetId, 'cuff');
  assert.equal(retry.request.direction, 'front');
  assert.equal(retry.thumb, '/front.jpg');
  assert.equal(retry.detailLabel, '소매 짜임');
  const assigned = assignGenerationExamples([block], { catalog: [], product, gender: 'women' });
  assert.deepEqual(assigned.blocks, [block]);
  assert.deepEqual(assigned.missingIds, []);
});
