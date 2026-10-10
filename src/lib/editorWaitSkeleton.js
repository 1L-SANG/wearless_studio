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

const autoBlockHeight = (elements) => Math.max(
  0,
  ...(elements || []).map((element) => (element.y || 0) + (element.h || 0)),
) + 50;

const copyTextPatch = (element, text) => element.copyRole === 'headline'
  ? { text, textSlotVersion: 1, h: captionHeight(text), style: { ...(element.style || {}), ...CAPTION_STYLE } }
  : { text };

const copyGeometry = ({ x, y, w, h }) => ({ x, y, w, h });
const markAutoCopy = (element) => ({ ...element, genAutoText: element.text,
  genAutoStyle: { ...(element.style || {}) }, genAutoGeometry: copyGeometry(element) });

function mergeAutoCopy(element, incoming) {
  const style = { ...(element.style || {}) };
  const baseline = element.genAutoStyle;
  if (baseline) {
    for (const [key, value] of Object.entries(incoming.style || {})) {
      if (Object.is(style[key], baseline[key])) style[key] = value;
    }
  }
  const geometry = element.genAutoGeometry;
  const untouchedBox = geometry && Object.entries(geometry).every(([key, value]) => Object.is(element[key], value));
  const unchangedTextSize = baseline && ['size', 'lineHeight', 'font', 'weight', 'tracking']
    .every(key => Object.is(element.style?.[key], baseline[key]));
  return { ...element, text: incoming.text, style,
    ...(incoming.h != null && untouchedBox && unchangedTextSize ? { h: incoming.h } : {}),
    genAutoText: incoming.text,
    genAutoStyle: { ...(baseline || {}), ...(incoming.style || {}) },
    genAutoGeometry: geometry ? { ...geometry, ...(incoming.h != null ? { h: incoming.h } : {}) } : undefined,
  };
}

/* mock 조립기는 구조(블록·행 분할·자동 블록)의 정본이지만, 단일 AI 컷의 지오메트리가
   서버 조립기와 다르다 — 서버는 이미지 비율(미상 시 2:3 폴백=880×1320)로 높이를 잡는다.
   구조는 그대로 쓰고 단일 AI 블록의 사진과 사진 아래 headline 캡션만 함께 내린다. */
export function alignSkeletonToServer(blocks, copywriting) {
  const IMG_H = 1320, MARGIN = 50;
  const PLACEHOLDER = '사진을 설명하는 소제목 (17자 내외)';
  return (blocks || []).map((b) => {
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
      if (el.type === 'image' && el.sourceBlockId) {
        const cut = job.cuts[el.sourceBlockId];
        if (cut?.url) return { ...el, src: cut.url, genAutoSrc: cut.url };
        // src 를 반드시 비운다 — mock 빌더는 자리마다 placeholder src 를 미리 넣는데,
        // 그대로 두면 "이미 다 채워진 에디터"로 보여 생성 서사가 사라진다(2026-08-03 데모 피드백).
        // 진짜 이미지는 cut_done 이벤트가 채운다.
        return { ...el, src: null, genPending: cutStateOf(job, el.sourceBlockId),
          genExample: exThumbById?.[el.sourceBlockId] || null,
          ...(job.failedCutReasons?.[el.sourceBlockId] ? {
            genFailReason: job.failedCutReasons[el.sourceBlockId].reason,
            genFailRetryable: job.failedCutReasons[el.sourceBlockId].retryable,
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
    return {
      ...b,
      elements,
      ...(copyChanged ? { h: Math.max(Number(b.h) || 0, autoBlockHeight(elements)) } : {}),
    };
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
          const { genPending, genExample, genFailed, genFailReason, genFailRetryable, ...rest } = el;
          return { ...rest, src: cut.url, genAutoSrc: cut.url };
        }
        const st = cutStateOf(job, el.sourceBlockId);
        if (job.status === 'running' && st !== 'failed' && el.genFailed) {
          const { genFailed, ...rest } = el;
          return { ...rest, genPending: st };
        }
        const info = job.failedCutReasons?.[el.sourceBlockId];
        return st === el.genPending && !info ? el : { ...el, genPending: st,
          ...(info ? { genFailReason: info.reason, genFailRetryable: info.retryable } : {}) };
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
    return {
      ...b,
      elements,
      ...(copyChanged ? { h: Math.max(Number(b.h) || 0, autoBlockHeight(elements)) } : {}),
    };
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

/** @param {Set<string>} [failedSourceIds] 서버가 못 만든 컷의 sourceBlockId — 그 자리는
    "그냥 빈 칸"이 아니라 '만들지 못함' 표식(genFailed)을 남긴다. 표식이 없으면 완료 순간
    실패 컷이 일반 빈 슬롯으로 둔갑해 셀러가 이유도, 과금 여부도 모른 채 넘어간다. */
export function mergeServerBlocks(blocks, serverBlocks, failedSourceIds, failedReasons = {}) {
  const failureById = { ...failedReasons };
  for (const block of serverBlocks || []) {
    for (const element of block.elements || []) {
      if (element.type === 'image' && element.genFailed && !element.src) {
        failureById[element.sourceBlockId] = {
          reason: element.genFailReason, retryable: element.genFailRetryable,
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
        (copyById[el.sourceBlockId] ||= {})[el.copyRole] = {
          text: el.text,
          h: el.h,
          style: el.style,
        };
      }
    }
  }
  const out = (blocks || []).map((b) => {
    let copyChanged = false;
    const elements = (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId) {
        const { genPending, genExample, genAutoSrc, genFailed, genFailReason, genFailRetryable, ...rest } = el;
        // 대기 중 자동으로 채운 미리보기(1h 서명 주소)는 임시다. 완료 병합(failedSourceIds 를
        // 아는 호출)에서 서버 완성본에 그 컷이 없으면 저장본에 싣지 않는다 — 앞선 실패 잡의
        // 미리보기가 한 시간 뒤 깨진 사진으로 남지 않게. 완료 결과가 이긴다(2026-09-26).
        const tempPreview = Boolean(failedSourceIds) && Boolean(genAutoSrc) && rest.src === genAutoSrc;
        // 셀러가 일부러 비운 자리는 되살리지 않는다 — 지운 사진이 완료 순간 말없이
        // 돌아오면 "내가 지운 게 왜 있지"가 된다(2026-08-17 검증).
        const src = rest.slotCleared ? null
          : (srcById[el.sourceBlockId] || (tempPreview ? null : rest.src) || null);
        // 실패 목록을 아는 호출(완료 병합)만 표식을 새로 판정한다. 목록 없이 부르는
        // 재진입 병합은 이미 저장된 표식을 그대로 지킨다 — 안 그러면 다시 열 때마다
        // '만들지 못했어요'가 평범한 빈 칸으로 둔갑한다(2026-08-17 리뷰).
        const failure = failurePatch({ ...el, src });
        if (failure.genFailed) return { ...rest, src: null,
          ...(genFailReason != null ? { genFailReason } : {}),
          ...(typeof genFailRetryable === 'boolean' ? { genFailRetryable } : {}), ...failure };
        return { ...rest, src };
      }
      if (el.type === 'text' && el.copyRole && el.sourceBlockId) {
        const { genAutoText, genAutoStyle, genAutoGeometry, ...rest } = el;
        const serverCopy = copyById[el.sourceBlockId]?.[el.copyRole];
        if (serverCopy && el.text === genAutoText) {
          copyChanged = true;
          const merged = mergeAutoCopy(el, {
            ...copyTextPatch(rest, serverCopy.text),
            ...(serverCopy.h != null ? { h: serverCopy.h } : {}),
            ...(serverCopy.style ? { style: serverCopy.style } : {}),
          });
          const { genAutoText: _text, genAutoStyle: _style, genAutoGeometry: _geometry, ...result } = merged;
          return result;
        }
        return rest;
      }
      return el;
    });
    return {
      ...b,
      elements,
      ...(copyChanged ? { h: Math.max(Number(b.h) || 0, autoBlockHeight(elements)) } : {}),
    };
  });
  if (serverNotice) {
    const i = out.findIndex(noticeBlock);
    if (i >= 0) out[i] = serverNotice; else out.push(serverNotice);
  }
  return out;
}
