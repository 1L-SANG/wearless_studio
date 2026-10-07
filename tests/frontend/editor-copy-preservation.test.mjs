import test from 'node:test';
import assert from 'node:assert/strict';
import { stripPhotoBlockTextElements } from '../../src/features/editor/editorAppearance.js';
import { decorateGenBlocks, fillGenBlocks, mergeServerBlocks } from '../../src/lib/editorWaitSkeleton.js';

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
