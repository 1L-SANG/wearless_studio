/* 셀러는 라이선스 확인 서비스(holder)의 사정을 몰라야 한다 — 2026-09-14 제품 결정.

   실제 모델을 고르면 그때부터 서버를 켜고, 준비되는 동안은 평소 "생성 중"으로만 보인다.
   확인은 잡이 하고(fail-closed), 끝내 못 하면 잡은 **일반 실패**로 끝난다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { jobFailure } from '../../src/lib/api/jobFailure.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('holder 사정은 잡 실패 코드로도 새지 않는다', () => {
  // 서버가 이미 일반 실패로 바꿔 보내지만, 옛 잡 결과가 남아 있어도 화면은 분기하지 않는다.
  const error = jobFailure({
    status: 'error', errorMessage: '이미지 생성 중 오류가 발생했어요. 다시 시도해 주세요.',
    result: { errorCode: 'holder_starting' },
  });
  assert.equal(error.code, 'job_failed');
});

test('에디터가 holder 코드로 분기하지 않는다', () => {
  const source = read('../../src/features/editor/Editor.jsx');
  assert.doesNotMatch(source, /holder_starting/);
});

test('서버가 셀러에게 보내기 전에 내부 사유를 가린다', () => {
  for (const worker of ('editor_image_job detail_page_job'.split(' '))) {
    const source = read(`../../server/app/workers/${worker}.py`);
    assert.match(source, /_INTERNAL_FAILURE_CODES = \{"holder_starting", "holder_unavailable"\}/,
      worker);
  }
});

test('라우트는 holder 를 부르지 않는다', () => {
  // 셀러 요청 경로에서 holder 를 부르면 "확인 중"이 화면 시간이 된다(콜드스타트 ~2분).
  const routes = read('../../server/app/routes.py');
  assert.doesNotMatch(routes, /await facemarket\.verify_license\(/);
  assert.match(routes, /facemarket\.verify_license_local\(/);
});
