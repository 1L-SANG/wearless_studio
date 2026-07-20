/* =============================================================
   lib/sections — 콘티보드 섹션 파생 유틸 (2026-07 역할 중심 개편)
   섹션은 별도 엔티티가 아니라 블록 필드(sectionId/Title/Layout)의
   "연속 run"으로만 존재한다 — 저장 계약(blocks: list)은 그대로.
   사용자 섹션은 핵심 장점/핏·코디/제품 확인 3개다. 같은 장소 정보는
   핏·코디 안의 배지일 뿐 별도 섹션을 만들지 않는다.
   ============================================================= */
import { uid } from '@/lib/ids.js';
import {
  STORYBOARD_TAXONOMY_VERSION,
  SECTION_ROLES,
  defaultContentRoleForSection,
  contentTitle,
  isContentRole,
  isSectionRole,
  inferContentRole,
  inferSectionRole,
  normalizedRecipePatch,
  sectionRoleForContentRole,
  sectionTitle,
} from '@/lib/storyboardTaxonomy.js';

const SECTION_ORDER = new Map([
  [SECTION_ROLES.BENEFIT, 0],
  [SECTION_ROLES.FIT, 1],
  [SECTION_ROLES.PRODUCT, 2],
]);

/* v2 역할의 공개 파생 함수. source='mine'은 이웃 섹션을 상속한다. */
function sectionKeyOf(b) { return inferSectionRole(b); }
export const titleForKey = (key) => sectionTitle(key);

/* ensureSections(blocks) — v2 역할·레시피를 검증하고 섹션을 부여한다.
   유효한 v2 저장분은 기존 sectionId/레이아웃을 존중한다. 필수 역할이 없거나
   유효하지 않은 입력은 cutType 기반 방어 정규화를 거쳐 정본 순서로 묶고,
   충돌 가능한 섹션 행/레이아웃은 stack으로 초기화한다. */
export function ensureSections(blocks, { hasDetailImage = null } = {}) {
  if (!Array.isArray(blocks)) return blocks;
  const needsNormalization = blocks.some((b) => b.taxonomyVersion !== STORYBOARD_TAXONOMY_VERSION
    || !isSectionRole(b.sectionRole) || !isContentRole(b.contentRole));
  const out = blocks.map((raw) => {
    const b = { ...raw };
    let sectionRole = inferSectionRole(b);
    let contentRole = inferContentRole(b);
    const expectedSectionRole = sectionRoleForContentRole(contentRole);
    if (!isSectionRole(sectionRole)) sectionRole = expectedSectionRole;
    else if (expectedSectionRole && expectedSectionRole !== sectionRole) {
      // 섹션 위치가 정본이다. 섹션과 목적이 어긋난 카드는 해당 섹션의
      // 안전한 기본 목적으로 맞춘다.
      contentRole = defaultContentRoleForSection(sectionRole);
    }
    b.sectionRole = sectionRole;
    const previousRecipe = { cutType: b.cutType, direction: b.direction, shot: b.shot };
    Object.assign(b, normalizedRecipePatch(b, contentRole, { hasDetailImage }));
    if (previousRecipe.cutType !== b.cutType || previousRecipe.direction !== b.direction || previousRecipe.shot !== b.shot) {
      b.exampleId = null;
      b.baseThumb = null;
    }
    b.taxonomyVersion = STORYBOARD_TAXONOMY_VERSION;
    b.title = b.source === 'mine' ? '내 이미지' : contentTitle(b.contentRole);
    // EditorBlock의 kind=sectionRole 동치 외의 kind 토큰은 v2에 속하지 않는다.
    if (!isSectionRole(b.kind)) delete b.kind;
    if (needsNormalization) {
      delete b.sectionId;
      delete b.layoutRowId;
      delete b.layoutRowVersion;
      b.sectionLayout = 'stack';
      b.sectionCustom = false;
    }
    return b;
  });

  // 내 이미지는 먼저 앞 섹션, 맨 앞이면 뒤의 첫 유효 섹션을 상속한다.
  let previousRole = null;
  for (const b of out) {
    if (!isSectionRole(b.sectionRole)) b.sectionRole = previousRole;
    if (isSectionRole(b.sectionRole)) previousRole = b.sectionRole;
  }
  let nextRole = null;
  for (let i = out.length - 1; i >= 0; i -= 1) {
    if (!isSectionRole(out[i].sectionRole)) out[i].sectionRole = nextRole || SECTION_ROLES.BENEFIT;
    if (isSectionRole(out[i].sectionRole)) nextRole = out[i].sectionRole;
  }

  // 정규화가 필요한 입력도 화면과 서버 생성이 같은 순서를 보도록 실제 배열을
  // 안정 정렬한다. 같은 섹션 안의 사용자 순서는 보존한다.
  if (needsNormalization) {
    out.sort((a, b) => (SECTION_ORDER.get(a.sectionRole) ?? 99) - (SECTION_ORDER.get(b.sectionRole) ?? 99));
  }

  let prevKey = null, prevSid = null;
  for (const b of out) {
    // '내 이미지'는 영속 데이터에 잘못된 행 id가 있어도 컴포지트 행에 합류하지 않는다.
    if (b.source === 'mine') delete b.layoutRowId;
    else if (b.layoutRowId && !b.layoutRowVersion) b.layoutRowVersion = 1;
    // 같은 장소 시리즈 컷: 예시 범위가 불일치하면 'pose' 명시 — 서버 규칙의 실체화.
    // 시리즈를 떠나 spaceGroupId 가 풀려도 이 명시값이 남아 화면·생성 결과가 어긋나지 않는다.
    if (b.spaceGroupId && b.exampleId && b.refScope !== 'pose') b.refScope = 'pose';
    const key = b.sectionRole;
    if (b.sectionId && !needsNormalization) {
      b.sectionTitle = titleForKey(key);
      b.sectionLayout = b.sectionLayout || 'stack';
      prevKey = key; prevSid = b.sectionId;
      continue;
    }
    if (key !== prevKey) prevSid = uid('sec');
    b.sectionId = prevSid;
    b.sectionTitle = titleForKey(key);
    b.sectionLayout = b.sectionLayout || 'stack';
    prevKey = key;
  }
  return out;
}

/* deriveSections(blocks) — 연속 run 파생. 메타는 run 첫 블록 기준.
   반환: [{ id, title, layout, custom, samePlace, start, items:[{ b, i }] }] */
export function deriveSections(blocks) {
  const secs = [];
  let cur = null;
  (blocks || []).forEach((b, i) => {
    if (!cur || cur.id !== (b.sectionId || '_none')) {
      cur = {
        id: b.sectionId || '_none',
        title: b.sectionTitle || titleForKey(sectionKeyOf(b)),
        role: b.sectionRole || sectionKeyOf(b),
        layout: b.sectionLayout || 'stack',
        custom: !!b.sectionCustom,
        samePlace: !!b.spaceGroupId,
        spaceGroupId: b.spaceGroupId || null,
        start: i, items: [],
      };
      secs.push(cur);
    }
    cur.custom = cur.custom || !!b.sectionCustom;
    cur.samePlace = cur.samePlace && !!b.spaceGroupId; // 전원이 같은 공간일 때만 배지
    cur.items.push({ b, i });
  });
  return secs;
}

/* adoptSection(blocks, movedId|movedIds) — 이동한 블록(들)이 이웃 run 안/경계에 놓였을 때
   그 run 의 섹션을 채택 + 대상 섹션을 '직접 구성'(sectionCustom) 처리.
   반환: 새 배열 (변경 없으면 동일 참조 유지 아님 — 호출부에서 setBlocks 로 사용) */
export function adoptSection(blocks, movedId, targetSid, targetRole = null) {
  const ids = Array.isArray(movedId) ? movedId : [movedId];
  const moving = new Set(ids.filter(Boolean));
  const i = blocks.findIndex((b) => moving.has(b.id));
  if (i < 0) return blocks;
  const moved = blocks[i];
  const movedBlocks = blocks.filter((b) => moving.has(b.id));
  // 드롭 대상 섹션이 명시되면(드롭라인·덱은 특정 섹션 몸통 안에 있다) 그 섹션이 정답 —
  // 경계 인덱스의 중의성(위 섹션 끝 == 아래 섹션 시작)을 추론 없이 해소한다.
  let host = targetSid ? blocks.find((b) => !moving.has(b.id) && b.sectionId === targetSid) : null;
  if (!host && !targetRole) {
    const prev = blocks.slice(0, i).reverse().find((b) => !moving.has(b.id));
    const next = blocks.slice(i + 1).find((b) => !moving.has(b.id));
    // 폴백(화살표 이동·타깃 불명): 이웃 중 자기 섹션 우선 — 경계로의 "섹션 내 재정렬"이
    // 이웃 섹션 이탈로 오분류되지 않게. 둘 다 아니면 위 이웃 우선.
    host = (prev && prev.sectionId === moved.sectionId) ? prev
      : (next && next.sectionId === moved.sectionId) ? next
        : (prev || next);
  }
  if (!host && isSectionRole(targetRole)) {
    host = {
      sectionId: uid('sec'),
      sectionTitle: sectionTitle(targetRole),
      sectionRole: targetRole,
      sectionLayout: 'stack',
    };
  }
  if (!host) return blocks;
  const sid = host.sectionId;
  const crossedFromSids = new Set(movedBlocks.filter((b) => b.sectionId !== sid).map((b) => b.sectionId));
  return blocks.map((b) => {
    if (moving.has(b.id)) {
      const crossed = b.sectionId !== sid;
      if (!crossed) return b; // 같은 섹션 내 순서 변경 — 소속·공간 유지
      const { layoutRowId: _layoutRowId, ...single } = b;
      const rowVersion = host.layoutRowVersion || b.layoutRowVersion;
      const purposePatch = b.source === 'mine' ? {}
        : {
          ...normalizedRecipePatch(b, defaultContentRoleForSection(host.sectionRole)),
          exampleId: null,
          baseThumb: null,
        };
      return {
        ...single,
        ...purposePatch,
        sectionId: sid,
        sectionTitle: host.sectionTitle,
        sectionRole: host.sectionRole,
        taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
        sectionLayout: host.sectionLayout || 'stack',
        // 시리즈 이탈 시 공간 계약 해제. 시리즈 '가입'은 자동이 아니라 명시 액션으로만.
        spaceGroupId: b.spaceGroupId && b.spaceGroupId === host.spaceGroupId ? b.spaceGroupId : null,
        spaceVariation: b.spaceGroupId && b.spaceGroupId === host.spaceGroupId ? b.spaceVariation : null,
        // 행 전체를 옮겨도 섹션 경계를 넘으면 예전 섹션의 행 계약은 해제한다.
        ...(rowVersion ? { layoutRowVersion: rowVersion } : {}),
        sectionCustom: true,
      };
    }
    // 직접 구성 표시 — 구성원이 바뀐 양쪽(대상·출발) 섹션 모두
    if (b.sectionId === sid || crossedFromSids.has(b.sectionId)) return { ...b, sectionCustom: true };
    return b;
  });
}

/* patchSection(blocks, sectionId, patch) — 섹션 멤버 전체에 메타 patch (레이아웃 변경 등) */
export function patchSection(blocks, sectionId, patch) {
  const changesLayout = Object.prototype.hasOwnProperty.call(patch, 'sectionLayout');
  const out = blocks.map((b) => {
    if (b.sectionId !== sectionId) return b;
    const next = { ...b, ...patch };
    if (changesLayout) {
      delete next.layoutRowId; // 같은 칩 재적용도 기존 행을 폐기하고 순서대로 다시 묶는다.
      // 완성 행이 하나도 없는 신규 섹션도 generator가 레거시 청킹으로 되돌리지 않게 모델 버전을 남긴다.
      if (patch.sectionLayout !== 'stack') next.layoutRowVersion = 1;
    }
    return next;
  });
  if (!changesLayout || patch.sectionLayout === 'stack') return out;

  const size = rowSizeFor(patch.sectionLayout);
  let run = [];
  const flush = () => {
    // 크기대로 청킹하고 2컷 이상인 미완성 꼬리도 한 행으로 둔다. 1컷 꼬리만 싱글이다.
    for (let i = 0; i < run.length; i += size) {
      const end = Math.min(i + size, run.length);
      if (end - i < 2) continue;
      const rowId = uid('row');
      for (let j = i; j < end; j++) out[run[j]] = { ...out[run[j]], layoutRowId: rowId };
    }
    run = [];
  };
  out.forEach((b, i) => {
    if (b.sectionId === sectionId && b.source !== 'mine') run.push(i);
    else flush(); // 내 이미지·다른 섹션은 연속 AI run을 끊는다.
  });
  flush();
  return out;
}

/* normalizeRows(blocks) — 행 계약 위생: "같은 섹션 · 연속 · 2개 이상"이 아닌 행 id는 전부 해제.
   개별 카드 드래그로 행이 쪼개지거나 남의 행 사이에 끼어든 뒤의 잔재(스테일 id 재결합 포함)를 결정적으로 정리. */
export function normalizeRows(blocks) {
  const groups = new Map();
  blocks.forEach((b, i) => {
    if (!b.layoutRowId || b.source === 'mine') return;
    const g = groups.get(b.layoutRowId) || []; g.push(i); groups.set(b.layoutRowId, g);
  });
  const bad = new Set();
  groups.forEach((idxs, rowId) => {
    const contiguous = idxs.every((v, k) => k === 0 || v === idxs[k - 1] + 1);
    const sameSection = idxs.every((v) => blocks[v].sectionId === blocks[idxs[0]].sectionId);
    if (idxs.length < 2 || !contiguous || !sameSection) bad.add(rowId);
  });
  if (!bad.size) return blocks;
  return blocks.map((b) => {
    if (!bad.has(b.layoutRowId)) return b;
    const { layoutRowId: _drop, ...rest } = b; return rest;
  });
}

/* ---- 컷 수 → 제공 그리드 레이아웃 (배타 규칙: 2=2단, 3=3단, 4=2×2, 5+=2단) ---- */
export const gridLayoutForCount = (n) => (n === 2 ? 'twoColumn' : n === 3 ? 'threeColumn' : n === 4 ? 'grid2x2' : n >= 5 ? 'twoColumn' : null);

/* normalizeSectionLayouts(blocks) — 컷 수가 바뀌어 저장된 그리드 레이아웃이 더 이상 제공 대상이
   아니면(배타 규칙 위반) 세로 1열로 강등 + 행 해제. stack/colorCompare 는 손대지 않는다. */
export function normalizeSectionLayouts(blocks) {
  const counts = new Map();
  blocks.forEach((b) => {
    // 컷 수 = '내 이미지' 제외 전부(미확정 placeholder 포함) — 칩 제공·행 묶기(patchSection)와 동일 정의.
    // placeholder 추가로 규칙을 벗어나면 즉시 강등되지만, '이 블록 취소'가 삽입 전 상태를 통짜 복원한다.
    if (b.source !== 'mine') counts.set(b.sectionId, (counts.get(b.sectionId) || 0) + 1);
  });
  let changed = false;
  const out = blocks.map((b) => {
    const lay = b.sectionLayout;
    if (!lay || lay === 'stack' || lay === 'colorCompare') return b;
    if (gridLayoutForCount(counts.get(b.sectionId) || 0) === lay) return b;
    changed = true;
    const { layoutRowId: _drop, ...rest } = b;
    return { ...rest, sectionLayout: 'stack' };
  });
  return changed ? out : blocks;
}

/* normalizeBoard — 구조 변경 후 공통 위생 절차 (행 위생 + 레이아웃 배타 규칙) */
export const normalizeBoard = (blocks) => normalizeSectionLayouts(normalizeRows(blocks));

/* ---- 섹션 레이아웃 행 크기 — 보드 행 모델과 mock 조립기가 공유하는 단일 소스 ---- */
export const LAYOUT_ROW = { stack: 1, twoColumn: 2, threeColumn: 3, grid2x2: 2, colorCompare: 3 };   // 행당 컷 수
export const rowSizeFor = (layout) => LAYOUT_ROW[layout] || 1;
