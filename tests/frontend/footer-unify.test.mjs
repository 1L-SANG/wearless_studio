import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { StaticRouter } from 'react-router-dom/server.js';
import { createServer } from 'vite';

const root = new URL('../..', import.meta.url).pathname;
let vite, SiteFooter, FooterSection, LegalPage, styles;
before(async () => {
  vite = await createServer({
    configFile: false,
    root,
    logLevel: 'silent',
    server: { middlewareMode: true, watch: null, ws: false },
    appType: 'custom',
    resolve: { alias: { '@': `${root}/src` } },
    esbuild: { jsx: 'automatic' },
    plugins: [{
      name: 'footer-legal-ready-state',
      enforce: 'pre',
      // SSR에서는 fetch effect가 실행되지 않으므로 문서를 읽은 상태로만 초기화한다.
      // 회사 정보와 화면 JSX는 실제 컴포넌트를 그대로 렌더한다.
      transform(source, id) {
        if (id.endsWith('/LegalPage.jsx')) {
          return source.replace("from 'react'", "from 'virtual:footer-legal-react'");
        }
      },
      resolveId(id) {
        if (id === 'virtual:footer-legal-react') return '\0footer-legal-react';
      },
      load(id) {
        if (id === '\0footer-legal-react') return `
          import { useState as useReactState } from 'react';
          export { useEffect, useMemo } from 'react';
          export const useState = (initial) => useReactState(initial?.phase === 'loading' ? {
            phase: 'ready',
            document: { title: '모델 이용약관', effectiveDate: '2026-09-08', version: '1.0' },
            markdown: '# 모델 이용약관\\n\\n테스트 문서',
          } : initial);
        `;
      },
    }],
  });
  ({ SiteFooter } = await vite.ssrLoadModule('/src/features/shell/SiteFooter.jsx'));
  ({ FooterSection } = await vite.ssrLoadModule('/src/features/facemarket-landing/sections/FooterSection.jsx'));
  ({ LegalPage } = await vite.ssrLoadModule('/src/features/legal/LegalPage.jsx'));
  ({ default: styles } = await vite.ssrLoadModule('/src/features/facemarket-landing/FacemarketLanding.module.css'));
});
after(() => vite?.close());

const render = (Component, props) => renderToStaticMarkup(
  React.createElement(StaticRouter, null, React.createElement(Component, props)),
);
const textContent = (html) => html.replace(/<[^>]+>/g, '');
const legalNav = (html) => html.match(/<nav\b[^>]*aria-label="법적 고지"[^>]*>([\s\S]*?)<\/nav>/)[1];

test('두 푸터와 법무 화면은 같은 회사 정보 7행과 연락처 링크를 순서대로 표시한다', () => {
  const expected = [
    `Copyright © ${new Date().getFullYear()} 데일리모먼트. All rights reserved.`,
    '사업자등록번호: 371-02-03688',
    '통신판매업번호: 면제 대상(직전연도 거래 50회 미만)',
    '대표자: 정일상',
    '연락처: 010-9592-0333',
    '이메일: contact@wearless.kr',
    '사업자주소: 서울특별시 노원구 석계로 98-2 광운대역 3층 스타트업스테이션',
  ];
  for (const [name, html, selector] of [
    ['셀러 푸터', render(SiteFooter), 'site-footer__company'],
    ['FaceMarket 푸터', render(FooterSection), styles.footerCompany],
    ['FaceMarket compact 푸터', render(FooterSection, { compact: true }), styles.footerCompany],
    ['법무 화면', render(LegalPage, { slug: 'terms-model' }), 'legal-business'],
  ]) {
    const company = html.match(new RegExp(`<(?:div|section) class="${selector}"[^>]*>([\\s\\S]*?)</(?:div|section)>`))?.[1];
    assert.ok(company, `${name}에 회사 정보 영역이 있어야 해요`);
    const rows = [...company.matchAll(/<p\b[^>]*>([\s\S]*?)<\/p>/g)].map((match) => match[1]);
    assert.deepEqual(rows.map(textContent), expected, name);
    assert.match(rows[4], /^연락처: <a\b[^>]*href="tel:010-9592-0333"[^>]*>010-9592-0333<\/a>$/);
    assert.match(rows[5], /^이메일: <a\b[^>]*href="mailto:contact@wearless.kr"[^>]*>contact@wearless.kr<\/a>$/);
    assert.doesNotMatch(company, /개인정보 보호책임자|Amazon Web Services|AWS|\|/);
    assert.doesNotMatch(html, /호스팅 Amazon Web Services/);
  }
});

test('두 푸터는 개인정보 처리방침 강조를 유지하고 법적 링크 구분점을 제거한다', () => {
  const sellerNav = legalNav(render(SiteFooter));
  const modelNav = legalNav(render(FooterSection));
  assert.equal((sellerNav.match(/<a\b/g) || []).length, 4);
  assert.equal((modelNav.match(/<a\b/g) || []).length, 5);
  assert.doesNotMatch(sellerNav + modelNav, /·/);
  assert.match(sellerNav, /<a href="https:\/\/wearless.kr\/privacy"><strong>개인정보 처리방침<\/strong><\/a>/);
  assert.match(modelNav, new RegExp(`<a class="[^"]*${styles.footerLinkStrong}[^"]*"[^>]*>개인정보 처리방침</a>`));
  const sellerCss = readFileSync(`${root}/src/features/shell/SiteFooter.css`, 'utf8');
  const modelCss = readFileSync(`${root}/src/features/facemarket-landing/FacemarketLanding.module.css`, 'utf8');
  assert.match(sellerCss, /\.site-footer__links strong\s*\{[^}]*font-weight:\s*600/);
  assert.match(modelCss, /\.footerLinkStrong\s*\{[^}]*font-weight:\s*600/);
});

test('두 푸터에 같은 창 문의 링크가 있고 브랜드와 FaceMarket 안내를 유지한다', () => {
  const seller = render(SiteFooter);
  const model = render(FooterSection, { compact: true });
  for (const html of [seller, model]) {
    const contact = html.match(/<a\b[^>]*href="https:\/\/wearless.kr\/#contact"[^>]*>문의하기<\/a>/)?.[0];
    assert.ok(contact);
    assert.doesNotMatch(contact, /target=/);
  }
  assert.match(seller, /<img\b[^>]*src="\/assets\/brand\/logo.svg"[^>]*alt=""/);
  assert.match(seller, /<img\b[^>]*src="\/assets\/brand\/wordmark.png"[^>]*alt="Wearless"/);
  assert.match(seller, /쇼핑몰 촬영의 새로운 기준/);
  assert.match(model, new RegExp(`<footer class="[^"]*${styles.footerCompact}`));
  assert.match(textContent(model), /FaceMarket · Wearless/);
  assert.match(textContent(model), /상품 상세페이지를 만드는 셀러라면 ai.wearless.kr 로 오세요\./);
  assert.match(textContent(model), /모델 등록 안내 · 내 얼굴이 어떻게 다뤄지나요/);
  for (const href of ['https://ai.wearless.kr', '/register', '/model-info']) {
    assert.ok(model.includes(`href="${href}"`));
  }
});
