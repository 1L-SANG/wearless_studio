import {productNameAfterAnalysis,sellerProductName,productNamePatch} from '../../src/lib/productNameSource.js';
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import React from 'react';
import {createServer,transformWithEsbuild} from 'vite';
import {fileURLToPath} from 'node:url';
import {splitAnalysisEditPatch,analysisResponseProductPatch} from '../../src/features/product-input/saveRouting.js';

async function field(bindings={}) {
 const source=readFileSync('src/features/product-input/ProductInput.jsx','utf8');
 const start=source.indexOf('  const nameCard = (');
 const code=source.slice(start,source.indexOf('  const imgCount =',start));
 const built=await transformWithEsbuild(code,'name-field.jsx',{loader:'jsx',jsxFactory:'React.createElement'});
 const scope={React,sellerProductName,product:{name:'구김 없는 셔츠',nameSource:'ai'},phase:'done',mobileEditingLocked:false,set(){},colorSaveSchedulerRef:{current:{schedule(){}}},...bindings};
 const tree=new Function(...Object.keys(scope),built.code+';return nameCard;')(...Object.values(scope));
 return tree.props.children.find(child=>child?.type==='input');
}
test('typing a product name records explicit seller origin and persists it with the same name',async()=>{
 const patches=[],saved=[];
 const input=await field({set:patch=>patches.push(patch),colorSaveSchedulerRef:{current:{schedule:patch=>saved.push(patch)}}});
 input.props.onChange({target:{value:'셀러가 쓴 셔츠'}});
 assert.deepEqual(patches.at(-1),{name:'셀러가 쓴 셔츠',nameSource:'seller'});
 assert.deepEqual(saved.at(-1),patches.at(-1));
});
test('Enter explicitly confirms an unchanged suggested name, while IME composition does not',async()=>{
 const patches=[];const input=await field({set:patch=>patches.push(patch)});
 assert.equal(typeof input.props.onKeyDown,'function');
 input.props.onKeyDown({key:'Enter',nativeEvent:{isComposing:true},currentTarget:{value:'구김 없는 셔츠'},preventDefault(){}});
 assert.equal(patches.length,0);
 input.props.onKeyDown({key:'Enter',nativeEvent:{isComposing:false},currentTarget:{value:'구김 없는 셔츠'},preventDefault(){}});
 assert.deepEqual(patches.at(-1),{name:'구김 없는 셔츠',nameSource:'seller'});
});
test('nameSource is a product patch and an analysis response cannot assert seller name provenance',()=>{
 assert.deepEqual(splitAnalysisEditPatch({name:'새 이름',nameSource:'seller'}).productPatch,{name:'새 이름',nameSource:'seller'});
 assert.deepEqual(analysisResponseProductPatch({name:'AI 응답 이름',nameSource:'seller',clothingType:'top'}),{clothingType:'top'});
});


test('automatic names retain AI origin through analysis while seller and unknown names keep their origins',()=>{
 assert.deepEqual(productNameAfterAnalysis({name:''},'구김 없는 셔츠'),{name:'구김 없는 셔츠',nameSource:'ai'});
 assert.deepEqual(productNameAfterAnalysis({name:'구김 없는 셔츠',nameSource:'ai'},'새 제안'),{name:'구김 없는 셔츠',nameSource:'ai'});
 assert.deepEqual(productNameAfterAnalysis(sellerProductName('구김 없는 셔츠'),'새 제안'),{name:'구김 없는 셔츠',nameSource:'seller'});
 assert.deepEqual(productNameAfterAnalysis({name:'기존 이름'},'새 제안'),{name:'기존 이름',nameSource:'unknown'});
 assert.equal(productNamePatch({name:'자동 이름',nameSource:'ai'},{colors:[]}).nameSource,'ai');
 assert.equal(productNamePatch({name:'자동 이름',nameSource:'ai'},{name:'새 이름'}).nameSource,'unknown');
 assert.equal(productNamePatch({name:'자동 이름',nameSource:'ai'},{nameSource:'seller'}).nameSource,'seller');
});

test('mock product saves preserve origin across metadata saves and explicit same-name confirmation',async()=>{
 const vite=await createServer({configFile:false,optimizeDeps:{noDiscovery:true,include:[]},
  resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},
  server:{middlewareMode:true,watch:null},appType:'custom',logLevel:'silent'});
 let restore=()=>{};
 try {
  const {api}=await vite.ssrLoadModule('/src/mock/api.js');const {DB}=await vite.ssrLoadModule('/src/mock/db.js');
  const originalProduct=structuredClone(DB.product),originalAnalysis=structuredClone(DB.analysis),originalProject=structuredClone(DB.project);
  restore=()=>{DB.product=originalProduct;DB.analysis=originalAnalysis;DB.project=originalProject;};
  const ai=await api.saveProduct('b5-name',{name:'구김 없는 셔츠',nameSource:'ai'});
  assert.equal(ai.nameSource,'ai');
  assert.equal((await api.saveProduct('b5-name',{uploadComplete:true})).nameSource,'ai');
  await api.saveAnalysis('b5-name',{productNameOrigin:{name:'구김 없는 셔츠',source:'seller'}},{recommendOutfit:false});
  assert.equal(DB.analysis.productNameOrigin.source,'ai');
  assert.equal((await api.saveProduct('b5-name',{nameSource:'seller'})).nameSource,'seller');
  assert.deepEqual(DB.analysis.productNameOrigin,{name:'구김 없는 셔츠',source:'seller'});
 } finally {restore();await vite.close();}
});
