import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {editedTextElement} from '../../src/features/editor/editorTextEdit.js';
import {editedLineContrast,measureEditedTemplateContrast,measureWrappedTextLines} from '../../src/features/editor/editedTemplateContrast.js';
import {patchTemplateElement} from '../../src/lib/editorWaitSkeleton.js';
import {waitForEditedContrast} from '../../src/features/editor/editorContrastPending.js';
import {hookHarness} from './helpers/storyboardComponents.mjs';
import {loadEditedContrastHook} from './helpers/editorContrast.mjs';
const title={id:'title',type:'text',text:'원래 제목',keepWords:true,x:10,y:20,w:200,h:40,style:{font:'Gowun Batang',size:30},contrastFor:{text:'원래 제목',x:10,y:20,w:200,style:{font:'Gowun Batang',size:30}}};
test('WOC13 only changed text or size releases keepWords; saved originals and undo snapshots remain fixed',()=>{
 const before=structuredClone(title);
 for(const patch of [{text:title.text},{style:{...title.style,color:'red'}},{h:60},{x:30}])assert.equal(editedTextElement(title,patch).keepWords,true);
 for(const patch of [{text:'내용을 길게 바꾼 제목입니다'},{style:{...title.style,size:70}}]){
   const next=editedTextElement(title,patch);assert.equal(next.keepWords,false);assert.deepEqual(title,before);
   assert.equal(patchTemplateElement({elements:[title]},title.id,patch).elements[0].keepWords,false);
 }
});
test('WOC13 actual text panel patch and text commit release only selected title and undo restores it',()=>{
 const source=readFileSync('src/features/editor/Editor.jsx','utf8');let blocks=[{id:'b',elements:[structuredClone(title),{...title,id:'other'}]}];
 const before=structuredClone(blocks),scope={selEl:'title',editedTextElement,setBlocks:fn=>{blocks=fn(blocks);}};
 const slice=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
 const patch=new Function('scope',`with(scope){${slice('  const patchEl =','  const patchBubbleAppearance =')};return patchEl;}`)(scope);
 patch({style:{...title.style,size:60}});assert.equal(blocks[0].elements[0].keepWords,false);assert.equal(blocks[0].elements[1].keepWords,true);
 const current=blocks,hist={current:{past:[before],future:[]}},fromHistory={current:{}};
 new Function('scope',`with(scope){${slice('  const undo =','  const redo =')};undo();}`)({hist,fromHistory,prevBlocks:{current},croppingRef:{current:null},clearSel(){},setBlocks:v=>{blocks=v;},toast:{push(){}}});
 assert.deepEqual(blocks,before);
 const commit=new Function('scope',`with(scope){${slice('  const commitTextValue =','  // 내용이 빈 일반 텍스트')};return commitTextValue;}`)(scope);
 commit('b','title','더 길어진 새 제목');assert.equal(blocks[0].elements[0].keepWords,false);assert.equal(blocks[0].elements[1].keepWords,true);
 assert.match(source,/return editedTextElement\(el, \{ text, \.\.\.textSize \}\)/);
 assert.match(source,/classList.toggle\('el-keep-words', editableText\(e.currentTarget\) === el.text\)/);
 assert.match(source,/s0.font, s0.list, editing, preview, el.hidden, contrastElement.contrastLines\]/);
 assert.match(source,/hasTemplateContrastLines\(contrastElement\).*TemplateContrastText element=\{contrastElement\}/);
});
test('WOC13 edited line contrast matches light/dark and lower-decile shade policy',()=>{
 const pixels=values=>new Uint8ClampedArray(values.flatMap(v=>[v,v,v,255]));
 assert.deepEqual(editedLineContrast(pixels([0,0,0])),{color:'#ffffff',shade:false});
 assert.deepEqual(editedLineContrast(pixels([255,255,255])),{color:'#111111',shade:false});
 assert.equal(editedLineContrast(pixels([0,255,0,255])).shade,true);
});
function measuredNode(){
 const textNode={textContent:'가나다라'},rects=[0,1,2,3].map(i=>({left:10+(i%2)*10,right:20+(i%2)*10,top:10+Math.floor(i/2)*30,bottom:30+Math.floor(i/2)*30,height:20}));let index=0;
 const bounds={left:0,top:0,right:100,bottom:100,width:100,height:100},sampled=[];
 const image={src:'/photo',compareDocumentPosition:()=>4,getBoundingClientRect:()=>bounds,closest:s=>s==='.el-template-overlay'?null:{getBoundingClientRect:()=>bounds}};
 const root={querySelectorAll:()=>[image]};
 const doc = {
  fonts: { ready: Promise.resolve() }, defaultView: { getComputedStyle: () => ({ lineHeight: '30px', objectFit: 'cover' }) },
  createTreeWalker: () => { let used = false; return { nextNode: () => used ? null : (used = true, textNode) }; },
  createRange: () => ({ setStart: (_, i) => { index = i; }, setEnd() {}, getBoundingClientRect: () => rects[index] }),
  createElement: () => ({ getContext: () => ({
   drawImage: (_im, x, y, w, h) => sampled.push([x, y, w, h]),
   getImageData: () => ({ data: new Uint8ClampedArray(sampled.at(-1)[1] < 30 ? [0, 0, 0, 255] : [255, 255, 255, 255]) }),
  }) }),
 };
 const node={ownerDocument:doc,isConnected:true,offsetWidth:100,getBoundingClientRect:()=>bounds,closest:()=>root};return{node,sampled};
}
test('WOC13 wrapped browser lines are sampled at their new photo regions',async()=>{
 const {node,sampled}=measuredNode();assert.deepEqual(measureWrappedTextLines(node).map(l=>l.text),['가나','다라']);
 const result=await measureEditedTemplateContrast(node,{loadImage:async()=>({naturalWidth:100,naturalHeight:100})});
 assert.deepEqual(result,[{text:'가나',color:'#ffffff',shade:false},{text:'다라',color:'#111111',shade:false}]);
 assert.deepEqual(sampled,[[10,10,20,20],[10,40,20,20]]);
 const root=node.closest();root.querySelectorAll=()=>[];root.closest=()=>({surface:true});
 node.ownerDocument.defaultView.getComputedStyle=n=>({lineHeight:'30px',backgroundColor:n.surface?'rgb(0,0,0)':'rgba(0,0,0,0)'});
 const outside=await measureEditedTemplateContrast(node);assert.ok(outside.every(line=>line.color==='#ffffff'));

});
test('WOC13 edited contrast hook recomputes text/size and export waits for that same DOM result',async()=>{
 const h=hookHarness();let resolve,calls=0;const node={},root={contains:n=>n===node};
 const hook=loadEditedContrastHook(h,()=>{calls++;return new Promise(r=>{resolve=r;});});
 const run=el=>h.render(()=>hook(el,{current:node},{editing:false,preview:false}),{});
 try{
  assert.equal(run(title),title);assert.equal(calls,0);
  const edited=editedTextElement(title,{text:'긴 제목'});run(edited);assert.equal(calls,1);
  let finished=false;const waiting=waitForEditedContrast([root]).then(()=>finished=true);await Promise.resolve();assert.equal(finished,false);
  resolve([{text:'긴 제목',color:'#ffffff',shade:true}]);await waiting;const rendered=run(edited);assert.equal(rendered.contrastFor.text,'긴 제목');assert.equal(rendered.contrastLines[0].shade,true);
  const resized=editedTextElement(edited,{style:{...edited.style,size:60}});run(resized);assert.equal(calls,2);resolve([]);await waitForEditedContrast([root]);
  const colored=editedTextElement(resized,{style:{...resized.style,color:'red'}});
  assert.equal(run(colored),colored,'an explicit seller ink color keeps priority');assert.equal(calls,2);
  assert.equal(run(title),title,'undo restores original saved metadata');
 }finally{h.close();}
});
