/* 에디터 다운로드 — 블록 DOM 을 그대로 PNG 로 캡처한다 (서버 렌더 job 없음).
 *
 * 왜 클라 캡처인가: 에디터 화면 자체가 완성된 렌더라, 헤드리스 크롬 같은 서버 인프라 없이
 * 화면 그대로를 저장하는 게 가장 작고 정확한 경로다. 이미지 픽셀을 캔버스로 읽으려면 CORS 가
 * 필요한데 `/v1/assets/{id}/file` 은 R2 로 302 라 보장이 없다 → 같은 계약의 바이트 직접
 * 서빙 라우트 `/v1/assets/{id}/bytes` 로 바꿔 읽는다 (routes.py `_tone_bytes` 와 같은 근거).
 *
 * 경로 3개: ① 블록 1개 PNG(퀵바) ② 전체 세로 이어붙인 긴 PNG ③ 블록별 PNG ZIP (다운로드 모달).
 */
import { toCanvas } from 'html-to-image';
import JSZip from 'jszip';

export const EXPORT_WIDTH = 1000; // .ed-canvas 설계 폭 — 캡처는 화면 scale 과 무관하게 이 폭 기준
const PIXEL_RATIO = 2; // 상세페이지 업로드 기준 2000px — 쇼핑몰 권장폭(860~1280)을 여유 있게 커버
const MAX_CANVAS_DIM = 30000; // Safari/Chrome 캔버스 한 변 한계(32767) 아래로 여유

/* ---- 순수 헬퍼 (테스트 대상) ---- */

// `/v1/assets/{id}/file`(상대·절대 모두) → 같은 자산의 `/bytes`. 그 외 URL 은 그대로.
// blob:/data:/외부 URL 은 건드리지 않는다 — 캡처 실패 시 원인 파악이 쉽도록 보수적으로.
const ASSET_FILE_RE = /^(.*\/v1\/assets\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\/file$/i;
export function toBytesUrl(src) {
  const m = ASSET_FILE_RE.exec(String(src || '').split('?')[0]);
  return m ? `${m[1]}/bytes` : src;
}

// 긴 PNG 스티치 배치 — 각 블록 캔버스의 y 오프셋과 전체 크기. 폭은 최댓값 기준(전부 동일이 정상).
export function stitchLayout(sizes) {
  let y = 0;
  let width = 0;
  const offsets = sizes.map((s) => {
    const at = y;
    y += s.height;
    width = Math.max(width, s.width);
    return at;
  });
  return { width, height: y, offsets };
}

// 긴 PNG 가 캔버스 한계를 넘으면 pixelRatio 를 낮춘다 (2 → 가능한 최대, 최소 1).
export function fitPixelRatio(totalCssHeight, wanted = PIXEL_RATIO) {
  if (totalCssHeight <= 0) return wanted;
  return Math.max(1, Math.min(wanted, Math.floor((MAX_CANVAS_DIM / totalCssHeight) * 100) / 100));
}

// 파일명 — 상품명에서 파일시스템 금지문자만 걷어낸다. 빈 값은 '상세페이지'.
export function exportFileName(productName, suffix) {
  const base = String(productName || '').replace(/[\\/:*?"<>|]/g, ' ').replace(/\s+/g, ' ').trim();
  return `${base || '상세페이지'}${suffix ? `_${suffix}` : ''}.png`;
}

/* ---- DOM 캡처 ---- */

// 캡처에서 제외할 에디터 크롬 — 블록 안에 떠 있는 UI (퀵바·라벨·선택 핸들·크롭 UI).
const CHROME_SELECTOR = [
  '.quick', '.blk-label', '.selection-marquee', '.crop-bar', '.align-bar',
  '.rot', '.hdl', '.edge', '.moveable-control-box', '.ed-genwait', '.canvas-dropline',
].join(',');

async function prepareClone(blockNode) {
  const holder = document.createElement('div');
  holder.style.cssText = `position:fixed;left:-100000px;top:0;width:${EXPORT_WIDTH}px;pointer-events:none;`;
  const clone = blockNode.cloneNode(true);
  clone.classList.remove('on', 'obj-over');
  clone.querySelectorAll(CHROME_SELECTOR).forEach((n) => n.remove());
  clone.querySelectorAll('.el').forEach((n) => {
    n.classList.remove('on');
    n.style.outline = 'none';
  });
  // 이미지: /file → /bytes 로 바꿔 CORS 가 보장된 경로에서 픽셀을 읽는다.
  clone.querySelectorAll('img').forEach((img) => {
    const next = toBytesUrl(img.getAttribute('src'));
    if (next !== img.getAttribute('src')) img.setAttribute('src', next);
    img.removeAttribute('loading');
    img.crossOrigin = 'anonymous';
  });
  holder.appendChild(clone);
  document.body.appendChild(holder);
  await Promise.all([...clone.querySelectorAll('img')].map((img) => (
    img.decode ? img.decode().catch(() => {}) : Promise.resolve()
  )));
  return { clone, dispose: () => holder.remove() };
}

async function captureBlockCanvas(blockNode, pixelRatio) {
  const { clone, dispose } = await prepareClone(blockNode);
  try {
    return await toCanvas(clone, {
      pixelRatio,
      width: EXPORT_WIDTH,
      height: clone.offsetHeight,
      backgroundColor: '#ffffff',
    });
  } finally {
    dispose();
  }
}

function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('PNG 인코딩에 실패했어요.'))), 'image/png');
  });
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/* ---- 공개 API ---- */

// ① 블록 1개 → PNG 저장
export async function exportBlockPng(blockNode, productName, blockIndex) {
  const canvas = await captureBlockCanvas(blockNode, PIXEL_RATIO);
  saveBlob(await canvasToBlob(canvas), exportFileName(productName, `블록${blockIndex + 1}`));
}

// ② 전체 블록 → 세로로 이어붙인 긴 PNG 1장
export async function exportLongPng(blockNodes, productName, onProgress) {
  const totalCssHeight = blockNodes.reduce((sum, n) => sum + n.offsetHeight, 0);
  const ratio = fitPixelRatio(totalCssHeight);
  const canvases = [];
  for (let i = 0; i < blockNodes.length; i += 1) {
    onProgress?.(i, blockNodes.length);
    canvases.push(await captureBlockCanvas(blockNodes[i], ratio)); // eslint-disable-line no-await-in-loop
  }
  const { width, height, offsets } = stitchLayout(canvases);
  const out = document.createElement('canvas');
  out.width = width;
  out.height = height;
  const ctx = out.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, width, height);
  canvases.forEach((c, i) => ctx.drawImage(c, 0, offsets[i]));
  saveBlob(await canvasToBlob(out), exportFileName(productName));
}

// ③ 전체 블록 → 블록별 PNG 를 담은 ZIP
export async function exportBlocksZip(blockNodes, productName, onProgress) {
  const zip = new JSZip();
  const pad = (n) => String(n).padStart(2, '0');
  for (let i = 0; i < blockNodes.length; i += 1) {
    onProgress?.(i, blockNodes.length);
    const canvas = await captureBlockCanvas(blockNodes[i], PIXEL_RATIO); // eslint-disable-line no-await-in-loop
    zip.file(`${pad(i + 1)}_${exportFileName(productName, `블록${i + 1}`)}`, await canvasToBlob(canvas)); // eslint-disable-line no-await-in-loop
  }
  const blob = await zip.generateAsync({ type: 'blob' }); // PNG 는 이미 압축 — STORE 기본이면 충분
  const name = exportFileName(productName).replace(/\.png$/, '.zip');
  saveBlob(blob, name);
}
