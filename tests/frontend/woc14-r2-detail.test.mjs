import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as preview from '../../src/features/storyboard/storyboardPreview.js';
import {pageTemplatesCatalog as catalog} from '../../src/lib/pageTemplates/catalog.js';
import {assemblePage, collectOverlayRegions} from '../../src/lib/pageTemplates/assemble.js';
const css=readFileSync('src/features/storyboard/StoryboardLayout.module.css','utf8');
const tokens=readFileSync('src/styles/tokens.css','utf8');
const rule=selector=>css.slice(css.indexOf(`${selector} {`)).split('}')[0];
test('R2 notice bottom offset resolves and its containing block is the clipped photograph',()=>{
 const photo=rule('.photo'),notice=rule('.detailNotice');
 assert.match(photo,/position: relative/);assert.match(photo,/overflow: hidden/);
 assert.match(notice,/position: absolute/);assert.match(notice,/bottom: calc\(var\(--sp-8\) \+ var\(--sp-2\)\)/);
 for(const token of notice.matchAll(/var\((--[\w-]+)\)/g))assert.ok(tokens.includes(`${token[1]}:`),`undefined ${token[1]}`);
 // jsdom has no layout: assert the real CSS offsets and compute the specified box
 // for 1280/1440 stage widths, including a two-line subtitle.
 for(const width of [150,174]){const height=width*4/3,lines=15*1.3+2+2*12*1.35;
  const box={left:12,right:width-12,top:height-10-lines,bottom:height-10};
  assert.ok(box.left>=0&&box.right<=width&&box.top>=0&&box.bottom<=height);
 }
});
for(const region of [{x:.2,y:.12,w:.6,h:.2},{x:.35,y:.15,w:.2,h:.6}]){
 for(const width of [768,2268])test(`R2 contains the whole ${region.w}x${region.h} region from ${width}px source`,()=>{
  const source={width,height:width*4/3,region};
  for(const [w,h] of [[880,1100],[174,232],[428,535]]){
   const crop=preview.detailPreviewCrop(w,h,source),x=region.x*crop.iw-crop.ox,y=region.y*crop.ih-crop.oy;
   assert.ok(x>=-1&&y>=-1&&x+region.w*crop.iw<=w+1&&y+region.h*crop.ih<=h+1,JSON.stringify({crop,x,y,w,h}));
   assert.ok(crop.iw<=w/.3+.5);assert.ok(crop.iw>=w&&crop.ih>=h,'surrounding source fills the viewport');
  }
 });
}
test('R2 stage recalculates the same contain crop for its own 3:4 visible box',()=>{
 const source={width:768,height:1024,region:{x:.2,y:.12,w:.6,h:.2}};
 const el={w:880,h:1100,crop:preview.detailPreviewCrop(880,1100,source),previewDetailLabel:'목선',previewDetailSource:source};
 const stage=preview.detailStageGeometry(el);assert.deepEqual(stage.crop,preview.detailPreviewCrop(880,stage.h,source));
 assert.equal(stage.crop.iw,Math.round(880/.6));assert.ok(stage.crop.iw>source.width);
});
test('R2 overview notice is SVG text bounded inside the photo, including wrapped Korean words',()=>{
 assert.equal(typeof preview.detailNoticeLines,'function');
 for(const [w,h] of [[428,535],[880,1100]]){
  const lines=preview.detailNoticeLines(w,h,'목선이 앉는 모양');
  assert.ok(lines.some(l=>l.text.includes('확대컷')));
  assert.ok(lines.map(l=>l.text).join(' ').includes('AI가 이 부분을 새로 만들어요'));
  for(const line of lines)assert.ok(line.x>=0&&line.x+line.w<=w&&line.y-line.size>=0&&line.y<h);
 }
 const source=readFileSync('src/features/storyboard/StoryboardPreview.jsx','utf8');
 assert.doesNotMatch(source,/foreignObject/);assert.match(source,/data-detail-notice/);
});
test('R2 planned detail card never translates the photograph, veil or text on hover',()=>{
 assert.match(css,/\.photoCard:not\(\[data-planned-detail\]\):hover\s*\{/);
 assert.doesNotMatch(css,/\.photoCard:hover\s*\{\s*transform:\s*translate/);
 assert.doesNotMatch(css,/\.detail\w*[^{}]*:(?:hover|focus)[^{]*\{/);
});
test('R2 both title plans keep three unplated positions but only center/bottom for a plate',()=>{
 assert.deepEqual(catalog.firstTitlePlate.platePreference,['center','bottom']);
 const fixture=JSON.parse(readFileSync('tests/fixtures/page_templates_cases.json')).cases.find(c=>c.name==='emo-2-mono').input;
 const plan=collectOverlayRegions(fixture).find(p=>p.positionCandidates);
 assert.deepEqual(plan.plateCandidates.map(p=>p.position),['center','bottom']);
 assert.equal(plan.positionCandidates.length,3);
 const input=structuredClone(fixture);const cut=input.cutResults.find(c=>c.blockId===plan.sourceBlockId);
 cut.overlayTextPlates={[plan.key]:true};cut.overlayTextPositions={[plan.key]:'top'};
 const plate=assemblePage(input).flatMap(b=>b.elements).find(e=>e.groupId?.startsWith('title-plate:')&&e.type==='shape');
 assert.equal(plate.y+plate.h/2,plan.frame.h/2,'invalid old top plate metadata resolves to center');
});
