import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { sectionRuns, changeTemplateSectionDesign as savedRunDesign } from '../../src/lib/pageTemplates/sectionRuns.js';
import { addTemplateSection, removeTemplateSection, changeTemplateSectionDesign } from '../../src/lib/pageTemplates/sectionActions.js';
import { patchStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { assemblePage } from '../../src/lib/pageTemplates/assemble.js';
import { validateStoryboardV4 } from '../../src/lib/pageTemplates/validateStoryboard.js';

const product = { name: '가을 셔츠', clothingType: 'top', colors: [{ id: 'base', isBase: true }, { id: 'second' }] };
const inputs = [];
for (const templateId of ['emo', 'show']) {
  const analysis = { targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } };
  let original = seedStoryboard(product, analysis);
  original = addTemplateSection(original, product, analysis, 'sns');
  // Previously saved two-photo SNS layouts remain valid even though a current
  // one-photo section may no longer switch to a different photo count.
  original = savedRunDesign(original, original.find(block => block.pageSectionKind === 'sns').pageSectionKey, 'P07-D08');
  original = addTemplateSection(original, product, analysis, 'wear', 'styling');
  for (const seed of [7, 19, 41, 83]) {
    const runs = sectionRuns(original), rest = runs.slice(1); let state = seed;
    for (let i = rest.length - 1; i > 0; i--) {
      state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
      const j = state % (i + 1); [rest[i], rest[j]] = [rest[j], rest[i]];
    }
    const storyboard = [runs[0], ...rest].flatMap(run => run.blocks);
    validateStoryboardV4(storyboard);
    inputs.push({ storyboard, product, analysis });
  }
  let removed = original;
  for (const run of sectionRuns(original).filter(run => run.kind === 'wear' && run.blocks.some(block => /\.g\d+$/.test(block.slotKey)))) removed = removeTemplateSection(removed, run.key, { product });
  removed = changeTemplateSectionDesign(removed, removed[0].pageSectionKey, 'P01-D01', { product });
  const cp = removed.find(block => block.pageSectionKind === 'color_product');
  removed = removeTemplateSection(removed, cp.pageSectionKey, { product });
  inputs.push({ storyboard: removed, product, analysis });
  const fallback = removed.find(block => block.pageSectionKind === 'sns' && block.source === 'ai');
  const mine = patchStoryboardPhoto(removed, fallback.id, { source: 'mine', cutType: null, thumb: 'seller:photo', ownImages: ['seller:photo'] }, product);
  validateStoryboardV4(mine); inputs.push({ storyboard: mine, product, analysis });
}
for (const input of inputs) input.cutResults = input.storyboard.filter(block => block.source !== 'text').map(block => ({ blockId: block.id, imageUrl: `photo:${block.id}`, width: 1024, height: 1365 }));

test('JS and Python assemble identical shuffled section runs and dependency fallbacks across two templates and four fixed seeds', () => {
  const script = `import json,sys
from app.agents.content_roles import validate_storyboard_v4
from app.agents.page_template_assembler import assemble_v4
values=json.load(sys.stdin)
results=[]
for value in values:
    validate_storyboard_v4(value['storyboard'])
    results.append(assemble_v4(value['storyboard'],value['cutResults'],[],value['product'],True,analysis=value['analysis']))
json.dump(results,sys.stdout,ensure_ascii=False)`;
  const result = spawnSync('/Users/daily/Documents/wearless_studio/server/.venv/bin/python', ['-c', script], {
    input: JSON.stringify(inputs), encoding: 'utf8', maxBuffer: 32 * 1024 * 1024,
    env: { ...process.env, PYTHONPATH: 'server' },
  });
  assert.equal(result.status, 0, result.stderr);
  const python = JSON.parse(result.stdout);
  assert.equal(python.length, 12);
  inputs.forEach((input, index) => {
    assert.deepEqual(assemblePage(input), python[index], `case ${index}: ${input.analysis.pageStyle.templateId}`);
    assert.deepEqual([...new Set(python[index].filter(block => block.pageSectionKey).map(block => block.pageSectionKey))],
      sectionRuns(input.storyboard).map(run => run.key));
  });
});
