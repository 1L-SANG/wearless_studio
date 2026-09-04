/* 대시보드 계약 — 큐 숫자가 목록으로 이어지는지, 기간 토글이 서버 허용값만 쓰는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 adminOverview 가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  assert.ok(api.includes('export function adminOverview'));
  assert.ok(api.includes('/v1/facemarket/admin/overview'));
});

test('기간 토글은 서버 허용값(7·30·90)만 낸다', () => {
  const source = read('src/features/admin/AdminDashboard.jsx');
  const periods = source.match(/PERIODS\s*=\s*\[([^\]]+)\]/);
  assert.ok(periods, 'PERIODS 상수가 없다');
  assert.deepEqual(
    periods[1].match(/\d+/g).map(Number).sort((a, b) => a - b),
    [7, 30, 90],
  );
});

test('큐 카드는 처리 화면으로 이어진다', () => {
  const source = read('src/features/admin/AdminDashboard.jsx');
  assert.ok(source.includes('/applications?status=under_review'), '검토 대기가 목록으로 안 이어진다');
});

test('다시 시도는 자기 자신을 돌려주는 no-op 함수형 업데이트가 아니고, 실제로 effect 를 재실행시킨다', () => {
  // setDays((d) => d) 처럼 함수형 업데이트가 이전과 같은 값을 돌려주면 리액트는
  // bail-out 해서 리렌더도 useEffect([days]) 재실행도 안 한다 — 버튼이 눌려도 아무 일도
  // 안 일어난다. 여기서는 버튼이 부르는 세터를 찾아 (1) 자기 인자를 그대로 돌려주는
  // 패턴이 아닌지, (2) 그 세터의 상태가 fetch 를 부르는 effect 의 의존성 배열에 실제로
  // 걸려 있는지 둘 다 확인한다.
  const source = read('src/features/admin/AdminDashboard.jsx');
  const retryIdx = source.indexOf('다시 시도');
  assert.ok(retryIdx !== -1, '다시 시도 버튼이 없다');
  const buttonTagStart = source.lastIndexOf('<Button', retryIdx);
  assert.ok(buttonTagStart !== -1, '다시 시도 버튼 태그를 못 찾았다');
  const buttonTag = source.slice(buttonTagStart, retryIdx);

  assert.ok(
    !/set\w+\(\s*\(?\s*(\w+)\s*\)?\s*=>\s*\1\s*\)/.test(buttonTag),
    '다시 시도가 자기 인자를 그대로 돌려주는 no-op 함수형 업데이트를 쓴다 — 리렌더도 재조회도 안 일어난다',
  );

  const setterMatch = buttonTag.match(/set([A-Z]\w*)\(/);
  assert.ok(setterMatch, '다시 시도 버튼에 상태 세터 호출이 없다');
  const stateName = setterMatch[1][0].toLowerCase() + setterMatch[1].slice(1);

  const effectMatch = source.match(/adminOverview\(days\)[\s\S]*?\}, \[([^\]]+)\]\);/);
  assert.ok(effectMatch, 'adminOverview 를 부르는 useEffect 의 의존성 배열을 못 찾았다');
  const deps = effectMatch[1].split(',').map((s) => s.trim());
  assert.ok(
    deps.includes(stateName),
    `재시도가 바꾸는 상태(${stateName})가 effect 의존성(${deps.join(', ')})에 없다 — 상태는 바뀌어도 재조회는 안 된다`,
  );
});
