// 목 디테일 추천(mockDetail=full)이 서버의 실제 저장 계약을 지키는지 보는 서버 시험은
// 프론트 패키지(vite)가 있어야 돈다. 서버 CI에는 그 패키지가 없어 건너뛰므로,
// 프론트 패키지와 server/.venv가 함께 있는 프론트 CI에서 같은 시험을 돌린다.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const PYTHON = process.env.PAGE_TEMPLATE_PYTHON || new URL('../../server/.venv/bin/python', import.meta.url).pathname;

test('mock full detail subjects satisfy the persisted recommendation contract (server test)', () => {
  const result = spawnSync(PYTHON, ['-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_woc3_round2_mock_details.py'], {
    cwd: `${ROOT}server`, encoding: 'utf8', timeout: 120_000,
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  assert.match(result.stdout, /2 passed/);
  assert.doesNotMatch(result.stdout, /skipped/);
});
