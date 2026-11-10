import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { patchStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { normalizeBoard } from '../../src/lib/sections.js';
import { preserveDetailTargetBinding } from '../../src/lib/detailRecommendations.js';
import { inheritSimilarCutRecipes } from '../../src/lib/storyboardSimilarCuts.js';
import * as spaces from '../../src/lib/storyboardSpaceSets.js';
import { spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { productSectionState } from '../../src/lib/pageTemplates/productPresentation.js';

test('WOC4 actual board atomic action saves one run, rolls back all its cards, blocks a racing click and retries consistently', async()=>{
  const product={clothingType:'top',colors:[{id:'base',isBase:true,images:[{slot:'Front'},{slot:'Back'}]},{id:'second'}]};
  const before=seedStoryboard(product,{targetGenders:['women'],pageStyle:defaultPageStyle()}),snapshot=structuredClone(before);
  const photo=before.find(b=>b.pageSectionKind==='color_product');let state=before,undos=[];
  const saves=[],pending=[],atomicRetryRef={current:null};
  const context={...spaces,spaceSetGroupId,uid:()=> 'new-group',normalizeBoard,inheritSimilarCutRecipes,patchStoryboardPhoto,preserveDetailTargetBinding,
    atomicSavingRef:{current:false},setAtomicSaving(){},catalogs:{detailProduct:product},projectId:'p',atomicRetryRef,sbPending:new Map(),
    directSaveSnapshots:{current:new WeakSet()},setBlocks:next=>{state=next;},undoLabelForPatch:()=> '촬영 방식',
    showUndo:(previous,next)=>undos.push({previous,next}),clearUndoForSnapshot:next=>{undos=undos.filter(item=>item.next!==next);},
    sbSaveNow:(_id,get)=>{saves.push(get());return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
  };
  Object.defineProperty(context,'blocks',{get:()=>state});
  const source=readFileSync('src/features/storyboard/Storyboard.jsx','utf8');
  const slice=(from,to)=>source.slice(source.indexOf(from),source.indexOf(to,source.indexOf(from)));
  const action=new Function('scope',`with(scope) { ${slice('const nextSeparatedSpaceGroupId =','function usePhotoUploadProgress')}
    ${slice('  const atomicPatch =','  const atomicBoardChange =')} return atomicPatch; }`)(context);
  const first=action(photo.id,{productPresentation:'flat'},{retryAtomic:true});
  assert.equal(productSectionState(state,photo.pageSectionKey).value,'flat');assert.equal(saves.length,1);
  await assert.rejects(action(photo.id,{productPresentation:'natural_light'}),/atomic_save_in_progress/);
  assert.equal(saves.length,1);
  const failed=assert.rejects(first,/offline/);pending[0].reject(Error('offline'));await failed;
  assert.deepEqual(state,before);assert.equal(undos.length,0);
  assert.equal(productSectionState(atomicRetryRef.current.next,photo.pageSectionKey).value,'flat');
  const retry=action(photo.id,{productPresentation:'flat'},{retryAtomic:true});pending[1].resolve();await retry;
  assert.equal(productSectionState(state,photo.pageSectionKey).value,'flat');assert.equal(undos.length,1);
  assert.deepEqual(undos[0].previous,before);assert.deepEqual(before,snapshot);
  assert.equal(atomicRetryRef.current,null);
});
