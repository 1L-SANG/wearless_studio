/* =============================================================
   lib/editorWaitSkeleton — 에디터 통합 대기(editor_wait_dev_spec v2)의 순수 헬퍼.
   콘티 → 스켈레톤 블록(서버 조립 규칙 정렬) → 생성 이벤트 채움 → 완료 병합.
   전부 순수 함수 — Editor/Generating 어디서든 재사용, node 테스트 가능.
   ============================================================= */
import {
  CAPTION_GAP,
  CAPTION_STYLE,
  captionHeight,
} from './editorTextSlots.js';
import { editedTextElement } from '../features/editor/editorTextEdit.js';
import { getBlockRenderHeight } from '../features/editor/editorGeometry.js';
import { resolveTextSpacing } from '../features/editor/textSpacing.js';
import { estimateTextHeight } from './pageTemplates/assemble.js';

const autoBlockHeight = (elements) => Math.max(
  0,
  ...(elements || []).map((element) => (element.y || 0) + (element.h || 0)),
) + 50;

const copyTextPatch = (element, text) => {
  if (element.paragraphIndex != null && !(element.copyRole === 'body' && !element.aiDraft)) {
    const parts = String(text).split(/\n+/).map(part => part.trim()).filter(Boolean);
    text = element.paragraphIndex === 2 ? parts.slice(2).join('\n') : parts[element.paragraphIndex] || '';
  }
  return element.copyRole === 'headline'
    ? { text, textSlotVersion: 1, h: captionHeight(text), style: { ...(element.style || {}), ...CAPTION_STYLE } }
    : { text };
};

const copyIdentity = element => `${element.copyRole}:${element.paragraphIndex ?? ''}:${element.factLabel || ''}:${element.factPart || ''}`;
const templateSnapshot = element => structuredClone(Object.fromEntries(Object.entries(element).filter(([key]) => !key.startsWith('gen'))));
const templateBlockSnapshot = block => Object.fromEntries(Object.entries(block).filter(([key]) => !key.startsWith('gen') && !['elements', 'h'].includes(key)));
const sameValue = (a, b) => {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  return Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => sameValue(a[key], b[key]));
};

function untouchedTemplateBlock(block) {
  const baseline = block.genTemplateBaseline;
  if (!baseline || !sameValue(templateBlockSnapshot(block), baseline.block) || block.h !== baseline.h || block.elements.length !== baseline.elements.length) return false;
  return block.elements.every((element, index) => {
    if (element.type === 'text' && element.copyRole && element.sourceBlockId && !Object.hasOwn(element, 'genAutoText')) return false;
    const expected = { ...baseline.elements[index] };
    if ('genAutoText' in element) Object.assign(expected, { text: element.genAutoText, style: element.genAutoStyle, ...element.genAutoGeometry });
    if ('genAutoSrc' in element) expected.src = element.genAutoSrc;
    return sameValue(templateSnapshot(element), expected);
  });
}

/** DOM-derived text height is automatic only while the generated copy/style/box is untouched. */
export function patchTemplateElement(block, elementId, patch) {
  const element = block.elements.find(item => item.id === elementId);
  const autoHeight = block.genTemplateBaseline && element && Object.keys(patch).length === 1
    && Number.isFinite(patch.h) && element.text === element.genAutoText
    && sameValue(element.style, element.genAutoStyle)
    && sameValue(copyGeometry(element), element.genAutoGeometry);
  const elements = block.elements.map(item => item.id === elementId
    ? { ...editedTextElement(item, patch), ...(autoHeight ? { genAutoGeometry: { ...item.genAutoGeometry, h: patch.h } } : {}) } : item);
  const next = { ...block, elements };
  if (autoHeight && block.h === block.genTemplateBaseline.h)
    next.genTemplateBaseline = { ...block.genTemplateBaseline, h: getBlockRenderHeight(next) };
  return next;
}

const copyGeometry = ({ x, y, w, h }) => ({ x, y, w, h });
const markAutoCopy = (element) => ({ ...element, genAutoText: element.text,
  genAutoStyle: { ...(element.style || {}) }, genAutoGeometry: copyGeometry(element) });

function mergeAutoStyle(current, baseline, incoming) {
  const style = { ...current };
  if (baseline) for (const [key, value] of Object.entries(incoming || {})) {
    if (Object.is(style[key], baseline[key])) style[key] = value;
  }
  return style;
}

function mergeAutoCopy(element, incoming) {
  const baseline = element.genAutoStyle;
  const style = mergeAutoStyle(element.style, baseline, incoming.style);
  const geometry = element.genAutoGeometry;
  const untouchedBox = geometry && Object.entries(geometry).every(([key, value]) => Object.is(element[key], value));
  const unchangedTextSize = baseline && ['size', 'lineHeight', 'font', 'weight', 'tracking']
    .every(key => Object.is(element.style?.[key], baseline[key]));
  const labelWidth = element.copyRole === 'label' && incoming.w != null && untouchedBox && unchangedTextSize
    ? { w: incoming.w } : {};
  const next = { ...element, text: incoming.text, style,
    ...labelWidth,
    ...(incoming.h != null && untouchedBox && unchangedTextSize ? { h: incoming.h } : {}),
    genAutoText: incoming.text,
    genAutoStyle: { ...(baseline || {}), ...(incoming.style || {}) },
    genAutoGeometry: geometry ? { ...geometry, ...labelWidth, ...(incoming.h != null ? { h: incoming.h } : {}) } : undefined,
  };
  if (Object.hasOwn(incoming, 'aiDraft')) {
    if (incoming.aiDraft === true) next.aiDraft = true;
    else delete next.aiDraft;
  }
  return next;
}

/* mock 조립기는 구조(블록·행 분할·자동 블록)의 정본이지만, 단일 AI 컷의 지오메트리가
   서버 조립기와 다르다 — 서버는 이미지 비율(미상 시 2:3 폴백=880×1320)로 높이를 잡는다.
   구조는 그대로 쓰고 단일 AI 블록의 사진과 사진 아래 headline 캡션만 함께 내린다. */
export function alignSkeletonToServer(blocks, copywriting) {
  const IMG_H = 1320, MARGIN = 50;
  const PLACEHOLDER = '사진을 설명하는 소제목 (17자 내외)';
  return (blocks || []).map((b) => {
    if (b.pageSectionKey) return b;
    const els = b.elements || [];
    const imgs = els.filter((e) => e.type === 'image');
    const img = imgs.length === 1 && imgs[0].sourceBlockId ? imgs[0] : null;
    if (!img) return b;                            // 행 배치·내 이미지·자동 블록 = 서버와 동일
    let out = els.map((e) => {
      if (e === img) return { ...e, h: IMG_H };
      if (e.type === 'text' && e.copyRole === 'headline') {
        return {
          ...e,
          y: (img.y || 0) + IMG_H + CAPTION_GAP,
          h: captionHeight(e.text),
          style: { ...(e.style || {}), ...CAPTION_STYLE },
        };
      }
      if (e.type === 'text' && e.copyRole === 'hookTitle') {
        return {
          ...e,
          y: (img.y || 0) + Math.floor((IMG_H - e.h) / 2),
        };
      }
      return e;
    });
    const signature = out.some((e) => e.type === 'text' && e.copyRole === 'hookTitle');
    if (!signature && !out.some((e) => e.type === 'text' && e.copyRole === 'headline')) {
      out = [...out, {
        id: `${b.id}-ewheadline`, type: 'text', x: 60, y: (img.y || 0) + IMG_H + CAPTION_GAP,
        w: 880, h: captionHeight(''), text: '', placeholder: PLACEHOLDER, textSlotVersion: 1,
        style: CAPTION_STYLE,
        sourceBlockId: img.sourceBlockId, copyRole: 'headline',
      }];
    }
    const bottom = Math.max(...out.map((e) => (e.y || 0) + (e.h || 0)));
    return { ...b, elements: out, h: bottom + MARGIN };
  });
}

/* 'done' = 컷은 끝났지만 아직 보여 줄 주소가 없다(2026-09-26). REAL(실존 모델) 얼굴 컷은
   서버가 최종 권한 확인 전까지 미리보기 주소를 주지 않는다 — 그 자리를 '대기'로 되돌려
   보이면 다 만든 컷이 시작도 안 한 것처럼 보인다. 완료 병합이 안정 주소로 채운다. */
const cutStateOf = (job, sbId) => (
  (job.failedCuts || []).includes(sbId) ? 'failed'
    : (job.live || []).includes(sbId) ? 'live'
      : job.cuts?.[sbId]?.done ? 'done' : 'wait');

/* 생성 모드 진입 장식 — 빈 이미지 슬롯에 상태 플래그+잠금+호버용 예시 썸네일, 카피 슬롯에
   자동 텍스트 마커(genAutoText: 이 값과 같으면 "셀러가 아직 안 고침" = 서버본으로 교체 가능). */
export function decorateGenBlocks(blocks, job, exThumbById) {
  return (blocks || []).map((b) => {
    let copyChanged = false;
    const elements = (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId && el.roleHint !== 'custom') {
        const cut = job.cuts[el.sourceBlockId];
        if (cut?.url) {
          const { genPending, genExample, genFailed, genFailReason, genFailRetryable, genFailSourceBlockId, ...rest } = el;
          return { ...rest, src: cut.url, genAutoSrc: cut.url };
        }
        // src 를 반드시 비운다 — mock 빌더는 자리마다 placeholder src 를 미리 넣는데,
        // 그대로 두면 "이미 다 채워진 에디터"로 보여 생성 서사가 사라진다(2026-08-03 데모 피드백).
        // 진짜 이미지는 cut_done 이벤트가 채운다.
        return { ...el, src: null, genPending: cutStateOf(job, el.sourceBlockId),
          genExample: exThumbById?.[el.sourceBlockId] || null,
          ...(job.failedCutReasons?.[el.sourceBlockId] ? {
            genFailReason: job.failedCutReasons[el.sourceBlockId].reason,
            genFailRetryable: job.failedCutReasons[el.sourceBlockId].retryable,
            ...(job.failedCutReasons[el.sourceBlockId].sourceBlockId !== undefined
              ? { genFailSourceBlockId: job.failedCutReasons[el.sourceBlockId].sourceBlockId } : {}),
          } : {}) };
      }
      if (el.type === 'text' && el.copyRole && el.sourceBlockId) {
        const t = (job.copy[el.sourceBlockId] || []).find((x) => x.role === el.copyRole)?.text;
        if (t) {
          copyChanged = true;
          return markAutoCopy({ ...el, ...copyTextPatch(el, t) });
        }
        return markAutoCopy(el);
      }
      return el;
    });
    const next = {
      ...b,
      elements,
      ...(copyChanged ? { h: Math.max(Number(b.h) || 0, autoBlockHeight(elements)) } : {}),
    };
    if (b.pageDesignId) next.genTemplateBaseline = { block: templateBlockSnapshot(next), h: next.h, elements: elements.map(templateSnapshot) };
    return next;
  });
}

/* 이벤트 도착분 반영 — 셀러가 이미 손댄 것(src 채워진 슬롯·genAutoText 와 달라진 텍스트)은
   건드리지 않는다(셀러 편집 승리). */
export function fillGenBlocks(blocks, job) {
  return (blocks || []).map((b) => {
    let copyChanged = false;
    const elements = (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId && !el.slotCleared
          && ((!el.src && el.genPending) || ('genAutoSrc' in el && el.src === el.genAutoSrc))) {
        const cut = job.cuts[el.sourceBlockId];
        if (cut?.url) {
          const { genPending, genExample, genFailed, genFailReason, genFailRetryable, genFailSourceBlockId, ...rest } = el;
          return { ...rest, src: cut.url, genAutoSrc: cut.url };
        }
        const st = cutStateOf(job, el.sourceBlockId);
        if (job.status === 'running' && st !== 'failed' && el.genFailed) {
          const { genFailed, ...rest } = el;
          return { ...rest, genPending: st };
        }
        const info = job.failedCutReasons?.[el.sourceBlockId];
        return st === el.genPending && !info ? el : { ...el, genPending: st,
          ...(info ? { genFailReason: info.reason, genFailRetryable: info.retryable,
            ...(info.sourceBlockId !== undefined ? { genFailSourceBlockId: info.sourceBlockId } : {}) } : {}) };
      }
      if (el.type === 'text' && el.copyRole && el.sourceBlockId && 'genAutoText' in el) {
        const t = (job.copy[el.sourceBlockId] || []).find((x) => x.role === el.copyRole)?.text;
        if (t && t !== el.text && el.text === el.genAutoText) {
          copyChanged = true;
          return mergeAutoCopy(el, copyTextPatch(el, t));
        }
        return el;
      }
      return el;
    });
    const next = {
      ...b,
      elements,
      ...(copyChanged ? { h: Math.max(Number(b.h) || 0, autoBlockHeight(elements)) } : {}),
    };
    if (b.genTemplateBaseline && b.h === b.genTemplateBaseline.h)
      next.genTemplateBaseline = { ...b.genTemplateBaseline, h: next.h };
    return next;
  });
}

/* 전체 잡 실패 시 진행 중 노출했던 임시 컷은 결과가 아니다. 브라우저 임시 작업본·undo
   스냅샷·의류 탭에 남은 주소까지 같은 집합으로 철회할 수 있게 추출을 분리한다. */
export function failedJobPreviewUrls(blocks) {
  return new Set((blocks || []).flatMap((block) => (block.elements || [])
    .map((el) => el.type === 'image' && el.genAutoSrc)
    .filter((src) => typeof src === 'string' && src.length > 0)));
}

/* keptSourceIds(2026-09-28, 부분 실패 incomplete_cuts): 서버가 완성했다고 알린 컷 자리. 임시 주소는
   똑같이 철회하되(출력 객체는 서버가 이미 지웠다) '실패'가 아니라 '완성됐어요' 타일로 남긴다 —
   그림은 미리보기 라우트가 보관본에서 요청마다 소유·라이선스를 확인해 준다(GenDonePreview). */
export function clearFailedJobPreviewBlocks(blocks, previewUrls = failedJobPreviewUrls(blocks),
  { keptSourceIds } = {}) {
  return (blocks || []).map((block) => ({
    ...block,
    elements: (block.elements || []).map((el) => {
      if (el.type !== 'image') return el;
      const { genAutoSrc, ...rest } = el;
      const revoked = previewUrls.has(el.src);
      const generatedSlot = Boolean(el.sourceBlockId && !el.slotCleared
        && (genAutoSrc || el.genPending));
      if (generatedSlot && (!el.src || revoked || el.src === genAutoSrc)) {
        if (keptSourceIds?.has(el.sourceBlockId)) {
          const { genFailed, ...kept } = rest;
          return { ...kept, src: null, genPending: 'done' };
        }
        return { ...rest, src: null, genPending: 'failed', genFailed: true };
      }
      if (revoked) return { ...rest, src: null };
      return genAutoSrc ? rest : el;
    }),
  }));
}

/* 완료 병합 — 셀러가 만진 레이아웃·텍스트가 정본. 서버 조립본에서 가져오는 것만:
   ① 안정 /file src(프리뷰 presigned 1h 대체) ② 셀러 미편집 카피의 최종본
   ③ AI 고지 블록(라이선스 문구는 컴플라이언스라 서버본으로 통째 교체). */
const sourceIdsOf = (block) => new Set((block?.elements || [])
  .map((el) => el?.sourceBlockId)
  .filter(Boolean));

const noticeBlock = (block) => (block?.elements || []).some(
  (el) => el.type === 'license-verify'
    || (el.type === 'text' && typeof el.text === 'string' && el.text.startsWith('본 상세페이지')));

const FAQ_FACE_DESIGN = 'T08-D07';
const faqHeading = el => el.type === 'text' && (el.copyRole === 'label' || el.copyRole === 'title');

export function pendingFaqHeadingIds(blocks, { active, status, finalizeError }) {
  const ids = new Set();
  if (!active || status === 'error' || status === 'blocked' || finalizeError) return ids;
  for (const block of blocks || []) if (block.pageDesignId === FAQ_FACE_DESIGN) {
    for (const el of block.elements) if (faqHeading(el) && el.sourceBlockId) ids.add(el.id);
  }
  return ids;
}

function faqElementKey(el) {
  if (el.type === 'image') return JSON.stringify([el.type, el.sourceBlockId, el.slotKey]);
  if (el.type === 'text' && el.copyRole) return JSON.stringify([el.type, el.sourceBlockId, copyIdentity(el)]);
  if (el.groupId) return JSON.stringify([el.type, el.groupId, el.templateCopyDecoration || el.shape]);
  if (el.pageSectionGap) return 'section-gap';
  return null;
}

// The server numbers elements by paint order. Moving the heading outside the
// photo changes that order, so keep the existing canvas IDs for the same items.
function preserveFaqElementIds(local, final) {
  const byKey = new Map(local.elements.map(el => [faqElementKey(el), el.id]));
  const reservedIds = new Set(local.elements.map(el => el.id));
  return { ...final, id: local.id, elements: final.elements.map(el => {
    const key = faqElementKey(el);
    if (key && byKey.has(key)) {
      const id = byKey.get(key);
      byKey.delete(key);
      return { ...el, id };
    }
    let id = el.id;
    while (reservedIds.has(id)) id += ':final';
    reservedIds.add(id);
    return { ...el, id };
  }) };
}

// Keep the layout and its contrast together. Restored drafts can still have
// seller edits from before the headings became read-only during generation.
function mergeFaqFaceLayout(before, merged, server) {
  const baseline = before.genTemplateBaseline?.elements;
  if (!baseline) return merged;
  const photo = baseline.find(el => el.type === 'image');
  const finalPhoto = server.elements.find(el => el.type === 'image' && el.sourceBlockId === photo?.sourceBlockId);
  if (!photo || !finalPhoto || finalPhoto.y <= photo.y) return merged;
  const originalPhoto = before.elements.find(el => el.id === photo.id);
  const sameBox = (a, b) => a && b && ['x', 'y', 'w', 'h', 'rotate'].every(key => a[key] === b[key]);
  if (!sameBox(originalPhoto, photo) || !sameValue(originalPhoto.crop, photo.crop)) return merged;
  const headings = ['label', 'title'].map(role => {
    const base = baseline.find(el => el.copyRole === role);
    return { base, current: before.elements.find(el => el.id === base?.id), final: server.elements.find(el => el.copyRole === role) };
  });
  if (headings.some(({ base, current, final }) => !base || !current || !final
      || !['x', 'y', 'w', 'rotate'].every(key => base[key] === current[key]))) return merged;
  const placed = headings.map(({ base, current, final }) => {
    const { text, placeholder } = merged.elements.find(el => el.id === current.id);
    const style = mergeAutoStyle(current.style, current.genAutoStyle || base.style, final.style);
    const { lineHeight, tracking } = style.spacingMode === 'auto' ? resolveTextSpacing(style) : style;
    const h = text === final.text && sameValue(style, final.style) ? final.h
      : estimateTextHeight(text || placeholder, final.w, style.size, lineHeight || style.size * 1.4, undefined, tracking);
    return { id: current.id, ...copyGeometry(final), style, h };
  });
  const [label, title] = placed;
  title.y = label.y + label.h + (headings[1].final.y - headings[0].final.y - headings[0].final.h);
  const offset = title.y + title.h + (finalPhoto.y - headings[1].final.y - headings[1].final.h) - photo.y;
  const scrim = baseline.find(el => el.type === 'shape' && !el.groupId && sameBox(el, photo));
  const originalScrim = before.elements.find(el => el.id === scrim?.id);
  const removeScrim = scrim && sameValue(templateSnapshot(originalScrim || {}), scrim);
  const elements = merged.elements.filter(el => !removeScrim || el.id !== scrim.id).map(el => {
    const heading = placed.find(item => item.id === el.id);
    return heading ? { ...el, ...copyGeometry(heading), style: heading.style } : { ...el, y: el.y + offset };
  });
  return { ...merged, elements, h: Math.max(merged.h + offset, server.h) };
}

/* 로컬 스켈레톤이 서버 완성본의 구조를 전부 대표할 때만 로컬 기준 병합이 안전하다.
   콘티 조회 실패·mock/server 행 규칙 차이로 서버 컷 하나라도 로컬에 없으면, 로컬본을 PUT할 때
   그 컷이 영구 삭제된다. 그런 불일치에서는 셀러 편집보다 서버 생성물 보존을 우선한다. */
export function canSafelyMergeServerBlocks(blocks, serverBlocks) {
  const local = blocks || [];
  const localSources = new Set(local.flatMap((block) => [...sourceIdsOf(block)]));
  return (serverBlocks || []).every((serverBlock) => {
    if (noticeBlock(serverBlock)) return true;
    const serverSources = [...sourceIdsOf(serverBlock)];
    if (serverSources.length) return serverSources.every((id) => localSources.has(id));
    return local.some((block) => block?.id === serverBlock?.id
      || (serverBlock?.kind && block?.kind === serverBlock.kind));
  });
}

const itemIndex = element => element.copyRole?.match(/^items\.(\d+)(?:\.|$)/)?.[1];
const withoutGeneration = value => Object.fromEntries(Object.entries(value).filter(([key]) => !key.startsWith('gen')));
const expectedElement = (block, element) => {
  const base = block.genTemplateBaseline?.elements.find(row => row.id === element.id);
  if (!base) return null;
  return { ...base, ...('genAutoText' in element ? { text: element.genAutoText, style: element.genAutoStyle, ...element.genAutoGeometry } : {}),
    ...('genAutoSrc' in element ? { src: element.genAutoSrc } : {}) };
};
const manuallyChanged = (block, element) => !sameValue(templateSnapshot(element), expectedElement(block, element));
const contentKey = element => element.copyRole ? `copy:${element.copyRole.replace(/^items\.\d+/, 'items.n')}`
  : element.type === 'image' ? `image:${element.slotKey || element.sourceBlockId}`
    : `${element.type}:${element.templateCopyDecoration || element.shape || element.text || ''}`;

function mergeSellerElement(incoming, current, expected) {
  if (!current) return { ...incoming };
  if (!expected) return withoutGeneration(current);
  const result = { ...incoming, id: current.id };
  const identity = new Set(['id', 'sourceBlockId', 'slotKey', 'copyRole', 'groupId']);
  for (const key of new Set([...Object.keys(expected), ...Object.keys(current)])) {
    if (key.startsWith('gen') || identity.has(key) || sameValue(current[key], expected[key])) continue;
    if (key === 'style') {
      result.style = { ...incoming.style };
      for (const prop of new Set([...Object.keys(expected.style || {}), ...Object.keys(current.style || {})])) {
        if (sameValue(current.style?.[prop], expected.style?.[prop])) continue;
        if (Object.hasOwn(current.style || {}, prop)) result.style[prop] = current.style[prop];
        else delete result.style[prop];
      }
    } else if ((key === 'x' || key === 'y') && Number.isFinite(current[key]) && Number.isFinite(expected[key])) {
      result[key] = incoming[key] + current[key] - expected[key];
    } else if (Object.hasOwn(current, key)) result[key] = current[key];
    else delete result[key];
  }
  if (current.type === 'image' && current.slotCleared) result.src = null;
  if (current.type === 'text' && current.copyRole && !Object.hasOwn(current, 'genAutoText')) {
    result.text = current.text;
    if (!Object.hasOwn(current, 'aiDraft')) delete result.aiDraft;
  }
  return result;
}

function mergeElementSet(incoming, current, baseline, block) {
  const remaining = [...current], expected = [...baseline];
  const output = [];
  for (const element of incoming) {
    const key = contentKey(element);
    const index = remaining.findIndex(row => contentKey(row) === key);
    const baseIndex = expected.findIndex(row => contentKey(row) === key);
    const base = baseIndex >= 0 ? expected.splice(baseIndex, 1)[0] : null;
    const local = index >= 0 ? remaining.splice(index, 1)[0] : null;
    if (!local && base) continue; // A seller-deleted element must stay deleted.
    output.push(mergeSellerElement(element, local, local ? expectedElement(block, local) || base : null));
  }
  output.push(...remaining.filter(element => manuallyChanged(block, element)).map(withoutGeneration));
  return output;
}

function listGroups(elements) {
  const grouped = new Map();
  for (const element of elements) {
    const index = itemIndex(element);
    if (index != null && !grouped.has(index)) grouped.set(index, { index, id: element.groupId });
  }
  for (const group of grouped.values()) group.elements = elements.filter(element =>
    itemIndex(element) === group.index || group.id && element.groupId === group.id);
  return [...grouped.values()].sort((a, b) => Number(a.index) - Number(b.index));
}
const groupTop = group => Math.min(...group.elements.map(element => element.y));
const groupBottom = group => Math.max(...group.elements.map(element => element.y + element.h));
const renameItem = (element, index, groupId) => ({ ...element,
  ...(itemIndex(element) != null ? { copyRole: element.copyRole.replace(/^items\.\d+/, `items.${index}`) } : {}),
  ...(element.groupId && groupId ? { groupId } : {}),
});

function finishReconciledBlock(local, final, elements) {
  const baseline = local.genTemplateBaseline;
  const baseBottom = Math.max(0, ...final.elements.map(element => element.y + element.h));
  const padding = Math.max(0, final.h - baseBottom);
  const bottom = Math.max(0, ...elements.map(element => element.y + element.h));
  const h = Math.max(final.h, bottom + padding) + (baseline && local.h !== baseline.h ? local.h - baseline.h : 0);
  const block = mergeSellerElement({ ...final, h }, { ...withoutGeneration(local), h }, { ...baseline?.block, h, elements: local.elements });
  return { ...withoutGeneration(block), id: local.id, elements, h };
}

// A single seller edit must not freeze the other empty loading cards. Final
// geometry is authoritative; differences from each automatic baseline survive.
function reconcileEditedList(local, final) {
  const baseline = local.genTemplateBaseline?.elements;
  if (!baseline) return null;
  if (local.pageDesignId === FAQ_FACE_DESIGN && local.elements.some(element => {
    if (!faqHeading(element) && element.type !== 'image') return false;
    const expected = expectedElement(local, element);
    return expected && ['x', 'y', 'w', 'rotate', 'crop'].some(key => !sameValue(element[key], expected[key]));
  })) {
    // A seller-positioned photo/heading keeps its original contrast composition.
    // List removal still proceeds, but an outside-heading result cannot move it.
    const oldPhoto = baseline.find(element => element.type === 'image');
    const newPhoto = final.elements.find(element => element.type === 'image');
    const shift = (newPhoto?.y || 0) - (oldPhoto?.y || 0);
    const oldItems = new Set(listGroups(baseline).flatMap(group => group.elements));
    const newItems = new Set(listGroups(final.elements).flatMap(group => group.elements));
    final = { ...final, h: local.genTemplateBaseline.h, elements: [
      ...baseline.filter(element => !oldItems.has(element)).map(element => {
        const incoming = final.elements.find(row => contentKey(row) === contentKey(element));
        return { ...element, ...(incoming && element.type === 'image' ? { src: incoming.src } : {}),
          ...(incoming && element.copyRole ? { text: incoming.text } : {}) };
      }),
      ...final.elements.filter(element => newItems.has(element)).map(element => ({ ...element, y: element.y - shift })),
    ] };
  }
  const groups = listGroups(local.elements), baseGroups = listGroups(baseline), finalGroups = listGroups(final.elements);
  const groupElements = new Set(groups.flatMap(group => group.elements));
  const baseElements = new Set(baseGroups.flatMap(group => group.elements));
  const finalElements = new Set(finalGroups.flatMap(group => group.elements));
  const outputGroups = [];
  for (const incoming of finalGroups) {
    const current = groups.find(group => group.index === incoming.index);
    const base = baseGroups.find(group => group.index === incoming.index);
    if (!current && base) continue;
    outputGroups.push({ ...incoming, elements: mergeElementSet(incoming.elements, current?.elements || [], base?.elements || [], local) });
  }
  for (const current of groups) {
    if (finalGroups.some(group => group.index === current.index) || !current.elements.some(element =>
      element.type === 'text' && element.copyRole && !element.templateCopyDecoration
      && !Object.hasOwn(element, 'genAutoText') && String(element.text || '').trim())) continue;
    const base = baseGroups.find(group => group.index === current.index);
    const donor = baseGroups[outputGroups.length] || base;
    if (!donor) continue;
    const elements = mergeElementSet(donor.elements, current.elements, base?.elements || [], local);
    const previous = outputGroups.at(-1);
    const gap = baseGroups.length > 1 ? Math.max(0, groupTop(baseGroups[1]) - groupBottom(baseGroups[0])) : 0;
    const offset = previous ? Math.max(0, groupBottom(previous) + gap - Math.min(...elements.map(element => element.y))) : 0;
    outputGroups.push({ ...donor, elements: elements.map(element => ({ ...element, y: element.y + offset })) });
  }
  const image = final.elements.find(element => element.type === 'image');
  const anchor = finalGroups.length ? groupBottom(finalGroups.at(-1)) : baseGroups.length ? groupBottom(baseGroups.at(-1)) : 0;
  const overflow = image && outputGroups.length ? Math.max(0, groupBottom(outputGroups.at(-1)) - anchor) : 0;
  const nonList = mergeElementSet(final.elements.filter(element => !finalElements.has(element)),
    local.elements.filter(element => !groupElements.has(element)), baseline.filter(element => !baseElements.has(element)), local);
  const elements = [...nonList, ...outputGroups.flatMap((group, index) => group.elements.map(element => ({
    ...renameItem(element, index, group.id), y: element.y - overflow,
  })))];
  return finishReconciledBlock(local, final, elements);
}

const pointPhoto = block => block.elements.find(element => element.type === 'image' && element.sourceBlockId);
const pointPhotoKey = block => JSON.stringify([pointPhoto(block)?.sourceBlockId, pointPhoto(block)?.slotKey]);
const pointCardElements = block => {
  const first = block.elements.findIndex(element => itemIndex(element) != null
    || element.type === 'image' && element.sourceBlockId);
  if (first < 0) return [];
  const photo = pointPhoto(block);
  const background = photo && block.elements.findIndex(element => element.type === 'shape' && !element.groupId
    && element.x <= photo.x && element.y <= photo.y && element.x + element.w >= photo.x + photo.w
    && element.y + element.h >= photo.y + photo.h);
  return block.elements.slice(background >= 0 ? Math.min(first, background) : first);
};
const pointHeaderElements = block => block.elements.filter(element => !pointCardElements(block).includes(element));

function reconcileEditedPoints(localRun, finalRun) {
  if (localRun.some(block => !block.genTemplateBaseline || block.elements.filter(element => element.type === 'image').length > 1)) return null;
  const localHeader = localRun.find(block => pointHeaderElements(block).length) || localRun[0];
  const finalHeader = finalRun.find(block => pointHeaderElements(block).length) || finalRun[0];
  const cards = localRun.filter(block => pointPhoto(block)).filter(block => {
    const incoming = finalRun.find(row => pointPhotoKey(row) === pointPhotoKey(block));
    return incoming || pointCardElements(block).some(element => manuallyChanged(block, element));
  });
  if (!cards.length) return finalRun.map(block => {
    const elements = mergeElementSet(block.elements, localHeader.elements, localHeader.genTemplateBaseline.elements, localHeader);
    return finishReconciledBlock(localHeader, block, elements);
  });
  return cards.map((local, index) => {
    const incoming = finalRun.find(block => pointPhotoKey(block) === pointPhotoKey(local));
    const layout = incoming || local;
    const baseline = { ...local, elements: local.genTemplateBaseline.elements };
    const card = pointCardElements(layout);
    const elements = incoming ? mergeElementSet(card, pointCardElements(local), pointCardElements(baseline), local)
      : pointCardElements(local).map(withoutGeneration);
    const header = index === 0 ? mergeElementSet(pointHeaderElements(finalHeader), pointHeaderElements(localHeader),
      pointHeaderElements({ ...localHeader, elements: localHeader.genTemplateBaseline.elements }), localHeader) : [];
    const originalTop = Math.min(...card.map(element => element.y));
    const headingCard = pointCardElements(finalHeader);
    const targetTop = index === 0 ? (headingCard.length ? Math.min(...headingCard.map(element => element.y)) : originalTop) : 0;
    const shifted = elements.map(element => ({ ...renameItem(element, index), y: element.y + targetTop - originalTop,
      ...(element.type === 'text' && !element.copyRole && /^\d{2}$/.test(element.text) ? { text: String(index + 1).padStart(2, '0') } : {}),
    }));
    // Match text by field only after its card has been matched by original photo.
    const final = { ...layout, elements: [...header, ...shifted] };
    return finishReconciledBlock(local, final, final.elements);
  });
}

/** @param {Set<string>} [failedSourceIds] 서버가 못 만든 컷의 sourceBlockId — 그 자리는
    "그냥 빈 칸"이 아니라 '만들지 못함' 표식(genFailed)을 남긴다. 표식이 없으면 완료 순간
    실패 컷이 일반 빈 슬롯으로 둔갑해 셀러가 이유도, 과금 여부도 모른 채 넘어간다. */
export function mergeServerBlocks(blocks, serverBlocks, failedSourceIds, failedReasons = {}) {
  const failureById = { ...failedReasons };
  for (const block of serverBlocks || []) {
    for (const element of block.elements || []) {
      if (element.type === 'image' && element.genFailed && !element.src) {
        failureById[element.sourceBlockId] = {
          ...failureById[element.sourceBlockId],
          reason: element.genFailReason, retryable: element.genFailRetryable,
          ...(element.genFailSourceBlockId !== undefined ? { sourceBlockId: element.genFailSourceBlockId } : {}),
        };
      }
    }
  }
  const failurePatch = (element) => {
    const info = failureById[element.sourceBlockId];
    const failed = info || failedSourceIds?.has(element.sourceBlockId) || element.genFailed;
    if (element.src || !failed) return {};
    return { genFailed: true,
      ...(info?.reason != null ? { genFailReason: info.reason } : {}),
      ...(typeof info?.retryable === 'boolean' ? { genFailRetryable: info.retryable } : {}),
      ...(info?.sourceBlockId !== undefined ? { genFailSourceBlockId: info.sourceBlockId } : {}),
    };
  };
  if (!canSafelyMergeServerBlocks(blocks, serverBlocks)) return (serverBlocks || []).map(block => ({
    ...block, elements: (block.elements || []).map(element => element.type === 'image'
      ? { ...element, ...failurePatch(element) } : element),
  }));
  const srcById = {}; const copyById = {};
  let serverNotice = null;
  for (const b of serverBlocks || []) {
    if (noticeBlock(b)) serverNotice = b;
    for (const el of (b.elements || [])) {
      if (el.type === 'image' && el.sourceBlockId && el.src) srcById[el.sourceBlockId] = el.src;
      if (el.type === 'text' && el.sourceBlockId && el.copyRole) {
        (copyById[el.sourceBlockId] ||= {})[copyIdentity(el)] = el;
      }
    }
  }
  const originalImages = (blocks || []).flatMap(block => block.elements || []).filter(element => element.type === 'image');
  const finalizeImage = (element, original = element) => {
    const { genPending, genExample, genAutoSrc, genFailed, genFailReason, genFailRetryable, genFailSourceBlockId, ...rest } = element;
    // Completion revokes temporary previews that have no stable final result.
    // Re-entry without a failure set retains the existing compatible behavior.
    const tempPreview = Boolean(failedSourceIds) && Boolean(original.genAutoSrc) && original.src === original.genAutoSrc;
    const src = rest.slotCleared ? null : (srcById[element.sourceBlockId] || (tempPreview ? null : rest.src) || null);
    const failure = failurePatch({ ...original, ...element, src });
    if (failure.genFailed) return { ...rest, src: null,
      ...(original.genFailReason != null ? { genFailReason: original.genFailReason } : {}),
      ...(typeof original.genFailRetryable === 'boolean' ? { genFailRetryable: original.genFailRetryable } : {}),
      ...(original.genFailSourceBlockId !== undefined ? { genFailSourceBlockId: original.genFailSourceBlockId } : {}), ...failure };
    return { ...rest, src };
  };
  const templateRuns = new Map();
  for (const block of blocks || []) if (block.pageSectionKey && block.pageDesignId) {
    const run = templateRuns.get(block.pageSectionKey) || [];
    run.push(block); templateRuns.set(block.pageSectionKey, run);
  }
  const untouched = new Set([...templateRuns].filter(([key, run]) => run.every(untouchedTemplateBlock)
    && (serverBlocks || []).some(block => block.pageSectionKey === key)).map(([key]) => key));
  const emitted = new Set();
  const reconciled = new Map();
  for (const [key, run] of templateRuns) {
    if (untouched.has(key)) continue;
    const finalRun = (serverBlocks || []).filter(block => block.pageSectionKey === key);
    if (!finalRun.length) continue;
    if (run[0].pageSectionKind === 'points') {
      const merged = reconcileEditedPoints(run, finalRun);
      if (merged) reconciled.set(key, merged);
    } else if (['faq', 'review', 'for_you'].includes(run[0].pageSectionKind) && run.length === 1 && finalRun.length === 1) {
      const merged = reconcileEditedList(run[0], finalRun[0]);
      if (merged) reconciled.set(key, [merged]);
    }
  }
  const out = (blocks || []).flatMap((b) => {
    if (reconciled.has(b.pageSectionKey)) {
      if (emitted.has(b.pageSectionKey)) return [];
      emitted.add(b.pageSectionKey);
      return reconciled.get(b.pageSectionKey).map(block => ({ ...block, elements: block.elements.map(element =>
        element.type === 'image' ? finalizeImage(element, originalImages.find(original =>
          original.sourceBlockId === element.sourceBlockId && original.slotKey === element.slotKey) || element) : element) }));
    }
    if (untouched.has(b.pageSectionKey)) {
      if (emitted.has(b.pageSectionKey)) return [];
      emitted.add(b.pageSectionKey);
      const finalRun = serverBlocks.filter(block => block.pageSectionKey === b.pageSectionKey);
      return finalRun.map((block) => {
        if (b.pageDesignId !== FAQ_FACE_DESIGN || !block.elements.some(el => el.type === 'image' && el.y > 0)
          || !block.elements.some(faqHeading)) return block;
        return preserveFaqElementIds(b, block);
      });
    }
    const finalFaq = b.pageDesignId === FAQ_FACE_DESIGN && serverBlocks.find(block => block.pageSectionKey === b.pageSectionKey
      && block.elements.some(el => el.type === 'image' && el.y > 0) && block.elements.some(faqHeading));
    let copyChanged = false;
    const elements = (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId) return finalizeImage(el);
      if (el.type === 'text' && el.copyRole && el.sourceBlockId) {
        const { genAutoText, genAutoStyle, genAutoGeometry, ...rest } = el;
        const serverCopy = copyById[el.sourceBlockId]?.[copyIdentity(el)];
        if (serverCopy && el.text === genAutoText) {
          const keepFaqHeading = finalFaq && faqHeading(el);
          if (!keepFaqHeading) copyChanged = true;
          const merged = mergeAutoCopy(el, { ...(keepFaqHeading ? { text: serverCopy.text } : serverCopy),
            aiDraft: serverCopy.aiDraft });
          const { genAutoText: _text, genAutoStyle: _style, genAutoGeometry: _geometry, ...result } = merged;
          return { ...result, ...(serverCopy.contrastLines ? { contrastLines: serverCopy.contrastLines, contrastFor: serverCopy.contrastFor } : {}) };
        }
        return rest;
      }
      return el;
    });
    const merged = {
      ...Object.fromEntries(Object.entries(b).filter(([key]) => key !== 'genTemplateBaseline')),
      elements,
      ...(copyChanged ? { h: Math.max(Number(b.h) || 0, autoBlockHeight(elements)) } : {}),
    };
    return finalFaq ? mergeFaqFaceLayout(b, merged, finalFaq) : merged;
  });
  if (serverNotice) {
    const i = out.findIndex(noticeBlock);
    if (i >= 0) {
      const id = out[i].id;
      out[i] = { ...serverNotice, id, elements: serverNotice.elements.map((el, index) => ({ ...el, id: `${id}e${index}` })) };
    } else out.push(serverNotice);
  }
  // Reconciled cards retain editor selection IDs, while untouched server runs
  // may have shifted ordinal IDs after compaction. Allocate only collisions.
  const blockIds = new Set(), elementIds = new Set();
  return out.map(block => {
    let id = block.id;
    while (blockIds.has(id)) id += ':final';
    blockIds.add(id);
    return { ...block, id, elements: block.elements.map(element => {
      let elementId = element.id;
      while (elementIds.has(elementId)) elementId += ':final';
      elementIds.add(elementId);
      return elementId === element.id ? element : { ...element, id: elementId };
    }) };
  });
}
