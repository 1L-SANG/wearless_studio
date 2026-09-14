/* 라이선스 확인 서비스(holder)는 scale-to-zero 다. 0대일 때 셀러가 컷을 만들면 "켜는 중"이고,
   1~2분 뒤 같은 버튼이 그대로 된다 — 고장이 아니다.

   2026-09-14 운영: 그 상황에서 셀러는 "라이선스 자격 증명 확인 서비스를 사용할 수 없습니다" 를
   x 아이콘으로 받았다. 문구도 아이콘도 "끝났다"로 읽혀서 다시 누를 이유가 없었다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { jobFailure } from '../../src/lib/api/jobFailure.js';
import { isNonRetryableRegenerateError } from '../../src/features/mannequin/generationRunnerCore.js';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');

test('holder_starting 은 잡 실패에서도 코드가 살아남는다', () => {
  const error = jobFailure({
    status: 'error',
    errorMessage: '라이선스 확인 서비스를 켜는 중이에요. 1~2분 뒤 다시 시도해 주세요.',
    result: { errorCode: 'holder_starting' },
  });
  assert.equal(error.code, 'holder_starting');
  // 재시도 금지 목록에 들어가면 안 된다 — 다시 시도하는 게 정답인 유일한 경로다.
  assert.equal(isNonRetryableRegenerateError(error), false);
});

test('에디터는 holder_starting 을 실패 아이콘으로 띄우지 않는다', () => {
  const source = read('../../src/features/editor/Editor.jsx');
  const handler = source.slice(source.indexOf('const handleImageJobFailure'));
  const body = handler.slice(0, handler.indexOf('\n  };'));
  assert.match(body, /holder_starting/);
  assert.match(body, /icon: e\?\.code === 'holder_starting' \? 'sparkles' : 'x'/);
});

test('서버와 화면이 같은 문구를 쓴다', () => {
  // 문구는 서버가 만든다(라우트 503·잡 실패 메시지 모두). 화면은 그대로 보여 준다 —
  // 두 벌로 나뉘면 한쪽만 고쳐지고 다른 쪽이 옛말을 계속한다.
  const server = read('../../server/app/facemarket.py');
  assert.match(server, /"라이선스 확인 서비스를 켜는 중이에요\. 1~2분 뒤 다시 시도해 주세요\."/);
  assert.match(server, /"holder_starting",/);
});
