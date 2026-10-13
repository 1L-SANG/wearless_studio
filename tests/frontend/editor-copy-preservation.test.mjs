import test from 'node:test';
import assert from 'node:assert/strict';
import { stripPhotoBlockTextElements } from '../../src/features/editor/editorAppearance.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';
import { loadEditorWaitDraft, saveEditorWaitDraft } from '../../src/lib/editorWaitDraft.js';

const caption = () => ({ id: 'text', type: 'text', sourceBlockId: 'shot', copyRole: 'headline',
  textSlotVersion: 1, text: '처음 문구', x: 60, y: 1402, w: 880, h: 60,
  style: { size: 44, color: '#0e0d14', lineHeight: 60 } });
const block = (text = caption()) => ({ id: 'block', contentRole: 'benefit', h: 1512, elements: [
  { id: 'photo', type: 'image', sourceBlockId: 'shot', x: 60, y: 50, w: 880, h: 1320 }, text,
] });
const job = (text) => ({ cuts: {}, copy: text ? { shot: [{ role: 'headline', text }] } : {}, status: 'running' });
const textOf = blocks => blocks[0].elements.find(el => el.id === 'text');

test('새 캡션을 사진 안으로 옮겨 편집해도 다시 열 때 보존한다', () => {
  const moved = { ...caption(), x: 120, y: 100, text: '셀러가 직접 고친 문구' };
  assert.deepEqual(textOf(stripPhotoBlockTextElements([block(moved)])), moved);
});

for (const mode of ['fill', 'complete']) {
  test(`생성 ${mode} 때 문구가 같아도 셀러의 색·크기·위치를 보존한다`, () => {
    const decorated = decorateGenBlocks([block()], job(), {});
    const edited = textOf(decorated);
    edited.style = { ...edited.style, size: 55, color: '#ff0000' };
    edited.x = 100; edited.y = 200; edited.w = 700; edited.h = 100;
    const server = [block({ ...caption(), text: '새 자동 문구' })];
    const updated = mode === 'fill' ? fillGenBlocks(decorated, job('새 자동 문구')) : mergeServerBlocks(decorated, server);
    const text = textOf(updated);
    assert.equal(text.text, '새 자동 문구');
    assert.equal(text.style.size, 55);
    assert.equal(text.style.color, '#ff0000');
    assert.deepEqual([text.x, text.y, text.w, text.h], [100, 200, 700, 100]);
    assert.equal(text.textSlotVersion, 1);
  });
}

test('손대지 않은 자동 서식은 서버의 대비색을 반영하고 생성 표식을 정리한다', () => {
  const decorated = decorateGenBlocks([block()], job(), {});
  const server = [block({ ...caption(), style: { size: 44, color: '#ffffff', lineHeight: 60 } })];
  const text = textOf(mergeServerBlocks(decorated, server));
  assert.equal(text.style.color, '#ffffff');
  assert.equal('genAutoStyle' in text, false);
  assert.equal('genAutoGeometry' in text, false);
});

test('서식을 고친 뒤 여러 번 자동 문구가 도착해도 편집한 값을 보존한다', () => {
  const decorated = decorateGenBlocks([block()], job(), {});
  textOf(decorated).style.color = '#ff0000';
  const once = fillGenBlocks(decorated, job('두 번째 문구'));
  const twice = fillGenBlocks(once, job('세 번째 문구'));
  assert.equal(textOf(twice).style.color, '#ff0000');
  assert.equal(textOf(twice).text, '세 번째 문구');
});

const oldLabel = () => ({ id: 'text', type: 'text', sourceBlockId: 'shot', copyRole: 'label',
  textSlotVersion: 1, text: 'FRONT', x: 60, y: 44, w: 400, h: 38,
  style: { size: 28, weight: 700, tracking: 3, color: '#9a9aa2', lineHeight: 38 } });
const serverColorLabel = () => ({ ...oldLabel(), w: 880,
  text: 'FRONT · 워시드네이비워시드네이비워시드네이비워시드네이비',
  style: { size: 11.29, weight: 700, tracking: 1.2, color: '#9a9aa2', lineHeight: 38 } });
const labelBlock = (label) => ({ id: 'block', kind: 'product', contentRole: 'productOverview',
  h: 1484, elements: [
    { id: 'photo', type: 'image', sourceBlockId: 'shot', x: 60, y: 114, w: 880, h: 1320 }, label,
  ] });

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

test('이전 대기 작업본의 미편집 색상 라벨은 서버 폭을 받아 저장 후에도 한 줄 공간을 유지한다', () => {
  const storage = memoryStorage();
  const pending = decorateGenBlocks([labelBlock(oldLabel())], job(), {});
  assert.equal(saveEditorWaitDraft('color-label-upgrade', pending, storage), true);
  const restored = loadEditorWaitDraft('color-label-upgrade', storage);
  assert.equal(textOf(restored).w, 400);

  const merged = mergeServerBlocks(restored, [labelBlock(serverColorLabel())]);
  assert.equal(textOf(merged).w, 880);
  const image = merged[0].elements.find(el => el.type === 'image');
  assert.deepEqual([image.x, image.y, image.w, image.h], [60, 114, 880, 1320]);
  assert.equal(saveEditorWaitDraft('color-label-upgrade', merged, storage), true);
  const reloaded = textOf(loadEditorWaitDraft('color-label-upgrade', storage));
  assert.equal(reloaded.w, 880);
  assert.equal(reloaded.text, 'FRONT · 워시드네이비워시드네이비워시드네이비워시드네이비');
  assert.deepEqual([reloaded.x, reloaded.y, reloaded.h], [60, 44, 38]);
  assert.equal(reloaded.style.size, 11.29);
  assert.equal(reloaded.style.tracking, 1.2);
});

for (const [field, value] of [['x', 100], ['y', 200], ['w', 700], ['h', 100]]) {
  test(`셀러가 색상 라벨의 ${field}를 바꾸면 서버 폭으로 덮어쓰지 않는다`, () => {
    const pending = decorateGenBlocks([labelBlock(oldLabel())], job(), {});
    textOf(pending)[field] = value;
    const merged = textOf(mergeServerBlocks(pending, [labelBlock(serverColorLabel())]));
    assert.equal(merged[field], value);
    assert.equal(merged.w, field === 'w' ? 700 : 400);
    assert.equal(merged.text, 'FRONT · 워시드네이비워시드네이비워시드네이비워시드네이비');
  });
}

for (const [field, value] of [['size', 55], ['tracking', 5]]) {
  test(`셀러가 색상 라벨의 ${field}를 바꾸면 기존 글상자 폭을 보존한다`, () => {
    const pending = decorateGenBlocks([labelBlock(oldLabel())], job(), {});
    textOf(pending).style[field] = value;
    const merged = textOf(mergeServerBlocks(pending, [labelBlock(serverColorLabel())]));
    assert.equal(merged.style[field], value);
    assert.equal(merged.w, 400);
  });
}

test('셀러가 고친 색상 라벨 문구는 서버 문구와 폭으로 덮어쓰지 않는다', () => {
  const pending = decorateGenBlocks([labelBlock(oldLabel())], job(), {});
  textOf(pending).text = '내가 고친 라벨';
  const merged = textOf(mergeServerBlocks(pending, [labelBlock(serverColorLabel())]));
  assert.equal(merged.text, '내가 고친 라벨');
  assert.equal(merged.w, 400);
  assert.equal(merged.style.size, 28);
});

test('색상 라벨의 미편집 폭을 갱신해도 셀러가 고른 글자 색은 보존한다', () => {
  const pending = decorateGenBlocks([labelBlock(oldLabel())], job(), {});
  textOf(pending).style.color = '#ff0000';
  const merged = textOf(mergeServerBlocks(pending, [labelBlock(serverColorLabel())]));
  assert.equal(merged.style.color, '#ff0000');
  assert.equal(merged.w, 880);
});

test('단일 색상 라벨은 원래 문구와 폭을 유지한다', () => {
  const pending = decorateGenBlocks([labelBlock(oldLabel())], job(), {});
  const merged = textOf(mergeServerBlocks(pending, [labelBlock(oldLabel())]));
  assert.equal(merged.text, 'FRONT');
  assert.equal(merged.w, 400);
  assert.equal(merged.style.size, 28);
});

test('서버 라벨 폭 병합은 일반 캡션의 기존 폭을 바꾸지 않는다', () => {
  const pending = decorateGenBlocks([block()], job(), {});
  const merged = textOf(mergeServerBlocks(pending, [block({ ...caption(), w: 600 })]));
  assert.equal(merged.w, 880);
});
