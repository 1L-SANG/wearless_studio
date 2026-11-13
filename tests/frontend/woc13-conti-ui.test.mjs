import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {storyboardScreenHarness} from './helpers/storyboardScreenHarness.mjs';
import {nodes,textOf,loadStoryboardComponent} from './helpers/storyboardComponents.mjs';
import {readFileSync} from 'node:fs';
const label=(h,text)=>nodes(h.tree).find(n=>n.props?.['aria-label']===text);
const button=(h,text)=>nodes(h.tree).find(n=>n.type==='button'&&textOf(n)===text);
test('WOC13 selecting a singleton section closes design pane but opens the selected stage',async t=>{
 const h=await storyboardScreenHarness();t.after(()=>h.close());h.render();await h.flush();
 button(h,'섹션 디자인 변경').props.onClick();h.render();assert.ok(label(h,'섹션 디자인 바꾸기'));
 label(h,'후기 열기').props.onClick();h.render();assert.equal(label(h,'섹션 디자인 바꾸기'),undefined);assert.ok(label(h,'후기 무대'));
 label(h,'일상 착용컷 열기').props.onClick();h.render();button(h,'섹션 디자인 변경').props.onClick();h.render();
 label(h,'색상과 제품 열기').props.onClick();h.render();assert.ok(label(h,'섹션 디자인 바꾸기'));
});
test('WOC13 section names use the stable 섹션을 particle in added notice and removal question',async()=>{
 const Dialog=await loadStoryboardComponent('StoryboardRemoveDialog.jsx','StoryboardRemoveDialog',{useRef:React.useRef,useEffect:React.useEffect,createPortal:node=>node});
 for(const name of ['후기','디테일 요소','일상 착용컷']){
  assert.match(renderToStaticMarkup(React.createElement(Dialog,{name})),new RegExp(`${name} 섹션을 뺄까요`));
 }
 const source=readFileSync('src/features/storyboard/Storyboard.jsx','utf8');assert.match(source,/message: `\$\{name\} 섹션을 넣었어요`/);assert.doesNotMatch(source,/\$\{name\}(?:을|를)/);
});
