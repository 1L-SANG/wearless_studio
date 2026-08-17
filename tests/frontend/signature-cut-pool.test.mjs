import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  SIGNATURE_CUTS, isSignatureExampleId, signatureCutsFor, signatureCutById, pickSignatureCut,
} from '../../src/lib/signatureCutPool.js';

/* 시그니처 컷은 생성예시 카탈로그와 분리된 전용 풀이다(오너 확정 2026-08-17).
   정본 규칙: documents/genexamples_release_contract.md 와 별개 — 갤러리 나열·자동 배정·
   발행 게이트 어디에도 이 풀이 섞이면 안 된다. */

test('풀은 성별로 나뉘고 id 는 sig_ 접두를 쓴다', () => {
  assert.ok(SIGNATURE_CUTS.length >= 8);
  for (const cut of SIGNATURE_CUTS) {
    assert.ok(isSignatureExampleId(cut.id), `${cut.id} 는 sig_ 접두여야 한다`);
    assert.ok(['men', 'women'].includes(cut.gender));
    assert.match(cut.thumb, /^\/assets\/signature\/thumb\//);
    assert.match(cut.url, /^\/assets\/signature\//);
  }
  assert.ok(signatureCutsFor('men').every((cut) => cut.gender === 'men'));
  assert.ok(signatureCutsFor('women').every((cut) => cut.gender === 'women'));
});

test('성별 불명이면 women 풀로 떨어진다', () => {
  assert.deepEqual(signatureCutsFor(undefined), signatureCutsFor('women'));
  assert.deepEqual(signatureCutsFor('unknown'), signatureCutsFor('women'));
});

test('같은 프로젝트는 항상 같은 컷을 받는다(진입할 때마다 바뀌면 안 된다)', () => {
  const first = pickSignatureCut({ gender: 'women', projectId: 'proj-A' });
  for (let i = 0; i < 5; i += 1) {
    assert.equal(pickSignatureCut({ gender: 'women', projectId: 'proj-A' })?.id, first?.id);
  }
  assert.equal(first?.gender, 'women');
});

test('뽑힌 컷은 요청한 성별 풀 안에서만 나온다', () => {
  for (const projectId of ['p1', 'p2', 'p3', 'p4', 'p5', 'p6']) {
    assert.equal(pickSignatureCut({ gender: 'men', projectId })?.gender, 'men');
    assert.equal(pickSignatureCut({ gender: 'women', projectId })?.gender, 'women');
  }
});

test('생성예시 카탈로그에 sig_ 항목이 섞여 있지 않다', () => {
  const catalog = JSON.parse(readFileSync(new URL('../../src/data/genExamples.json', import.meta.url), 'utf8'));
  const items = Array.isArray(catalog) ? catalog : (catalog.examples || Object.values(catalog)[0]);
  assert.equal(items.filter((item) => isSignatureExampleId(item?.id)).length, 0);
});

test('썸네일 조회는 카탈로그 없이도 풀에서 해결된다', () => {
  const cut = SIGNATURE_CUTS[0];
  assert.equal(signatureCutById(cut.id)?.thumb, cut.thumb);
  assert.equal(signatureCutById('ex_styling_women_top_full_01'), null);
});

test('보드는 시그니처 슬롯에만 풀을 배정하고 사용자 선택은 보존한다', () => {
  const source = readFileSync(new URL('../../src/features/storyboard/Storyboard.jsx', import.meta.url), 'utf8');
  assert.match(source, /block\.hookSlotRole !== 'signature'/);
  assert.match(source, /exampleSelectionOrigin === 'user' && signatureCutById\(block\.exampleId\)/);
  // 썸네일 관문이 카탈로그보다 풀을 먼저 본다 — 이 순서가 깨지면 자리표시자가 뜬다.
  assert.match(source, /signatureCutById\(exampleId\)\?\.thumb\s*\n\s*\|\| \(catalogs\?\.genExamples/);
});
