import assert from 'node:assert/strict';
import test from 'node:test';
const helpers = await import('../../src/features/editor/canvasAiPoll.js').catch(() => ({}));

test('폴링 시간 초과는 같은 작업을 따라가고 새 요청은 보내지 않는다', async () => {
  assert.equal(typeof helpers.resolveCanvasAiSubmission, 'function');
  let submissions = 0, polls = 0;
  const result = await helpers.resolveCanvasAiSubmission(async () => {
    submissions += 1;
    throw Object.assign(new Error('아직 만들고 있어요.'), { code: 'job_timeout', jobId: 'accepted' });
  }, { sleep: async () => {}, getJob: async (id) => {
    assert.equal(id, 'accepted'); polls += 1;
    return polls === 1 ? { status: 'running' } : { status: 'done', result: { data: { src: 'new' }, credits: 81 } };
  } });
  assert.equal(submissions, 1);
  assert.equal(polls, 2);
  assert.equal(result.data.src, 'new');
});

test('늦게 도착한 서버 실패 message도 그대로 보여 준다', async () => {
  assert.equal(typeof helpers.resolveCanvasAiSubmission, 'function');
  await assert.rejects(helpers.resolveCanvasAiSubmission(async () => {
    throw Object.assign(new Error('대기'), { code: 'job_timeout', jobId: 'accepted' });
  }, { getJob: async () => ({ status: 'error', errorMessage: '이 색 상품 사진이 없어요.' }), sleep: async () => {} }),
  /이 색 상품 사진이 없어요\./);
});

test('성공 봉투에 사진 주소가 없으면 빈 액자와 로딩 타일을 남기지 않는다', async () => {
  assert.equal(typeof helpers.resolveCanvasAiSubmission, 'function');
  await assert.rejects(helpers.resolveCanvasAiSubmission(async () => ({ data: {} })),
    (error) => /사진/.test(error.message) && error.submissionUnknown === true);
});

test('접수 뒤 연결 오류도 같은 작업을 추적해 결과를 잃지 않는다', async () => {
  assert.equal(typeof helpers.resolveCanvasAiSubmission, 'function');
  let submissions = 0;
  const result = await helpers.resolveCanvasAiSubmission(async () => {
    submissions += 1;
    throw Object.assign(new Error('연결 오류'), { code: 'api_network' });
  }, { acceptedJobId: () => 'accepted', sleep: async () => {},
    getJob: async (id) => { assert.equal(id, 'accepted'); return { status: 'done', result: { data: { src: 'recovered' } } }; } });
  assert.equal(submissions, 1);
  assert.equal(result.data.src, 'recovered');
});

test('접수된 작업의 접근 거절은 크레딧 미차감이라고 단정하지 않는다', async () => {
  assert.equal(typeof helpers.resolveCanvasAiSubmission, 'function');
  await assert.rejects(helpers.resolveCanvasAiSubmission(async () => {
    throw Object.assign(new Error('로그인을 확인해 주세요.'), { status: 401 });
  }, { acceptedJobId: () => 'accepted', getJob: async () => assert.fail('접근 거절을 폴링하지 않는다') }),
  (error) => error.submissionUnknown === true && error.status === 401);
});
