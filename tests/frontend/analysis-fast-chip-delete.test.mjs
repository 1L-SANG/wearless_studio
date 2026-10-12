import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { transformWithEsbuild } from 'vite';
import { applySellingPointEdit, SELLING_POINTS_MAX } from '../../src/features/analysis/sellingPoints.js';
import { isFeatureRunPending } from '../../src/lib/featureRun.js';
const source = readFileSync(new URL('../../src/features/analysis/AnalysisForm.jsx', import.meta.url), 'utf8');
const title = source.indexOf('className="sec-title">강조하고 싶은 특징');
const fragment = source.slice(source.lastIndexOf('<div className="surface">', title), source.indexOf('{/* MD 코멘트 */}', title)).trim();
const { code } = await transformWithEsbuild(`const chips = () => (${fragment});`, 'Chips.jsx', { jsx: 'transform' });
const find = (tree, predicate) => !tree || typeof tree !== 'object' ? null : predicate(tree) ? tree
  : (tree.props?.children || []).flat(Infinity).map((child) => find(child, predicate)).find(Boolean);
for (const fast of [false, true]) test(`특징 삭제 버튼은 붙여넣은 보이지 않는 문구도 지운다, 빠른 진입 ${fast}`, () => {
  let patch;
  const a = { sellingPoints: ['\u200b'], aiSuggestedPoints: [], dismissedAiPoints: [] };
  const bindings = { React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    a, spEditIdx: null, spAdding: false, spDraft: '', highlightedPoints: [], aiSet: new Set(), SELLING_POINTS_MAX,
    Icon: 'Icon', isAnalysisFastEntryEnabled: () => fast, isFeatureRunPending, applySellingPointEdit,
    onChange: (value) => { patch = value; }, setSpAdding() {} };
  const chips = new Function(...Object.keys(bindings), `${code}; return chips;`)(...Object.values(bindings));
  const button = find(chips(), (node) => node.props.className === 'sp-chip-x');
  button.props.onClick({ stopPropagation() {} });
  assert.ok(patch);
  assert.deepEqual(patch.sellingPoints, []);
});
test('빠른 진입의 AI 칩 삭제 버튼은 버린 문구 목록도 함께 기록한다', () => {
  let patch; const a = { sellingPoints: ['AI 문구'], aiSuggestedPoints: ['AI 문구'], dismissedAiPoints: [] };
  const bindings = { React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    a, spEditIdx: null, spAdding: false, spDraft: '', highlightedPoints: [], aiSet: new Set(a.aiSuggestedPoints), SELLING_POINTS_MAX,
    Icon: 'Icon', isAnalysisFastEntryEnabled: () => true, isFeatureRunPending, applySellingPointEdit,
    onChange: (value) => { patch = value; }, setSpAdding() {} };
  const chips = new Function(...Object.keys(bindings), `${code}; return chips;`)(...Object.values(bindings));
  find(chips(), (node) => node.props.className === 'sp-chip-x').props.onClick({ stopPropagation() {} });
  assert.deepEqual(patch.dismissedAiPoints, ['AI 문구']);
});

function featureStatus(initial) {
  const effectsSource = source.slice(source.indexOf('  const [highlightedPoints, setHighlightedPoints]'), source.indexOf('  const commitSp ='));
  const frame = []; let cursor = 0, effects = [], a = initial;
  const bindings = { React: { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) },
    spEditIdx: null, spAdding: false, spDraft: '', aiSet: new Set(), SELLING_POINTS_MAX, Icon: 'Icon',
    isAnalysisFastEntryEnabled: () => true, isFeatureRunPending, applySellingPointEdit, onChange() {}, setSpAdding() {},
    getActiveFeatureRun: () => null,
    useState(initial) { const index = cursor++; if (!(index in frame)) frame[index] = initial;
      return [frame[index], (next) => { frame[index] = next; }]; },
    useRef(current) { const index = cursor++; return frame[index] ||= { current }; },
    useEffect(effect, deps) { const index = cursor++, prior = frame[index];
      if (!prior || deps.some((value, i) => !Object.is(value, prior.deps[i]))) {
        const record = { deps }; frame[index] = record;
        effects.push(() => { prior?.cleanup?.(); record.cleanup = effect(); });
      } },
  };
  const render = new Function('a', ...Object.keys(bindings), `${effectsSource}\n${code}; return chips();`);
  return { show(next = a) { a = next; cursor = 0; render(a, ...Object.values(bindings));
    effects.splice(0).forEach((effect) => effect()); cursor = 0; return render(a, ...Object.values(bindings)); },
    cleanup() { frame.forEach((record) => record?.cleanup?.()); } };
}
const analysisWithStage = (stage, changedPoints = ['정확한 핏']) => ({ sellingPoints: ['정확한 핏'], aiSuggestedPoints: ['정확한 핏'],
  featureRun: { runId: 'highlight_run_123', stage, changedPoints } });
const notice = (tree) => find(tree, (node) => node.props.role === 'status' && node.props.children.includes('더 정확한 특징으로 다듬었어요'));

test('applied 초안 복원 직후에는 다듬기 안내와 칩 강조를 다시 보이지 않는다', (t) => {
  const h = featureStatus(analysisWithStage('applied')); t.after(() => h.cleanup());
  const tree = h.show();
  assert.equal(notice(tree), undefined);
  assert.equal(find(tree, (node) => node.props.className?.includes('feature-refined')), undefined);
});
test('현재 화면에서 applied로 정착하면 바뀐 칩과 안내를 3초 동안 보인다', (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const h = featureStatus(analysisWithStage('decided', [])); t.after(() => h.cleanup());
  assert.equal(notice(h.show()), undefined);
  const tree = h.show(analysisWithStage('applied'));
  assert.ok(notice(tree)); assert.ok(find(tree, (node) => node.props.className?.includes('feature-refined')));
  t.mock.timers.tick(3000);
  assert.equal(notice(h.show()), undefined);
  assert.equal(notice(h.show(analysisWithStage('applied'))), undefined);
});
test('현재 화면에서 정착해도 바뀐 칩이 없으면 다듬기 안내는 없다', (t) => {
  const h = featureStatus(analysisWithStage('decided', [])); t.after(() => h.cleanup()); h.show();
  assert.equal(notice(h.show(analysisWithStage('applied', []))), undefined);
});
