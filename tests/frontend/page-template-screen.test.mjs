import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

test('both loaded storyboard screens and the old-page notice render without a browser', async t => {
  const logError = console.error;
  t.mock.method(console, 'error', (...args) => {
    if (String(args[0]).startsWith('Warning: useLayoutEffect does nothing on the server')) return;
    logError(...args);
  });
  const vite = await createServer({ configFile: false, plugins: [react()],
    optimizeDeps: { noDiscovery: true, include: [] },
    resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
    server: { middlewareMode: true, watch: null }, appType: 'custom', logLevel: 'silent' });
  t.after(() => vite.close());
  const { DB } = await vite.ssrLoadModule('/src/mock/db.js');
  const { useAppStore } = await vite.ssrLoadModule('/src/store/useAppStore.js');
  const { Storyboard } = await vite.ssrLoadModule('/src/features/storyboard/Storyboard.jsx');
  const { prefetchStoryboardEntry, invalidateStoryboardEntryPrefetch } = await vite.ssrLoadModule('/src/features/storyboard/storyboardEntryPrefetch.js');
  const { seedStoryboard } = await vite.ssrLoadModule('/src/lib/pageTemplates/seed.js');
  const { selectTemplateStyle } = await vite.ssrLoadModule('/src/lib/pageTemplates/pageStyle.js');
  const { PageTemplatePicker } = await vite.ssrLoadModule('/src/features/analysis/PageTemplatePicker.jsx');
  const picker = renderToStaticMarkup(React.createElement(PageTemplatePicker, { value: 'show', onChange() {} }));
  assert.equal((picker.match(/type="radio"/g) || []).length, 4);
  assert.match(picker, /checked="" value="show"/);
  assert.match(picker, /견본 사진이에요/);
  useAppStore.setState({ projectId: DB.project.id, projectPersisted: true });
  useAppStore.getInitialState = useAppStore.getState;
  const render = () => renderToStaticMarkup(React.createElement(MemoryRouter,
    { initialEntries: ['/create/storyboard'] }, React.createElement(Storyboard, { toastOverride: { push() {} } })));
  for (const templateId of ['emo', 'show']) {
    DB.analysis.pageStyle = selectTemplateStyle(templateId);
    DB.storyboard = seedStoryboard(DB.product, DB.analysis);
    invalidateStoryboardEntryPrefetch(DB.project.id);
    await prefetchStoryboardEntry(DB.project.id);
    const html = render();
    assert.match(html, /섹션 추가/);
    if (templateId === 'emo') assert.match(html, /일상 착용컷/);
    else assert.doesNotMatch(html, /일상 착용컷/);
    assert.match(html, /스튜디오 착용컷/);
    assert.match(html, /Section 1/);
    assert.match(html, /1번째 컷/);
    assert.doesNotMatch(html, /aria-label="첫 장 섹션 빼기"/);
    assert.match(html, /aria-label="페이지 구성"/);
    assert.match(html, /aria-label="첫 장 무대"/);
    assert.match(html.replace(/<[^>]*>/g, ''), /구성컷.*예상.*크레딧.*다음: 마네킹컷 선택/);
    assert.doesNotMatch(html, /콘티보드를 불러오는 중이에요|첫 화면 스타일|사진 양/);
  }
  DB.storyboard = DB.storyboard.map(b => ({ ...b, taxonomyVersion: 3 }));
  invalidateStoryboardEntryPrefetch(DB.project.id);
  await prefetchStoryboardEntry(DB.project.id);
  assert.match(render(), /새 템플릿 구성으로 다시 만들었어요/);
  DB.storyboard = DB.storyboard.map(b => ({ ...b, taxonomyVersion: 3 }));
  DB.project.status = 'done';
  invalidateStoryboardEntryPrefetch(DB.project.id);
  await prefetchStoryboardEntry(DB.project.id);
  const legacy = render();
  assert.match(legacy, /예전 방식으로 만든 페이지예요/);
  assert.match(legacy, /에디터 열기/);
  assert.match(legacy, /보관함으로/);
  assert.match(legacy, /role="img" aria-label="OPEN IN EDITOR 라고 적힌 옷 태그"/);
});
