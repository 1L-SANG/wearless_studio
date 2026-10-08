import test from 'node:test';
import assert from 'node:assert/strict';
import {isBlankImageRegion,rasterizeExportSvg} from '../../src/features/editor/editorExportRaster.js';
const colors = Array.from({length:16},(_,i)=>[40+i*5,70+i*3,110+i*2,255]);
const white = colors.map(()=>[255,255,255,255]);
const flat = c => colors.map(()=>c);
test('사진 대신 균일한 흰색이나 블록 배경만 남으면 누락으로 판정한다',()=>{
 assert.equal(isBlankImageRegion(colors,white),true);
 assert.equal(isBlankImageRegion(colors,flat([238,242,245,255])),true);
});
test('정상 사진, 단색 사진과 원래 흰 이미지, 투명 영역은 누락으로 오인하지 않는다',()=>{
 assert.equal(isBlankImageRegion(colors,colors),false);
 assert.equal(isBlankImageRegion(flat([30,40,50,255]),flat([30,40,50,255])),false);
 assert.equal(isBlankImageRegion(white,white),false);
 assert.equal(isBlankImageRegion(flat([0,0,0,0]),white),false);
 assert.equal(isBlankImageRegion([],[]),false);
});
function canvasFactory(state){
 return (w,h)=>{
  // 브라우저 Canvas와 같이 소수 크기를 정수로 버리는 경계.
  const c={width:Math.trunc(w),height:Math.trunc(h),pixels:white};
  c.getContext=()=>({fillRect(){c.pixels=white;},clearRect(){c.pixels=white;},drawImage(){c.pixels=state.ready?colors:white;},getImageData(x){return {data:Uint8ClampedArray.from(c.pixels[Math.min(15,x)]||[255,255,255,255])};}});return c;
 };
}
const probes=[{points:colors.map((rgba,x)=>({x,y:0,rgba}))}];
test('높이 1,333px 사진의 1290 출력은 왕복 소수 오차와 무관하게 1,719행을 만든다',async()=>{
 const state={ready:true};const c=await rasterizeExportSvg('svg',{width:1290,height:1719,probes},{createCanvas:canvasFactory(state),loadImage:async()=>({}),waitForPaint:async()=>{}});
 assert.deepEqual(c&&[c.width,c.height],[1290,1719]);
 assert.ok(1719/1.29*1.29<1719,'재현 입력은 기존 Canvas 왕복에서 1px 손실이 있다');
});
test('SVG decode가 끝났어도 사진이 빠졌으면 픽셀이 나타난 후에만 캔버스를 반환한다',async()=>{
 const state={ready:false};let waits=0;
 const c=await rasterizeExportSvg('svg',{width:860,height:1146,probes},{createCanvas:canvasFactory(state),loadImage:async()=>({}),waitForPaint:async()=>{waits++;if(waits===2)state.ready=true;}});
 assert.deepEqual(c?.pixels,colors);
});
test('사진이 끝내 그려지지 않으면 저장용 캔버스 반환 대신 실패하고 버퍼를 해제한다',async()=>{
 const state={ready:false};let allocated;
 await assert.rejects(rasterizeExportSvg('svg',{width:860,height:1146,probes},{createCanvas:(w,h)=>{allocated=canvasFactory(state)(w,h);return allocated;},loadImage:async()=>({}),waitForPaint:async()=>{}}),/사진.*빠져|사진.*확인/);
 assert.deepEqual([allocated.width,allocated.height],[0,0]);
});
test('원래 빈 블록은 사진 검증을 건너뛰고 정상 파일로 반환한다',async()=>{
 const state={ready:false};const c=await rasterizeExportSvg('svg',{width:860,height:258,probes:[]},{createCanvas:canvasFactory(state),loadImage:async()=>({}),waitForPaint:async()=>{throw Error('빈 블록에서 사진 대기 금지');}});
 assert.deepEqual(c&&[c.width,c.height],[860,258]);
});

test('검은색과 흰색 잔무늬가 정상 축소되어 회색이 된 경우 누락으로 오인하지 않는다',()=>{
 const pattern=colors.map((_,i)=>i%2?[255,255,255,255]:[0,0,0,255]);
 assert.equal(isBlankImageRegion(pattern,flat([128,128,128,255])),false);
});
