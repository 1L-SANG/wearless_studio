/* 테스트컷 보정 3종 — 관리자가 고르고, 등록자는 받은 4장만 본다 (2026-09-16 대표 결정).

   화면이 지켜야 하는 계약만 본다(문자열 앵커): 관리자에는 셋이 나란히 있고 묶음마다 보내기가
   있어야 하며, 등록자 화면에는 보정 이름표가 **없어야** 한다. 이름표가 남으면 등록자는
   고를 수 없는 것을 고르라는 화면을 본다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

test('보정 코드가 서버 정본과 같다', () => {
  // 코드가 갈라지면 전송이 400 으로 막히거나, 화면이 빈 묶음을 그린다.
  const server = read('server/app/agents/face_identity.py');
  const block = /SKIN_FINISH_CODES: tuple\[str, \.\.\.\] = \(([^)]*)\)/.exec(server)?.[1];
  assert.ok(block, '서버 SKIN_FINISH_CODES 를 못 찾았다');
  const codes = [...block.matchAll(/"([a-z0-9]+)"/g)].map((m) => m[1]);
  assert.deepEqual(codes, ['prod', 'texture', 'soft50']);

  const admin = read('src/features/admin/AdminModels.jsx');
  for (const code of codes) assert.match(admin, new RegExp(`code: '${code}'`));
});

test('관리자 화면은 묶음마다 보내기를 가진다', () => {
  const admin = read('src/features/admin/AdminModels.jsx');
  // 보내기는 묶음 안에 있어야 한다 — 바깥에 하나면 어느 보정을 보내는지 알 수 없다.
  assert.match(admin, /adminSendModelTestCuts\(modelId, code\)/);
  assert.match(admin, /이 보정으로 보내기/);
  // 진행 표시와 다시 생성.
  assert.match(admin, /adminBuildModelTestCuts\(modelId\)/);
  for (const label of ['생성 대기', '생성 중', '부분 완료', '생성 완료', '생성 실패']) {
    assert.ok(admin.includes(label), `진행 표시에 "${label}" 이 없다`);
  }
  // 묶음 구성은 서버가 센 값을 쓴다 — 화면에서 다시 세면 둘이 갈라진다.
  assert.match(admin, /state\.finishCounts/);
});

test('등록자 화면에는 보정 이름표가 없다', () => {
  const confirm = read('src/features/model/ModelConfirm.jsx');
  assert.ok(!confirm.includes('SKIN_FINISH_LABEL'), '보정 이름표가 남아 있다');
  assert.ok(!confirm.includes('skinFinish'), '등록자 화면이 보정 값을 읽는다');
  // 하는 일은 1+1 고르기뿐이다.
  assert.match(confirm, /확대샷 1장, 전신샷 1장을 골라 주세요/);
});

test('업로드와 전송 API 가 보정을 함께 보낸다', () => {
  const api = read('src/lib/api/facemarket.js');
  assert.match(api, /form\.append\('skin_finish', skinFinish\)/);
  assert.match(api, /body: \{ skinFinish \}/);
  assert.match(api, /export function adminBuildModelTestCuts/);
});
