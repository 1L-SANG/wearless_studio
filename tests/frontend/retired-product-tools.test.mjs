import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import * as generation from '../../src/lib/generationExamples.js';

test('사용이 끝난 방향 선택 모듈과 방향별 갤러리 도구를 내보내지 않는다', () => {
  assert.equal(existsSync(new URL('../../src/lib/directionChoice.js', import.meta.url)), false);
  for (const name of ['repickExampleForDirection', 'GENERATION_DIRECTION_SECTIONS', 'groupGenerationExamplesByDirection', 'exampleDirectionFamilyLabel', 'directionBadgeLabel']) {
    assert.equal(name in generation, false, name);
  }
});

test('새 콘티 카드와 촬영 세트는 폐기된 포즈 선택값을 만들지 않는다', () => {
  for (const file of ['storyboardCatalogDrag.js', 'storyboardSpaceSets.js']) {
    const source = readFileSync(new URL(`../../src/lib/${file}`, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /pose:\s*'auto'|poseLabel:/);
  }
});
