/* =============================================================
   features/editor/presets/textPresets.js — 텍스트 프리셋 4종
   값의 정본은 docs/superpowers/specs/2026-08-04-editor-text-slots-design.md
   (셀러 텍스트 568건 실측 타이포 위계). 여기서 임의로 바꾸면 나중에 자동
   배치되는 텍스트 자리와 셀러가 직접 넣는 텍스트의 결이 갈라진다.

   요소는 빈 텍스트 + textSizing 'auto'로 만든다(4e53fe7의 즉시 입력 UX).
   샘플 문구를 el.text에 넣지 않으므로 안내 문구가 정본으로 둔갑할 경로가 없다.
   `sample`은 패널 목록에 보여줄 표시용 글자일 뿐 콘텐츠가 아니다.

   node --test 에서 직접 import 되므로 Vite 별칭(@/) 대신 상대 경로만 쓴다.
   ============================================================= */
import { uid } from '../../../lib/ids.js';

/* 위계를 만드는 속성만 프리셋이 소유한다. 폰트·정렬·기울임 등은 셀러 몫이라
   빠른 스타일 전환에서도 건드리지 않는다. */
const HIERARCHY_PROPS = ['size', 'weight', 'color', 'lineHeight', 'tracking'];

export const TEXT_PRESETS = [
  { key: 'headline', label: '큰 제목', hint: '첫 화면 한 방 문구', sample: '큰 제목',
    style: { size: 40, weight: 600, color: '#0e0d14' } },
  { key: 'subtitle', label: '소제목', hint: '사진 아래 한 줄', sample: '소제목',
    style: { size: 26, weight: 600, color: '#0e0d14' } },
  { key: 'body', label: '설명글', hint: '긴 설명 문단', sample: '설명글',
    style: { size: 17, weight: 400, color: '#6b6b73', lineHeight: 26 } },
  { key: 'tag', label: '꼬리표', hint: '짧은 라벨', sample: 'POINT 01',
    style: { size: 19, weight: 700, color: '#9a9aa2', tracking: 3 } },
];

/* 소제목이 기본 — 실측에서 셀러 텍스트의 65.5%가 한 줄 소제목이었다(중앙값 17자). */
export const DEFAULT_TEXT_PRESET = 'subtitle';

const presetOf = (key) =>
  TEXT_PRESETS.find((p) => p.key === key) || TEXT_PRESETS.find((p) => p.key === DEFAULT_TEXT_PRESET);

/** 프리셋 키 → 새 텍스트 요소. x=60 은 이미지 기둥 왼끝 정렬(캔버스 1000px 기준).
    폭·높이는 자동 모드가 타이핑에 맞춰 다시 재므로 초기값은 커서 자리만 잡는다. */
export function buildTextPresetElement(key) {
  const p = presetOf(key);
  const h = p.style.lineHeight || Math.round(p.style.size * 1.4);
  return { id: uid('el'), type: 'text', x: 60, y: 80, w: 12, h, text: '', textSizing: 'auto', style: { font: 'Pretendard', ...p.style } };
}

/** 선택된 텍스트에 프리셋을 입히는 스타일 패치. 행간·자간은 프리셋에 없으면 0으로
    리셋한다 — 설명글(행간 26)에서 큰 제목(40px)으로 바꿀 때 행간이 남으면 글줄이
    겹친다. 렌더러는 lineHeight 0(falsy)을 자동 1.4배로 처리한다(Editor.jsx). */
export function quickStylePatch(key) {
  const p = presetOf(key);
  return { size: p.style.size, weight: p.style.weight, color: p.style.color,
    lineHeight: p.style.lineHeight ?? 0, tracking: p.style.tracking ?? 0 };
}

/** 현재 스타일이 어느 프리셋 상태인지. 위계 속성이 전부 일치할 때만 그 키, 아니면 null.
    셀러가 값을 하나라도 바꿨으면 어떤 칩도 켜지 않는 게 정직하다. */
export function activeTextPreset(style) {
  if (!style) return null;
  const found = TEXT_PRESETS.find((p) => HIERARCHY_PROPS.every((prop) => {
    const preset = p.style[prop] ?? 0;
    const current = style[prop] ?? 0;
    return preset === current;
  }));
  return found ? found.key : null;
}
