import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assemblePage, wrapText } from '../../src/lib/pageTemplates/assemble.js';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/page_templates_cases.json', import.meta.url)));
const baseInput = fixture.cases.find(item => item.name === 'emo-2-mono').input;

for (const colorMode of ['mono', 'point']) {
  for (const [displayName, initial] of [[undefined, 'MD'], ['', 'MD'], ['김민지', '김'], ['Wearless', 'W'], ['MD', 'M'], ['🧵옷가게', '🧵']]) {
    test(`WOQ4 MD avatar fits one centered line: ${colorMode}/${displayName ?? 'no profile'}`, () => {
      const input = structuredClone(baseInput);
      input.pageStyle = { ...input.analysis.pageStyle, colorMode };
      if (displayName !== undefined) input.profile = { display_name: displayName };
      const elements = assemblePage(input).filter(block => block.pageSectionKey === 'emo.intro').flatMap(block => block.elements);
      const avatar = elements.find(element => element.type === 'text' && !element.copyRole && element.text === initial);
      const circle = elements.find(element => element.type === 'shape' && element.radius === 999 && element.x <= avatar.x && element.y <= avatar.y);
      assert.ok(avatar);
      assert.ok(circle);
      assert.deepEqual([circle.w, circle.h], [98, 98]);
      assert.equal(avatar.w, 50, 'leave room for the actual 37px Pretendard MD, including rounding slack');
      assert.equal(avatar.style.size, 24);
      assert.equal(avatar.style.align, 'center');
      assert.equal(avatar.h, avatar.style.lineHeight);
      assert.equal(avatar.x + avatar.w / 2, circle.x + circle.w / 2);
      assert.equal(avatar.y + avatar.h / 2, circle.y + circle.h / 2);
      assert.deepEqual(wrapText(avatar.text, avatar.w, avatar.style.size), [initial]);
    });
  }
}
