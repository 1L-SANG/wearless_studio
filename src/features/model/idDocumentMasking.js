/* =============================================================
   idDocumentMasking — 신분증 촬영본의 마스킹 순수 로직(IdDocumentStep.jsx 전용).
   개인정보보호법상 주민등록번호는 법적 근거 없이 수집할 수 없고, 신분증 사진에는
   그 번호가 그대로 보인다. 서버는 클라이언트가 실제로 마스킹했는지 검증할 수 없으므로
   (픽셀을 봐야 알 수 있는데, 그건 클라이언트만 원본을 들고 있을 때 이야기다) 이 파일이
   내보내는 buildMaskedBlob 이 캔버스에 불투명 사각형을 "실제로" 그려 넣은 뒤 그 결과만
   blob 으로 뽑는다. 원본 File/Image 는 이 함수의 인자로 딱 한 번(drawImage 소스로만)
   쓰이고 반환값에는 전혀 나타나지 않는다 — 호출부가 원본을 들고 있더라도 여기서
   나온 blob 만 서버로 보내면 원본은 네트워크에 닿지 않는다.
   순수 함수만 모아 둔다(React 없음) — IdDocumentStep.jsx 와 별도로 plain node 테스트가
   가능해야 한다(이 레포엔 DOM 렌더러가 없다. tests/frontend/image-transcode.test.mjs 와
   같은 패턴: document.createElement 를 흉내 낸 캔버스 스텁만으로 검증한다).
   ============================================================= */

import { rrnRectInFrame } from './idCardGeometry.js';

// 서버 계약(facemarket_id_document.ID_DOCUMENT_TYPES)과 같은 집합이어야 한다.
// v1 은 주민등록증만 받는다 — 마스크가 사각형 하나뿐이라 면허번호·여권번호·외국인등록번호는
// 가려지지 않은 채 저장되는데, 셋 다 고유식별정보다(최종리뷰 I12).
export const ID_DOCUMENT_TYPES = Object.freeze([
  { value: 'rrc', label: '주민등록증' },
]);

// 카드 종류별 기본 마스킹 박스(이미지 비율 0~1) — 주민등록번호 뒷자리가 대체로 놓이는
// 자리의 대략적인 출발점일 뿐이다. 최종 위치·크기는 사용자가 캔버스 위에서 드래그로
// 맞춘다 — 이 기본값이 틀려도 사용자가 조정하면 되므로 정밀도보다 "안전한 쪽으로 넉넉히"
// 잡는다(가로 폭을 넓게 잡아 실제 번호를 놓치지 않게 한다).
// v1 에 열려 있는 건 rrc 하나지만, 다른 키도 남겨 둔다 — 종류를 다시 넓힐 때
// **마스크가 하나뿐이라는 사실**을 먼저 해결해야 한다는 걸 여기서 잊지 않기 위해서다.
const DEFAULT_MASK_RATIOS = Object.freeze({
  rrc: { xr: 0.04, yr: 0.60, wr: 0.60, hr: 0.16 },
  dl: { xr: 0.04, yr: 0.56, wr: 0.60, hr: 0.16 },
  passport: { xr: 0.06, yr: 0.68, wr: 0.88, hr: 0.14 },
  arc: { xr: 0.04, yr: 0.60, wr: 0.60, hr: 0.16 },
});

export function defaultMaskRatio(documentType) {
  return DEFAULT_MASK_RATIOS[documentType] || DEFAULT_MASK_RATIOS.rrc;
}

// 비율(0~1) 박스를 실제 캔버스 픽셀 좌표로 바꾼다. 드래그 중엔 비율로 들고 있다가
// (원본 이미지 크기와 화면 표시 크기가 달라도 안전) 전송 직전에만 픽셀로 바꾼다.
// ⚠️ 이 함수는 **비율의 기준이 자연 픽셀 격자일 때만** 맞다. 화면 오버레이는
// `<img>` 엘리먼트 박스 기준 비율이고, 그 엘리먼트가 `object-fit: contain` 으로
// 레터박스되면 두 공간이 어긋난다 — 그 경우엔 elementRatioToImagePixels 를 써야 한다.
export function maskRatioToPixels(ratio, width, height) {
  return {
    x: Math.round(ratio.xr * width),
    y: Math.round(ratio.yr * height),
    w: Math.round(ratio.wr * width),
    h: Math.round(ratio.hr * height),
  };
}

/* `object-fit: contain` 으로 그려진 이미지가 엘리먼트 박스 안에서 실제로 차지하는
   사각형(레터박스 여백을 뺀 내용 영역). 가로세로비가 같으면 박스 전체와 같다. */
export function containContentRect(elementBox, natural) {
  const ew = Number(elementBox?.width) || 0;
  const eh = Number(elementBox?.height) || 0;
  const nw = Number(natural?.width) || 0;
  const nh = Number(natural?.height) || 0;
  if (ew <= 0 || eh <= 0 || nw <= 0 || nh <= 0) return null;
  const scale = Math.min(ew / nw, eh / nh);
  const w = nw * scale;
  const h = nh * scale;
  return { x: (ew - w) / 2, y: (eh - h) / 2, w, h };
}

/* 화면에서 사용자가 놓은 마스킹 박스(=`<img>` **엘리먼트 박스** 기준 비율)를 원본
   이미지의 **자연 픽셀** 좌표로 옮긴다.

   왜 필요한가: 드래그는 `getBoundingClientRect()`(엘리먼트 박스)로 정규화되고 오버레이도
   그 박스의 %로 배치되는데, 캔버스 burn 은 naturalWidth/Height 격자에 그린다. CSS 가
   `object-fit: contain`(+ `max-height`)이라 이미지가 레터박스되는 순간 두 좌표계가
   어긋나고, **주민등록번호가 아닌 엉뚱한 곳이 칠해진 채 업로드된다**(최종리뷰 C3).
   사용자는 화면에서 가려진 걸 봤으니 "가렸어요" 체크를 정직하게 누르고, 서버는 픽셀을
   검증할 수 없다 — 이 변환이 유일한 방어선이다.

   레터박스 여백 위로 끌린 부분은 이미지 밖이라 자연 격자에서 잘라 낸다(clamp). 박스가
   통째로 여백에 있으면(가려진 게 없다) 0 크기를 돌려주지 않고 null 로 알린다 —
   호출부가 조용히 "칠했다"고 믿으면 안 된다. */
export function elementRatioToImagePixels(ratio, elementBox, natural) {
  const content = containContentRect(elementBox, natural);
  const nw = Number(natural?.width) || 0;
  const nh = Number(natural?.height) || 0;
  // 측정할 수 없으면(테스트 스텁·레이아웃 전) 자연 격자 기준으로 되돌린다 —
  // 가로세로비가 같은 흔한 경우엔 두 결과가 같다.
  if (!content) return maskRatioToPixels(ratio, nw, nh);
  const scale = nw / content.w;
  const left = (ratio.xr * elementBox.width - content.x) * scale;
  const top = (ratio.yr * elementBox.height - content.y) * scale;
  const right = left + ratio.wr * elementBox.width * scale;
  const bottom = top + ratio.hr * elementBox.height * scale;
  const x0 = Math.max(0, Math.min(nw, left));
  const y0 = Math.max(0, Math.min(nh, top));
  const x1 = Math.max(0, Math.min(nw, right));
  const y1 = Math.max(0, Math.min(nh, bottom));
  if (x1 - x0 <= 0 || y1 - y0 <= 0) return null;
  return {
    x: Math.round(x0),
    y: Math.round(y0),
    w: Math.round(x1 - x0),
    h: Math.round(y1 - y0),
  };
}

// 마스킹 박스가 이미지 밖으로 나가거나 크기가 0/음수가 되지 않게 자른다. 드래그·리사이즈
// 핸들러가 매 이동마다 부르면 화면 밖으로 끌려나가거나 뒤집히는 사각형을 막을 수 있다.
export function clampMaskRatio({ xr, yr, wr, hr }) {
  const w = Math.min(Math.max(wr, 0.02), 1);
  const h = Math.min(Math.max(hr, 0.02), 1);
  const x = Math.min(Math.max(xr, 0), 1 - w);
  const y = Math.min(Math.max(yr, 0), 1 - h);
  return { xr: x, yr: y, wr: w, hr: h };
}

// 전송 전 캔버스에 실제로 채운다 — 이 캔버스에서 뽑은 blob 만 서버로 간다. 서버는 마스킹
// 여부를 검증할 수 없으므로 클라이언트가 확실히 픽셀을 덮어써야 한다. image 는 원본을
// 그리는 소스로만 쓰이고, 반환되는 건 fillRect 이후의 캔버스 blob 뿐이다 — 원본 File 은
// 이 함수의 반환값에 전혀 등장하지 않는다(호출부가 이 blob 만 업로드해야 하는 이유).
export function buildMaskedBlob({ canvas, image, mask, quality = 0.92 }) {
  const ctx = canvas.getContext('2d');
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#111';
  ctx.fillRect(mask.x, mask.y, mask.w, mask.h);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
}

/* 가이드 촬영용 자동 마스킹. 카드가 가이드를 채운 상태로 찍혔으므로 주민등록번호
   위치가 규격으로 계산된다 — 사용자가 박스를 끌지 않는다.

   drawImage → fillRect 순서가 뒤집히면 원본이 마스크 위에 다시 그려져 번호가
   살아난다. 순서를 테스트로 고정한다(v1 에서 그 순서를 못 잡는 테스트가 있었다). */
export function burnGuideMask(canvas, source, width, height, quality = 0.92) {
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(source, 0, 0, width, height);
  const r = rrnRectInFrame(width, height);
  ctx.fillStyle = '#111';
  ctx.fillRect(r.x, r.y, r.w, r.h);
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
}
