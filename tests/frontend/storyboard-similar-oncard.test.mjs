import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { nodes, textOf } from './helpers/storyboardComponents.mjs';
import { storyboardStage, stagePhoto } from './helpers/storyboardStageHarness.mjs';
const similarButton = tree => nodes(tree).find(node => node.type === 'button' && textOf(node) === '비슷한 컷 추가');

test('eligible originals retain the similar-cut action, while followups and product/seller photos omit it', async () => {
  for (const cutType of ['styling', 'horizon']) assert.ok(similarButton(await storyboardStage([stagePhoto('original', { cutType })])));
  for (const extra of [{ cutType: 'product' }, { source: 'mine' }, { exampleId: null }, { similarSourceBlockId: 'source' }]) {
    assert.equal(similarButton(await storyboardStage([stagePhoto('photo', extra)])), undefined);
  }
});
test('similar-cut activation uses the original ID without selecting another photo', async () => {
  const added = [], selected = [];
  const tree = await storyboardStage([stagePhoto('original')], { onSimilar: id => added.push(id), onSelectPhoto: id => selected.push(id) });
  similarButton(tree).props.onClick(); assert.deepEqual(added, ['original']); assert.deepEqual(selected, []);
});
test('followup cards keep the original preview and show the source cut number through a white veil', async () => {
  const root = stagePhoto('root'), child = stagePhoto('child', { similarSourceBlockId: 'root', thumb: '/wrong.webp' });
  const tree = await storyboardStage([root, child]);
  assert.ok(textOf(tree).includes('01번째 컷과 비슷하게'));
  assert.equal(nodes(tree).filter(node => node.type === 'img' && node.props.src === '/released.webp').length, 2);
  assert.equal(nodes(tree).filter(node => node.type === 'button' && textOf(node) === '비슷한 컷 추가').length, 1);
});
test('twelve cuts and borrowed SNS slots do not offer an unsaveable similar-cut action', async () => {
  assert.equal(similarButton(await storyboardStage(Array.from({ length: 12 }, (_, i) => stagePhoto(String(i))))), undefined);
  const fallback = stagePhoto('sns', { pageSectionKey: 'emo.sns', pageSectionKind: 'sns', pageSectionUsage: undefined, pageDesignId: 'I09-D14', slotKey: 'sns.sns1' });
  const tree = await storyboardStage([fallback]);
  assert.equal(similarButton(tree), undefined);
  assert.equal(nodes(tree).some(node => node.props?.['aria-label']?.endsWith('컷 빼기')), false);
});
test('hover, selection and keyboard focus expose the similar button without changing the photo ratio', () => {
  const css = readFileSync(new URL('../../src/features/storyboard/StoryboardLayout.module.css', import.meta.url), 'utf8');
  assert.match(css, /\.photoFrame:hover \.similar, \.photoFrame:focus-within \.similar, \.photoFrame:has\(\.selectedPhoto\) \.similar/);
  assert.match(css, /aspect-ratio: 3 \/ 4/);
  assert.match(css, /\.selectedPhoto::after[^}]*border: 2px solid/);
});
