/* 업로드 사진 캡션 — OS 가 지어준 임시 이름을 사람이 읽을 이름으로 바꿔 보여준다.

   macOS 는 실제 파일이 없는 이미지를 드래그·붙여넣기할 때(미리보기·Quick Look·사진 앱,
   Safari 복사) `tempImage6mNIRA.jpg` 같은 임시 파일을 만들어 넘긴다. 우리 업로드 경로는
   원본 이름을 보존할 뿐이라(imageTranscode 는 확장자만 바꾼다) 그 이름이 그대로 캡션에
   찍혔다(2026-08-15 사용자 지적). 원본 이름은 메타(im.name)·업로드 filename 으로 그대로
   남기고 **보이는 캡션만** 정리한다 — 순수 함수라 화면 없이 검증한다. */

/* OS·앱이 지어준 의미 없는 이름들. 사람이 붙인 이름(예: IMG_1234, 니트_앞면)은 살린다 —
   카메라 기본명(IMG_/DSC_)도 셀러가 파일을 찾는 단서라 그대로 둔다. */
const MEANINGLESS_NAME = new RegExp([
  '^tempimage[\\w-]*$',          // macOS 드래그·붙여넣기 임시 파일
  // 브라우저·OS 기본명. 중복 저장 시 붙는 '(1)' 꼬리까지 함께 본다 — image (1).png 등
  '^image[\\s_-]*(\\(\\d+\\)|\\d*)$',
  '^unnamed[\\s_-]*(\\(\\d+\\)|\\d*)$',
  '^photo[\\s_-]*(\\(\\d+\\)|\\d*)$',
  '^다운로드$', '^download$',
  '^스크린샷.*', '^screenshot.*', '^screen shot.*',   // 스크린샷 + 날짜·시간
  '^clipboard.*', '^pasted[\\s_-]*image.*',
].join('|'), 'i');

const stripExt = (name) => String(name || '').replace(/\.[^.]+$/, '').trim();

/** 이 이름이 OS·앱이 지어준 임시 이름인가(= 셀러에게 아무 정보도 주지 않는 이름). */
export function isMeaninglessPhotoName(name) {
  const base = stripExt(name);
  if (!base) return true;                       // 이름 없음도 같은 취급
  return MEANINGLESS_NAME.test(base);
}

/** 캡션 한 줄. 임시 이름이면 `앞면 사진 1` 처럼 슬롯 라벨+순번으로 바꾼다.
 *  slotLabel 이 없으면(레거시 단일 그리드) '사진 1' 로 떨어진다. */
export function photoCaption(name, slotLabel, index = 0) {
  if (!isMeaninglessPhotoName(name)) return String(name);
  const label = String(slotLabel || '').trim();
  const order = Number.isFinite(index) ? Math.max(0, Math.trunc(index)) + 1 : 1;
  return label ? `${label} 사진 ${order}` : `사진 ${order}`;
}
