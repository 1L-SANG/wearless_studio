import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { seedStoryboard } from '../../src/lib/pageTemplates/seed.js';
import { defaultPageStyle } from '../../src/lib/pageTemplates/pageStyle.js';
import { patchStoryboardPhoto } from '../../src/lib/pageTemplates/photoActions.js';
import { productSectionState, changeProductSectionPresentation, inheritProductSectionPresentation, productSectionNeedingSelection } from '../../src/lib/pageTemplates/productPresentation.js';
import { productPresentationExample } from '../../src/lib/productPresentationExamples.js';
import { PHOTO_PLACE_TABS, initialPhotoPlace, initialPhotoFraming, framingLabel, orderPhotoChoices, photoChoicePages } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { SIGNATURE_CUTS, signatureCutsFor, signatureCutById, signatureCardForBlock, signatureExamplesForBlock, isSignatureBlock } from '../../src/lib/signatureCutPool.js';
import { assignGenerationExamples } from '../../src/lib/generationExamples.js';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import { withStoryboardSpaceSetExamples, storyboardSpaceSetsFor } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { photoGalleryChoices } from '../../src/lib/pageTemplates/storyboardPhotoChoices.js';
import { filterSpaceSetsForModel, blockAllowedForModel } from '../../src/lib/identityScope.js';
import { exampleSpaceSet } from '../../src/lib/pageTemplates/storyboardGallery.js';

const product = { clothingType: 'top', colors: [{ id: 'base', isBase: true, images: [{slot:'Front'}, {slot:'Back'}] }, { id: 'blue' }] };
const analysis = templateId => ({ targetGenders: ['women'], pageStyle: { ...defaultPageStyle(), templateId } });
const board = templateId => seedStoryboard(product, analysis(templateId));

for (const mode of ['ghost', 'flat', 'natural_light']) test(`WOC4 one product run gets ${mode} in one change without altering faces/colors/other sections`, () => {
  const before = board('show'), photo = before.find(b => b.pageSectionKind === 'color_product');
  const next = patchStoryboardPhoto(before, photo.id, { productPresentation: mode }, product);
  assert.equal(productSectionState(next, photo.pageSectionKey).value, mode);
  for (const old of before) {
    const changed = next.find(b => b.id === old.id);
    if (old.pageSectionKind === 'color_product' && old.source === 'ai') {
      assert.deepEqual(changed, { ...old, productPresentation: mode });
    } else assert.deepEqual(changed, old);
  }
  assert.equal(productSectionState(before, photo.pageSectionKey).value, 'ghost', 'undo snapshot stays exact');
});

test('WOC4 missing/mixed old product runs remain untouched on read and require a deliberate selection', () => {
  const seeded = board('emo'), cp = seeded.filter(b => b.pageSectionKind === 'color_product'), key = cp[0].pageSectionKey;
  const old = seeded.map(b => b.pageSectionKey === key ? (({ productPresentation, ...rest }) => rest)(b) : b);
  const saved = structuredClone(old);
  assert.equal(productSectionState(old,key).status,'missing'); assert.equal(productSectionNeedingSelection(old).key,key);
  assert.deepEqual(old,saved);
  assert.throws(()=>patchStoryboardPhoto(old,cp[0].id,{colorId:'blue'},product),/product_presentation_required/);
  const mixed=old.map(b=>b.id===cp[0].id?{...b,productPresentation:'flat'}:b);
  assert.equal(productSectionState(mixed,key).status,'mixed');
  const fixed=changeProductSectionPresentation(mixed,key,'natural_light');
  assert.equal(productSectionNeedingSelection(fixed),null);
});

test('WOC4 run edits reject noncontiguous keys and ignore detail/mine/first cards', () => {
  const b=board('show'), cp=b.filter(b=>b.pageSectionKind==='color_product'), key=cp[0].pageSectionKey;
  assert.throws(()=>changeProductSectionPresentation([cp[0],b[0],cp[1]],key,'flat'),/noncontiguous/);
  const extra=[{...cp[0],id:'detail',shot:'detail'}, {...cp[0],id:'mine',source:'mine'}, {...b[0],cutType:'product',shot:'ghost'}];
  const next=changeProductSectionPresentation([...cp,...extra.slice(0,2),extra[2]],key,'flat');
  assert.deepEqual(next.slice(-3),extra);
});

test('WOC4 an added or permitted moved full product inherits its destination run mode', () => {
  const before=board('emo'), cp=before.find(b=>b.pageSectionKind==='color_product'), key=cp.pageSectionKey;
  const flat=changeProductSectionPresentation(before,key,'flat');
  const added={...cp,id:'new',productPresentation:'ghost'};
  assert.equal(inheritProductSectionPresentation(flat,[...flat,added]).at(-1).productPresentation,'flat');
  const cloned=flat.filter(b=>b.pageSectionKey===key).map(b=>({...b,id:`copy-${b.id}`,pageSectionKey:'emo.color_copy',sectionId:'color-copy'}));
  assert.ok(inheritProductSectionPresentation(flat,[...flat,...cloned]).slice(-cloned.length).every(b=>b.productPresentation==='flat'));
  const emptyRun=flat.map(b=>b.pageSectionKey===key?{...b,source:'mine',productPresentation:undefined}:b);
  assert.equal(inheritProductSectionPresentation(emptyRun,[...emptyRun,{...added,productPresentation:'natural_light'}]).at(-1).productPresentation,'ghost');
  const missing=flat.map(b=>b.pageSectionKey===key?{...b,productPresentation:undefined}:b);
  assert.throws(()=>inheritProductSectionPresentation(missing,[...missing,added]),/required/);
});

test('WOC4 display-only product examples use exact face, then same-mode front, then no picture', () => {
  const images={top_flat_front:'/front.webp'};
  assert.deepEqual(productPresentationExample('top','flat','back',images),{src:'/front.webp',face:'front',fallback:true});
  assert.equal(productPresentationExample('top','ghost','back',images),null);
  assert.equal(productPresentationExample('unknown','flat','front',images),null);
  assert.equal(productPresentationExample('top','flat','side',images),null);
  const manifest=JSON.parse(readFileSync(new URL('../../src/data/productPresentationExamples.json',import.meta.url),'utf8'));
  assert.equal(Object.keys(manifest.images).length,24);
  for(const src of Object.values(manifest.images)) assert.ok(existsSync(`public${src}`));
});

test('WOC4 all place tabs are visible, cafe first/default, and a real selected place opens its tab', () => {
  assert.equal(PHOTO_PLACE_TABS[0].value,'cafe'); assert.ok(PHOTO_PLACE_TABS.some(t=>t.value==='park'));
  assert.ok(!PHOTO_PLACE_TABS.some(t=>['all','more'].includes(t.value)));
  assert.equal(initialPhotoPlace({cutType:'horizon',id:'sig_women_01'}),'cafe');
  assert.equal(initialPhotoPlace(null),'cafe'); assert.equal(initialPhotoPlace({placeType:'park-garden'}),'park');
});

test('WOC4 missing-photo framing defaults to medium, with lower-body wording only for bottoms', () => {
  for(const pageSectionKind of ['first','review','faq']) assert.equal(initialPhotoFraming({pageSectionKind}),'medium');
  assert.equal(initialPhotoFraming({pageSectionKind:'wear'}),'medium');
  assert.equal(framingLabel('medium','bottom'),'하반신');
  for(const type of ['top','outer','dress']) assert.equal(framingLabel('medium',type),'상반신');
});

test('WOC4 tone ordering is a rotated ascending ring followed by stable unknown entries', () => {
  const entries=['z','c','b','u','a'].map(id=>({id,example:{id}})),order={a:10,b:20,c:30};
  const result=orderPhotoChoices(entries,{projectId:'p1',scope:'cafe',order});
  assert.deepEqual(result.slice(-2).map(e=>e.id),['z','u']);
  const ring=result.slice(0,3).map(e=>e.id).join(''); assert.ok(['abc','bca','cab'].includes(ring));
  assert.deepEqual(orderPhotoChoices(entries,{projectId:'p1',scope:'cafe',order}),result);
  assert.ok(new Set(Array.from({length:20},(_,i)=>orderPhotoChoices(entries,{projectId:`p${i}`,scope:'cafe',order})[0].id)).size>1);
  assert.deepEqual(orderPhotoChoices(entries,{projectId:'p2',order:{}}),entries);
  assert.deepEqual(entries.map(e=>e.id),['z','c','b','u','a']);
});

test('WOC4 uses the displayed stack cover for tone lookup and pins one signature on each page', () => {
  const stack={id:'set-id',example:{id:'cover'}},single={id:'single',example:{id:'single'}};
  const ordered=orderPhotoChoices([single,stack],{order:{cover:1},projectId:'p'});assert.equal(ordered[0],stack);
  const signature={id:'sig_women_01'}; const pages=photoChoicePages(Array.from({length:12},(_,i)=>({id:`${i}`})),signature);
  assert.equal(pages.length,3); assert.ok(pages.every(p=>p[0].example===signature && p.length<=6));
});

test('WOC4 active signature pool has39 approved cuts; retired assets remain readable only', () => {
  assert.equal(SIGNATURE_CUTS.length,39); assert.equal(new Set(SIGNATURE_CUTS.map(c=>c.id)).size,39);
  const count=(gender,clothingType,subCategory)=>signatureCutsFor({gender,clothingType,subCategory}).length;
  assert.deepEqual([count('women','top'),count('men','top'),count('women','bottom'),count('women','bottom','skirt'),count('women','dress'),count('men','bottom')],[4,5,9,8,6,7]);
  for(const id of ['sig_women_bottom_skirt_01','sig_men_bottom_pants_02']) {assert.ok(!SIGNATURE_CUTS.some(c=>c.id===id));assert.ok(signatureCutById(id));}
  for(const cut of SIGNATURE_CUTS) {assert.ok(existsSync(`public${cut.url}`));assert.ok(existsSync(`public${cut.thumb}`));}
});

test('WOC4 signature eligibility includes both first pages without changing emotional default placement', () => {
  for(const template of ['emo','show']) {
    const seeded=board(template),first=seeded[0],context={product,analysis:analysis(template),gender:'women',projectId:'fixed'};
    assert.ok(isSignatureBlock(first)); assert.equal(signatureExamplesForBlock(first,context).length,4);
    const card=signatureCardForBlock(first,context);assert.deepEqual(signatureCardForBlock(first,context),card);
    const placed=assignGenerationExamples(seeded,{...context,catalog:[]}).blocks[0];
    if(template==='show') assert.equal(placed.exampleId,card.id);else assert.equal(placed.cutType,'styling');
    const selected={...first,cutType:'horizon',shot:'medium',direction:'front',refScope:'all',exampleId:card.id,exampleSelectionOrigin:'user'};
    assert.equal(assignGenerationExamples([selected],{...context,catalog:[]}).blocks[0].exampleId,card.id);
  }
  assert.equal(isSignatureBlock({...board('emo')[0],pageSectionKind:'review'}),false);
});

test('WOC4 saved approved signatures keep the same card and stage image when the pool grows', () => {
  const block={...board('show')[0],exampleId:'sig_women_bottom_pants_01'};
  const context={product:{clothingType:'bottom'},gender:'women',projectId:'many-new-cuts'};
  assert.equal(signatureCardForBlock(block,context).id,block.exampleId);
  assert.equal(assignGenerationExamples([block],{...context,catalog:[]}).blocks[0].exampleId,block.exampleId);
});

test('WOC4 gallery excludes forbidden model recipes and never exposes rejected set members as singles', () => {
  const catalog=withStoryboardSpaceSetExamples({genExamples:raw}).genExamples;
  const sets=storyboardSpaceSetsFor({gender:'women',clothingType:'top'});
  const allowed=new Set(filterSpaceSetsForModel(sets,'real').map(set=>set.id));
  for(const cutType of ['styling','horizon']) {
    const {candidates}=photoGalleryChoices(catalog,sets,{gender:'women',clothingType:'top',identityKind:'real',cutTypes:[cutType],place:cutType==='horizon'?'studio':'cafe'});
    for(const example of candidates) {
      const set=exampleSpaceSet(example);if(set)assert.ok(allowed.has(set.id));
      assert.ok(blockAllowedForModel({source:'ai',exampleId:example.id,cutType:example.cutType,shot:example.shot,direction:example.direction||'front',spaceSetId:set?.id,refScope:set&&cutType==='styling'?'pose':'all'},'real'));
    }
  }
});
