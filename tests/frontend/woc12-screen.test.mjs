import test from 'node:test';
import assert from 'node:assert/strict';
import {storyboardScreenHarness} from './helpers/storyboardScreenHarness.mjs';
import {nodes,textOf} from './helpers/storyboardComponents.mjs';
const byLabel=(h,label)=>nodes(h.tree).find(n=>n.props?.['aria-label']===label);
const byText=(h,label)=>nodes(h.tree).find(n=>n.type==='button'&&textOf(n)===label);
const dialog=h=>nodes(h.tree).find(n=>n.props?.role==='dialog'&&n.props['aria-label']==='섹션 디자인 바꾸기');
const flush=async h=>{await new Promise(resolve=>setTimeout(resolve,5));await h.flush();h.render();};
async function openDaily(h){
 h.render();await h.flush();byLabel(h,'일상 착용컷 열기').props.onClick();h.render();
 byText(h,'섹션 디자인 변경').props.onClick();h.render();await flush(h);
 return h.DB.storyboard.find(b=>b.pageSectionKind==='wear'&&b.pageSectionUsage==='styling').pageSectionKey;
}

test('WOC12 opening the design panel reveals the matching outline row and stage, and resyncs after a design change',async t=>{
 const h=await storyboardScreenHarness();t.after(()=>h.close());
 const key=await openDaily(h);
 assert.ok(dialog(h));assert.ok(byLabel(h,'일상 착용컷 무대'));
 const row=nodes(h.tree).find(n=>n.props?.['data-section-key']===key);
 assert.ok(nodes(row).some(n=>n.props?.['aria-current']==='true'));
 assert.ok(h.scrollCalls.some(call=>call.sectionKey===key&&call.options.block==='nearest'));
 const prior=h.scrollCalls.length;
 byLabel(h,'배치 · 2열').props.onClick();h.render();await flush(h);
 assert.ok(h.scrollCalls.length>prior);
 assert.ok(nodes(h.tree).some(n=>n.props?.className?.includes('designDrawerOpen')));
});

test('WOC12 design undo stays visible in the open panel and restores the actual outline geometry',async t=>{
 const h=await storyboardScreenHarness();t.after(()=>h.close());await openDaily(h);
 const outline=()=>nodes(h.tree).find(n=>n.props?.role==='img'&&n.props['aria-label']==='일상 착용컷 실제 모습');
 const geometry=()=>({box:outline().props.viewBox,images:nodes(outline()).filter(n=>n.type==='image').map(n=>({href:n.props.href,x:n.props.x,y:n.props.y,w:n.props.width,h:n.props.height})),positions:nodes(outline()).filter(n=>n.type==='g'&&n.props.transform).map(n=>n.props.transform)});
 const before=geometry();
 byLabel(h,'배치 · 2열').props.onClick();h.render();await flush(h);
 assert.notDeepEqual(geometry(),before);
 const pane=dialog(h),status=nodes(pane).find(n=>n.props?.role==='status');assert.ok(status);
 assert.match(textOf(status),/1가지 바꿨어요·되돌리기/);
 const undo=nodes(status).find(n=>n.type==='button'&&textOf(n)==='되돌리기');assert.ok(undo&&!undo.props.disabled);
 status.props.onMouseEnter();status.props.onFocus();status.props.onBlur();status.props.onMouseLeave();
 undo.props.onClick();h.render();await flush(h);
 assert.ok(dialog(h),'design undo keeps the panel open');assert.deepEqual(geometry(),before);
 const continueBar=nodes(h.tree).find(n=>n.props?.className?.includes('continueBar'));
 assert.equal(continueBar.props['aria-hidden'],true);
});

for(const mode of ['photo','add'])test(`WOC12 ${mode} panel keeps the wide mode while design panel alone narrows`,async t=>{
 const h=await storyboardScreenHarness();t.after(()=>h.close());h.render();await h.flush();
 if(mode==='add')byText(h,'＋ 섹션 추가').props.onClick();
 else nodes(h.tree).find(n=>n.type==='button'&&n.props?.['data-storyboard-photo']).props.onClick();
 h.render();await flush(h);
 assert.ok(!nodes(h.tree).some(n=>n.props?.className?.includes('designDrawerOpen')));
 const pane=nodes(h.tree).find(n=>n.props?.role==='dialog');assert.ok(pane);assert.ok(!pane.props.className.includes('designDrawer'));
});

for(const ending of ['close','undo'])test(`WOC12 ${ending} while undo is hovered releases the pause before the next change`,async t=>{
 const h=await storyboardScreenHarness();t.after(()=>h.close());await openDaily(h);
 const original=setTimeout;let expirySchedules=0;
 globalThis.setTimeout=(fn,delay,...args)=>{if(delay===10000&&String(fn).includes('undoEntryRef'))expirySchedules++;return original(fn,delay,...args);};
 t.after(()=>{globalThis.setTimeout=original;});
 byLabel(h,'배치 · 2열').props.onClick();h.render();await flush(h);
 const status=nodes(dialog(h)).find(n=>n.props?.role==='status');status.props.onMouseEnter();
 if(ending==='close'){
   byLabel(h,'옆 판 닫기').props.onClick();h.render();await flush(h);
   byText(h,'섹션 디자인 변경').props.onClick();h.render();await flush(h);
 }else{
   nodes(status).find(n=>n.type==='button').props.onClick();h.render();await flush(h);
 }
 assert.ok(expirySchedules>=1,'initial undo expiry is observed');
 const before=expirySchedules;
 byLabel(h,ending==='close'?'배치 · 한 열':'배치 · 2열').props.onClick();h.render();await flush(h);
 assert.ok(expirySchedules>before,'the next change schedules expiry without requiring a missing mouseleave');
});
