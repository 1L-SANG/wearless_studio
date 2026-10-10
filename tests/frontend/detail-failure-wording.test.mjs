import { failedCutPresentation } from '../../src/lib/cutFailureMessages.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import { incompleteMessage, retryOfIncomplete, retryReuseHint } from '../../src/lib/detailPageJobFailure.js';
import { hasImageSource } from '../../src/features/editor/editorImageSource.js';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const editor = read('src/features/editor/Editor.jsx');
const shell = read('src/features/shell/ChromeLayout.jsx');
const band = editor.slice(editor.indexOf('      {((genActive && dpJob.status'), editor.indexOf('      {/* FaceMarket 차단'));
const declarations = editor.slice(editor.indexOf('  const genFailed ='), editor.indexOf('\n  return (', editor.indexOf('  const genFailed =')));
const { code: bandCode } = await transformWithEsbuild(`function Band(){${declarations} return <>${band}</>}`, 'Band.jsx', { jsx: 'transform' });
const ribbon = shell.slice(shell.indexOf('function DetailPageJobRibbon()'), shell.indexOf('export function ChromeLayout()'));
const { code: ribbonCode } = await transformWithEsbuild(ribbon, 'Ribbon.jsx', { jsx: 'transform' });
const incomplete = { requested: 2, completed: ['cut1'], failed: ['cut2'], retained: ['cut1'], expired: false };

function failureViews(job, overrides = {}) {
  const bindings = { ClientErrorCapture: () => null, genFinalizeOriginalError: null, React, dpJob: job, projectId: 'p', genActive: false, genFinalizeError: null,
    incompleteMessage, retryOfIncomplete, retryReuseHint, genNotif: 'denied',
    genMergedRef: { current: false }, genActiveRef: { current: false }, setGenActive() {},
    useAppStore: Object.assign((select) => select({ detailPageJob: job }), { getState: () => ({}) }),
    Icon: 'svg', Button: 'button', SmoothProgressTrack: 'span', EXPECTED_MS: { detailPage: 1 }, leaveToLibrary() {},
    useNavigate: () => () => {}, useLocation: () => ({ pathname: '/library' }), useEffect() {}, useSmoothProgress: () => 0,
    ...overrides };
  const Band = new Function(...Object.keys(bindings), `${bandCode}; return Band;`)(...Object.values(bindings));
  const Ribbon = new Function(...Object.keys(bindings), `${ribbonCode}; return DetailPageJobRibbon;`)(...Object.values(bindings));
  return { Band, Ribbon };
}

for (const message of ['완성본을 불러오지 못했어요.', '편집 내용을 저장하지 못했어요.']) {
  test(`생성 완료 후 마무리 오류는 생성 성공을 안내하고 다시 불러오기만 실행한다: ${message}`, () => {
    let attempts = 0;
    const job = { projectId: 'p', jobId: 'j', status: 'done', progress: 100 };
    const { Band } = failureViews(job, {
      genActive: true, genFinalizeError: message,
      setGenFinalizeAttempt: (update) => { attempts = update(attempts); },
      useAppStore: { getState() { throw new Error('완료된 생성 작업을 다시 시작하면 안 된다'); } },
    });
    const html = renderToStaticMarkup(React.createElement(Band));
    assert.match(html, /생성은 완료됐어요/);
    assert.ok(html.includes(message));
    assert.doesNotMatch(html, /상세페이지를 아직 만들지 못했어요|아직 못 만든 컷|크레딧 미차감|>다시 만들기</);
    const descendants = (node) => !node || typeof node !== 'object' ? []
      : [node, ...React.Children.toArray(node.props?.children).flatMap(descendants)];
    const retry = descendants(Band()).find((node) => node.type === 'button'
      && node.props.children === '완성본 다시 불러오기');
    assert.ok(retry);
    retry.props.onClick();
    assert.equal(attempts, 1);
  });
}

for (const [name, extra, heading] of [
  ['부분 미완성', { incomplete }, '아직 못 만든 컷이 있어요'],
  ['복원된 부분 미완성', { errorCode: 'incomplete_cuts' }, '아직 못 만든 컷이 있어요'],
  ['전체 미완성', {}, '상세페이지를 아직 만들지 못했어요'],
  ['취소', { errorCode: 'job_cancelled' }, '생성을 멈췄어요'],
]) test(`${name}: 에디터와 경로 리본은 부드러운 제목과 서버 상세 문구를 표시한다`, () => {
  const job = { projectId: 'p', jobId: 'j', status: 'error', errorMessage: '서버 상세 안내. 크레딧이 차감되지 않았어요.', ...extra };
  const { Band, Ribbon } = failureViews(job);
  for (const component of [Band, Ribbon]) {
    const html = renderToStaticMarkup(React.createElement(component));
    assert.ok(html.includes(heading), html);
    assert.ok(html.includes(job.errorMessage), html);
    assert.doesNotMatch(html, /상세페이지 생성에 실패했어요/);
  }
  const html = renderToStaticMarkup(React.createElement(Band));
  assert.match(html, />다시 만들기<\/button>/);
  assert.doesNotMatch(html, />다시 시도<\/button>/);
});

for (const [name, extra, heading] of [
  ['부분 미완성', { incomplete }, '아직 못 만든 컷이 있어요'],
  ['전체 미완성', {}, '상세페이지를 아직 만들지 못했어요'],
  ['취소', { errorCode: 'job_cancelled' }, '생성을 멈췄어요'],
]) test(`${name}: 에디터 상세 줄은 비었거나 제목과 같으면 숨긴다`, () => {
  for (const errorMessage of ['', heading]) {
    const job = { projectId: 'p', status: 'error', errorMessage, ...extra };
    const { Band } = failureViews(job);
    const html = renderToStaticMarkup(React.createElement(Band));
    assert.equal(html.split(heading).length - 1, 1);
    assert.doesNotMatch(html, /ed-genbar-detail/);
  }
});

test('만들지 못한 컷은 미완성 태그와 기존 크레딧 안내를 표시한다', async () => {
  const start = editor.indexOf('    if (!hasImageSource(el) && el.genPending) {');
  const tile = editor.slice(start, editor.indexOf('    if (!hasImageSource(el)) {', start));
  const { code } = await transformWithEsbuild(`function Tile(){${tile}}`, 'Tile.jsx', { jsx: 'transform' });
  const Tile = new Function('React', 'el', 'base', 'hasImageSource', 'failedCutPresentation', `${code}; return Tile;`)(React,
    { id: 'cut', type: 'image', src: null, genPending: 'failed', radius: 0 }, {}, hasImageSource, failedCutPresentation);
  const html = renderToStaticMarkup(React.createElement(Tile));
  assert.match(html, /class="ed-genwait-tag fail">미완성<\/span>/);
  assert.match(html, /이 컷은 만들지 못했어요<br\/><small>이 컷 값은 받지 않았어요<br\/>저희 쪽에서 다시 만들 수 있어요<\/small>/);
  assert.doesNotMatch(html, /실패/);
});

const mock = read('src/mock/api.js');
const getJobSource = mock.slice(mock.indexOf('  async getJob(jobId) {'), mock.indexOf('  async getJobEvents(', mock.indexOf('  async getJob(jobId) {')));
for (const [status, detail] of [['error', '크레딧은 차감되지 않았어요. 다시 만들 수 있어요.'],
  ['cancelled', '다시 만들 수 있어요.']]) test(`목의 복원된 ${status} 잡은 제목 대신 기본 상세 문구를 준다`, async () => {
  const api = new Function('detailJobs', 'projectDrafts', 'ewSim', `return ({${getJobSource}});`)(new Map(),
    new Map([['p', { project: { latestJobs: { detailPage: { id: 'j', status } } } }]]), null);
  assert.equal((await api.getJob('j')).errorMessage, detail);
});
