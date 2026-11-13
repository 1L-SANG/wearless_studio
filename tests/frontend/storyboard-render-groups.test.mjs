import test from 'node:test';
import assert from 'node:assert/strict';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { renderGroups,renderGroupKey } from '../../src/lib/storyboardRenderGroups.js';
for (const [templateId,count] of [['emo',8],['show',5]]) test(`${templateId} renders section instances in page order without an unavailable fabric run`,()=>{
 const blocks=seedStoryboard({colors:[{id:'base',isBase:true}]},{pageStyle:{...defaultPageStyle(),templateId}});
 const groups=renderGroups(blocks); assert.equal(groups.length,count);
 assert.ok(blocks.every(block=>block.pageSectionKind!=='fabric'));
 assert.deepEqual(groups.flatMap(g=>g.items.map(i=>i.block.id)),blocks.map(b=>b.id));
 assert.ok(groups.every(g=>g.items.every(i=>renderGroupKey(i.block)===g.key)));
});
test('empty board has no fixed bands',()=>assert.deepEqual(renderGroups([]),[]));
