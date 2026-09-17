import test, { after, before } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { createServer } from 'vite';

const root = new URL('../..', import.meta.url).pathname;
const cacheRoot = mkdtempSync(join(tmpdir(), 'facemarket-landing-home-'));
let vite;

before(async () => {
  vite = await createServer({
    configFile: false,
    root,
    cacheDir: join(cacheRoot, 'default'),
    logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } },
    esbuild: { jsx: 'automatic' },
  });
});

after(async () => {
  await vite?.close();
  rmSync(cacheRoot, { recursive: true, force: true });
});

const load = (entry) => vite.ssrLoadModule(entry);
const render = (Component) => renderToStaticMarkup(
  React.createElement(StaticRouter, null, React.createElement(Component)),
);

function visitTree(node, visitor) {
  if (node == null || typeof node === 'boolean') return;
  if (Array.isArray(node)) {
    node.forEach((child) => visitTree(child, visitor));
    return;
  }
  if (typeof node !== 'object') return;
  visitor(node);
  visitTree(node.props?.children, visitor);
}

function nodesBy(tree, predicate) {
  const matches = [];
  visitTree(tree, (node) => {
    if (predicate(node)) matches.push(node);
  });
  return matches;
}

function treeText(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(treeText).join('');
  return treeText(node.props?.children);
}

async function loadExport(entry, exportName) {
  try {
    return (await load(entry))[exportName];
  } catch (error) {
    assert.fail(`${exportName}을 불러와야 해요: ${error.message}`);
  }
}

test('홈 캐러셀 안내는 신뢰 pill 세 개를 그대로 보여줘요', async () => {
  const [GallerySection, styles] = await Promise.all([
    loadExport(
      '/src/features/facemarket-landing/sections/GallerySection.jsx',
      'GallerySection',
    ),
    load('/src/features/facemarket-landing/FacemarketLanding.module.css').then((module) => module.default),
  ]);
  const html = render(GallerySection);
  const trustPills = html.match(
    new RegExp(`<ul[^>]*class="${styles.trustPills}"[^>]*>([\\s\\S]*?)</ul>`),
  );
  assert.ok(trustPills, '신뢰 pill 목록이 있어야 해요');
  assert.equal((trustPills[1].match(/<li>/g) || []).length, 3);
});

test('FoundingSection은 전체 자리와 완료 자리와 남은 자리를 상수에서 만들고 지원 동작을 전달해요', async () => {
  const [{ FOUNDING_TOTAL, FOUNDING_FILLED }, FoundingSection, styles] = await Promise.all([
    load('/src/features/facemarket-landing/data/foundingModels.js'),
    loadExport(
      '/src/features/facemarket-landing/sections/FoundingSection.jsx',
      'FoundingSection',
    ),
    load('/src/features/facemarket-landing/FacemarketLanding.module.css').then((module) => module.default),
  ]);
  let primaryCalls = 0;
  const tree = FoundingSection({ onPrimary: () => { primaryCalls += 1; } });
  const slots = nodesBy(tree, (node) => node.props?.className?.split(' ').includes(styles.foundingSlot));
  const filled = nodesBy(tree, (node) => node.props?.className?.split(' ').includes(styles.foundingSlotFilled));
  assert.equal(FOUNDING_TOTAL, 7);
  assert.equal(FOUNDING_FILLED.length, 3);
  assert.equal(slots.length, 7);
  assert.equal(filled.length, 3);
  assert.ok(treeText(tree).includes('4자리남았어요'));
  const apply = nodesBy(tree, (node) => node.type === 'button' && treeText(node) === '지원하기')[0];
  assert.ok(apply, '첫 빈 자리는 지원하기 버튼이어야 해요');
  assert.equal(slots[3], apply, '네 번째 칸이 지원하기 버튼이어야 해요');
  assert.deepEqual(slots.slice(4).map(treeText), ['5', '6', '7']);
  apply.props.onClick();
  assert.equal(primaryCalls, 1);
});

test('RightsSection은 명세의 원칙 제목 다섯 문장을 순서대로 보여줘요', async () => {
  const RightsSection = await loadExport(
    '/src/features/facemarket-landing/sections/RightsSection.jsx',
    'RightsSection',
  );
  const headings = nodesBy(RightsSection(), (node) => node.type === 'h3').map(treeText);
  assert.deepEqual(headings, [
    '내 얼굴을 어떤 옷에 쓸지, 내가 정해요.',
    '쓰일 때마다 결제 금액의 70%가 내 몫이에요.',
    '어디에 쓰였는지 전부 볼 수 있어요.',
    '착용컷마다 위조할 수 없는 기록이 남아요.',
    '언제든 철회할 수 있어요.',
  ]);
});

test('HowItWorksSection은 세 시간 알약을 보여주고 기존 rail을 렌더하지 않아요', async () => {
  const [{ REVIEW_SLA_LABEL }, HowItWorksSection, styles] = await Promise.all([
    load('/src/features/facemarket-landing/facemarketTerms.js'),
    loadExport(
      '/src/features/facemarket-landing/sections/HowItWorksSection.jsx',
      'HowItWorksSection',
    ),
    load('/src/features/facemarket-landing/FacemarketLanding.module.css').then((module) => module.default),
  ]);
  const tree = HowItWorksSection();
  const times = nodesBy(tree, (node) => node.props?.className === styles.stepTime).map(treeText);
  assert.deepEqual(times, ['지원서 약 3분', REVIEW_SLA_LABEL, '촬영 약 15분']);
  assert.ok(!styles.rail || nodesBy(tree, (node) => node.props?.className === styles.rail).length === 0);
});

test('FaqSection은 details 없이 질문 여섯 개와 항상 보이는 답을 렌더해요', async () => {
  const [FaqSection, styles] = await Promise.all([
    loadExport('/src/features/facemarket-landing/sections/FaqSection.jsx', 'FaqSection'),
    load('/src/features/facemarket-landing/FacemarketLanding.module.css').then((module) => module.default),
  ]);
  const tree = FaqSection();
  const questions = nodesBy(tree, (node) => node.type === 'h3').map(treeText);
  assert.deepEqual(questions, [
    '제가 돈 쓰는 부분은 없나요?',
    '얼마를, 언제 받나요?',
    '모델 경력이 없어도 되나요?',
    '내 얼굴은 어디에 쓰이나요?',
    '등록할 때 무엇이 필요한가요?',
    '조건은 나중에 바꿀 수 있나요?',
  ]);
  assert.equal(nodesBy(tree, (node) => node.type === 'details').length, 0);
  assert.equal(nodesBy(tree, (node) => node.props?.className === styles.faqAnswerText).length, 6);
});

async function renderWithPricing(entry, exportName, pricing) {
  const vite = await createServer({
    configFile: false,
    root,
    cacheDir: join(cacheRoot, `${exportName}-${pricing.perCut}`),
    logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'facemarket-landing-pricing-override',
      enforce: 'pre',
      transform(source, id) {
        if (!id.endsWith('/src/lib/facemarketPricing.js')) return null;
        return source
          .replace('perCut: 14900', `perCut: ${pricing.perCut}`)
          .replace('monthly: 49900', `monthly: ${pricing.monthly}`);
      },
    }],
  });
  try {
    const module = await vite.ssrLoadModule(entry);
    return renderToStaticMarkup(
      React.createElement(StaticRouter, null, React.createElement(module[exportName])),
    ).replace(/<[^>]+>/g, '');
  } finally {
    await vite.close();
  }
}

test('RightsSection과 FAQ 금액은 pricing 상수가 바뀌면 함께 바뀌어요', async () => {
  const pricing = { perCut: 12345, monthly: 67890 };
  const [rights, faq] = await Promise.all([
    renderWithPricing(
      '/src/features/facemarket-landing/sections/RightsSection.jsx',
      'RightsSection',
      pricing,
    ),
    renderWithPricing(
      '/src/features/facemarket-landing/sections/FaqSection.jsx',
      'FaqSection',
      pricing,
    ),
  ]);
  assert.match(rights, /12,345원을 내면 8,642원이 내 몫으로 쌓여요/);
  assert.match(faq, /화면에 보이는 12,345원과 67,890원은 셀러가 내는 금액이에요/);
  assert.match(faq, /한 건이면 8,642원이고/);
});
