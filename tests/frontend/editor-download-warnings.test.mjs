import test from 'node:test';
import assert from 'node:assert/strict';
import { downloadChecks, missingNoticeFields } from '../../src/features/editor/editorDownloadWarnings.js';
import { buildInfoBlock } from '../../src/features/editor/presets/infoPresets.js';

test('고시정보 값을 개별 드래그해도 빈 항목명을 유지한다', () => {
  const block = buildInfoBlock('required_notice', { fields: [
    { key: 'sizes', label: '치수', value: '' },
    { key: 'origin', label: '제조국', value: '한국' },
  ] });
  block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, y: el.y + 12 } : el);
  assert.deepEqual(missingNoticeFields([block]), ['치수']);
});

test('고시정보 값이 항목명보다 왼쪽으로 이동해도 요소 순서로 판정한다', () => {
  const block = buildInfoBlock('required_notice', { fields: [
    { key: 'origin', label: '제조국', value: '' },
    { key: 'sizes', label: '치수', value: 'FREE' },
  ] });
  const placeholder = block.elements.find((element) => element.text === '정보 입력 필요');
  const cleared = block.elements.find((element) => element.text === 'FREE');
  placeholder.x = 20;
  cleared.x = 20;
  cleared.y += 12;
  cleared.text = '  ';
  assert.deepEqual(missingNoticeFields([block]), ['제조국', '치수']);
});

test('요소 순서로 짝을 찾을 수 없으면 고시정보의 같은 행 좌표를 쓴다', () => {
  const block = buildInfoBlock('required_notice', { fields: [{ key: 'origin', label: '제조국', value: '' }] });
  const [heading, label, value] = block.elements;
  block.elements = [heading, value, label];
  assert.deepEqual(missingNoticeFields([block]), ['제조국']);
});

test('폼에서 모두 채운 고시정보와 고시 블록 없는 문서는 안내하지 않는다', () => {
  const block = buildInfoBlock('required_notice', { fields: [
    { key: 'sizes', label: '치수', value: 'FREE' },
    { key: 'origin', label: '제조국', value: '한국' },
  ] });
  assert.deepEqual(missingNoticeFields([block]), []);
  assert.deepEqual(missingNoticeFields([]), []);
});

test('지운 고시정보 값과 안내 문구를 세고, 지운 항목명은 세지 않는다', () => {
  const filled = buildInfoBlock('required_notice', { fields: [
    { key: 'origin', label: '제조국', value: '치수' },
    { key: 'sizes', label: '치수', value: 'FREE' },
    { key: 'maker', label: '제조자(수입자)', value: '웨어리스' },
  ] });
  // 앞 행의 값이 고시 항목명과 같아도 다음 행의 왼쪽 칸을 값으로 세지 않는다.
  const sizesLabel = filled.elements.findLast((element) => element.text === '치수');
  sizesLabel.text = '  ';
  filled.elements.find((element) => element.text === '제조자(수입자)').text = '정보 입력 필요';
  const empty = buildInfoBlock('required_notice', { fields: [
    { key: 'origin', label: '제조국', value: '한국' },
    { key: 'sizes', label: '치수', value: 'FREE' },
    { key: 'maker', label: '제조자(수입자)', value: '' },
  ] });
  empty.elements.find((element) => element.text === '한국').text = '';
  empty.elements.find((element) => element.text === 'FREE').text = ' \t ';

  assert.deepEqual(missingNoticeFields([filled]), []);
  assert.deepEqual(downloadChecks([filled, empty]), [
    { kind: 'notice', count: 3, blockId: empty.id, labels: ['제조국', '치수', '제조자(수입자)'] },
  ]);
});

test('숨김, 시스템, 투명도 0인 고시정보 빈칸은 세지 않는다', () => {
  for (const patch of [{ hidden: true }, { system: true }, { opacity: 0 }, { style: { opacity: 0 } }]) {
    const block = buildInfoBlock('required_notice', { fields: [{ key: 'origin', label: '제조국', value: '' }] });
    block.elements = block.elements.map((el) => el.text === '정보 입력 필요' ? { ...el, ...patch } : el);
    assert.deepEqual(missingNoticeFields([block]), []);
    assert.deepEqual(downloadChecks([block]), []);
  }
});

test('사진이 없는 요소를 종류별로 세고 각 종류의 첫 블록을 찾는다', () => {
  const notice = buildInfoBlock('required_notice', { fields: [
    { key: 'sizes', label: '치수', value: '' },
    { key: 'origin', label: '제조국', value: '' },
    { key: 'maker', label: '제조자(수입자)', value: '입력한 제조자' },
  ] });
  const blocks = [
    { id: 'pending-first', elements: [{ type: 'image', genPending: 'wait' }, { type: 'image', genPending: 'live' }] },
    { id: 'empty-first', elements: [{ type: 'image' }] },
    notice,
    { id: 'failed-first', elements: [{ type: 'image', genPending: 'failed' }] },
    { id: 'later', elements: [
      { type: 'image', genFailed: true },
      { type: 'image', genFailed: true, genPending: 'live' },
      { type: 'image', genPending: 'done' },
      { type: 'image' },
      { type: 'image', hidden: true, genPending: 'failed' },
      { type: 'image', system: true },
      { type: 'image', opacity: 0 },
      { type: 'image', style: { opacity: 0 } },
      { type: 'image', src: '/photo.jpg', genPending: 'failed', genFailed: true },
      { type: 'text', genPending: 'live' },
    ] },
    { ...notice, id: 'notice-later' },
  ];
  const before = structuredClone(blocks);
  assert.deepEqual(downloadChecks(blocks), [
    { kind: 'failed', count: 3, blockId: 'failed-first' },
    { kind: 'empty', count: 2, blockId: 'empty-first' },
    { kind: 'notice', count: 2, blockId: notice.id, labels: ['치수', '제조국'] },
    { kind: 'pending', count: 3, blockId: 'pending-first' },
  ]);
  assert.deepEqual(blocks, before);
});

test('대기 중인 컷에 사진이 들어오면 다음 확인 목록에서 빠진다', () => {
  const pending = { type: 'image', genPending: 'live' };
  assert.deepEqual(downloadChecks([{ id: 'photos', elements: [pending] }]), [
    { kind: 'pending', count: 1, blockId: 'photos' },
  ]);
  assert.deepEqual(downloadChecks([{ id: 'photos', elements: [{ ...pending, src: '/ready.jpg' }] }]), []);
});

test('개별 AI 작업이 진행 중이거나 실패하면 상태를 표시하고 사진이 들어오면 제외한다', () => {
  const block = { id: 'photos', elements: [{ id: 'slot', type: 'image' }] };
  const job = { isSlot: true, blockId: block.id, elementId: 'slot' };
  for (const [status, kind] of [['cancel', 'pending'], ['busy', 'pending'], ['failed', 'failed']]) {
    assert.deepEqual(downloadChecks([block], new Map([['slot', { ...job, status }]])), [
      { kind, count: 1, blockId: block.id },
    ]);
  }
  const failedJobs = new Map([['slot', { ...job, status: 'failed', dismissed: true }]]);
  assert.deepEqual(downloadChecks([block], failedJobs), [{ kind: 'empty', count: 1, blockId: block.id }]);
  const busyJobs = new Map([['slot', { ...job, status: 'busy' }]]);
  const retry = { ...block, elements: [{ ...block.elements[0], genFailed: true }] };
  assert.deepEqual(downloadChecks([retry], busyJobs), [{ kind: 'pending', count: 1, blockId: block.id }]);
  const completed = { ...block, elements: [{ ...block.elements[0], src: '/ready.jpg' }] };
  assert.deepEqual(downloadChecks([completed], busyJobs), []);
});
