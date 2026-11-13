import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {getPageFontEmbedCSS} from '../../src/features/editor/editorExportFonts.js';
test('WOC13 all block font families are embedded once, including a family used only later',async()=>{
 let calls=0,ready=false;
 const doc={fonts:{ready:Promise.resolve().then(()=>{ready=true;})},defaultView:{getComputedStyle:n=>({fontFamily:n.computed})},createElement:()=>({ownerDocument:doc,style:{},children:[]})};
 const blocks=[{ownerDocument:doc,computed:'Pretendard, sans-serif',children:[]},{ownerDocument:doc,computed:'Pretendard',children:[{computed:'"Gowun Batang", serif',children:[]}]},{ownerDocument:doc,computed:'"Gowun Batang"',children:[]}];
 const css=await getPageFontEmbedCSS(blocks,{embed:async node=>{calls++;assert.ok(ready);assert.equal((node.style.fontFamily.match(/Gowun Batang/g)||[]).length,1);assert.match(node.style.fontFamily,/Pretendard/);return '@font-face {font-family:"Gowun Batang"}';}});
 assert.equal(calls,1);assert.match(css,/Gowun Batang/);
});
test('WOC13 single/long/ZIP/storefront/publication paths all use the common page font CSS',()=>{
 const source=readFileSync('src/features/editor/editorExport.js','utf8'),publish=readFileSync('src/features/editor/detailPublish.js','utf8');
 assert.match(source,/getPageFontEmbedCSS\(\[blockNode\]\)/);assert.equal((source.match(/await getPageFontEmbedCSS\(blockNodes\)/g)||[]).length,2);
 assert.match(publish,/await getPageFontEmbedCSS\(blockNodes\)/);assert.doesNotMatch(source+publish,/getFontEmbedCSS\(blockNodes\[0\]\)/);
 assert.match(source,/captureBlockCanvas\(blockNodes\[i\], ratio, fontEmbedCSS/);assert.match(source,/captureBlockCanvasSlices\(node, ratio, fontEmbedCSS/);assert.match(publish,/captureBlockCanvasSlices\(node, ratio, fontEmbedCSS/);
});
test('WOC13 empty page requires no font embedding',async()=>{assert.equal(await getPageFontEmbedCSS([],{embed(){throw Error('unused');}}),'');});

test('WOC13 installed html-to-image embeds both local Batang faces used only by a later block',async t=>{
 const cssUrl=new URL('../../src/styles/tokens.css',import.meta.url),css=readFileSync(cssUrl,'utf8');
 const rules=[...css.matchAll(/@font-face\s*\{([^}]+)\}/g)].filter(m=>/font-family: "(?:Pretendard Variable|Gowun Batang)"/.test(m[1]));
 const fetched=[];let doc;
 class El {constructor(family=''){this.ownerDocument=doc;this.style={fontFamily:family};this.children=[];}}
 const sheet={href:cssUrl.href,cssRules:[]};
 doc={styleSheets:[sheet],fonts:{ready:Promise.resolve()},defaultView:{getComputedStyle:n=>({fontFamily:n.style.fontFamily})},createElement:()=>new El(),implementation:{createHTMLDocument(){
  const base={href:cssUrl.href};return{head:{appendChild(){}},body:{appendChild(){}},createElement(tag){if(tag==='base')return base;let href;return{set href(v){href=new URL(v,base.href).href;},get href(){return href;}};}};
 }}};
 sheet.cssRules=rules.map(([cssText,body])=>{const fields=Object.fromEntries([...body.matchAll(/([\w-]+):\s*([^;]+);/g)].map(([,k,v])=>[k,v.trim()]));return{type:5,cssText,parentStyleSheet:sheet,style:{fontFamily:fields['font-family'],getPropertyValue:k=>fields[k]||''}};});
 const globals={document:doc,HTMLElement:El,CSSRule:{FONT_FACE_RULE:5,IMPORT_RULE:3},getComputedStyle:doc.defaultView.getComputedStyle,
  fetch:async url=>{assert.match(url,/^file:.*\/src\/fonts\/(?:PretendardVariable|GowunBatang-(?:Regular|Bold))\.woff2$/);fetched.push(url);return new Response(new Blob([readFileSync(new URL(url))],{type:'font/woff2'}));},
  FileReader:class{readAsDataURL(blob){blob.arrayBuffer().then(bytes=>{this.result=`data:${blob.type};base64,${Buffer.from(bytes).toString('base64')}`;this.onloadend();});}}};
 for(const [k,v]of Object.entries(globals)){const old=Object.getOwnPropertyDescriptor(globalThis,k);Object.defineProperty(globalThis,k,{value:v,configurable:true,writable:true});t.after(()=>old?Object.defineProperty(globalThis,k,old):delete globalThis[k]);}
 const first=new El('"Pretendard Variable"'),later=new El('"Gowun Batang"');
 const embedded=await getPageFontEmbedCSS([first,later,later]);
 assert.equal((embedded.match(/font-family: "Gowun Batang"/g)||[]).length,2);
 for(const weight of [400,700])assert.match(embedded,new RegExp(`font-weight: ${weight};`));
 assert.equal(fetched.length,3);assert.equal(new Set(fetched).size,3);assert.doesNotMatch(embedded,/url\(["']?(?:file:|https?:|\.\.)/);
});
