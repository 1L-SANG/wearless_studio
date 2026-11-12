import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
import {readFileSync,existsSync} from 'node:fs';
import {seedStoryboard} from '../../src/lib/pageTemplates/seed.js';
import {defaultPageStyle} from '../../src/lib/pageTemplates/pageStyle.js';
import {sectionRuns} from '../../src/lib/pageTemplates/sectionRuns.js';
import {changeTemplateSectionDesign} from '../../src/lib/pageTemplates/sectionActions.js';
import details from '../../src/mock/detailRecommendationsFixture.json' with {type:'json'};
let vite,Picker;
before(async()=>{vite=await createServer({configFile:false,plugins:[react()],logLevel:'silent',optimizeDeps:{noDiscovery:true,include:[]},resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},server:{middlewareMode:true,watch:null,hmr:false,ws:false},appType:'custom'});({StoryboardSectionPicker:Picker}=await vite.ssrLoadModule('/src/features/storyboard/StoryboardSectionPicker.jsx'));});
after(()=>vite?.close());
const product={name:'테스트 상품',clothingType:'top',colors:[{id:'base',isBase:true,name:'블루',swatchId:'blue',images:[{slot:'Front',src:'/front.jpg'},{slot:'Detail',src:'/detail.jpg'}]},{id:'ivory',name:'아이보리',swatchId:'ivory'}]};
const analysis={targetGenders:['women'],pageStyle:defaultPageStyle(),detailRecommendations:details};
const catalogs={detailProduct:product,analysis,genExamples:[]};
const board=()=>seedStoryboard(product,analysis);
const runOf=(blocks,kind,usage)=>sectionRuns(blocks).find(r=>r.kind===kind&&(!usage||r.usage===usage));
const render=(blocks,run,props={})=>renderToStaticMarkup(React.createElement(Picker,{mode:'design',blocks,runKey:run.key,catalogs,...props}));
const options=html=>[...html.matchAll(/<button\b([^>]*aria-pressed[^>]*)>([\s\S]*?)<\/button>/g)];

for(const [kind,usage] of [['first'],['wear','styling'],['points'],['fabric']])test(`WOC12 ${kind}/${usage||''} uses fixed silver pictures and names only in accessibility/tooltips`,()=>{
 const blocks=board(),html=render(blocks,runOf(blocks,kind,usage));
 assert.match(html,/aria-label="디자인 선택"/);
 assert.equal((html.match(/data-design-illustration=/g)||[]).length,2);
 assert.doesNotMatch(html,/완성 모습|액자에 넣을 사진|data-frame-count|<image|<text|<tspan|designCheck|<small>기본/);
 for(const [,attrs,content] of options(html)){assert.match(attrs,/aria-label="[^"]+"/);assert.match(attrs,/title="[^"]+"/);assert.equal(content.replace(/<[^>]+>/g,'').trim(),'');}
});

test('WOC12 fixed daily design illustration ignores photos and selected layout',()=>{
 const blocks=board(),run=runOf(blocks,'wear','styling');
 const html=render(blocks,run),changed=changeTemplateSectionDesign(blocks,run.key,'WEAR-STYLING-2COL-01',{product});
 const replaced=changed.map(b=>b.pageSectionKey===run.key?{...b,source:'mine',ownImages:['/different.jpg'],thumb:'/different.jpg'}:b);
 const picture=markup=>[...markup.matchAll(/<svg\b[^>]*data-design-illustration=[\s\S]*?<\/svg>/g)].map(m=>m[0]);
 assert.deepEqual(picture(render(replaced,runOf(replaced,'wear','styling'))),picture(html));
});

test('WOC12 two-column glyph has two columns and three rows while other layouts retain their shapes',()=>{
 const blocks=board(),html=render(blocks,runOf(blocks,'wear','styling'));
 const glyph=html.match(/<svg\b[^>]*data-layout-glyph="wear:twoColumn"[^>]*>([\s\S]*?)<\/svg>/)?.[1];assert.ok(glyph);
 const rects=[...glyph.matchAll(/<rect\b[^>]*>/g)];assert.equal(rects.length,6);
 const xs=new Set(rects.map(m=>m[0].match(/ x="([^"]+)"/)[1])),ys=new Set(rects.map(m=>m[0].match(/ y="([^"]+)"/)[1]));
 assert.equal(xs.size,2);assert.equal(ys.size,3);
});

test('WOC12 design footer exposes the existing undo action even though the outer continue bar is hidden',()=>{
 const blocks=board(),html=render(blocks,runOf(blocks,'wear','styling'),{undoEntry:{operationCount:1,message:'섹션 디자인을 바꿨어요'}});
 assert.match(html,/role="status"/);assert.match(html,/1가지 바꿨어요/);assert.match(html,/>되돌리기<\/button>/);
 assert.ok(html.indexOf('되돌리기')>html.indexOf('data-design-illustration'));
});

test('WOC12 only design mode uses narrow width and the same variable bounds header and stage',()=>{
 const tokens=readFileSync('src/styles/tokens.css','utf8'),panels=readFileSync('src/features/storyboard/StoryboardPanels.module.css','utf8'),layout=readFileSync('src/features/storyboard/StoryboardLayout.module.css','utf8');
 assert.match(tokens,/--sb-drawer-width: min\(520px, 40vw\)/);assert.match(tokens,/--sb-design-drawer-width: min\(330px,/);
 assert.match(panels,/\.designDrawer[^{}]*\{[^}]*--sb-drawer-width: var\(--sb-design-drawer-width\)/);
 assert.match(layout,/\.page\.designDrawerOpen[^{}]*\{[^}]*--sb-drawer-width: var\(--sb-design-drawer-width\)/);
 assert.match(layout,/\.page\.designDrawerOpen \.layout[^{}]*\{[^}]*100vw - var\(--sb-drawer-width\)/);
 const panel=readFileSync('src/features/storyboard/StoryboardDesignPanel.module.css','utf8');
 assert.doesNotMatch(panel,/finished|thumbnailName|thumbnailView|segment|frameCounts|word-break:/);
 assert.match(panel,/place-items:\s*center/);
 assert.equal(Math.min(1030,1280-330-12-16-24)-216-28,654);
 assert.equal(Math.min(1030,1440-330-12-16-24)-228-28,774);
 assert.equal(existsSync('src/features/storyboard/storyboardDesignPreview.js'),false);
});
