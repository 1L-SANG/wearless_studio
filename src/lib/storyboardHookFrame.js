/* =============================================================
   lib/storyboardHookFrame — 후킹 '첫 화면 스타일' 계약 (P1: 읽기·사양)
   정본: docs/superpowers/specs/2026-08-14-hooking-first-screen-design.md

   스타일은 틀(슬롯 수·샷 역할)만 정한다 — 각 슬롯의 그림은 그 컷의
   생성예시가 정본이고, 고정 여부는 기존 exampleSelectionOrigin('user')이 담당한다.
   저장 정본은 개별 컷: 프레임은 후킹 섹션 안의 연속 run 에 붙는
   hookFrameId/hookStyle/hookFrameVersion 참조 표식일 뿐, 합성 이미지를 저장하지 않는다.
   ============================================================= */

import { clearExampleSelection } from './storyboardExampleStaleness.js';
import { uid } from './ids.js';

export const HOOK_STYLES = Object.freeze(['signature', 'pair', 'moodGrid']);
export const DEFAULT_HOOK_STYLE = 'signature';
export const HOOK_FRAME_VERSION = 1;

export const HOOK_STYLE_LABELS = Object.freeze({
  signature: '시그니처 컷',
  pair: '두컷 프레임',
  moodGrid: '네컷 프레임',
});

// 무드 그리드 내용은 선택지 없이 자동 — 색상 2개↑면 색상별 1컷, 단색이면 같은 색 4컷
// (2026-08-14 오너 확정). colors 는 product.colors 형태(배열)를 그대로 받는다.
export function moodGridContent(colors) {
  return (Array.isArray(colors) ? colors : []).length >= 2 ? 'byColor' : 'byCuts';
}

const isHookFrameBlock = (block) => (
  !!block
  && typeof block.hookFrameId === 'string'
  && HOOK_STYLES.includes(block.hookStyle)
);

// 후킹 섹션 블록들에서 프레임을 파생한다 — 첫 연속 run 만 프레임으로 인정.
// (섹션·행과 같은 run 원칙: 흩어진 표식은 프레임이 아니라 손상으로 본다.)
export function deriveHookFrame(blocks) {
  const list = Array.isArray(blocks) ? blocks : [];
  const start = list.findIndex(isHookFrameBlock);
  if (start < 0) return null;
  const frameId = list[start].hookFrameId;
  const style = list[start].hookStyle;
  const slotIds = [];
  let end = start;
  while (end < list.length && isHookFrameBlock(list[end])
    && list[end].hookFrameId === frameId && list[end].hookStyle === style) {
    slotIds.push(list[end].id);
    end += 1;
  }
  // run 밖(비연속)에 같은 frameId 가 남아 있으면 손상 — 프레임으로 취급하지 않는다.
  const strayExists = list.some((block, index) => (
    (index < start || index >= end) && isHookFrameBlock(block) && block.hookFrameId === frameId
  ));
  if (strayExists) return null;
  return {
    style,
    frameId,
    slotIds,
    version: list[start].hookFrameVersion ?? HOOK_FRAME_VERSION,
  };
}

/* 스타일별 슬롯 사양 — 틀만 정한다. cutType/shot 은 후킹 섹션 허용 컷(스타일링·호리존) 안.
   signature 는 기본 구성의 benefit(호리존 미디움)을, pair 는 hero(스타일링 풀)+benefit 을
   그대로 재사용하도록 사양을 맞춰 컷 수·크레딧을 바꾸지 않는다(스펙 §2). */
export function hookSlotPlan(style, { colors } = {}) {
  if (style === 'signature') {
    return [{ role: 'signature', cutType: 'horizon', shot: 'medium', titleOverlay: true }];
  }
  if (style === 'pair') {
    // '두컷 프레임' = 의류 위주 미디움샷 2장(오너 카피 확정) — hero는 샷만 미디움으로 전환해 재사용.
    return [
      { role: 'left', cutType: 'styling', shot: 'medium' },
      { role: 'right', cutType: 'horizon', shot: 'medium' },
    ];
  }
  if (style === 'moodGrid') {
    // '네컷 프레임' — 이름 그대로 항상 4칸.
    if (moodGridContent(colors) === 'byColor') {
      // 등록 색상이 1번씩(같은 컷 종류·샷 — 비교 가능성 유지, 컬러웨이 페어와 동일 원리),
      // 색상이 2~3개면 남는 칸은 기준색 추가 컷으로 채운다(2026-08-14 확정).
      const base = colors.find((color) => color.isBase) || colors[0];
      const colorSlots = colors.slice(0, 4).map((color) => ({
        role: `color:${color.id}`, cutType: 'horizon', shot: 'medium', colorId: color.id,
      }));
      const fillCuts = [
        { cutType: 'styling', shot: 'full' },
        { cutType: 'styling', shot: 'medium' },
        { cutType: 'horizon', shot: 'full' },
      ];
      const fills = fillCuts.slice(0, 4 - colorSlots.length).map((cut, index) => ({
        role: `fill:${index + 1}`, ...cut, colorId: base.id,
      }));
      return [...colorSlots, ...fills];
    }
    // 단색 4컷 — 같은 색, 다른 네 컷(후킹 허용 컷 안에서 실루엣·디테일 커버)
    return [
      { role: 'grid:1', cutType: 'styling', shot: 'full' },
      { role: 'grid:2', cutType: 'horizon', shot: 'medium' },
      { role: 'grid:3', cutType: 'styling', shot: 'medium' },
      { role: 'grid:4', cutType: 'horizon', shot: 'full' },
    ];
  }
  throw new Error(`unknown_hook_style:${style}`);
}

// 프레임 밖 후킹 컷(구성 미사용) — 삭제하지 않고 프레임 뒤에 일반 컷으로 이어진다(스펙 §2).
export function unslottedHookBlocks(blocks, frame) {
  const list = Array.isArray(blocks) ? blocks : [];
  if (!frame) return list;
  const slotted = new Set(frame.slotIds);
  return list.filter((block) => block && !slotted.has(block.id));
}

/* ---------- P2: 스타일 전환 엔진 ---------- */

const HOOK_FIELDS = ['hookFrameId', 'hookStyle', 'hookFrameVersion', 'hookTitleOverlay', 'hookSlotRole'];

const isHookingAiBlock = (block) => (
  !!block && block.sectionRole === 'hooking' && block.source !== 'mine'
);

// 이전 프레임·오프닝 행 소유 필드를 걷어낸 사본 — 프레임을 다시 깔기 전의 중립 상태.
function clearFrameOwnership(block) {
  const hadFrame = isHookFrameBlock(block);
  const openingRow = typeof block.layoutRowId === 'string' && block.layoutRowId.startsWith('row__opening__');
  if (!hadFrame && !openingRow) return block;
  const next = { ...block };
  for (const field of HOOK_FIELDS) delete next[field];
  delete next.layoutRowId;
  delete next.layoutRowVersion;
  delete next.sectionLayout;
  return next;
}

// 슬롯 사양에 맞춰 컷의 틀(컷 종류·샷·색상)을 조정한 사본. 틀이 바뀌면 물고 있던
// 생성예시 선택은 더는 유효하지 않으므로 함께 걷어낸다(재배정기가 새로 채운다).
function fitBlockToSlot(block, slot) {
  const reshaped = block.cutType !== slot.cutType
    || block.shot !== slot.shot
    || (slot.colorId != null && block.colorId !== slot.colorId);
  let next = { ...block, cutType: slot.cutType, shot: slot.shot };
  if (slot.colorId != null) next.colorId = slot.colorId;
  if (reshaped && next.exampleId) next = clearExampleSelection(next);
  if (reshaped) {
    // 방향·포즈 등 세부는 보수적으로 초기화하지 않는다 — 보드 정규화가 역할을 재산출하고,
    // 컷 종류가 바뀐 경우의 잔여 설정은 인스펙터 규칙이 이미 흡수한다.
    next.exampleSelectionOrigin = next.exampleId ? next.exampleSelectionOrigin : null;
  }
  return next;
}

/* 후킹 섹션에 스타일을 적용해 새 보드를 돌려준다 — 저장 정본은 개별 컷이므로
   합성은 없고, 슬롯 선발·틀 조정·프레임/행 표식·순서 재배치만 한다.
   - 슬롯 선발: ① 컷 종류+샷(+색상) 정확 일치 → ② 컷 종류 일치(샷 전환) → ③ 아무 컷(틀 전환).
   - 부족분은 createBlock({ cutType, shot, colorId }) 팩토리로 만든다(moodGrid 전용).
   - 슬롯에서 빠진 컷은 삭제하지 않고 프레임 뒤에 일반 컷으로 남긴다(오너 확정). */
export function applyHookStyle(blocks, style, {
  colors = [], createBlock = null, frameId = null,
} = {}) {
  if (!HOOK_STYLES.includes(style)) throw new Error(`unknown_hook_style:${style}`);
  const list = Array.isArray(blocks) ? blocks : [];
  const start = list.findIndex(isHookingAiBlock);
  if (start < 0) return list;
  let end = start;
  while (end < list.length && list[end]?.sectionRole === 'hooking') end += 1;

  const previous = deriveHookFrame(list.slice(start, end));
  const section = list.slice(start, end).map(clearFrameOwnership);
  const plan = hookSlotPlan(style, { colors });
  const nextFrameId = frameId || previous?.frameId || `hookframe__${uid('hf')}`;

  const pool = section.filter(isHookingAiBlock);
  const used = new Set();
  const pick = (predicate) => {
    const found = pool.find((block) => !used.has(block.id) && predicate(block));
    if (found) used.add(found.id);
    return found || null;
  };
  const slotBlocks = plan.map((slot) => {
    const base = pick((block) => (
      block.cutType === slot.cutType && block.shot === slot.shot
      && (slot.colorId == null || block.colorId === slot.colorId)
    ))
      || pick((block) => block.cutType === slot.cutType)
      || pick(() => true)
      || (createBlock ? createBlock({ cutType: slot.cutType, shot: slot.shot, colorId: slot.colorId }) : null);
    if (!base) throw new Error('hook_frame_slot_underflow');
    const fitted = fitBlockToSlot(base, slot);
    const framed = {
      ...fitted,
      hookFrameId: nextFrameId,
      hookStyle: style,
      hookFrameVersion: HOOK_FRAME_VERSION,
      hookSlotRole: slot.role,
    };
    if (slot.titleOverlay) framed.hookTitleOverlay = true;
    else delete framed.hookTitleOverlay;
    return framed;
  });

  // pair = 1행, moodGrid = 2행(위 2·아래 2) — 기존 영속 행 계약(layoutRow) 재사용.
  if (style === 'pair' || style === 'moodGrid') {
    slotBlocks.forEach((block, index) => {
      block.layoutRowId = `row__${nextFrameId}__${Math.floor(index / 2) + 1}`;
      block.layoutRowVersion = 1;
      block.sectionLayout = 'twoColumn';
    });
  }

  const rest = section.filter((block) => !used.has(block.id));
  return [
    ...list.slice(0, start),
    ...slotBlocks,
    ...rest,
    ...list.slice(end),
  ];
}

/* 저장된 보드의 레거시 승격 — 진입 시 1회.
   - 이미 프레임이 있으면 그대로.
   - 기존 '오프닝 2단 행'(hero+benefit 미디움 2장)은 두컷 프레임의 전신 — pair 로 표식만 승격.
   - 그 밖의 구형 보드는 건드리지 않는다(프레임 없음 = UI 가 기존 스택으로 폴백). */
export function adoptHookFrame(blocks) {
  const list = Array.isArray(blocks) ? blocks : [];
  const hooking = list.filter((block) => block?.sectionRole === 'hooking');
  if (deriveHookFrame(hooking)) return { blocks: list, changed: false };
  const [first, second] = hooking;
  const openingPair = !!first && !!second
    && first.source !== 'mine' && second.source !== 'mine'
    && first.contentRole === 'hero' && second.contentRole === 'benefit'
    && first.shot === 'medium' && second.shot === 'medium'
    && !!first.layoutRowId && first.layoutRowId === second.layoutRowId;
  if (!openingPair) return { blocks: list, changed: false };
  const frameId = `hookframe__${first.layoutRowId}`;
  const stamped = new Map([
    [first.id, { ...first, hookFrameId: frameId, hookStyle: 'pair', hookFrameVersion: HOOK_FRAME_VERSION, hookSlotRole: 'left' }],
    [second.id, { ...second, hookFrameId: frameId, hookStyle: 'pair', hookFrameVersion: HOOK_FRAME_VERSION, hookSlotRole: 'right' }],
  ]);
  return {
    blocks: list.map((block) => stamped.get(block?.id) || block),
    changed: true,
  };
}
