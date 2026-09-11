import test from 'node:test';
import assert from 'node:assert/strict';
import * as report from '../../src/features/model/mypage/usageReportState.js';

test('신고는 기록을 선택하고 사유를 작성한 뒤에만 보낼 수 있어요', () => {
  let state = report.usageReportReducer(undefined, { type: 'open', paymentId: 'payment:1' });
  assert.equal(report.reportCanSubmit(state), false);
  state = report.usageReportReducer(state, { type: 'reason', value: '허용하지 않은 품목이에요' });
  assert.equal(report.reportCanSubmit(state), true);
  state = report.usageReportReducer(state, { type: 'detail', value: '  확인 부탁해요  ' });
  assert.deepEqual(report.reportPayload(state), { paymentId: 'payment:1', reason: '허용하지 않은 품목이에요\n확인 부탁해요' });
  state = report.usageReportReducer(state, { type: 'submit' });
  assert.equal(report.reportCanSubmit(state), false);
  assert.equal(report.usageReportReducer(state, { type: 'close' }), state);
  state = report.usageReportReducer(state, { type: 'success' });
  assert.equal(state.phase, 'closed');
});

test('실패해도 신고 사유는 남고 다시 시도할 수 있어요', () => {
  let state = report.usageReportReducer(undefined, { type: 'open', paymentId: 'p2' });
  state = report.usageReportReducer(state, { type: 'detail', value: '직접 적은 사유예요' });
  state = report.usageReportReducer(state, { type: 'submit' });
  state = report.usageReportReducer(state, { type: 'error', message: '다시 시도해 주세요' });
  assert.equal(state.detail, '직접 적은 사유예요');
  assert.equal(report.reportCanSubmit(state), true);
  assert.equal(report.usageReportReducer(state, { type: 'close' }).paymentId, null);
});

import { toggleAllowedCategory } from '../../src/features/model/mypage/conditionState.js';

test('마지막 허용 품목은 끄지 못하고 다시 켠 품목만 저장해요', () => {
  const last = ['액티브웨어'];
  assert.deepEqual(toggleAllowedCategory(last, '액티브웨어'), ['액티브웨어']);
  assert.deepEqual(toggleAllowedCategory(last, '일반 의류'), ['일반 의류', '액티브웨어']);
  assert.deepEqual(toggleAllowedCategory(['일반 의류', '액티브웨어'], '일반 의류'), ['액티브웨어']);
  assert.deepEqual(last, ['액티브웨어']);
});
