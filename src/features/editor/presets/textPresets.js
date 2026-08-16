/* =============================================================
   features/editor/presets/textPresets.js — 텍스트 프리셋 4종
   값의 정본은 docs/superpowers/specs/2026-08-04-editor-text-slots-design.md
   (셀러 텍스트 568건 실측 타이포 위계). 여기서 임의로 바꾸면 텍스트 자리
   슬롯 구현(대기 중)이 같은 값을 읽을 때 결이 갈라진다 — 자동 배치 쪽
   (editorWaitSkeleton·page_assembler)은 아직 이 값을 안 읽는다.

   요소는 빈 텍스트 + textSizing 'auto'로 만든다(4e53fe7의 즉시 입력 UX).
   샘플 문구를 el.text에 넣지 않으므로 안내 문구가 정본으로 둔갑할 경로가 없다.
   `sample`·`previewSize`는 패널 목록 표시용일 뿐 콘텐츠가 아니다.

   node --test 에서 직접 import 되므로 Vite 별칭(@/) 대신 상대 경로만 쓴다.
   ============================================================= */
import { uid } from '../../../lib/ids.js';

/* 위계를 만드는 속성만 프리셋이 소유한다. 폰트·정렬·기울임 등은 셀러 몫이라
   빠른 스타일 전환에서도 건드리지 않는다. */
const HIERARCHY_PROPS = ['size', 'weight', 'color', 'lineHeight', 'tracking'];

/* 에디터 텍스트 회색의 정본 — 자동 배치 카피(mock/db.js·editorWaitSkeleton.js·
   server/page_assembler.py)와 정보 블록(infoPresets.js)도 같은 값을 쓴다.
   서버는 JS를 import할 수 없어 값을 복사해 두므로, 바꿀 땐 함께 바꾼다. */
export const TEXT_INK = '#0e0d14';
export const TEXT_MUTED = '#6b6b73';
export const TEXT_FAINT = '#9a9aa2';

/* previewSize는 패널 축소판 크기 — 실제 크기 순서(40>26>19>17)를 보존해야
   목록에서 "꼬리표가 설명글보다 크다"는 사실이 왜곡되지 않는다. */
/* 꼬리표(POINT 01)는 뺐다(오너 8/16) — 실측에서도 11%뿐이고, 같은 라벨은 '특징 포인트'
   정보 블록이 이미 제공한다. 남은 셋은 실측 상위 3종 그대로다:
   소제목 372건(65.5%)·문단 75건(13.2%)이 본문 텍스트의 대부분이고, 큰 제목은 첫 화면의
   한 방 문구다(§2-A). 크기도 스펙 값 그대로 — 40/26/17px. */
export const TEXT_PRESETS = [
  { key: 'headline', label: '큰 제목', hint: '첫 화면 한 방 문구', previewSize: 22,
    style: { size: 40, weight: 600, color: TEXT_INK } },
  { key: 'subtitle', label: '소제목', hint: '사진 아래 한 줄', previewSize: 17,
    style: { size: 26, weight: 600, color: TEXT_INK } },
  { key: 'body', label: '설명글', hint: '긴 설명 문단', previewSize: 13,
    style: { size: 17, weight: 400, color: TEXT_MUTED, lineHeight: 26 } },
];

/* 소제목이 기본 — 실측에서 셀러 텍스트의 65.5%가 한 줄 소제목이었다(중앙값 17자). */
export const DEFAULT_TEXT_PRESET = 'subtitle';

/** 키 → 프리셋. 모르는 키·미지정은 기본 프리셋 — 요소 생성과 토스트 라벨이
    반드시 이 하나의 폴백을 공유해야 "만든 것"과 "말한 것"이 안 갈라진다. */
export function textPresetOf(key) {
  return TEXT_PRESETS.find((p) => p.key === key) || TEXT_PRESETS.find((p) => p.key === DEFAULT_TEXT_PRESET);
}

/** 프리셋 키 → 새 텍스트 요소. x=60 은 이미지 기둥 왼끝 정렬(캔버스 1000px 기준).
    w=12 는 포인트 텍스트의 캐럿 씨앗값(previewAutoTextSize 하한과 짝) — 키우면
    빈 상자가 넓게 그려진다. */
export function buildTextPresetElement(key) {
  const p = textPresetOf(key);
  const h = p.style.lineHeight || Math.round(p.style.size * 1.4);
  return { id: uid('el'), type: 'text', x: 60, y: 80, w: 12, h, text: '', textSizing: 'auto', style: { font: 'Pretendard', ...p.style } };
}

/** 끌어다 놓은 자리 → 새 텍스트 요소의 좌표(블록 기준). 요소는 캐럿 씨앗(w=12)이라
    포인터가 가리킨 곳이 글자가 시작될 자리다: x는 포인터 그대로, y는 글줄 높이의
    절반만 올려 포인터가 줄 한가운데 오게 한다. 블록 밖으로는 못 나간다 — 경계 밖
    요소는 화면에 아예 안 보여서 "놨는데 아무 일도 없다"로 보이기 때문(오너 8/16). */
export function textPresetDropPlacement({ x, y, w = 12, h = 24, blockW = 0, blockH = 0 }) {
  const clamp = (value, max) => Math.round(Math.min(Math.max(0, value), max > 0 ? max : Math.max(0, value)));
  return { x: clamp(x, blockW - w), y: clamp(y - h / 2, blockH - h) };
}

/** 선택된 텍스트에 프리셋을 입히는 스타일 패치. 행간·자간은 프리셋에 없으면
    undefined로 리셋한다 — 설명글(행간 26)에서 큰 제목(40px)으로 바꿀 때 행간이 남으면
    글줄이 겹친다. 0이 아니라 undefined인 이유: 0을 쓰면 "명시적 0"과 "미설정"이 저장
    문서에서 구분 불가능해지고, JSON 직렬화는 undefined를 자연스럽게 떨군다. */
export function quickStylePatch(key) {
  const p = textPresetOf(key);
  return Object.fromEntries(HIERARCHY_PROPS.map((prop) => [prop, p.style[prop]]));
}

/* 실효값 비교 — 렌더러(Editor.jsx)의 기본값과 색상 UI의 대문자 저장
   (normalizeHexColor·hsvToHex)을 흡수한다. 화면에 같게 그려지면 같은 프리셋이다:
   행간 0(자동)과 명시된 size×1.4, '#0e0d14'와 '#0E0D14'는 같은 상태다. */
const effectiveOf = (style, prop) => {
  if (prop === 'weight') return style.weight || 400;
  if (prop === 'color') return String(style.color || TEXT_INK).toLowerCase();
  if (prop === 'lineHeight') return style.lineHeight || Math.round((style.size || 18) * 1.4);
  if (prop === 'tracking') return style.tracking || 0;
  return style[prop];
};

/** 현재 스타일이 어느 프리셋 상태인지. 위계 속성의 실효값이 전부 일치할 때만 그 키,
    아니면 null. 셀러가 값을 실제로 바꿨으면 어떤 칩도 켜지 않는 게 정직하다. */
export function activeTextPreset(style) {
  if (!style) return null;
  const found = TEXT_PRESETS.find((p) => HIERARCHY_PROPS.every(
    (prop) => effectiveOf(p.style, prop) === effectiveOf(style, prop)));
  return found ? found.key : null;
}
