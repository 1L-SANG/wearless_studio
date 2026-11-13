import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {supportedWeights,boldToggle,cssWeight} from '../../src/features/editor/fontWeights.js';
import {pageTemplatesCatalog as catalog} from '../../src/lib/pageTemplates/catalog.js';
import {analysisPageStyle,defaultPageStyle} from '../../src/lib/pageTemplates/pageStyle.js';
import {resolveStoryboardEntry,seedStoryboard} from '../../src/lib/pageTemplates/seed.js';
import {assembleStoryboardPreview} from '../../src/features/storyboard/storyboardPreview.js';
const read=p=>readFileSync(p,'utf8');
test('WOC13 Gowun Batang wires local 400/700 faces, token, editor map and both-mode catalog',()=>{
 const css=read('src/styles/tokens.css'),faces=[...css.matchAll(/@font-face\s*\{([^}]+)\}/g)].map(m=>m[1]).filter(s=>s.includes('"Gowun Batang"'));
 assert.equal(faces.length,2);for(const [i,w] of [400,700].entries()){assert.match(faces[i],new RegExp(`font-weight: ${w};`));assert.match(faces[i],/font-display: swap/);assert.match(faces[i],/GowunBatang-(Regular|Bold).woff2/);}
 assert.match(css,/--font-batang: "Gowun Batang"/);assert.match(read('src/features/editor/Editor.jsx'),/'Gowun Batang': 'var\(--font-batang\)'/);
 assert.match(read('src/mock/db.js'),/fonts:.*'Gowun Batang'/);assert.match(read('src/lib/api/index.js'),/CLIENT_ONLY = \['getCatalogs'/);
 assert.doesNotMatch(read('seller.html'),/family=Gowun\+Batang/);assert.match(read('src/fonts/LICENSES/README.md'),/OFL-GowunBatang.txt/);
 assert.ok(!catalog.pendingFontBundles.includes('Gowun Batang'));
 assert.deepEqual(supportedWeights('Gowun Batang'),[400,700]);assert.deepEqual(boldToggle('Gowun Batang'),{regular:400,bold:700});
 assert.equal(cssWeight('Gowun Batang',700),700);
});
test('WOC13 bold template titles are 700 and editor does not offer 800',()=>{
 assert.equal(catalog.fontStyles.boldGothic.titleKo.weight,700);assert.ok(!supportedWeights('Pretendard').includes(800));
});
const product={name:'셔츠',clothingType:'top',colors:[{id:'base',isBase:true,images:[]}]};
for(const templateId of [null,'emo','show'])test(`WOC13 missing style uses the same read-time seed and preview template (${templateId}) without backfill`,()=>{
 const analysis={targetGenders:['women']},before=structuredClone(analysis);
 const saved=templateId?seedStoryboard(product,{...analysis,pageStyle:{...defaultPageStyle(),templateId}}):[];
 const blocks=resolveStoryboardEntry(saved,product,analysis).blocks,style=analysisPageStyle(undefined,blocks);
 assert.equal(style.templateId,templateId||defaultPageStyle().templateId);
 assert.ok(blocks.every(b=>b.pageSectionKey.startsWith(`${style.templateId}.`)));
 assert.doesNotThrow(()=>assembleStoryboardPreview({storyboard:blocks,product,analysis,pageStyle:style}));
 assert.deepEqual(analysis,before);
});
test('WOC13 HTTP seeding does not borrow another project last style and selection uses the same default resolver',()=>{
 const source=read('src/lib/api/httpAdapter.js'),body=source.slice(source.indexOf('  async getStoryboard('),source.indexOf('  storyboardEntryNotice,'));
 assert.doesNotMatch(body,/getLastPageStyle/);assert.match(body,/resolveStoryboardEntry\(saved, product \|\| \{\}, analysis \|\| \{\}/);
 assert.match(read('src/features/storyboard/Storyboard.jsx'),/analysisPageStyle\(catalogs\?\.analysis\?\.pageStyle, blocks\)/);
});
