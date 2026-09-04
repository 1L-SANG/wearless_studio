/* 프로덕션 문서 호스트는 전부 API 의 CORS 허용 오리진에 있어야 한다.

   호스트를 새로 띄우는 일(진입 문서 + vercel rewrite + 번들)과 그 호스트에서 API 를 부를 수
   있게 하는 일(CORS_ORIGINS)이 서로 다른 파일에 있어서, 앞의 셋만 하고 뒤를 빼먹으면 화면은
   멀쩡히 뜨는데 모든 요청이 preflight 에서 죽는다 — 2026-09-04 admin.wearless.kr 이 정확히
   그랬다(로그인까지 되고 대시보드만 "서버에 연결하지 못했어요"). 이 저장소는 같은 실수를
   전에도 한 번 했다.

   그래서 두 파일을 여기서 맞대 본다. host.js 의 PRODUCTION_DOCUMENT_HOSTS 가 곧 "우리가 문서를
   내보내는 호스트" 목록이므로, 그것이 단일 출처다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');

function productionDocumentHosts() {
  const source = read('src/lib/host.js');
  const match = source.match(/PRODUCTION_DOCUMENT_HOSTS\s*=\s*\[([^\]]+)\]/);
  assert.ok(match, 'host.js 에서 PRODUCTION_DOCUMENT_HOSTS 를 찾지 못했다');
  return [...match[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
}

function corsOrigins() {
  const manifest = read('copilot/api/manifest.yml');
  const match = manifest.match(/^\s*CORS_ORIGINS:\s*(.+)$/m);
  assert.ok(match, 'manifest 에서 CORS_ORIGINS 를 찾지 못했다');
  return match[1].trim().split(',').map((s) => s.trim());
}

test('프로덕션 문서 호스트가 전부 CORS 허용 오리진에 있다', () => {
  const origins = corsOrigins();
  for (const host of productionDocumentHosts()) {
    assert.ok(
      origins.includes(`https://${host}`),
      `${host} 에서 오는 요청이 preflight 에서 막힌다 — copilot/api/manifest.yml 의 CORS_ORIGINS 에 https://${host} 를 더해라`,
    );
  }
});

test('호스트 목록이 비어 있지 않다', () => {
  // 정규식이 조용히 빈 배열을 만들면 위 테스트가 아무것도 검사하지 않고 통과한다.
  assert.ok(productionDocumentHosts().length >= 3);
  assert.ok(corsOrigins().length >= 3);
});
