import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

/* =============================================================
   /model/* 흰 바탕 화면에서 푸터 색이 갈리지 않아야 한다.

   등록 위저드(ModelRegister)와 지원서(ModelApply)는 `.page` 가 `--bg-1`(흰색)을 칠한다.
   반면 셸(.fm-theme-page)의 기본 바탕은 facemarket 의 파란 기 도는 그라데이션이라, 본문이
   끝나는 자리에서 흰색 → 파랑으로 색이 갈렸다(2026-09-22 /model/register 실측:
   255,255,255 → 240,246,250). 지원서는 푸터가 없어 드러나지 않았고, 등록 위저드에서만
   보였다.

   위저드의 흰 바탕은 승인된 디자인이라 그대로 두고, **셸을 같은 흰색으로** 맞춘다.
   ============================================================= */

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const layout = read('../../src/features/facemarket-shell/FacemarketModelLayout.jsx');
const layoutCss = read('../../src/features/facemarket-shell/FacemarketModelLayout.module.css');
const registerCss = read('../../src/features/model/ModelRegister.module.css');
const applyCss = read('../../src/features/model/ModelApply.module.css');

const pageBackground = css => {
  const block = css.match(/\.page\s*\{([^}]*)\}/);
  assert.ok(block, '.page 규칙이 없어요');
  const color = block[1].match(/background:\s*([^;]+);/);
  assert.ok(color, '.page 에 background 가 없어요');
  return color[1].trim();
};

test('흰 바탕을 쓰는 화면은 셸도 같은 토큰으로 칠한다', () => {
  // 값이 아니라 **일치**를 본다 — 한쪽만 바꾸면 다시 갈린다.
  const shell = layoutCss.match(/\.plainSurface:global\(\.fm-theme-page\)\s*\{\s*background:\s*([^;]+);/);
  assert.ok(shell, '.plainSurface 규칙이 없어요');
  assert.equal(shell[1].trim(), pageBackground(registerCss));
  assert.equal(shell[1].trim(), pageBackground(applyCss));
});

test('등록 위저드와 지원서가 그 바탕을 켠다', () => {
  assert.match(layout, /pathname === '\/model\/register'/, '등록 위저드가 빠졌어요');
  assert.match(layout, /pathname === '\/model\/apply'/, '지원서가 빠졌어요');
  assert.match(layout, /s\.plainSurface/, '클래스가 연결되지 않았어요');
});

test('푸터를 감추는 것은 지원서 하나뿐이다', () => {
  // 바탕(plainSurface)과 푸터 숨김(application)은 서로 다른 조건이다. 등록 위저드는
  // 흰 바탕을 쓰지만 푸터는 그대로 보여야 한다 — 둘을 한 플래그로 합치지 마라.
  assert.match(layout, /\{!application && /, '푸터 숨김 조건이 바뀌었어요');
  assert.doesNotMatch(layout, /\{!plain && /, '푸터가 등록 위저드에서도 사라져요');
});
