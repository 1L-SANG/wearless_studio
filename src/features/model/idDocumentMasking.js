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

// 서버 계약(uploadIdDocument, server/app/facemarket_enrollment.py)이 받는 네 종류.
export const ID_DOCUMENT_TYPES = Object.freeze([
  { value: 'rrc', label: '주민등록증' },
  { value: 'dl', label: '운전면허증' },
  { value: 'passport', label: '여권' },
  { value: 'arc', label: '외국인등록증' },
]);

// 카드 종류별 기본 마스킹 박스(이미지 비율 0~1) — 주민등록번호 뒷자리가 대체로 놓이는
// 자리의 대략적인 출발점일 뿐이다. 최종 위치·크기는 사용자가 캔버스 위에서 드래그로
// 맞춘다 — 이 기본값이 틀려도 사용자가 조정하면 되므로 정밀도보다 "안전한 쪽으로 넉넉히"
// 잡는다(가로 폭을 넓게 잡아 실제 번호를 놓치지 않게 한다).
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
export function maskRatioToPixels(ratio, width, height) {
  return {
    x: Math.round(ratio.xr * width),
    y: Math.round(ratio.yr * height),
    w: Math.round(ratio.wr * width),
    h: Math.round(ratio.hr * height),
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
