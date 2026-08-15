/* =============================================================
   lib/editorWaitSkeleton — 에디터 통합 대기(editor_wait_dev_spec v2)의 순수 헬퍼.
   콘티 → 스켈레톤 블록(서버 조립 규칙 정렬) → 생성 이벤트 채움 → 완료 병합.
   전부 순수 함수 — Editor/Generating 어디서든 재사용, node 테스트 가능.
   ============================================================= */
import { TEXT_MUTED } from '../features/editor/presets/textPresets.js';

/* mock 조립기는 구조(블록·행 분할·자동 블록)의 정본이지만, 단일 AI 컷의 지오메트리가
   서버 조립기와 다르다 — 서버는 이미지 비율(미상 시 2:3 폴백=880×1320)로 높이를 잡고
   hero 외 모든 역할에 body 카피 요소를 만든다(page_assembler.py, codex 리뷰 F4).
   구조는 그대로 쓰고 단일 AI 블록만 서버 규칙으로 보정한다. */
export function alignSkeletonToServer(blocks, copywriting) {
  const IMG_Y = 50, IMG_H = 1320, MARGIN = 50;   // 서버 _image_box 2:3 폴백 + _IMG_MARGIN_B
  return (blocks || []).map((b) => {
    const els = b.elements || [];
    const imgs = els.filter((e) => e.type === 'image');
    const img = imgs.length === 1 && imgs[0].sourceBlockId ? imgs[0] : null;
    if (!img) return b;                            // 행 배치·내 이미지·자동 블록 = 서버와 동일
    const out = els.map((e) => (
      e === img ? { ...e, h: IMG_H }
        : e.type === 'text' && e.copyRole === 'body' ? { ...e, y: IMG_Y + IMG_H - MARGIN } : e));
    const isHero = b.contentRole === 'hero';
    if (copywriting && !isHero && !out.some((e) => e.type === 'text' && e.copyRole === 'body')) {
      out.push({ id: `${b.id}-ewbody`, type: 'text', x: 120, y: IMG_Y + IMG_H - MARGIN, w: 760, h: 40,
        text: '', style: { size: 17, color: TEXT_MUTED, lineHeight: 26 }, sourceBlockId: img.sourceBlockId, copyRole: 'body' });
    }
    const bottom = Math.max(...out.map((e) => (e.y || 0) + (e.h || 0)));
    return { ...b, elements: out, h: bottom + MARGIN };
  });
}

const cutStateOf = (job, sbId) => (
  job.failedCuts.includes(sbId) ? 'failed' : job.live.includes(sbId) ? 'live' : 'wait');

/* 생성 모드 진입 장식 — 빈 이미지 슬롯에 상태 플래그+잠금+호버용 예시 썸네일, 카피 슬롯에
   자동 텍스트 마커(genAutoText: 이 값과 같으면 "셀러가 아직 안 고침" = 서버본으로 교체 가능). */
export function decorateGenBlocks(blocks, job, exThumbById) {
  return (blocks || []).map((b) => ({
    ...b,
    elements: (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId) {
        const cut = job.cuts[el.sourceBlockId];
        if (cut?.url) return { ...el, src: cut.url, genAutoSrc: cut.url };
        // src 를 반드시 비운다 — mock 빌더는 자리마다 placeholder src 를 미리 넣는데,
        // 그대로 두면 "이미 다 채워진 에디터"로 보여 생성 서사가 사라진다(2026-08-03 데모 피드백).
        // 진짜 이미지는 cut_done 이벤트가 채운다.
        return { ...el, src: null, genPending: cutStateOf(job, el.sourceBlockId),
          genExample: exThumbById?.[el.sourceBlockId] || null };
      }
      if (el.type === 'text' && el.copyRole && el.sourceBlockId) {
        const t = (job.copy[el.sourceBlockId] || []).find((x) => x.role === el.copyRole)?.text;
        return t ? { ...el, text: t, genAutoText: t } : { ...el, genAutoText: el.text };
      }
      return el;
    }),
  }));
}

/* 이벤트 도착분 반영 — 셀러가 이미 손댄 것(src 채워진 슬롯·genAutoText 와 달라진 텍스트)은
   건드리지 않는다(셀러 편집 승리). */
export function fillGenBlocks(blocks, job) {
  return (blocks || []).map((b) => ({
    ...b,
    elements: (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId
          && ((!el.src && el.genPending) || ('genAutoSrc' in el && el.src === el.genAutoSrc))) {
        const cut = job.cuts[el.sourceBlockId];
        if (cut?.url) {
          const { genPending, genExample, ...rest } = el;
          return { ...rest, src: cut.url, genAutoSrc: cut.url };
        }
        const st = cutStateOf(job, el.sourceBlockId);
        return st === el.genPending ? el : { ...el, genPending: st };
      }
      if (el.type === 'text' && el.copyRole && el.sourceBlockId && 'genAutoText' in el) {
        const t = (job.copy[el.sourceBlockId] || []).find((x) => x.role === el.copyRole)?.text;
        if (t && t !== el.text && el.text === el.genAutoText) return { ...el, text: t, genAutoText: t };
        return el;
      }
      return el;
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
export function mergeServerBlocks(blocks, serverBlocks, failedSourceIds) {
  if (!canSafelyMergeServerBlocks(blocks, serverBlocks)) return serverBlocks || [];
  const srcById = {}; const copyById = {};
  let serverNotice = null;
  for (const b of serverBlocks || []) {
    if (noticeBlock(b)) serverNotice = b;
    for (const el of (b.elements || [])) {
      if (el.type === 'image' && el.sourceBlockId && el.src) srcById[el.sourceBlockId] = el.src;
      if (el.type === 'text' && el.sourceBlockId && el.copyRole) {
        (copyById[el.sourceBlockId] ||= {})[el.copyRole] = el.text;
      }
    }
  }
  const out = (blocks || []).map((b) => ({
    ...b,
    elements: (b.elements || []).map((el) => {
      if (el.type === 'image' && el.sourceBlockId) {
        const { genPending, genExample, genAutoSrc, genFailed, ...rest } = el;
        const src = srcById[el.sourceBlockId] || rest.src || null;
        if (!src && failedSourceIds?.has(el.sourceBlockId)) {
          return { ...rest, src: null, genFailed: true };
        }
        return { ...rest, src };
      }
      if (el.type === 'text' && el.copyRole && el.sourceBlockId) {
        const { genAutoText, ...rest } = el;
        const t = copyById[el.sourceBlockId]?.[el.copyRole];
        return t && el.text === genAutoText ? { ...rest, text: t } : rest;
      }
      return el;
    }),
  }));
  if (serverNotice) {
    const i = out.findIndex(noticeBlock);
    if (i >= 0) out[i] = serverNotice; else out.push(serverNotice);
  }
  return out;
}
