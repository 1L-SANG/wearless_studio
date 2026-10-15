import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const features = read('src/styles/features.css');
const seller = read('src/styles/sellerDesign.css');
const light = read('src/styles/wl-light.css');
const tokens = read('src/styles/tokens.css');

// 브라우저 실행 없이 선택 상태, 토큰 소비처, 미디어 경계의 CSS 계약을 검사한다.
function rule(css, selector) {
  const clean = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const bodies = [...clean.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selectors]) => selectors.trim().split(/,\s*(?![^()]*\))/).includes(selector));
  assert.ok(bodies.length, `${selector} 규칙이 있다`);
  return Object.fromEntries(bodies.flatMap(([, , body]) => body.split(';')
    .filter((declaration) => declaration.includes(':'))
    .map((declaration) => {
      const colon = declaration.indexOf(':');
      return [declaration.slice(0, colon).trim(), declaration.slice(colon + 1).trim()];
    })));
}

test('캡슐과 메뉴 고리는 구슬과 같은 범위의 하늘, 연보라, 살구 토큰을 쓴다', () => {
  const palette = rule(features, '.canvas-ai-layer');
  assert.equal(palette['--canvas-ai-sky'], '#B5DDFF');
  assert.equal(palette['--bead-rim-sky'], '#7DB8F2');
  assert.equal(palette['--bead-rim-lilac'], '#B49BEA');
  assert.equal(palette['--bead-rim-peach'], '#F2A985');
  assert.equal(palette['--bead-rim'], 'linear-gradient(120deg, color-mix(in srgb, var(--bead-rim-sky) 75%, transparent), color-mix(in srgb, var(--bead-rim-lilac) 65%, transparent) 50%, color-mix(in srgb, var(--bead-rim-peach) 65%, transparent))');
  assert.equal(rule(features, '.canvas-ai-read')['--bead-rim'], palette['--bead-rim']);
  for (const selector of ['.canvas-ai-orb::before', '.canvas-ai-branch::before',
    '.canvas-ai-option:not(.is-color)::before', '.canvas-ai-color-dot::before', '.canvas-ai-read::before']) {
    assert.equal(rule(features, selector).background, 'var(--bead-rim)', selector);
    assert.equal(rule(features, selector)['mask-composite'], 'exclude', selector);
  }
});

test('고리는 평소 .6, 올림과 열림과 초점에서 1이며 회전 생성 고리는 유지한다', () => {
  const palette = rule(features, '.canvas-ai-layer');
  assert.equal(palette['--bead-rim-opacity'], '.6');
  assert.equal(palette['--bead-rim-active-opacity'], '1');
  for (const selector of ['.canvas-ai-orb::before', '.canvas-ai-branch::before',
    '.canvas-ai-option:not(.is-color)::before', '.canvas-ai-color-dot::before']) {
    assert.equal(rule(features, selector).opacity, 'var(--bead-rim-opacity)', selector);
  }
  for (const selector of ['.canvas-ai-orb:is(:hover, :focus-visible, [aria-expanded="true"])::before',
    '.canvas-ai-branch:is(:hover, :focus-visible, .is-open)::before',
    '.canvas-ai-option:not(.is-color):is(:hover, :focus-visible)::before',
    '.canvas-ai-option.is-color:is(:hover, :focus-visible) .canvas-ai-color-dot::before',
    '.canvas-ai-read::before']) {
    assert.equal(rule(features, selector).opacity, 'var(--bead-rim-active-opacity)', selector);
  }
  const fill = rule(features, '.canvas-ai-pill.btn-glowring::before');
  assert.match(fill.background, /conic-gradient.*var\(--ring-flow-stops\)/);
  assert.equal(fill.opacity, '1');
});

test('셀러 보조 글자는 흰 바탕 5:1 이상이며 공용 회색과 빛 위 글자는 보존한다', () => {
  assert.equal(rule(seller, ':root')['--fg-2'], '#6E6E6E');
  assert.equal(rule(seller, ':root')['--fg-on-glow'], '#5C5C5C');
  assert.equal(rule(tokens, ':root')['--fg-2'], '#898989');
  const channel = parseInt(rule(seller, ':root')['--fg-2'].slice(1, 3), 16) / 255;
  const luminance = ((channel + .055) / 1.055) ** 2.4;
  assert.ok(1.05 / (luminance + .05) >= 5);
  assert.equal((1.05 / (luminance + .05)).toFixed(1), '5.1');
});

test('보관함 단계 알약은 흰 면과 경계, 선택은 검정 면과 흰 글자로 구분한다', () => {
  const idle = rule(features, '.lib-filter');
  assert.equal(idle.background, 'var(--bg-1)');
  assert.equal(idle.border, '1px solid var(--ring-strong)');
  assert.equal(idle.color, 'var(--fg-1)');
  assert.equal(idle['box-shadow'], 'var(--elev-soft)');
  const selected = rule(features, '.lib-filter[aria-pressed="true"]');
  assert.equal(selected.background, 'var(--fg-1)');
  assert.equal(selected.color, 'var(--bg-1)');
  const dot = rule(features, '.lib-stage-dot');
  assert.equal(dot.width, 'var(--sp-8)');
  assert.equal(dot.height, 'var(--sp-8)');
  // 흰 알약 위 점은 흰 테두리 없이 진한 단계색, 선택(검정) 알약에서만 흰 고리
  assert.equal(dot.border, undefined);
  assert.equal(dot.background, 'var(--stage-dot, var(--stage-fill))');
  assert.equal(rule(features, '.lib-filter[aria-pressed="true"] .lib-stage-dot')['box-shadow'], '0 0 0 1.5px var(--bg-1)');
  for (const stage of ['storyboard', 'mannequin', 'editor']) {
    const r = rule(features, `.stage-${stage}`);
    assert.equal(r['--stage-fill'], `var(--stage-${stage})`);
    assert.match(r['--stage-dot'], /^color-mix\(/);
  }
});

test('보관함 탭은 틀을 또렷하게 하고 선택 글자를 600으로, 나란한 알약 크기는 같게 한다', () => {
  assert.equal(rule(features, '.lib-tabs').border, '1px solid var(--ring-strong)');
  const selected = rule(features, '.lib-tab[aria-selected="true"]');
  assert.equal(selected.background, 'var(--bg-1)');
  assert.equal(selected.color, 'var(--fg-1)');
  assert.equal(selected['font-weight'], '600');
  for (const selector of ['.lib-tab', '.lib-filter']) {
    const pill = rule(features, selector);
    assert.equal(pill['border-radius'], 'var(--r-pill)');
    assert.equal(pill['min-height'], 'var(--cta-height)');
  }
  assert.ok(parseFloat(rule(tokens, ':root')['--cta-height']) >= 36);
});

test('768px부터 목록 오브는 모달 아래에서도 숨기고, 휴대폰 .62와 양옆 리본은 유지한다', () => {
  assert.match(light, /@media\s*\(min-width:\s*768px\)\s*\{\s*\.app-bg\[data-glow="list"\] \.wl-orb-root\s*\{\s*opacity:\s*0;/);
  assert.match(light, /@media\s*\(max-width:\s*767px\)\s*\{\s*\.app-bg\[data-glow="list"\]\s*\{\s*--wl-app-orb-opacity:\s*\.62;/);
  assert.match(light, /\.wl-orb-root\s*\{[^}]*transition:\s*opacity 200ms;/);
  assert.equal(rule(light, '.app-bg[data-glow="list"] .wl-orb-root').opacity, '0');
  assert.equal(rule(light, '.wl-aurora').display, 'block');
  assert.equal(rule(light, ':root')['--wl-app-aurora-opacity'], '.52');
  assert.equal(rule(light, 'body:has(.overlay, [aria-modal="true"], .storyboard-transition-overlay) :is(.wl-orb-root, .wl-aurora)').opacity, '.22');
});
