import test from 'node:test';
import assert from 'node:assert/strict';
import raw from '../../src/data/genExamples.json' with { type: 'json' };
import * as options from '../../src/lib/storyboardSetOptions.js';
import { withStoryboardSpaceSetExamples, STORYBOARD_SPACE_SETS, spaceSetGroupId } from '../../src/lib/storyboardSpaceSetCatalog.js';
import { createSpaceSetMembers, nextSpaceSetMemberReservation } from '../../src/lib/storyboardSpaceSets.js';
import { updateHorizonGroupBackground } from '../../src/lib/horizonBackground.js';
import { hookHarness, loadStoryboardComponent, nodes, textOf } from './helpers/storyboardComponents.mjs';
import * as generation from '../../src/lib/generationExamples.js';
import * as identity from '../../src/lib/identityScope.js';
import { storyboardSpaceSetsFor } from '../../src/lib/storyboardSpaceSetCatalog.js';
const catalogs={...withStoryboardSpaceSetExamples({genExamples:raw}),shotTypes:[{value:'full'},{value:'medium'}],productShotTypes:[{value:'ghost'},{value:'detail'}]};
const set=STORYBOARD_SPACE_SETS.find(s=>s.setType==='horizon-sequence'&&s.gender==='men'&&s.applicableClothingTypes.includes('top'));
const members=createSpaceSetMembers(set,{source:'ai',sectionRole:'studio',colorId:'blue',outerClosureState:'open'},{spaceGroupId:spaceSetGroupId(set.id,'r5')});
const product={clothingType:'outer',colors:[{id:'blue',swatchId:'blue',hex:'#3467a0',isBase:true,images:[{slot:'Front',src:'/front.png'}]}]};
const frontClosure={version:1,kind:'zipper',range:'graded'};

test('R5 set option values update every worn member and persist through replacement and reservation',()=>{
 const mixed=members.map((b,i)=>({...b,outerClosureState:i?'closed':'open'}));
 assert.equal(options.setOptionState(mixed,product,frontClosure).outerClosed,false);
 const closed=options.updateSetOuterClosure(mixed,mixed[0].spaceGroupId,true);
 assert.ok(closed.every(b=>b.outerClosureState==='closed'));
 assert.ok(options.updateSetOuterClosure(closed,closed[0].spaceGroupId,false).every(b=>b.outerClosureState==='open'));
 const toned=updateHorizonGroupBackground(closed,closed[0].spaceGroupId,'garment-tone');
 const replaced=createSpaceSetMembers(set,toned[0],{spaceGroupId:spaceSetGroupId(set.id,'r5new'),previousMembers:toned});
 assert.ok(replaced.every(b=>b.outerClosureState==='closed'&&b.horizonBackgroundMode==='garment-tone'));
 const reserved=nextSpaceSetMemberReservation(set,toned.slice(0,1));
 assert.equal(reserved.blockPatch.outerClosureState,'closed');
 assert.equal(options.normalizeNeutralSetBackground(toned,toned[0].spaceGroupId,{...product,colors:[{...product.colors[0],swatchId:'black'}]})[0].horizonBackgroundMode,'reference');
});

test('R5 set cut lookup also locks followers whose source owns a set',()=>{
 const child={id:'child',source:'ai',similarSourceBlockId:members[0].id};
 assert.equal(options.setPhotoGroupId(child,[...members,child]),members[0].spaceGroupId);
 assert.equal(options.setPhotoGroupId({...child,spaceGroupId:members[0].spaceGroupId},[]),null);
 assert.equal(options.setPhotoGroupId({source:'mine',spaceGroupId:members[0].spaceGroupId},members),null);
});

async function galleryHarness(props){
 const h=hookHarness();const Gallery=await loadStoryboardComponent('StoryboardPhotoGallery.jsx','StoryboardPhotoGallery',{...h.hooks,...generation,...identity,storyboardSpaceSetsFor,Icon:'icon'});
 let current={catalogs,block:{id:'gallery',pageSectionKind:'wear'},cut:'horizon',studioStyle:'horizon',clothingType:'top',gender:'men',...props};
 return {render(next={}){current={...current,...next};h.render(Gallery,current);return h.render(Gallery,current);},close:h.close};
}

test('R5 framing has two options and missing female bottom minimal full framing starts on medium',async()=>{
 const h=await galleryHarness({cut:'styling',studioStyle:'minimal',clothingType:'bottom',gender:'women'});
 try{const tree=h.render();const frame=nodes(tree).find(n=>n.type==='photo-framing');assert.equal(frame.props.value,'medium');assert.equal(frame.props.options.find(o=>o.value==='full').disabled,true);assert.equal(frame.props.options.length,2);assert.ok(!textOf(tree).includes('빛'));}finally{h.close();}
});

test('R5 opened shoot stays in the top tray across pagination and changes selection by example ID',async()=>{
 const selected='ex_horizon_men_top_full_front_02',picked=[];
 const h=await galleryHarness({exampleId:selected,onExampleChange:id=>picked.push(id)});
 try{let tree=h.render();let tray=nodes(tree).find(n=>n.props?.['data-photo-tray']);assert.ok(tray);assert.equal(tray.props['data-pending'],false);
 const cards=nodes(tree).filter(n=>n.props?.['data-photo-choice']);const other=cards.find(n=>n.props['aria-expanded']===false);assert.ok(other);other.props.onClick();tree=h.render();tray=nodes(tree).find(n=>n.props?.['data-photo-tray']);assert.equal(tray.props['data-pending'],true);
 const id=tray.props['data-photo-tray'];const pager=nodes(tree).find(n=>n.type==='photo-pages');if(pager.props.count>1){pager.props.onChange(1);tree=h.render();assert.equal(nodes(tree).find(n=>n.props?.['data-photo-tray']).props['data-photo-tray'],id);}
 const cut=nodes(nodes(tree).find(n=>n.props?.['data-photo-tray'])).find(n=>n.type==='button');await cut.props.onClick();assert.equal(picked[0],cut.props['data-example-id']);
 }finally{h.close();}
});

async function pickerHarness(props = {}) {
 const h=hookHarness(); const Picker=await loadStoryboardComponent('StoryboardSpaceSetPanel.jsx','StoryboardSpaceSetPanel',{...h.hooks,...identity,storyboardSpaceSetsFor,Icon:'icon'});
 let current={mode:'replace',cutType:'horizon',currentMembers:members,gender:'men',clothingType:'outer',product,frontClosure,...props};
 return {render(next={}){current={...current,...next};h.render(Picker,current);return h.render(Picker,current);},close:h.close};
}
const byText=(tree,label)=>nodes(tree).find(n=>n.type==='button'&&textOf(n).includes(label));
const trayOf=tree=>nodes(tree).find(n=>n.props?.['data-photo-tray']);
const cardsOf=tree=>nodes(tree).filter(n=>n.props?.['data-photo-choice']);

test('R5 mosaic shows at most three side pictures, exact overflow and mouse-only cover preview',async()=>{
 const h=hookHarness();const Mosaic=await loadStoryboardComponent('PhotoMosaic.jsx','PhotoMosaic',{...h.hooks,...generation});
 const pics=Array.from({length:5},(_,i)=>({id:`pic${i}`,thumb:`/${i}.webp`}));
 const render=members=>h.render(Mosaic,{members,cover:pics[0]});
 try{let tree=render(pics);assert.equal(nodes(tree).filter(n=>n.type==='example-thumb').length,4);assert.equal(textOf(tree),'+1장');
 const side=nodes(tree).find(n=>n.props?.onPointerEnter);side.props.onPointerEnter({pointerType:'touch'});tree=render(pics);assert.equal(nodes(tree).find(n=>n.type==='example-thumb').props.example.id,'pic0');
 side.props.onPointerEnter({pointerType:'mouse'});tree=render(pics);assert.equal(nodes(tree).find(n=>n.type==='example-thumb').props.example.id,'pic1');
 side.props.onPointerLeave();tree=render(pics);assert.equal(nodes(tree).find(n=>n.type==='example-thumb').props.example.id,'pic0');
 assert.equal(textOf(render(pics.slice(0,4))),'');assert.ok(!nodes(tree).some(n=>n.type==='button'||n.props?.tabIndex!=null));
 }finally{h.close();}
});

test('R5 tray keeps its card in the grid, swaps sets, clears on branch/place and handles framing and Escape',async()=>{
 const selected='ex_horizon_men_top_full_front_02',picked=[];const h=await galleryHarness({exampleId:selected,onExampleChange:id=>picked.push(id)});
 try{let tree=h.render();const initial=trayOf(tree);assert.ok(initial);assert.ok(nodes(initial).filter(n=>n.props?.['data-example-id']).every(n=>n.props['aria-label'].startsWith('전신')));
 const initialIds=cardsOf(tree).map(n=>n.props['data-photo-choice']);const another=cardsOf(tree).find(n=>n.props['aria-expanded']===false);another.props.onClick();tree=h.render();
 assert.deepEqual(cardsOf(tree).map(n=>n.props['data-photo-choice']),initialIds);assert.equal(trayOf(tree).props['data-photo-tray'],another.props['data-photo-choice']);assert.equal(trayOf(tree).props['data-pending'],true);
 const member=nodes(trayOf(tree)).find(n=>n.props?.['data-example-id']);await member.props.onClick();tree=h.render({exampleId:member.props['data-example-id']});assert.equal(trayOf(tree).props['data-pending'],false);
 let focused=0;cardsOf(tree).find(n=>n.props['data-photo-choice']===another.props['data-photo-choice']).ref({focus(){focused++;}});
 nodes(tree).find(n=>n.props?.role==='region').props.onKeyDown({key:'Escape',preventDefault(){},stopPropagation(){}});tree=h.render();assert.equal(trayOf(tree),undefined);assert.equal(focused,1);
 cardsOf(tree).find(n=>n.props['aria-expanded']===false).props.onClick();tree=h.render();nodes(tree).find(n=>n.type==='photo-framing').props.onChange('medium');tree=h.render();
 assert.ok(!trayOf(tree)||nodes(trayOf(tree)).filter(n=>n.props?.['data-example-id']).every(n=>n.props['aria-label'].startsWith('상반신')));
 tree=h.render({studioStyle:'minimal',cut:'styling'});assert.equal(trayOf(tree),undefined);
 }finally{h.close();}
});

test('R5 singles and signatures open without tray; a framing with one set member selects directly',async()=>{
 const minimal=STORYBOARD_SPACE_SETS.find(s=>s.studioStyle==='minimal'&&s.gender==='men'&&s.applicableClothingTypes.includes('top'));
 const only=STORYBOARD_SPACE_SETS.find(s=>s.setType==='horizon-sequence'&&s.gender==='men'&&s.members.filter(m=>m.shot==='full').length===1);
 const picked=[],h=await galleryHarness({block:{id:'single'},signature:{id:'sig_men_01'},exampleId:'sig_men_01',cut:'horizon',studioStyle:'minimal',onExampleChange:id=>picked.push(id)});
 try{let tree=h.render();assert.equal(trayOf(tree),undefined);assert.equal(cardsOf(tree)[0].props['data-example-id'],'sig_men_01');
 tree=h.render({studioStyle:'horizon',exampleId:only.members.find(m=>m.shot==='full').exampleId});const card=cardsOf(tree).find(n=>n.props['data-photo-choice']===only?.id);assert.ok(card);await card.props.onClick();tree=h.render();assert.equal(trayOf(tree),undefined);assert.equal(picked[0],only.members.find(m=>m.shot==='full').exampleId);
 }finally{h.close();}
 assert.ok(minimal);
});

test('R5 all set cards show the full count without added-count badge; chips and descriptions save the whole current set',async()=>{
 let live=members;const events=[];const h=await pickerHarness({onChangeSpaceBackground:async mode=>{events.push(['bg',mode]);live=updateHorizonGroupBackground(live,live[0].spaceGroupId,mode);},onChangeSpaceOuterClosure:async closed=>{events.push(['outer',closed]);live=options.updateSetOuterClosure(live,live[0].spaceGroupId,closed);}});
 try{let tree=h.render();assert.ok(nodes(tree).some(n=>n.props?.['data-space-set']));assert.ok(!textOf(tree).match(/\+\d+컷/));
 for(const card of nodes(tree).filter(n=>n.props?.['data-space-set'])){const s=STORYBOARD_SPACE_SETS.find(s=>s.id===card.props['data-space-set']);assert.ok(textOf(card).includes(`${s.members.length}컷`));assert.equal(nodes(card).find(n=>n.type==='photo-mosaic').props.members.length,s.members.length);}
 assert.ok(byText(tree,'아우터 잠그기'));assert.ok(byText(tree,'배경색 맞추기'));
 await byText(tree,'아우터 잠그기').props.onClick();tree=h.render({currentMembers:live});assert.ok(live.every(b=>b.outerClosureState==='closed'));assert.equal(byText(tree,'아우터 잠그기').props['aria-pressed'],true);
 assert.equal(nodes(tree).find(n=>n.props?.className==='optionExplanation').props.children[0].props.children,'아우터 앞을 잠근 모습으로 만들어요');
 await byText(tree,'배경색 맞추기').props.onClick();tree=h.render({currentMembers:live});assert.ok(live.every(b=>b.horizonBackgroundMode==='garment-tone'));
 assert.equal(nodes(tree).find(n=>n.props?.className==='optionExplanation').props.children[0].props.children,'뒷배경이 의류색과 톤온톤으로 맞춰져요');
 await byText(tree,'배경색 맞추기').props.onClick();tree=h.render({currentMembers:live});assert.ok(live.every(b=>b.horizonBackgroundMode==='reference'));assert.equal(nodes(tree).find(n=>n.props?.className==='optionExplanation').props.children[0].props.children,'아우터 앞을 잠근 모습으로 만들어요');
 await byText(tree,'아우터 잠그기').props.onClick();tree=h.render({currentMembers:live});assert.ok(live.every(b=>b.outerClosureState==='open'));assert.equal(nodes(tree).find(n=>n.props?.className==='optionExplanation').props.children[0].props.children,'');
 assert.deepEqual(events,[['outer',true],['bg','garment-tone'],['bg','reference'],['outer',false]]);
 }finally{h.close();}
});

test('R5 option visibility follows studio branch, closure hardware and neutral garment swatches',async()=>{
 const h=await pickerHarness();try{let tree=h.render();assert.ok(byText(tree,'배경색 맞추기'));
 nodes(tree).find(n=>n.type==='photo-segment').props.onChange('minimal');tree=h.render();assert.equal(byText(tree,'배경색 맞추기'),undefined);assert.ok(byText(tree,'아우터 잠그기'));
 tree=h.render({clothingType:'top'});assert.equal(byText(tree,'아우터 잠그기'),undefined);
 tree=h.render({clothingType:'outer',frontClosure:{version:1,kind:'none',range:null}});assert.equal(byText(tree,'아우터 잠그기'),undefined);
 nodes(tree).find(n=>n.type==='photo-segment').props.onChange('horizon');tree=h.render({product:{...product,colors:[{...product.colors[0],swatchId:'black'}]}});assert.equal(byText(tree,'배경색 맞추기'),undefined);assert.ok(!nodes(tree).some(n=>n.props?.className==='optionRow'));
 }finally{h.close();}
 const styling=STORYBOARD_SPACE_SETS.find(s=>s.setType==='styling'&&!s.studioStyle&&s.gender==='men');
 const originals=createSpaceSetMembers(styling,members[0],{spaceGroupId:spaceSetGroupId(styling.id,'style')});
 const style=await pickerHarness({cutType:'styling',currentMembers:originals});try{const tree=style.render();assert.ok(byText(tree,'아우터 잠그기'));assert.equal(byText(tree,'배경색 맞추기'),undefined);assert.equal(nodes(tree).find(n=>n.type==='photo-segment'),undefined);}finally{style.close();}
});

test('R5 unavailable background remains focusable, exposes its reason and never saves; minimal remembers draft until horizon selection',async()=>{
 let changes=0;const bad=await pickerHarness({product:{...product,colors:[{...product.colors[0],images:[]}]},onChangeSpaceBackground:()=>{changes++;}});
 try{const tree=bad.render(),chip=byText(tree,'배경색 맞추기');assert.equal(chip.props['aria-disabled'],true);assert.ok(!chip.props.disabled);assert.equal(chip.props['aria-describedby'],'sb-background-reason');await chip.props.onClick();assert.equal(changes,0);}finally{bad.close();}
 const minimal=STORYBOARD_SPACE_SETS.find(s=>s.studioStyle==='minimal'&&s.gender==='men'&&s.applicableClothingTypes.includes('top'));
 const current=createSpaceSetMembers(minimal,members[0],{spaceGroupId:spaceSetGroupId(minimal.id,'draft')});const chosen=[];
 const h=await pickerHarness({currentMembers:current,cutType:'styling',gender:'men',clothingType:'top',onChangeSpaceBackground:()=>{changes++;},onChoose:(s,o)=>chosen.push([s,o])});
 try{let tree=h.render();nodes(tree).find(n=>n.type==='photo-segment').props.onChange('horizon');tree=h.render();await byText(tree,'배경색 맞추기').props.onClick();tree=h.render();assert.equal(changes,0);assert.equal(byText(tree,'배경색 맞추기').props['aria-pressed'],true);
 nodes(tree).find(n=>n.props?.['data-space-set']).props.onClick();assert.equal(chosen[0][1].backgroundMode,'garment-tone');
 }finally{h.close();}
});

test('R5 group heading marks show only enabled options with focusable accessible names',async()=>{
 const Marks=await loadStoryboardComponent('SetOptionMarks.jsx','SetHeadMarks');
 const state={outerVisible:true,outerClosed:true,backgroundOn:true,color:product.colors[0]};
 let tree=Marks({state,horizon:true});const focus=nodes(tree).find(n=>n.props?.tabIndex===0);assert.equal(focus.props['aria-label'],'배경색 맞춤, 아우터 잠금');
 assert.equal(nodes(tree).filter(n=>n.type?.name==='ToneIcon').length,1);assert.equal(nodes(tree).filter(n=>n.type?.name==='OuterLockIcon').length,1);
 tree=Marks({state,horizon:false});assert.equal(nodes(tree).find(n=>n.props?.tabIndex===0).props['aria-label'],'아우터 잠금');
 assert.equal(Marks({state:{...state,outerClosed:false,backgroundOn:false},horizon:true}),null);
});

test('R5 actual screen locks set photos and followers, nudges the head and saves both options atomically with rollback',async t=>{
 const {storyboardScreenHarness}=await import('./helpers/storyboardScreenHarness.mjs');
 const h=await storyboardScreenHarness({savedStyle:{templateId:'show'},productPatch:product,analysisPatch:{clothingType:'outer',subCategory:'coat',frontClosure}});t.after(()=>h.close());
 h.render();await h.flush();
 const originals=h.DB.storyboard.filter(b=>b.spaceGroupId&&b.cutType==='horizon');assert.ok(originals.length);
 let group=originals[0].spaceGroupId;const host=originals[0];
 const row=nodes(h.tree).find(n=>n.props?.['data-section-key']===host.pageSectionKey);assert.ok(row);
 nodes(row).find(n=>n.props?.role==='button').props.onClick();h.render();await h.flush();
 let photo=nodes(h.tree).find(n=>n.props?.['data-storyboard-photo']===host.id);assert.ok(photo);const before=structuredClone(h.DB.storyboard);
 photo.props.onClick();h.render();assert.equal(nodes(h.tree).some(n=>n.props?.role==='dialog'),false);assert.deepEqual(h.DB.storyboard,before);
 assert.ok(nodes(h.tree).find(n=>n.props?.['data-set-nudge']===true));assert.equal(photo.props['aria-label'].endsWith('세트 사진'),true);
 const add=byText(h.tree,'비슷한 컷 추가');assert.ok(add);await add.props.onClick();h.render();await h.flush();
 assert.ok(textOf(h.tree).includes('번째 컷과 비슷하게'));
 const child=nodes(h.tree).find(n=>n.props?.['data-storyboard-photo']&&!before.some(b=>b.id===n.props['data-storyboard-photo']));assert.ok(child);child.props.onClick();h.render();assert.equal(nodes(h.tree).some(n=>n.props?.role==='dialog'),false);
 nodes(h.tree).find(n=>n.props?.['data-set-change']===group).props.onClick();h.render();await h.flush();
 assert.ok(byText(h.tree,'아우터 잠그기'));assert.ok(byText(h.tree,'배경색 맞추기'));
 await byText(h.tree,'아우터 잠그기').props.onClick();h.render();await h.flush();assert.ok(h.DB.storyboard.filter(b=>b.spaceGroupId===group).every(b=>b.outerClosureState==='closed'));
 await byText(h.tree,'배경색 맞추기').props.onClick();h.render();await h.flush();assert.ok(h.DB.storyboard.filter(b=>b.spaceGroupId===group&&b.cutType==='horizon').every(b=>b.horizonBackgroundMode==='garment-tone'));
 assert.ok(nodes(h.tree).some(n=>n.props?.['aria-label']==='배경색 맞춤, 아우터 잠금'));
 const otherSet=nodes(h.tree).find(n=>n.props?.['data-space-set']&&n.props['aria-pressed']===false&&!n.props.disabled);assert.ok(otherSet);
 await otherSet.props.onClick();h.render();await h.flush();const newGroup=h.DB.storyboard.find(b=>b.id===host.id).spaceGroupId;assert.notEqual(newGroup,group);group=newGroup;
 assert.ok(h.DB.storyboard.filter(b=>b.spaceGroupId===group&&b.source==='ai').every(b=>b.outerClosureState==='closed'&&b.horizonBackgroundMode==='garment-tone'));
 const saved=structuredClone(h.DB.storyboard),save=h.api.saveStoryboard;h.api.saveStoryboard=async()=>{throw new Error('test save failure');};
 try{await byText(h.tree,'아우터 잠그기').props.onClick();h.render();await h.flush();assert.deepEqual(h.DB.storyboard,saved);assert.equal(byText(h.tree,'아우터 잠그기').props['aria-pressed'],true);assert.ok(textOf(h.tree).includes('변경 내용을 저장하지 못했어요'));}finally{h.api.saveStoryboard=save;}
});

test('R5 disabled framing uses focusable segments and a described popover reason',async()=>{
 const Segment=await loadStoryboardComponent('StoryboardChoiceControls.jsx','PhotoSegment',{useId:()=>':segment-test:'});
 const chosen=[];const tree=Segment({label:'구도',value:'medium',options:[{value:'full',label:'전신',disabled:true},{value:'medium',label:'하반신'}],onChange:value=>chosen.push(value)});
 const buttons=nodes(tree).filter(n=>n.type==='button');assert.equal(buttons.length,2);assert.equal(buttons[0].props['aria-disabled'],true);assert.ok(!buttons[0].props.disabled);
 const reason=nodes(tree).find(n=>n.type?.name==='ChoiceReason');
 assert.equal(buttons[0].props['aria-describedby'],reason.props.id);assert.doesNotMatch(reason.props.id,/\s/);buttons[0].props.onClick();buttons[1].props.onClick();assert.deepEqual(chosen,['medium']);
 assert.equal(reason.props.reason,'이 조건은 아직 서비스에 공개되지 않았어요');
});

test('R5 followup a singleton stored set keeps ordinary editing and its old saved closure',async()=>{
 const {sectionRuns}=await import('../../src/lib/pageTemplates/sectionRuns.js');const {packStageRows}=await import('../../src/lib/pageTemplates/storyboardStage.js');
 const {spaceSetDisplayName}=await import('../../src/lib/spaceSetDisplayNames.js');const {storyboardPhotoPanelHarness}=await import('./helpers/storyboardPhotoPanelHarness.mjs');
 const block={...members[0],pageSectionKey:'show.first',pageSectionKind:'first',pageDesignId:'P01-SHOW-01',slotKey:'first.hero',sectionId:'first',outerClosureState:'partial'};
 const panel=await storyboardPhotoPanelHarness({block,blocks:[block]});try{assert.ok(panel.render());assert.equal(panel.block.outerClosureState,'partial');}finally{panel.close();}
 const selected=[];const Stage=await loadStoryboardComponent('StoryboardStage.jsx','StoryboardStage',{packStageRows,spaceSetDisplayName,
  reuseSlotsForBlock:()=>[],reuseSourceForSlot:()=>null,canPlanSimilarCut:()=>true,rejectionOfBlock:()=>null,storyboardPreviewSource:()=>'/x.webp',Icon:'icon'});
 const props={run:sectionRuns([block])[0],blocks:[block],page:[],catalogs:{detailProduct:product},photoOrdinals:new Map([[block.id,1]]),onNudgeSet:()=>{throw new Error('must not nudge singleton');},onSelectPhoto:id=>selected.push(id)};
 const tree=Stage(props);assert.ok(!nodes(tree).some(n=>n.props?.['data-set-change']===block.spaceGroupId));const photo=nodes(tree).find(n=>n.props?.['data-storyboard-photo']);photo.props.onClick();assert.deepEqual(selected,[block.id]);assert.equal(photo.props['aria-label'],'1번째 컷 사진 바꾸기');
});

test('R5 a pinned shoot survives paging and Escape returns to its card page; drag pauses pending ants',async()=>{
 const base=raw.find(e=>e.id==='ex_horizon_men_top_full_front_02');
 const examples=Array.from({length:8},(_,i)=>['front','back'].map(direction=>({...base,id:`ex_horizon_men_top_full_${direction}_${i+30}`,direction}))).flat();
 const h=await galleryHarness({catalogs:{...catalogs,genExamples:examples},exampleId:examples[0].id,onExampleDrag(){}});
 try{let tree=h.render();const first=trayOf(tree).props['data-photo-tray'];assert.equal(nodes(tree).find(n=>n.type==='photo-pages').props.count,2);
 nodes(tree).find(n=>n.type==='photo-pages').props.onChange(1);tree=h.render();assert.equal(trayOf(tree).props['data-photo-tray'],first);
 // Removed page cards lose their refs. Escape must restore that page before focus.
 const region=nodes(tree).find(n=>n.props?.role==='region');region.props.onKeyDown({key:'Escape',preventDefault(){},stopPropagation(){}});tree=h.render();assert.equal(trayOf(tree),undefined);assert.equal(nodes(tree).find(n=>n.type==='photo-pages').props.page,0);
 cardsOf(tree).find(n=>n.props['data-photo-choice']!==first).props.onClick();tree=h.render();assert.equal(trayOf(tree).props['data-pending'],true);
 const cut=nodes(trayOf(tree)).find(n=>n.props?.['data-example-id']);cut.props.onDragStart({dataTransfer:{setData(){}}});tree=h.render();assert.equal(trayOf(tree).props['data-dragging'],true);cut.props.onDragEnd();tree=h.render();assert.equal(trayOf(tree).props['data-dragging'],false);
 }finally{h.close();}
});

test('R5 changing studio branch closes the tray even after returning; ordinary singleton has no tray',async()=>{
 const h=await galleryHarness({exampleId:'ex_horizon_men_top_full_front_02'});
 try{assert.ok(trayOf(h.render()));assert.equal(trayOf(h.render({studioStyle:'minimal',cut:'styling'})),undefined);assert.equal(trayOf(h.render({studioStyle:'horizon',cut:'horizon'})),undefined);}finally{h.close();}
 const singleton=raw.find(e=>e.id==='ex_horizon_women_dress_medium_front_satin_01_v2');const one=await galleryHarness({gender:'women',exampleId:singleton.id});
 try{assert.equal(trayOf(one.render()),undefined);}finally{one.close();}
});

test('R5 adding a released set member inherits the set outer lock',async()=>{
 const {stageAdditions,addStageAddition}=await import('../../src/lib/pageTemplates/stageAdditions.js');
 const place=STORYBOARD_SPACE_SETS.find(s=>s.setType==='styling'&&!s.studioStyle&&s.gender==='women');
 const key='emo.wear_styling';const blocks=createSpaceSetMembers(place,{source:'ai',taxonomyVersion:4,sectionRole:'styling',contentRole:'coordination',sectionId:'wear',pageSectionKey:key,pageSectionKind:'wear',pageSectionUsage:'styling',pageDesignId:'P02-D01',outerClosureState:'closed'}, {spaceGroupId:spaceSetGroupId(place.id,'add'),members:place.members.slice(0,1)});
 const item=stageAdditions(blocks,key,{product}).find(a=>a.kind==='spaceMember');assert.ok(item);
 const next=addStageAddition(blocks,key,item,{product,makeId:()=> 'added'});assert.equal(next.find(b=>b.id==='added').outerClosureState,'closed');
});

test('R5 an already pinned location stays open when another framing leaves one photo, and place change closes it',async()=>{
 const location=STORYBOARD_SPACE_SETS.find(s=>s.gender==='men'&&s.setType==='styling'&&s.applicableClothingTypes.includes('top')&&!s.studioStyle&&s.members.filter(m=>m.shot==='full').length>1&&s.members.filter(m=>m.shot==='medium').length===1);
 const h=await galleryHarness({cut:'styling',studioStyle:null,exampleId:location.members.find(m=>m.shot==='full').exampleId});
 try{let tree=h.render();assert.equal(trayOf(tree).props['data-photo-tray'],location.id);
 nodes(tree).find(n=>n.type==='photo-framing').props.onChange('medium');tree=h.render();assert.equal(trayOf(tree).props['data-photo-tray'],location.id);assert.equal(nodes(trayOf(tree)).filter(n=>n.props?.['data-example-id']).length,1);
 const places=nodes(tree).find(n=>n.type==='photo-filters');places.props.onPlace(places.props.tabs.find(tab=>tab.value!==places.props.place).value);tree=h.render();assert.equal(trayOf(tree),undefined);
 }finally{h.close();}
});

test('R5 a tone choice survives replacing horizon with minimal before returning to horizon in the same panel',async()=>{
 const minimal=STORYBOARD_SPACE_SETS.find(s=>s.studioStyle==='minimal'&&s.gender==='men'&&s.applicableClothingTypes.includes('top'));
 let live=updateHorizonGroupBackground(members,members[0].spaceGroupId,'garment-tone'),picked;
 const h=await pickerHarness({currentMembers:live,clothingType:'top',onChoose:(s,o)=>{picked={s,o};live=createSpaceSetMembers(s,live[0],{previousMembers:live,spaceGroupId:spaceSetGroupId(s.id,'carry')});}});
 try{let tree=h.render();assert.equal(byText(tree,'배경색 맞추기').props['aria-pressed'],true);
 nodes(tree).find(n=>n.type==='photo-segment').props.onChange('minimal');tree=h.render();nodes(tree).find(n=>n.props?.['data-space-set']===minimal.id).props.onClick();
 tree=h.render({currentMembers:live,cutType:'styling'});assert.equal(nodes(tree).find(n=>n.type==='photo-segment').props.value,'minimal');
 nodes(tree).find(n=>n.type==='photo-segment').props.onChange('horizon');tree=h.render();assert.equal(byText(tree,'배경색 맞추기').props['aria-pressed'],true);
 nodes(tree).find(n=>n.props?.['data-space-set']).props.onClick();assert.equal(picked.o.backgroundMode,'garment-tone');
 }finally{h.close();}
});

test('R5 unavailable reasons open as popovers for focus and stay visible while the trigger retains focus',async()=>{
 const h=hookHarness();const Reason=await loadStoryboardComponent('StoryboardChoiceControls.jsx','ChoiceReason',{...h.hooks});let open=false;
 try{const tree=h.render(Reason,{reason:'이 조건은 아직 서비스에 공개되지 않았어요',id:'reason',children:'trigger'});
 const tooltip=nodes(tree).find(n=>n.props?.role==='tooltip');tooltip.ref.current={matches:()=>open,showPopover(){open=true;},hidePopover(){open=false;}};
 tree.props.onFocusCapture();assert.equal(open,true);const active={},wrapper={ownerDocument:{activeElement:active},contains:node=>node===active,matches:()=>false};
 tree.props.onMouseLeave({currentTarget:wrapper});assert.equal(open,true);tree.props.onBlurCapture({currentTarget:wrapper,relatedTarget:null});assert.equal(open,false);assert.equal(tooltip.props.popover,'manual');
 }finally{h.close();}
});
