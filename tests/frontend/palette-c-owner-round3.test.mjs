import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const clean = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');
const rules = (css) => [...clean(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)];
function rule(css, selector) {
  const normalMotion = css.replace(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(?:[^{}]|\{[^{}]*\})*\}/g, '');
  const bodies = rules(normalMotion).filter(([, selectors]) => selectors.trim().split(/,\s*(?![^()]*\))/).includes(selector));
  assert.ok(bodies.length, `${selector} 규칙이 있다`);
  return Object.fromEntries(bodies.flatMap(([, , body]) => body.split(';').filter((d) => d.includes(':')).map((d) => {
    const colon = d.indexOf(':');
    return [d.slice(0, colon).trim(), d.slice(colon + 1).trim()];
  })));
}
const features = read('src/styles/features.css');
const seller = read('src/styles/sellerDesign.css');

test('AI 메뉴는 회전 없이 같은 1px 무채색 선을 쓰고 올림과 초점에서 진해진다', () => {
  const palette = rule(features, '.canvas-ai-layer');
  assert.equal(palette['--canvas-ai-menu-line'], 'color-mix(in srgb, var(--fg-1) 22%, transparent)');
  assert.equal(palette['--canvas-ai-menu-line-active'], 'color-mix(in srgb, var(--fg-1) 70%, transparent)');
  for (const selector of ['.canvas-ai-branch::before', '.canvas-ai-option:not(.is-color)::before',
    '.canvas-ai-color-dot::before', '.canvas-ai-read::before']) {
    const line = rule(features, selector);
    assert.equal(line.padding, '1px', selector);
    assert.equal(line.background, 'var(--canvas-ai-menu-line)', selector);
    assert.equal(line.opacity, '1', selector);
    assert.equal(line.transition, 'background-color 180ms ease', selector);
  }
  for (const selector of ['.canvas-ai-branch:is(:hover, :focus-visible, .is-open)::before',
    '.canvas-ai-option:not(.is-color):is(:hover, :focus-visible)::before',
    '.canvas-ai-option.is-color:is(:hover, :focus-visible) .canvas-ai-color-dot::before']) {
    assert.equal(rule(features, selector).background, 'var(--canvas-ai-menu-line-active)', selector);
  }
  const fill = rule(features, '.canvas-ai-pill.btn-glowring');
  assert.equal(fill.border, '0');
  assert.equal(fill.animation, 'none');
  assert.equal(fill.background, 'var(--canvas-ai-glass)');
  assert.equal(fill.padding, undefined, '다른 알약과 같은 안쪽 여백을 상속한다');
  assert.doesNotMatch(clean(features), /\.canvas-ai-pill\.btn-glowring::before\s*\{[^}]*conic-gradient/);
  assert.match(rule(features, '.btn-glowring').background, /conic-gradient/, '메뉴 밖 생성 버튼은 유지한다');
  assert.equal(rule(features, '.canvas-ai-orb::before').background, 'var(--bead-rim)');
  assert.equal(rule(features, '.canvas-ai-frame')['box-shadow'], '0 0 0 1px var(--ring-strong), var(--canvas-ai-soft-light)');
  assert.equal(rule(features, '.canvas-ai-read.is-result::before').background, 'var(--bead-rim)', '결과 액자 설명의 구슬 고리는 보존한다');
  assert.match(read('src/features/editor/CanvasAiLayer.jsx'), /className=\{`canvas-ai-read\$\{!open && showResult \? ' is-result' : ''\}`\}/);
});

test('셀러 초점은 불투명 토스 파랑으로 흰색 대비 3:1 이상이며 연한 면과 선은 같은 원색이다', () => {
  const palette = rule(seller, ':root');
  assert.equal(palette['--accent-blue'], '#3182F6');
  assert.equal(palette['--link'], 'var(--accent-blue)');
  assert.equal(palette['--focus'], 'var(--accent-blue)');
  assert.equal(palette['--accent-blue-soft'], 'color-mix(in srgb, var(--accent-blue) 7%, var(--bg-1))');
  assert.equal(palette['--accent-blue-line'], 'color-mix(in srgb, var(--accent-blue) 50%, var(--bg-1))');
  const channels = [49, 130, 246].map((v) => v / 255).map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  const luminance = channels.reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  assert.ok(1.05 / (luminance + .05) >= 3);
  assert.match(rule(features, '.md-field:focus-within')['box-shadow'], /var\(--focus\)/);
  assert.match(rule(read('src/styles/app.css'), '.chip-input:focus')['box-shadow'], /var\(--accent-blue,/);
  assert.match(read('seller.html'), /<meta name="theme-color" content="#ffffff"\s*\/>/);
});

function cssFiles(dir) {
  return readdirSync(new URL(`../../${dir}/`, import.meta.url), { withFileTypes: true }).flatMap((entry) => {
    const path = `${dir}/${entry.name}`;
    if (/\/(?:admin|model|facemarket[^/]*)$/.test(path)) return [];
    return entry.isDirectory() ? cssFiles(path) : path.endsWith('.css') ? [path] : [];
  });
}

// Hue alone also matches gray ink/shadows. Low saturation and the two existing
// dark photo overlays are neutral; saturated dark blues must still be caught.
function isBlue(value) {
  let rgb;
  if (value.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length <= 4) hex = [...hex].map((c) => c + c).join('');
    rgb = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  } else {
    rgb = (value.match(/[\d.]+%?/g) || []).slice(0, 3).map((v) => parseFloat(v) / (v.endsWith('%') ? 100 : 255));
  }
  const high = Math.max(...rgb), low = Math.min(...rgb), delta = high - low;
  const lightness = (high + low) / 2;
  const neutralOverlay = ['12,18,28', '18,24,38'].includes(rgb.map((v) => Math.round(v * 255)).join(','));
  if (!delta || neutralOverlay || delta / (1 - Math.abs(2 * lightness - 1)) < .25) return false;
  const [r, g, b] = rgb;
  const hue = ((high === r ? (g - b) / delta : high === g ? (b - r) / delta + 2 : (r - g) / delta + 4) * 60 + 360) % 360;
  return hue >= 190 && hue <= 250;
}

test('파랑 검사는 새 hex, rgba와 밝은 파랑을 잡고 무채색은 구분한다', () => {
  for (const value of ['#8fbfee', '#2d7fc4', '#eaf6ff', '#00f', '#000033', 'rgba(45,127,196,.08)']) assert.ok(isBlue(value), value);
  for (const value of ['#0e0d14', '#707985', '#f3f5f6', 'rgba(34,42,53,.16)', '#d92d20']) assert.equal(isBlue(value), false, value);
});

test('셀러 UI CSS에는 토스 파랑과 명시한 콘텐츠·빛 예외 밖의 파랑이 없다', () => {
  const excluded = new Set(['src/styles/tokens.css', 'src/styles/facemarketTheme.css', 'src/styles/wl-light.css']);
  const violations = [];
  for (const file of cssFiles('src').filter((path) => !excluded.has(path))) {
    for (const [, selectors, body] of rules(read(file))) {
      // app.css is shared. These fallback literals preserve FaceMarket's old
      // colors; sellerDesign.css always defines the first token for the seller.
      const declarations = body.replace(/var\(--(?:accent-blue(?:-(?:soft|line))?|focus),\s*(#[\da-f]{3,8}\b|rgba?\([^)]*\))\)/gi, 'var(--seller-color)');
      for (const declaration of declarations.split(';')) {
        const colon = declaration.indexOf(':');
        const property = declaration.slice(0, colon).trim();
        const value = declaration.slice(colon + 1).replace(/%23/gi, '#');
        const contentHue = file === 'src/styles/features.css' && selectors.trim() === ".sf-hue-control input[type='range']" && property === 'background';
        const contentThumb = file === 'src/styles/features.css' && selectors.trim() === '.object-preset-glyph.qa-bubbles' && property === 'background';
        const orbToken = file === 'src/styles/features.css' && ['--canvas-ai-sky', '--bead-rim-sky'].includes(property);
        const lightToken = file === 'src/styles/sellerDesign.css' && property === '--ring-flow-6';
        if (contentHue || contentThumb || orbToken || lightToken) continue;
        for (const match of value.matchAll(/#[\da-f]{3,8}\b|rgba?\([^()]*\)/gi)) {
          if (isBlue(match[0]) && !(file === 'src/styles/sellerDesign.css' && property === '--accent-blue' && match[0].toLowerCase() === '#3182f6')) {
            violations.push(`${file} ${selectors.trim()} ${property}: ${match[0]}`);
          }
        }
      }
    }
  }
  assert.deepEqual(violations, []);
});

test('추천과 답변 표시는 CSS 토큰을 쓰며 상품 색과 콘텐츠 견본은 보존한다', () => {
  assert.match(read('src/features/editor/ContentPanel.jsx'), /<i className="frame-rec content-rec">추천<\/i>/);
  assert.match(read('src/features/editor/InfoBlockModal.jsx'), /<span className="info-faq-answer-label">A<\/span>/);
  assert.match(read('src/features/storyboard/Storyboard.jsx'), /navy: '#1f2a44', blue: '#2a5db0'/);
  assert.match(read('src/features/auth/Login.jsx'), /fill="#4285F4"/);
  assert.match(read('src/features/editor/EditorPanels.jsx'), /const TEXT_PALETTE = .*'#4f88c9'/);
  assert.match(read('src/features/editor/ContentPanel.jsx'), /fill="#dcecff"/);
});
