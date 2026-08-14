/* 에디터 다운로드 — 블록 DOM 을 그대로 PNG 로 캡처한다 (서버 렌더 job 없음).
 *
 * 왜 클라 캡처인가: 에디터 화면 자체가 완성된 렌더라, 헤드리스 크롬 같은 서버 인프라 없이
 * 화면 그대로를 저장하는 게 가장 작고 정확한 경로다. 이미지 픽셀을 캔버스로 읽으려면 CORS 가
 * 필요한데 `/v1/assets/{id}/file` 은 R2 로 302 라 보장이 없다 → 같은 계약의 바이트 직접
 * 서빙 라우트 `/v1/assets/{id}/bytes` 로 바꿔 읽는다 (routes.py `_tone_bytes` 와 같은 근거).
 *
 * 이미지 실패 정책 (리뷰 반영): html-to-image 는 이미지 fetch 실패를 빈 칸으로 삼키고
 * 모듈 캐시에 실패까지 저장한다 — 그래서 캡처 전에 이미지를 직접 프리플라이트한다.
 *   - /bytes 자산(핵심 상품컷, CORS 는 우리 서버가 보장): 1장이라도 실패하면 **중단**.
 *     빈 상세페이지를 "저장 완료"로 속이지 않고, 재시도가 실제로 통하게 한다.
 *   - 그 외 외부 URL(모델 썸네일 등, CORS 보장 불가): 실패 시 그 이미지만 비우고 진행,
 *     저장 후 경고를 돌려준다. 외부 한 장 때문에 전체 다운로드를 막지 않는다.
 *
 * 경로 3개: ① 블록 1개 PNG(퀵바) ② 전체 세로 이어붙인 긴 PNG ③ 블록별 PNG ZIP (다운로드 모달).
 */
import { getFontEmbedCSS, toCanvas } from 'html-to-image';
import JSZip from 'jszip';

export const EXPORT_WIDTH = 1000; // .ed-canvas 설계 폭 — 캡처는 화면 scale 과 무관하게 이 폭 기준
const PIXEL_RATIO = 2; // 상세페이지 업로드 기준 2000px — 쇼핑몰 권장폭(860~1280)을 여유 있게 커버
const MAX_CANVAS_DIM = 30000; // 캔버스 한 변 한계(32767) 아래 여유 — 넘으면 긴 PNG 대신 ZIP 안내

/* ---- 순수 헬퍼 (테스트 대상) ---- */

// `/v1/assets/{id}/file`(상대·절대 모두) → 같은 자산의 `/bytes`. 그 외 URL 은 그대로.
// id 는 서버(/file 라우트)가 uuid 검증을 담당하므로 여기선 경로 모양만 본다 — 프론트가
// 더 엄격하면(예: uuid 강제) 서버는 받는데 프론트만 못 바꾸는 어긋남이 생긴다(리뷰 반영).
const ASSET_FILE_RE = /^(.*\/v1\/assets\/[^/?#]+)\/file$/;
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

// 긴 PNG pixelRatio — 한 변 한계 안에서 최대. 어떤 배율로도 못 담으면 null(호출부가 ZIP 안내).
export function fitPixelRatio(totalCssHeight, wanted = PIXEL_RATIO) {
  if (totalCssHeight <= 0) return wanted;
  if (totalCssHeight > MAX_CANVAS_DIM) return null;
  return Math.max(1, Math.min(wanted, Math.floor((MAX_CANVAS_DIM / totalCssHeight) * 100) / 100));
}

// 파일명 — 상품명에서 파일시스템 금지문자만 걷어낸다. 빈 값은 '상세페이지'.
export function exportFileName(productName, suffix) {
  const base = String(productName || '').replace(/[\\/:*?"<>|]/g, ' ').replace(/\s+/g, ' ').trim();
  return `${base || '상세페이지'}${suffix ? `_${suffix}` : ''}.png`;
}

/* ---- DOM 캡처 ---- */

// 캡처에서 제외할 에디터 크롬 — 블록 안에 떠 있는 UI. 목록은 렌더 지점과 눈으로 맞춘
// 블록리스트라, 새 크롬을 추가할 때 여기도 같이 봐야 한다(허용리스트 전환은 백로그).
const CHROME_SELECTOR = [
  '.quick', '.blk-label', '.blk-resize', '.selection-marquee', '.crop-bar', '.crop-layer',
  '.align-bar', '.rot', '.hdl', '.edge', '.moveable-control-box',
  '.ed-genwait', '.ed-uploadwait', '.canvas-dropline', '.slot-add', '.image-drop-guide',
].join(',');

const isRemoteSrc = (src) => !!src && !src.startsWith('data:') && !src.startsWith('blob:');

// 이미지 프리플라이트 — 정책은 파일 머리 주석 참조. { softFailed } 반환, 핵심 자산 실패는 throw.
async function preflightImages(clone) {
  const imgs = [...clone.querySelectorAll('img')];
  const bySrc = new Map();
  imgs.forEach((img) => {
    const src = img.getAttribute('src');
    if (isRemoteSrc(src)) bySrc.set(src, [...(bySrc.get(src) || []), img]);
  });
  let coreFailed = 0;
  let softFailed = 0;
  await Promise.all([...bySrc.keys()].map(async (src) => {
    let ok = false;
    try {
      const res = await fetch(src);
      // 핵심 자산은 이미지 타입까지 확인 — SPA 리라이트가 index.html 을 200으로 주는 함정 차단.
      ok = res.ok && (!src.endsWith('/bytes')
        || (res.headers.get('content-type') || '').startsWith('image/'));
    } catch { /* 네트워크/CORS 실패 → 아래에서 분류 */ }
    if (ok) return;
    if (src.endsWith('/bytes')) {
      coreFailed += 1;
    } else {
      softFailed += bySrc.get(src).length;
      // src 를 비워 html-to-image 가 실패를 모듈 캐시에 박제하는 것(재시도 불가)을 막는다.
      bySrc.get(src).forEach((img) => img.removeAttribute('src'));
    }
  }));
  if (coreFailed) {
    throw new Error(`상품 이미지 ${coreFailed}장을 불러오지 못해 다운로드를 중단했어요. 잠시 후 다시 시도해 주세요.`);
  }
  return { softFailed };
}

async function prepareClone(blockNode) {
  const holder = document.createElement('div');
  holder.style.cssText = `position:fixed;left:-100000px;top:0;width:${EXPORT_WIDTH}px;pointer-events:none;`;
  const clone = blockNode.cloneNode(true);
  clone.classList.remove('on', 'obj-over');
  // 카드 그림자·간격은 화면용 — 결과물에 회색 이음새를 남긴다(이어보기 모드와 불일치, 리뷰 반영).
  clone.style.boxShadow = 'none';
  clone.style.margin = '0';
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
  });
  holder.appendChild(clone);
  document.body.appendChild(holder);
  try {
    const { softFailed } = await preflightImages(clone);
    return { clone, softFailed, dispose: () => holder.remove() };
  } catch (e) {
    holder.remove();
    throw e;
  }
}

async function captureBlockCanvas(blockNode, pixelRatio, fontEmbedCSS) {
  const { clone, softFailed, dispose } = await prepareClone(blockNode);
  try {
    const canvas = await toCanvas(clone, {
      pixelRatio,
      width: EXPORT_WIDTH,
      height: clone.offsetHeight,
      backgroundColor: '#ffffff',
      fontEmbedCSS,
    });
    return { canvas, softFailed };
  } finally {
    dispose();
  }
}

// canvas.toBlob 은 초대형 캔버스에서 콜백이 영영 안 올 수 있다(리뷰 반영) — 타임아웃 방어.
function canvasToBlob(canvas, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('이미지 변환이 너무 오래 걸려 중단했어요. 블록별 ZIP으로 시도해 주세요.')),
      timeoutMs,
    );
    canvas.toBlob((b) => {
      clearTimeout(timer);
      if (b) resolve(b);
      else reject(new Error('PNG 인코딩에 실패했어요. 페이지가 길면 블록별 ZIP으로 시도해 주세요.'));
    }, 'image/png');
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

/* ---- 공개 API — 각 함수는 { softFailed } 를 돌려준다 (0 이면 완전 성공) ---- */

// ① 블록 1개 → PNG 저장
export async function exportBlockPng(blockNode, productName, blockIndex) {
  const fontEmbedCSS = await getFontEmbedCSS(blockNode);
  const { canvas, softFailed } = await captureBlockCanvas(blockNode, PIXEL_RATIO, fontEmbedCSS);
  saveBlob(await canvasToBlob(canvas), exportFileName(productName, `블록${blockIndex + 1}`));
  return { softFailed };
}

// ② 전체 블록 → 세로로 이어붙인 긴 PNG 1장.
// 스티치 캔버스를 먼저 만들고 블록을 그리는 즉시 해제해 피크 메모리를 절반으로(리뷰 반영).
export async function exportLongPng(blockNodes, productName, onProgress) {
  const heights = blockNodes.map((n) => n.offsetHeight);
  const totalCssHeight = heights.reduce((a, b) => a + b, 0);
  const ratio = fitPixelRatio(totalCssHeight);
  if (ratio == null) {
    throw new Error('페이지가 너무 길어 한 장으로는 저장할 수 없어요. 블록별 ZIP으로 받아주세요.');
  }
  const fontEmbedCSS = await getFontEmbedCSS(blockNodes[0]);
  const out = document.createElement('canvas');
  out.width = Math.trunc(EXPORT_WIDTH * ratio);
  // 블록별 캔버스 높이는 trunc(offsetHeight×ratio) — 합계도 같은 식으로 예약해 이음새를 없앤다.
  out.height = heights.reduce((sum, h) => sum + Math.trunc(h * ratio), 0);
  const ctx = out.getContext('2d');
  if (!ctx) throw new Error('저장용 캔버스를 만들지 못했어요. 블록별 ZIP으로 시도해 주세요.');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, out.width, out.height);
  let y = 0;
  let softFailed = 0;
  for (let i = 0; i < blockNodes.length; i += 1) {
    onProgress?.(i, blockNodes.length);
    const r = await captureBlockCanvas(blockNodes[i], ratio, fontEmbedCSS); // eslint-disable-line no-await-in-loop
    ctx.drawImage(r.canvas, 0, y);
    y += r.canvas.height;
    softFailed += r.softFailed;
    r.canvas.width = 0; // 그린 즉시 픽셀 버퍼 해제
    r.canvas.height = 0;
  }
  saveBlob(await canvasToBlob(out), exportFileName(productName));
  return { softFailed };
}

// ③ 전체 블록 → 블록별 PNG 를 담은 ZIP
export async function exportBlocksZip(blockNodes, productName, onProgress) {
  const zip = new JSZip();
  const pad = (n) => String(n).padStart(2, '0');
  const fontEmbedCSS = await getFontEmbedCSS(blockNodes[0]);
  let softFailed = 0;
  for (let i = 0; i < blockNodes.length; i += 1) {
    onProgress?.(i, blockNodes.length);
    const r = await captureBlockCanvas(blockNodes[i], PIXEL_RATIO, fontEmbedCSS); // eslint-disable-line no-await-in-loop
    zip.file(exportFileName(productName, `블록${pad(i + 1)}`), await canvasToBlob(r.canvas)); // eslint-disable-line no-await-in-loop
    softFailed += r.softFailed;
  }
  const blob = await zip.generateAsync({ type: 'blob' }); // PNG 는 이미 압축 — STORE 기본이면 충분
  saveBlob(blob, exportFileName(productName).replace(/\.png$/, '.zip'));
  return { softFailed };
}
