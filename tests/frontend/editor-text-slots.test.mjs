import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import * as textSlots from '../../src/lib/editorTextSlots.js';
import { alignSkeletonToServer } from '../../src/lib/editorWaitSkeleton.js';

const fixture = JSON.parse(readFileSync(new URL('../fixtures/hook-title-layout.json', import.meta.url), 'utf8'));
const SERVER_DIR = fileURLToPath(new URL('../../server/', import.meta.url));
const PYTHON = fileURLToPath(new URL('../../server/.venv/bin/python', import.meta.url));

test('44px 캡션은 20자까지 한 줄, 21자부터 두 줄 높이를 쓴다', () => {
  assert.equal(textSlots.captionHeight('가'.repeat(20)), 60);
  assert.equal(textSlots.captionHeight('가'.repeat(21)), 120);
});

test('60px 시그니처 제목은 단어 경계에서 최대 두 줄로 배치한다', () => {
  assert.deepEqual(
    textSlots.hookTitleLayout('프리미엄 울 블렌드 오버핏 케이블 라운드 니트'),
    {
      text: '프리미엄 울 블렌드\n오버핏 케이블 라운드 니트',
      lines: 2,
      size: 60,
      lineHeight: 72,
      h: 144,
    },
  );
});

test('옛 저장 글에 maxLines가 있어도 에디터는 줄을 잘라 숨기지 않는다', () => {
  const source = readFileSync(new URL('../../src/features/editor/Editor.jsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /textLineClampStyle|WebkitLineClamp|WebkitBoxOrient/);
  assert.ok(!Object.hasOwn(textSlots.hookTitleStyle('#ffffff', { size: 47, lineHeight: 56 }), 'maxLines'));
  assert.deepEqual(textSlots.hookTitleStyle('#ffffff', { size: 47, lineHeight: 56 }), {
    font: 'Pretendard', size: 47, weight: 700, color: '#ffffff', align: 'center', lineHeight: 56,
  });
});

for (const { name, input, expected } of fixture.cases) {
  test(`시그니처 제목 계약: ${name}`, () => {
    assert.deepEqual(textSlots.hookTitleLayout(input), expected);
  });
}

test('프론트 폭 표와 한계값은 공유 계약과 같다', async () => {
  const { HOOK_TITLE_METRICS, HOOK_TITLE_LIMITS } = await import('../../src/lib/hookTitleMetrics.js');
  const { source, ...metrics } = fixture.metrics;
  assert.deepEqual(HOOK_TITLE_METRICS, metrics);
  assert.deepEqual(HOOK_TITLE_LIMITS, fixture.limits);
});

test('모든 공유 예시에서 프론트와 서버의 제목 배치가 같다', () => {
  const python = spawnSync(PYTHON, ['-c', [
    'import json, sys',
    'from app.agents.page_assembler import _hook_title_layout',
    'print(json.dumps([_hook_title_layout(value) for value in json.load(sys.stdin)], ensure_ascii=False))',
  ].join('\n')], {
    cwd: SERVER_DIR, encoding: 'utf8', env: { ...process.env, PYTHONPATH: '.' },
    input: JSON.stringify(fixture.cases.map(({ input }) => input)),
  });
  assert.equal(python.status, 0, python.stderr);
  const results = JSON.parse(python.stdout);
  fixture.cases.forEach(({ name, input }, index) => {
    assert.deepEqual(results[index], textSlots.hookTitleLayout(input), name);
  });
});

test('제목은 한두 줄이며 정수 크기 44~60px에서 각 줄의 폭 예산을 지킨다', () => {
  const { metrics, limits } = fixture;
  for (const { name, input } of fixture.cases) {
    const result = textSlots.hookTitleLayout(input);
    const lines = result.text.split('\n');
    assert.equal(result.lines, lines.length, name);
    assert.ok([1, 2].includes(result.lines), name);
    assert.ok(Number.isInteger(result.size) && result.size >= limits.minSize && result.size <= limits.maxSize, name);
    assert.equal(result.lineHeight, Math.floor((result.size * 6 + 2) / 5), name);
    assert.equal(result.h, result.lines * result.lineHeight, name);
    for (const line of lines) {
      const width = Array.from(line).reduce((sum, character) => {
        const code = character.codePointAt(0);
        const hangul = (code >= 0xac00 && code <= 0xd7a3) || (code >= 0x3131 && code <= 0x318e);
        return sum + (hangul ? metrics.hangul : metrics.ascii[character] ?? metrics.extra[character] ?? metrics.fallback);
      }, 0);
      assert.ok(width * result.size <= limits.lineBudgetPx * metrics.unitsPerEm, name);
    }
  }
});

test('줄 경계는 원문의 공백이나 허용된 기호에 있고 어절 중간에서는 나누지 않는다', () => {
  for (const { name, input } of fixture.cases) {
    const original = input.normalize('NFC').replace(/[ \t\r\n\f\v\u00a0\u3000]+/g, ' ').replace(/^ +| +$/g, '');
    const lines = textSlots.hookTitleLayout(input).text.split('\n');
    if (lines.length === 1) continue;
    const [left, right] = lines;
    assert.ok(original.startsWith(left), name);
    const next = original.slice(left.length);
    const rightPrefix = right.replace(/…$/, '');
    if (next.startsWith(' ')) {
      assert.ok(next.slice(1).startsWith(rightPrefix), name);
    } else {
      assert.ok(/[)\]}】〕」』）］/·,]$/.test(left) || /^[([{【〔「『（［]/.test(next), name);
      assert.ok(next.startsWith(rightPrefix), name);
      assert.ok(!(/[\p{L}\p{N}]$/u.test(left) && /^[\p{L}\p{N}]/u.test(next)), name);
    }
  }
});

test('제목은 정해진 공백만 정리하고 NFC와 null 및 숫자 입력을 처리한다', () => {
  const empty = { text: '', lines: 1, size: 60, lineHeight: 72, h: 72 };
  for (const value of [null, undefined, ' \t\r\n\f\v\u00a0\u3000 ']) {
    assert.deepEqual(textSlots.hookTitleLayout(value), empty);
  }
  assert.deepEqual(textSlots.hookTitleLayout(' \t니트\r\n\f\v\u00a0\u3000 반팔 '), {
    ...empty, text: '니트 반팔',
  });
  assert.deepEqual(textSlots.hookTitleLayout('\u2003니트\u2003'), { ...empty, text: '\u2003니트\u2003' });
  assert.deepEqual(textSlots.hookTitleLayout(0), { ...empty, text: '0' });
});

test('대기 화면 정렬은 이미 배치한 제목의 글자와 스타일과 높이를 보존하고 y만 내림한다', () => {
  const title = {
    type: 'text', copyRole: 'hookTitle', text: '첫째 줄\n둘째 줄\n셋째 줄', y: 258, h: 145,
    style: { size: 47, lineHeight: 56, color: '#123456', maxLines: 2 },
  };
  const block = { elements: [{ type: 'image', sourceBlockId: 'signature', y: 50, h: 560 }, title] };
  const aligned = alignSkeletonToServer([block], true)[0].elements[1];
  assert.deepEqual(aligned, { ...title, y: 637 });
});
