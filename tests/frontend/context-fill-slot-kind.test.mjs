import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { editorSlotKind } from '../../src/lib/editorSlotKind.js';

const cases = JSON.parse(readFileSync(new URL('../fixtures/editor-slot-kind.json', import.meta.url), 'utf8'));

for (const fixture of cases) {
  test(`서버와 공유한 빈 칸 판정: ${fixture.name}`, () => {
    assert.equal(editorSlotKind(fixture.block, fixture.element, fixture.storyboard), fixture.expected);
  });
}
