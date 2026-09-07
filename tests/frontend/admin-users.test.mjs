/* 사용자 화면과 가입 출처 스탬프 — 셀러/FaceMarket 구분이 실제로 배선됐는지.

   되돌아가면: 콘솔이 두 서비스의 가입자를 다시 구분 없이 보여주거나(원래 문제), 프런트가
   스탬프를 안 보내 새 가입자까지 영영 '미상'으로 남는다. 둘 다 조용한 고장이다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('api 클라이언트에 사용자 목록 함수가 있다', () => {
  const api = read('src/lib/api/facemarket.js');
  assert.ok(api.includes('export function adminListUsers'), '누락: adminListUsers');
  for (const param of ['origin', 'cursor', 'limit']) {
    assert.ok(api.includes(`params.set('${param}'`) || api.includes(`limit: String(limit)`),
      `쿼리 파라미터 누락: ${param}`);
  }
});

test('출처 필터 값이 서버가 받는 집합과 같다', () => {
  const source = read('src/features/admin/AdminUsers.jsx');
  const server = read('server/app/facemarket_admin.py');
  // 서버: USER_ORIGINS = ("seller", "facemarket", "both", "unknown")
  const declared = server.split('USER_ORIGINS = (')[1].split(')')[0];
  for (const value of ['seller', 'facemarket', 'both', 'unknown']) {
    assert.ok(declared.includes(`"${value}"`), `서버 집합에 없다: ${value}`);
    assert.ok(source.includes(`'${value}'`), `화면 필터에 없다: ${value}`);
  }
});

test("'미상' 은 셀러로 뭉뚱그리지 않는다", () => {
  // app_origin 이 null 인 계정(스키마 변경 전 가입, 아직 재로그인 안 함)을 셀러로 그리면
  // 틀린 라벨이 사실처럼 보인다 — 빈 칸보다 나쁘다.
  const source = read('src/features/admin/AdminUsers.jsx');
  assert.ok(source.includes('미상'), '미상 상태를 표현하지 않는다');
  assert.ok(/if \(!origin\)/.test(source), '출처가 없는 계정을 따로 그리지 않는다');
});

test('검색은 타이핑마다가 아니라 확정할 때 보낸다', () => {
  // 서버가 이 목록 열람을 감사 원장에 남긴다 — 키 입력마다 요청하면 원장이 한 글자짜리
  // 조회로 가득 차서 "누가 무엇을 훑었나" 를 못 읽게 된다.
  const source = read('src/features/admin/AdminUsers.jsx');
  assert.ok(source.includes('const [term, setTerm]') && source.includes('const [query, setQuery]'),
    '입력값과 실제 검색어가 분리돼 있지 않다');
  const loadDeps = source.split('}, [')[1] || '';
  assert.ok(!loadDeps.startsWith('term'), 'load 가 입력값(term)에 직접 매여 있다');
});

test('목록은 "아직 안 불러옴" 과 "0건" 을 구분한다', () => {
  const source = read('src/features/admin/AdminUsers.jsx');
  assert.ok(/const \[items, setItems\] = useState\(null\)/.test(source),
    'items 초기값이 null 이 아니다 — []면 로딩 중에도 "결과 없음" 이 뜬다');
  assert.ok(source.includes('listError'), '조회 실패를 담을 에러 상태가 없다');
});

test('더 보기는 이어 붙이고, 커서가 있을 때만 보인다', () => {
  const source = read('src/features/admin/AdminUsers.jsx');
  assert.ok(source.includes('nextCursor'), '커서 페이징이 없다');
  assert.ok(/setItems\(\(prev\) => \[\.\.\.\(prev \|\| \[\]\), \.\.\.d\.items\]\)/.test(source),
    '더 보기가 목록을 이어 붙이지 않는다 — 다시 그리면 훑어 내린 위치를 잃는다');
});

test('로그인하면 가입 출처를 서버에 한 번 알린다', () => {
  const stamp = read('src/lib/appOrigin.js');
  assert.ok(stamp.includes("'/v1/me/app-origin'"), '스탬프 엔드포인트를 안 부른다');
  assert.ok(stamp.includes('IS_ADMIN'), '관리자 콘솔에서도 출처를 찍는다 — 진짜 출처가 덮인다');

  const provider = read('src/features/auth/AuthProvider.jsx');
  assert.ok(provider.includes('stampAppOrigin'), 'AuthProvider 가 스탬프를 안 부른다');
  // 토큰 갱신마다 session 객체는 새로 오지만 사용자는 그대로다 — deps 가 session 이면
  // 갱신마다 다시 보낸다.
  assert.ok(/\}, \[userId\]\)/.test(provider), '스탬프 effect 가 user.id 에 매여 있지 않다');
});

test('스탬프 실패는 로그인 흐름을 막지 않는다', () => {
  const stamp = read('src/lib/appOrigin.js');
  assert.ok(stamp.includes('.catch('), '실패가 그대로 던져진다 — 로그인 직후 정체불명 에러가 뜬다');
  // sessionStorage 접근 자체가 던지는 브라우저가 있다(사파리 프라이빗·사이트 데이터 차단).
  assert.equal((stamp.match(/try \{/g) || []).length, 2, 'sessionStorage 접근이 try/catch 밖에 있다');
});

test('감사 기록 화면이 목록 열람 액션을 이름으로 보여준다', () => {
  const staff = read('src/features/admin/AdminStaff.jsx');
  assert.ok(staff.includes("'users.list.view'"), '새 액션이 라벨 표에 없다 — 원문자열로 뜬다');
});
