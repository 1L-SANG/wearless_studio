import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { fitExampleImage, registeredFitExampleKeys } from '../../src/lib/fitExampleImages.js';

const DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '../../public/assets/fit-examples');

test('등록 목록은 디스크 파일과 일치하고 폐기된 tight 이미지는 참조하지 않는다', () => {
  // FILES 는 "존재하는 파일만 등록"하는 수동 목록이라 두 방향 모두 어긋날 수 있다:
  // 등록만 있고 파일이 없으면 깨진 <img>(안 뜨는 예시), 파일만 있고 등록이 없으면
  // 만들어 놓고도 텍스트 폴백이다(2026-08-01 WS3 — 사용자가 본 "예시 안 뜸"의 원인 계열).
  const disk = new Set(readdirSync(DIR).filter((f) => f.endsWith('.jpg')).map((f) => f.replace(/\.jpg$/, '')));
  const reg = new Set(registeredFitExampleKeys());
  const retired = new Set(['top-women-fit-tight']);
  assert.deepEqual([...reg].filter((k) => !disk.has(k)), [], '등록됐는데 파일 없음');
  assert.deepEqual([...disk].filter((k) => !reg.has(k) && !retired.has(k)), [], '파일 있는데 미등록');
  assert.deepEqual([...retired].filter((k) => reg.has(k)), [], '폐기 이미지가 아직 등록됨');
});

test('남성 바지 cut 6값 전부 이미지 타일이 뜬다 — WS3 의 직접 동기', () => {
  for (const v of ['slim', 'straight', 'tapered', 'relaxed', 'semi_wide', 'wide']) {
    assert.ok(fitExampleImage('pants', 'men', 'cut', v), `pants-men-cut-${v}`);
  }
});

test('매칭 상의 조정 스텝(WS2)이 쓰는 top length 예시가 남녀 모두 뜬다', () => {
  for (const v of ['crop', 'basic', 'long']) {
    assert.ok(fitExampleImage('top', 'men', 'length', v), `top-men length ${v}`);
    assert.ok(fitExampleImage('top', 'women', 'length', v), `top-women length ${v}`);
  }
});

test('없는 조합은 여전히 null — 텍스트 폴백 계약 불변', () => {
  assert.equal(fitExampleImage('pants', 'men', 'cut', 'banana'), null);
  assert.equal(fitExampleImage(null, 'men', 'cut', 'wide'), null);
});
