import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/* =============================================================
   /models 푸터가 목록 판과 같은 색이어야 한다.

   모델 리스트는 `.browse` 가 중립 회색 판(#f5f6f7)을 전폭으로 깔고, 그 판이
   `margin-bottom: -4rem` 으로 푸터를 끌어올려 겹친다. 푸터 자신은 배경이 없어
   셸 배경(파란 기 도는 --fm-page-bg #edf3f8 그라데이션)이 비치는데, 판이 끝나는
   지점에서 회색→파랑으로 색이 갈렸다(2026-09-22 실측: 245,246,247 → 238,243,248).

   두 색은 서로 다른 파일에 있어 한쪽만 바뀌면 다시 갈린다. 그래서 값이 아니라
   **일치**를 검사한다.
   ============================================================= */

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const landingCss = read('../../src/features/facemarket-landing/FacemarketLanding.module.css');
const browseCss = read('../../src/features/facemarket-landing/BrowseModels.module.css');
const shell = read('../../src/features/facemarket-landing/LandingShell.jsx');
const modelsPage = read('../../src/features/facemarket-landing/pages/ModelsPage.jsx');

const declaredColor = (css, selector) => {
  const block = css.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
  assert.ok(block, `${selector} 규칙이 없어요`);
  const color = block[1].match(/background:\s*(#[0-9a-fA-F]{3,8})\s*;/);
  assert.ok(color, `${selector} 에 단색 background 가 없어요`);
  return color[1].toLowerCase();
};

test('/models 셸 배경은 목록 판과 같은 색이다', () => {
  assert.equal(declaredColor(landingCss, '.plainSurface'), declaredColor(browseCss, '.browse'));
});

test('모델 리스트 페이지가 그 배경을 실제로 켠다', () => {
  assert.match(shell, /surface\b/, 'LandingShell 이 surface 를 받지 않아요');
  assert.match(shell, /surface === 'plain'\s*\?\s*s\.plainSurface/, 'surface="plain" 이 클래스로 이어지지 않아요');
  assert.match(modelsPage, /surface="plain"/, 'ModelsPage 가 surface 를 넘기지 않아요');
});

test('기본 페이지는 그대로 그라데이션을 쓴다', () => {
  // 회귀 방지: .shell 의 원래 배경(그라데이션 2겹)을 통째로 갈아치우면 안 된다.
  assert.match(landingCss, /linear-gradient\(180deg, #f8fbfd 0%, var\(--fm-page-bg\) 100%\)/);
});
