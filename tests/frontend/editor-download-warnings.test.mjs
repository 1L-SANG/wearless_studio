import test from 'node:test';
import assert from 'node:assert/strict';
import { missingNoticeFields } from '../../src/features/editor/editorDownloadWarnings.js';
import { buildInfoBlock } from '../../src/features/editor/presets/infoPresets.js';

test('고시정보 값을 개별 드래그해도 빈 항목명을 유지한다', () => {
  const block = buildInfoBlock('required_notice', { fields: [
    { key: 'sizes', label: '치수', value: '' },
    { key: 'origin', label: '제조국', value: '한국' },
  ] });
  block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, y: el.y + 12 } : el);
  assert.deepEqual(missingNoticeFields([block]), ['치수']);
});

test('폼에서 모두 채운 고시정보와 고시 블록 없는 문서는 안내하지 않는다', () => {
  const block = buildInfoBlock('required_notice', { fields: [
    { key: 'sizes', label: '치수', value: 'FREE' },
    { key: 'origin', label: '제조국', value: '한국' },
  ] });
  assert.deepEqual(missingNoticeFields([block]), []);
  assert.deepEqual(missingNoticeFields([]), []);
});

test('투명도 0으로 출력되지 않는 빈 항목 글자는 안내하지 않는다', () => {
  for (const patch of [{ opacity: 0 }, { style: { opacity: 0 } }]) {
    const block = buildInfoBlock('required_notice', { fields: [{ key: 'origin', label: '제조국', value: '' }] });
    block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, ...patch } : el);
    assert.deepEqual(missingNoticeFields([block]), []);
  }
});
