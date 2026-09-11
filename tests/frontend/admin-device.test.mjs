/* 관리자 콘솔 기기 게이트 — 토큰 저장·헤더 주입·가드 화면·Staff 섹션.
   설계: docs/superpowers/specs/2026-09-11-admin-device-gate-design.md §6 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import {
  DEVICE_HEADER, DEVICE_REJECTED_EVENT, STORAGE_KEY,
  clearDeviceToken, defaultDeviceLabel, readDeviceToken, writeDeviceToken,
} from '../../src/lib/adminDevice.js';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test('토큰은 주어진 스토리지에 하나의 키로 저장·조회·삭제된다', () => {
  const s = memoryStorage();
  assert.equal(readDeviceToken(s), null);
  writeDeviceToken('abc', s);
  assert.equal(readDeviceToken(s), 'abc');
  assert.equal(s.getItem(STORAGE_KEY), 'abc');
  clearDeviceToken(s);
  assert.equal(readDeviceToken(s), null);
});

test('빈 문자열·공백 토큰은 없는 것으로 친다', () => {
  const s = memoryStorage();
  s.setItem(STORAGE_KEY, '   ');
  assert.equal(readDeviceToken(s), null);
  writeDeviceToken('', s);
  assert.equal(readDeviceToken(s), null);
});

test('스토리지가 던져도 읽기는 null, 쓰기·삭제는 조용히 실패한다', () => {
  const broken = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
    removeItem() { throw new Error('blocked'); },
  };
  assert.equal(readDeviceToken(broken), null);
  assert.doesNotThrow(() => writeDeviceToken('x', broken));
  assert.doesNotThrow(() => clearDeviceToken(broken));
});

test('기본 기기명은 서버와 같은 규칙으로 OS · 브라우저를 만든다', () => {
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'), 'macOS · Chrome');
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'), 'iPhone · Safari');
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36 Edg/128.0'), 'Windows · Edge');
  assert.equal(defaultDeviceLabel('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Mobile Safari/537.36'), 'Android · Chrome');
  assert.equal(defaultDeviceLabel(''), '알 수 없는 기기');
});

test('http() 는 토큰이 있으면 X-Admin-Device 를 싣고, device_* 403 이면 이벤트를 쏜다', () => {
  const src = read('src/lib/api/httpAdapter.js');
  assert.ok(src.includes("from '@/lib/adminDevice.js'"), 'adminDevice 를 import 하지 않는다');
  assert.ok(src.includes('readDeviceToken()'), '토큰을 읽지 않는다');
  assert.ok(src.includes('[DEVICE_HEADER]'), '헤더 이름 상수를 쓰지 않는다');
  assert.ok(src.includes('DEVICE_REJECTED_EVENT'), 'device_* 403 을 화면에 알리지 않는다');
  assert.ok(/code\s*\)?\.startsWith\('device_'\)/.test(src) || src.includes("startsWith('device_')"), 'device_ 접두 판정이 없다');
});

test('api 클라이언트에 기기 함수 5개가 있고 경로가 맞다', () => {
  const api = read('src/lib/api/facemarket.js');
  for (const fn of ['adminRegisterDevice', 'adminDeviceMe', 'adminListDevices', 'adminApproveDevice', 'adminRevokeDevice']) {
    assert.ok(api.includes(`export function ${fn}`), `누락: ${fn}`);
  }
  assert.ok(api.includes('/v1/facemarket/admin/devices/register'));
  assert.ok(api.includes('/v1/facemarket/admin/devices/me'));
  assert.ok(api.includes('/approve`'), 'approve 경로');
  assert.ok(api.includes('/revoke`'), 'revoke 경로');
});

test('헤더 이름과 이벤트 이름은 서버·가드와 약속된 값이다', () => {
  assert.equal(DEVICE_HEADER, 'X-Admin-Device');
  assert.equal(DEVICE_REJECTED_EVENT, 'admin-device-rejected');
  assert.equal(STORAGE_KEY, 'wl.admin.device.v1');
  // 서버 CORS 허용 목록과 맞물린다(Task 1).
  assert.ok(read('server/app/main.py').includes('"X-Admin-Device"'));
});

test('RequireDevice 는 RequireAuth 안·AdminShell 밖에 선다', () => {
  const app = read('src/apps/admin/App.jsx');
  assert.ok(app.includes("import { RequireDevice } from './RequireDevice.jsx'"));
  const auth = app.indexOf('<Route element={<RequireAuth />}>');
  const device = app.indexOf('<Route element={<RequireDevice />}>');
  const shell = app.indexOf('<Route element={<AdminShell />}>');
  assert.ok(auth !== -1 && device !== -1 && shell !== -1);
  assert.ok(auth < device && device < shell, '순서: RequireAuth → RequireDevice → AdminShell');
});

test('RequireDevice 는 서버의 gate 를 단일 진실로 삼고 4상태를 다 그린다', () => {
  const src = read('src/apps/admin/RequireDevice.jsx');
  // gate 가 enforce 가 아니면 화면을 막지 않는다(shadow/off 는 서버가 안 막는다).
  assert.ok(src.includes("gate !== 'enforce'"), 'enforce 분기가 없다');
  for (const state of ['pending', 'approved', 'revoked', 'unknown']) {
    assert.ok(src.includes(`'${state}'`), `상태 누락: ${state}`);
  }
  // 등록 화면·대기 화면·회수 화면·비관리자 화면 문구
  assert.ok(src.includes('승인 요청'));
  assert.ok(src.includes('다른 관리자가 승인해야 해요'));
  assert.ok(src.includes('회수됐어요'));
  assert.ok(src.includes('관리자만 가능해요'));
  // 폴링은 보일 때만, 언마운트에 정리
  assert.ok(src.includes('setInterval') && src.includes('clearInterval'));
  assert.ok(src.includes('visibilityState'));
  // 회수 이벤트를 듣는다
  assert.ok(src.includes('DEVICE_REJECTED_EVENT'));
  // 실패는 화면에 남는 에러 + 재시도(전체 게이팅 금지)
  assert.ok(src.includes('다시 시도'));
  // 폴링·지금확인·재등록이 겹칠 때 늦게 온 옛 응답이 새 상태를 덮어쓰지 않는 순번 가드
  assert.ok(src.includes('checkSeq'), '응답 순서 가드가 없다');
});

test('RequireDevice 의 too_many_pending 은 안내 문구가 따로 있다', () => {
  const src = read('src/apps/admin/RequireDevice.jsx');
  assert.ok(src.includes("'too_many_pending'"));
});
