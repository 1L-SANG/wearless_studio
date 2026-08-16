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
  pair: '두 컷 구성',
  moodGrid: '네 컷 구성',
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

/* 후보 목록에서 "발행된 조합"만 골라 원하는 수만큼 서로 다른 컷을 뽑는다.
   isCutAvailable(cutType, shot)가 없으면 후보 순서 그대로(시드·지문 경로 — 결정적).
   전 조합이 닫힌 극단에서만 원래 후보로 돌아가고, 그래도 모자라면 중복을 허용한다
   (빈 칸을 만드는 것보다 낫다 — 2026-08-14 '이미지 사라짐' 사고의 원인 방지). */
const slotCutKey = (cut) => `${cut.cutType}|${cut.shot}|${cut.direction || 'front'}`;
function resolveSlotCuts(candidates, count, isCutAvailable) {
  const available = typeof isCutAvailable === 'function'
    ? candidates.filter((cut) => isCutAvailable(cut.cutType, cut.shot))
    : candidates;
  const pool = available.length ? available : candidates;
  const picked = [];
  const seen = new Set();
  for (const cut of pool) {
    if (picked.length >= count) break;
    if (seen.has(slotCutKey(cut))) continue;
    seen.add(slotCutKey(cut));
    picked.push(cut);
  }
  for (let index = 0; picked.length < count; index += 1) {
    picked.push(pool[index % pool.length]);
  }
  return picked;
}

/* 스타일별 슬롯 사양 — 틀만 정한다. cutType/shot(/direction) 은 후킹 섹션 허용 컷
   (스타일링·호리존) 안에서, 발행된 조합을 우선 선택한다(isCutAvailable 폴백 체인). */
export function hookSlotPlan(style, { colors, isCutAvailable } = {}) {
  if (style === 'signature') {
    const [cut] = resolveSlotCuts([
      { cutType: 'horizon', shot: 'medium' },
      { cutType: 'styling', shot: 'medium' },
    ], 1, isCutAvailable);
    return [{ role: 'signature', ...cut, titleOverlay: true }];
  }
  if (style === 'pair') {
    // '두 컷 구성' = 의류 위주 미디움샷 2장(오너 카피 확정) — 기본 왼쪽 스타일링·오른쪽 호리존.
    const cuts = resolveSlotCuts([
      { cutType: 'styling', shot: 'medium' },
      { cutType: 'horizon', shot: 'medium' },
      { cutType: 'styling', shot: 'full' },
      { cutType: 'styling', shot: 'medium', direction: 'back' },
    ], 2, isCutAvailable);
    return [
      { role: 'left', ...cuts[0] },
      { role: 'right', ...cuts[1] },
    ];
  }
  if (style === 'moodGrid') {
    // '네 컷 구성' — 이름 그대로 항상 4칸.
    if (moodGridContent(colors) === 'byColor') {
      // 등록 색상이 1번씩(같은 컷 종류·샷 — 비교 가능성 유지, 컬러웨이 페어와 동일 원리),
      // 색상이 2~3개면 남는 칸은 기준색 추가 컷으로 채운다(2026-08-14 확정).
      const base = colors.find((color) => color.isBase) || colors[0];
      const [colorCut] = resolveSlotCuts([
        { cutType: 'horizon', shot: 'medium' },
        { cutType: 'styling', shot: 'medium' },
      ], 1, isCutAvailable);
      const colorSlots = colors.slice(0, 4).map((color) => ({
        role: `color:${color.id}`, ...colorCut, colorId: color.id,
      }));
      const fills = resolveSlotCuts([
        { cutType: 'styling', shot: 'full' },
        { cutType: 'styling', shot: 'medium' },
        { cutType: 'styling', shot: 'full', direction: 'back' },
      ], 4 - colorSlots.length, isCutAvailable).map((cut, index) => ({
        role: `fill:${index + 1}`, ...cut, colorId: base.id,
      }));
      return [...colorSlots, ...fills];
    }
    // 단색 4컷 — 같은 색, 서로 다른 네 컷(후킹 허용 컷 안에서 실루엣·디테일 커버).
    // 호리존 풀샷은 낱장 발행이 닫힌 카테고리가 많아 후보 뒤로 미룬다 — 발행 조합 우선.
    return resolveSlotCuts([
      { cutType: 'styling', shot: 'full' },
      { cutType: 'horizon', shot: 'medium' },
      { cutType: 'styling', shot: 'medium' },
      { cutType: 'styling', shot: 'full', direction: 'back' },
      { cutType: 'horizon', shot: 'medium', direction: 'back' },
      { cutType: 'styling', shot: 'medium', direction: 'back' },
      { cutType: 'horizon', shot: 'full' },
    ], 4, isCutAvailable).map((cut, index) => ({ role: `grid:${index + 1}`, ...cut }));
  }
  throw new Error(`unknown_hook_style:${style}`);
}

/* ---------- P2: 스타일 전환 엔진 ---------- */

const HOOK_FIELDS = ['hookFrameId', 'hookStyle', 'hookFrameVersion', 'hookTitleOverlay', 'hookSlotRole'];

// 프레임 표식만 걷어낸 사본 — 컷 복제(복제본=구성 미사용)와 슬롯 이탈(컷 종류·샷·색 변경)이 쓴다.
export function stripHookFrameFields(block) {
  if (!block || !HOOK_FIELDS.some((field) => field in block)) return block;
  const next = { ...block };
  for (const field of HOOK_FIELDS) delete next[field];
  return next;
}

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

// 슬롯 사양에 맞춰 컷의 틀(컷 종류·샷·방향·색상)을 조정한 사본. 틀이 바뀌면 물고 있던
// 생성예시 선택은 더는 유효하지 않으므로 함께 걷어낸다(재배정기가 새로 채운다).
function fitBlockToSlot(block, slot) {
  const reshaped = block.cutType !== slot.cutType
    || block.shot !== slot.shot
    || (slot.direction != null && block.direction !== slot.direction)
    || (slot.colorId != null && block.colorId !== slot.colorId);
  let next = { ...block, cutType: slot.cutType, shot: slot.shot };
  if (slot.direction != null) next.direction = slot.direction;
  if (slot.colorId != null) next.colorId = slot.colorId;
  if (reshaped && next.exampleId) next = clearExampleSelection(next);
  if (reshaped) {
    // 방향 외 포즈 등 세부는 보수적으로 초기화하지 않는다 — 보드 정규화가 역할을 재산출하고,
    // 컷 종류가 바뀐 경우의 잔여 설정은 인스펙터 규칙이 이미 흡수한다.
    next.exampleSelectionOrigin = next.exampleId ? next.exampleSelectionOrigin : null;
  }
  return next;
}

/* 후킹 섹션에 스타일을 적용해 새 보드를 돌려준다 — 저장 정본은 개별 컷이므로
   합성은 없고, 슬롯 선발·틀 조정·프레임/행 표식·순서 재배치만 한다.
   - 슬롯 선발: ① 컷 종류+샷(+색상) 정확 일치 → ② 컷 종류 일치(샷 전환) → ③ 아무 컷(틀 전환).
   - 부족분은 createBlock({ cutType, shot, direction, colorId }) 팩토리로 만든다.
   - 슬롯에서 빠진 AI 컷은 **삭제**한다 — 후킹 섹션은 항상 그 스타일에 맞는 컷 구성·개수만
     갖는다(2026-08-14 오너 정정: "잔여 컷 남기지 말고 그때그때 다르게 배치").
     셀러가 직접 올린 '내 사진'만 프레임 뒤에 남긴다(오너 확정 — 업로드 자산 보호). */
export function applyHookStyle(blocks, style, {
  colors = [], createBlock = null, frameId = null, isCutAvailable = null,
} = {}) {
  if (!HOOK_STYLES.includes(style)) throw new Error(`unknown_hook_style:${style}`);
  const list = Array.isArray(blocks) ? blocks : [];
  const start = list.findIndex(isHookingAiBlock);
  if (start < 0) return list;
  let end = start;
  while (end < list.length && list[end]?.sectionRole === 'hooking') end += 1;

  const previous = deriveHookFrame(list.slice(start, end));
  /* 전환 전에 '첫 화면 구성 1묶음'이던 컷들. 셀러가 따로 추가한 개별컷은 여기에 없고,
     스타일 전환에 관여하지 않는다 — 슬롯으로 흡수되지도, 정리 대상이 되지도 않는다
     (2026-08-16 오너: "구성 바꿨더니 추가한 컷이 사라진다"). 묶음이 아예 없던 보드는
     예전대로 후킹 AI 컷 전체를 후보로 써서 프레임을 복구한다. */
  const previousSlotIds = new Set(
    list.slice(start, end).filter(isHookFrameBlock).map((block) => block.id),
  );
  const framedBefore = previousSlotIds.size > 0;
  const section = list.slice(start, end).map(clearFrameOwnership);
  const plan = hookSlotPlan(style, { colors, isCutAvailable });
  const nextFrameId = frameId || previous?.frameId || `hookframe__${uid('hf')}`;

  const pool = section.filter((block) => isHookingAiBlock(block)
    && (!framedBefore || previousSlotIds.has(block.id)));
  const used = new Set();
  // 사용자가 직접 고른 예시(origin 'user')는 계약상 고정이다 — 틀이 같은 슬롯에는 그 컷을
  // 우선 배치해 선택을 보존하고, 틀을 바꿔야 하는 자리에는 auto 컷을 먼저 소모해
  // 고정 선택이 불가피할 때만 초기화되게 한다(스펙 §2 핀 규칙, Codex 리뷰 #2).
  const pick = (predicate, { preferPinned }) => {
    const candidates = pool.filter((block) => !used.has(block.id) && predicate(block));
    if (!candidates.length) return null;
    const pinned = candidates.find((block) => block.exampleSelectionOrigin === 'user');
    const auto = candidates.find((block) => block.exampleSelectionOrigin !== 'user');
    const found = (preferPinned ? (pinned || auto) : (auto || pinned)) || candidates[0];
    used.add(found.id);
    return found;
  };
  const slotBlocks = plan.map((slot) => {
    const base = pick((block) => (
      block.cutType === slot.cutType && block.shot === slot.shot
      && (slot.direction == null || block.direction === slot.direction)
      && (slot.colorId == null || block.colorId === slot.colorId)
    ), { preferPinned: true })
      || pick((block) => block.cutType === slot.cutType, { preferPinned: false })
      || pick(() => true, { preferPinned: false })
      || (createBlock ? createBlock({
        cutType: slot.cutType, shot: slot.shot, direction: slot.direction, colorId: slot.colorId,
      }) : null);
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

  // pair = 2칸 1행, moodGrid = 4칸 1행(grid2x2) — 기존 영속 행 계약(layoutRow) 재사용.
  // 네 컷을 2행으로 쪼개면 콘티에서도 에디터에서도 두 덩어리로 읽힌다. 오너 확정(8/16)은
  // "네 컷이 하나로 합쳐 보이게" — 한 행이어야 콘티의 붙은 2×2와 에디터 조립이 같은 뜻이 된다.
  if (style === 'pair' || style === 'moodGrid') {
    const layout = style === 'moodGrid' ? 'grid2x2' : 'twoColumn';
    slotBlocks.forEach((block) => {
      block.layoutRowId = `row__${nextFrameId}__1`;
      block.layoutRowVersion = 1;
      block.sectionLayout = layout;
    });
  }

  // 슬롯에 안 쓰인 '구성 컷'만 버린다(스타일 = 정확한 컷 구성).
  // 내 사진과 구성 밖 개별컷은 그대로 뒤에 남는다(2026-08-16 오너).
  const rest = section.filter((block) => !used.has(block.id)
    && (!isHookingAiBlock(block) || (framedBefore && !previousSlotIds.has(block.id))));
  return [
    ...list.slice(0, start),
    ...slotBlocks,
    ...rest,
    ...list.slice(end),
  ];
}

/* 네 컷 구성의 행 승격 — 2026-08-16 이전 저장본은 2칸 2행(twoColumn)이라, 콘티는 붙은
   2×2로 보이는데(렌더가 프레임 표식으로 이어 붙인다) 에디터·발행 조립은 두 덩어리로
   갈렸다. 진입 시 한 번 4칸 1행(grid2x2)으로 올려 세 곳이 같은 뜻을 보게 한다.
   컷 자체는 손대지 않는다 — 바꾸는 건 '어디까지가 한 행인가' 표식뿐이다. */
function upgradeMoodGridRow(list, frame) {
  if (frame.style !== 'moodGrid' || frame.slotIds.length !== 4) return { blocks: list, changed: false };
  const slots = new Set(frame.slotIds);
  const rowId = `row__${frame.frameId}__1`;
  const stale = list.some((block) => slots.has(block?.id)
    && (block.layoutRowId !== rowId || block.sectionLayout !== 'grid2x2'));
  if (!stale) return { blocks: list, changed: false };
  return {
    blocks: list.map((block) => (slots.has(block?.id)
      ? { ...block, layoutRowId: rowId, layoutRowVersion: 1, sectionLayout: 'grid2x2' }
      : block)),
    changed: true,
  };
}

/* 저장된 보드의 레거시 승격 — 진입 시 1회.
   - 이미 프레임이 있으면 그대로.
   - 기존 '오프닝 2단 행'(hero+benefit 미디움 2장)은 두 컷 구성의 전신 — pair 로 표식만 승격.
   - 그 밖의 구형 보드는 건드리지 않는다(프레임 없음 = UI 가 기존 스택으로 폴백). */
export function adoptHookFrame(blocks) {
  const list = Array.isArray(blocks) ? blocks : [];
  const hooking = list.filter((block) => block?.sectionRole === 'hooking');
  const existing = deriveHookFrame(hooking);
  if (existing) return upgradeMoodGridRow(list, existing);
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
