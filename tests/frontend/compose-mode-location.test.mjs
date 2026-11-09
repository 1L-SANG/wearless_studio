import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { pageTemplatesCatalog } from '../../src/lib/pageTemplates/catalog.js';
const read = file => readFileSync(new URL(`../../${file}`, import.meta.url), 'utf8');
test('analysis confirmation presents two sample template cards and awaits style persistence', () => {
  const form = read('src/features/analysis/AnalysisForm.jsx');
  const picker = read('src/features/analysis/PageTemplatePicker.jsx');
  assert.match(form, /<PageTemplatePicker/);
  assert.match(form, /pending = pageStyleSaveRef.current;\s*await pending/);
  assert.match(form, /await onChange\(\{ pageStyle: style \}\)/);
  assert.match(picker, /견본 사진이에요/);
  assert.match(picker, /type="radio" name="page-template"/);
  assert.deepEqual(pageTemplatesCatalog.templates.map(t => t.id), ['emo','show']);
  assert.doesNotMatch(form, /상세페이지 사진 양|composeModeSaveRef/);
});
test('storyboard uses section components with no photo-volume or hook-style selector', () => {
  const storyboard = read('src/features/storyboard/Storyboard.jsx');
  for (const component of ['StoryboardPageList','StoryboardStage','StoryboardDrawer']) assert.match(storyboard, new RegExp(`<${component}`));
  assert.doesNotMatch(storyboard, /ComposeModeSegment|HookStyleChip|deriveFixedSections/);
});
