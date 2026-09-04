/* 모델 화면 계약 — 정지에 사유를 강제하는지, 상세가 네 블록을 다 내는지. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 모델 함수 네 개가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminListModels', 'adminModelDetail', 'adminSuspendModel', 'adminUnsuspendModel']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
});

test('사유가 비면 정지 버튼이 비활성이다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  assert.ok(/disabled=\{[^}]*!reason\.trim\(\)/.test(source), '빈 사유로 정지가 눌린다');
});

test('상세는 라이선스·정산·생체등록을 모두 보여준다', () => {
  const source = read('src/features/admin/AdminModels.jsx');
  for (const label of ['라이선스', '정산', '생체등록']) {
    assert.ok(source.includes(label), `상세 블록 누락: ${label}`);
  }
});

test('모델 행은 키보드만으로도 열 수 있다', () => {
  // TableRow 는 props 를 그대로 <tr> 로 흘려보낸다 — onClick 만 있으면 마우스가
  // 없는 관리자는 이 행을 절대 못 연다.
  const source = read('src/features/admin/AdminModels.jsx');
  const rowStart = source.indexOf('{items.map((m) =>');
  assert.ok(rowStart !== -1, '모델 행 렌더 블록을 못 찾았다');
  const rowEnd = source.indexOf('</TableRow>', rowStart);
  const rowBlock = source.slice(rowStart, rowEnd);

  assert.ok(/tabIndex=\{0\}/.test(rowBlock), '행에 tabIndex={0} 이 없다 — 포커스가 안 간다');
  assert.ok(/role=/.test(rowBlock), '행에 role 이 없다');
  assert.ok(
    /onKeyDown=\{[\s\S]*?e\.key === 'Enter'[\s\S]*?e\.key === ' '[\s\S]*?\}\}/.test(rowBlock)
      || /onKeyDown=\{[\s\S]*?e\.key === ' '[\s\S]*?e\.key === 'Enter'[\s\S]*?\}\}/.test(rowBlock),
    '행에 Enter·Space 를 둘 다 받는 onKeyDown 이 없다',
  );
  assert.ok(/focus-visible:ring/.test(rowBlock), '포커스된 행이 눈에 안 보인다 — 시각 포커스 스타일이 없다');
});
