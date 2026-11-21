import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithEsbuild } from 'vite';
import * as presets from '../../src/features/editor/presets/infoPresets.js';
import { hookHarness } from './helpers/storyboardComponents.mjs';

async function formFor(h) {
 const source=readFileSync(process.env.B4_MODAL_SOURCE || 'src/features/editor/InfoBlockModal.jsx','utf8').replace(/^import[\s\S]*?from ['"][^'"]+['"];\n/gm,'').replace(/^export default .*;\n?/gm,'').replace(/^export /gm,'');
 const {code}=await transformWithEsbuild(source,'InfoBlockModal.jsx',{loader:'jsx',jsxFactory:'React.createElement',jsxFragment:'React.Fragment'});
 const Button=({children,onClick,title,disabled})=>React.createElement('button',{onClick,title,disabled},children);
 const bindings={React,Fragment:React.Fragment,...h.hooks,...presets,Button,Icon:()=>null,IconButton:Button,Modal:({children})=>children,thumbUrl:x=>x};
 return new Function(...Object.keys(bindings),code+';return FeatureIconsForm;')(...Object.values(bindings));
}

test('manual redraft button is absent while empty-description auto fill and explanation stay',async()=>{
 const h=hookHarness(),Form=await formFor(h);let calls=0;
 let info={layout:'circle',items:[{title:'카라',desc:'',src:null},{title:'소매',desc:'셀러 설명',src:null}]};
 const props=()=>({info,setInfo:update=>{info=update(info);},onPickPhoto(){},onDraftCopy:async()=>{calls++;return[{point:'카라',desc:'자동으로 채운 설명'},{point:'소매',desc:'덮으면 안 됨'}];}});
 try {
  const first=h.render(Form,props());
  const html=renderToStaticMarkup(first);
  assert.doesNotMatch(html,/설명 다시 만들기/);
  assert.match(html,/분석에서 뽑은 핵심 장점과 설명이 미리 채워져요/);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(info.items[0].desc,'자동으로 채운 설명');
  assert.equal(info.items[1].desc,'셀러 설명');
  assert.equal(calls,1);
  const next=renderToStaticMarkup(h.render(Form,props()));
  assert.doesNotMatch(next,/설명 다시 만들기/);
  assert.equal(calls,1);
 } finally {h.close();}
});

test('opening a fully written form does not call the draft service',async()=>{
 const h=hookHarness(),Form=await formFor(h);let calls=0;
 try {
  h.render(Form,{info:{layout:'circle',items:[{title:'카라',desc:'있는 설명'},{title:'소매',desc:'있는 설명'}]},setInfo(){},onPickPhoto(){},onDraftCopy:async()=>{calls++;return[];}});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,0);
 } finally {h.close();}
});
