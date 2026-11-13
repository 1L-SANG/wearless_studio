import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {continueAfterStoryboardFlush} from '../../src/features/storyboard/storyboardNavigation.js';
let vite,createQueue;
before(async()=>{vite=await createServer({configFile:false,logLevel:'silent',resolve:{alias:{'@':fileURLToPath(new URL('../../src',import.meta.url))}},server:{middlewareMode:true,watch:null,ws:false},optimizeDeps:{noDiscovery:true,include:[]}});({createStoryboardStyleSaveQueue:createQueue}=await vite.ssrLoadModule('/src/features/storyboard/storyboardPageStyle.js'));});
after(()=>vite?.close());
for(const retryFails of [false,true])for(const boardFails of [false,true])test(`WOC13 next retries displayed style once; retryFails=${retryFails}, boardFails=${boardFails}`,async()=>{
 const visible={templateId:'emo',fontStyle:'template',colorMode:'mono'},calls=[],events=[];
 const q=createQueue(async style=>{calls.push(style);if(calls.length===1||retryFails)throw Error('style offline');});q.seed(visible);
 await assert.rejects(q.save({...visible,fontStyle:'serif'}));
 const source=readFileSync('src/features/storyboard/Storyboard.jsx','utf8');
 const start=source.indexOf('  const goToMannequin ='),end=source.indexOf('\n  const runs =',start);
 const scope={mannequinStageEnabled:true,blocks:[{source:'mine'}],productSectionNeedingSelection:()=>null,unsupportedAiBlock:null,continueAfterStoryboardFlush,
  styleSaves:q,styleValueRef:{current:visible},projectId:'p',useAppStore:{getState(){}},ensureV4StoryboardCopywriting:async()=>events.push('copy'),
  saveNow:async()=>{events.push('board');if(boardFails)throw Error('board offline');},navigate:path=>events.push(path),
  toast:{push:()=>events.push('failure')},needsGarmentColorMeasurement:()=>false,latestBlocks:{current:[]}};
 const go=new Function('scope',`with(scope){${source.slice(start,end)};return goToMannequin;}`)(scope);
 await go();assert.equal(calls.length,2);assert.deepEqual(calls[1],visible);
 assert.deepEqual(events,boardFails?['copy','board','failure']:['copy','board','/create/mannequin']);
});
test('WOC13 wait settles past failure; later successful choices do not trigger a retry',async()=>{
 let count=0;const q=createQueue(async()=>{if(++count===1)throw Error('offline');});
 await assert.rejects(q.save({fontStyle:'serif'}));await assert.doesNotReject(q.wait());
 await q.save({fontStyle:'template'});await q.flush(()=>({fontStyle:'boldGothic'}));assert.equal(count,2);
});
