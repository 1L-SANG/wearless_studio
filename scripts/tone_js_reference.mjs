// JS 정본 구현으로 고정 픽셀 집합을 렌더해 표준출력으로 뱉는다 (동등성 검증용).
import { applyTone } from '../src/lib/toneRender.js';
const N = 512;
let seed = 12345;
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const src = new Uint8ClampedArray(N * 4);
const mask = new Uint8Array(N);
for (let i = 0; i < N; i += 1) {
  src[i*4] = Math.floor(rnd()*256); src[i*4+1] = Math.floor(rnd()*256);
  src[i*4+2] = Math.floor(rnd()*256); src[i*4+3] = 255;
  mask[i] = [0, 64, 128, 200, 255][Math.floor(rnd()*5)];
}
const out = [];
for (const [s, e] of [[0,0],[30,0],[-30,0],[0,20],[0,-20],[-10,8],[20,-15],[100,0],[-100,0],[0,100],[0,-100],[100,100],[-100,-100]]) {
  const buf = new Uint8ClampedArray(src.length);
  applyTone(src, mask, buf, s, e);
  out.push({ s, e, px: Array.from(buf) });
}
console.log(JSON.stringify({ src: Array.from(src), mask: Array.from(mask), cases: out }));
