import test from 'node:test';
import assert from 'node:assert/strict';
import { outfitHarness, nodes, textOf } from './helpers/outfitHarness.mjs';

function environment(t) {
  const old = { window: globalThis.window, document: globalThis.document,
    requestAnimationFrame: globalThis.requestAnimationFrame, cancelAnimationFrame: globalThis.cancelAnimationFrame };
  const handlers = new Map(), frames = new Map(); let id = 0;
  const events = { addEventListener(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn); },
    removeEventListener(name, fn) { handlers.get(name)?.delete(fn); } };
  const attrs = new Map(); const calls = [];
  globalThis.document = { ...events, body: { style: { overflow: 'auto' } }, activeElement: null,
    documentElement: { style: { scrollBehavior: 'smooth' }, setAttribute: (k,v) => attrs.set(k,v), removeAttribute: k => attrs.delete(k) },
    querySelector: () => ({ getBoundingClientRect: () => ({ bottom: 60 }) }) };
  globalThis.window = { ...events, scrollY: 0, innerHeight: 800, matchMedia: () => ({ matches: false }), localStorage: { getItem: () => '1', setItem() {} },
    scrollTo(_x,y) { calls.push(['to',y]); this.scrollY=y; }, scrollBy(_x,y) { calls.push(['by',y]); this.scrollY+=y; } };
  globalThis.requestAnimationFrame = fn => { frames.set(++id,fn); return id; };
  globalThis.cancelAnimationFrame = key => frames.delete(key);
  t.after(() => Object.assign(globalThis, old));
  return { calls, attrs, handlers, frame(now) { const queued=[...frames.values()];frames.clear();queued.forEach(fn=>fn(now)); },
    emit(name,event={}) { [...(handlers.get(name)||[])].forEach(fn=>fn(event)); } };
}

test('서랍은 dialog 라벨, 초점 가두기, Esc와 바깥 닫기, 초점 복귀를 지킨다', async (t) => {
  const env = environment(t); const h = await outfitHarness();
  const { OutfitDrawer } = await h.load('/src/features/analysis/OutfitDrawer.jsx');
  const doc = globalThis.document; let closed = 0, restored = 0;
  const previous = { isConnected: true, focus(options) { assert.equal(options.preventScroll,true); restored++; doc.activeElement=this; } }; doc.activeElement=previous;
  const controls=Array.from({length:3},()=>({focus(){doc.activeElement=this;}}));
  const panel={querySelector:()=>controls[1],querySelectorAll:()=>controls,contains:node=>controls.includes(node)};
  let tree=h.render(OutfitDrawer,{role:'bottom',label:'하의',items:[],onClose(){closed++;}},tree=>{nodes(tree).find(node=>node.props.role==='dialog').props.ref.current=panel;});
  const dialog=nodes(tree).find(node=>node.props.role==='dialog');
  assert.equal(dialog.props['aria-modal'],'true'); assert.ok(dialog.props['aria-labelledby']);
  assert.equal(doc.activeElement,controls[1]); assert.equal(doc.body.style.overflow,'hidden');
  let prevented=0; doc.activeElement=controls.at(-1); env.emit('keydown',{key:'Tab',preventDefault(){prevented++;}}); assert.equal(doc.activeElement,controls[0]);
  env.emit('keydown',{key:'Tab',shiftKey:true,preventDefault(){prevented++;}});assert.equal(doc.activeElement,controls.at(-1)); assert.equal(prevented,2);
  env.emit('keydown',{key:'Escape',preventDefault(){},stopPropagation(){}}); assert.equal(closed,1);
  const backdrop=nodes(tree).find(node=>node.props.onPointerDown); const target={};backdrop.props.onPointerDown({target,currentTarget:target});assert.equal(closed,2);
  await h.close(); assert.equal(doc.activeElement,previous);assert.equal(restored,1);assert.equal(doc.body.style.overflow,'auto');
});

test('진입은 transform 800ms로 이동하고 접는 프레임의 화면 좌표를 유지한다', async (t) => {
  const env=environment(t); const h=await outfitHarness();
  const { AnalysisStepFlow }=await h.load('/src/features/analysis/AnalysisStepFlow.jsx');
  const root={style:{transform:'',willChange:''},closest(){return this;}};
  const offset=()=>Number(/translateY\(([-\d.]+)px\)/.exec(root.style.transform)?.[1]||0);
  const head={getBoundingClientRect:()=>({top:(h.runtime.slots[1]?500:1800)-window.scrollY+offset()}),focus(){}};
  const first={getBoundingClientRect:()=>({bottom:1780-window.scrollY+offset()})};
  const props={inline:true,analysis:{},product:{},children:'form',outfit:'picker',cta:'confirm'};
  let tree=h.render(AnalysisStepFlow,props,node=>{
    nodes(node).find(n=>n.props['data-stage']).props.ref.current=root;
    nodes(node).find(n=>n.props.hidden===false).props.ref.current=first;
  });
  // The second stage is mounted by flushSync in the live renderer.
  h.runtime.slots[8].current=head;
  await nodes(tree).find(node=>node.type==='button'&&textOf(node)==='코디 고르기').props.onClick();
  assert.equal(h.render(AnalysisStepFlow,props).props['data-folded'],false,'진입 시작에는 1단계 폭을 유지한다');
  assert.equal(env.attrs.get('data-outfit-gliding'),'true');assert.equal(root.style.willChange,'transform');
  env.frame(0); const before=window.scrollY; env.frame(400);assert.equal(window.scrollY,before,'매 프레임 scrollTo를 호출하지 않는다');
  assert.equal(head.getBoundingClientRect().top,476);
  env.frame(800);assert.equal(h.runtime.slots[1],true);assert.equal(head.getBoundingClientRect().top,76);
  assert.equal(h.render(AnalysisStepFlow,props).props['data-folded'],true,'접는 프레임에서만 요약 기둥 폭을 바꾼다');
  assert.equal(root.style.transform,'');assert.equal(env.attrs.has('data-outfit-gliding'),false);
  assert.equal(env.calls.filter(call=>call[0]==='by').length,1);assert.equal(document.documentElement.style.scrollBehavior,'smooth');
  await h.close();
});
