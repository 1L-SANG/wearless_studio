/* =============================================================
   features/storyboard — ⑤ 콘티보드 (PRD §8)
   blocks 는 "서버 상태의 working copy" 패턴: 진입 시 fetch → 로컬 편집
   → 생성 CTA 에서 saveStoryboard 로 한 번에 저장 (frontend_state_model §4).
   사용자 분류: sectionRole(핵심 장점/핏·코디/제품 확인) + contentRole.
   cutType은 contentRole에서 자동 파생되는 비노출 생성 레시피다.
   카피라이팅 토글은 store(copywriting) → patchProject 동기화.
   UnderlineTabs/ColorDots/MoodGuide/hexFor are exported for the editor.
   ============================================================= */
import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { uid } from '@/lib/ids.js';
import { Placeholder } from '@/mock/placeholders.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, IconButton, Button, Chips, EmptyState, Skeleton, Toggle, useToast } from '@/components/ui.jsx';
import { PageHead, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { ensureSections, deriveSections, adoptSection, patchSection, normalizeRows, normalizeBoard } from '@/lib/sections.js';
import {
  CONTENT_ROLES,
  SECTION_ROLES,
  SECTION_ROLE_OPTIONS,
  STORYBOARD_TAXONOMY_VERSION,
  blockPatchForContentRole,
  contentTemplatesForSection,
  contentTitle,
  hasDetailSource,
  sectionTitle,
} from '@/lib/storyboardTaxonomy.js';

const COLOR_HEX = {
  white: '#ffffff', ivory: '#f3eee1', beige: '#d8c4a3', brown: '#7a5230', black: '#15141a',
  gray: '#9a9aa1', navy: '#1f2a44', blue: '#2a5db0', green: '#3f7a4f', red: '#c0392b', pink: '#e3a7b8', yellow: '#e7c75c',
  '블랙': '#15141a', '아이보리': '#f3eee1', '화이트': '#ffffff', '베이지': '#d8c4a3',
};
export const hexFor = (c) => COLOR_HEX[c.swatchId] || COLOR_HEX[c.name] || '#d8d6dc';

/* 콘티 저장 직렬 체인 — 모듈 스코프: 컴포넌트 수명(빠른 이탈→재진입의 구·신 인스턴스)과
   프로젝트 경계를 넘어 전 저장의 순서를 보장한다. 늦게 도착한 옛 PUT이 최신을 덮어쓸 수 없다.
   lastSaved 는 프로젝트별 — 다른 프로젝트의 참조와 비교되는 오판 방지. */
let sbSaveChain = Promise.resolve();
const sbLastSaved = new Map();   // projectId → 마지막 "성공" 저장 blocks 참조
const sbPending = new Map();     // projectId → "실패"한 저장 스냅샷 — 다음 진입이 서버 대신 이걸 복원해 유실 방지
const sbSaveIdle = () => sbSaveChain.catch(() => {});   // 대기 중 저장이 모두 끝날 때까지 (로드 전 호출)
// 키 순서 무관 안정 직렬화 — 서버 왕복(JSONB 등)이 키 순서를 바꿔도 내용 동등성이 유지되게.
// 순진한 JSON.stringify 비교는 같은 내용을 다르다고 판정해 복구 가능한 편집분을 잘못 폐기한다.
const sbStable = (v) => JSON.stringify(v, (k, val) =>
  (val && typeof val === 'object' && !Array.isArray(val))
    ? Object.keys(val).sort().reduce((o, key) => { o[key] = val[key]; return o; }, {})
    : val);
function sbSaveNow(pid, getSnap) {
  const run = sbSaveChain.catch(() => {}).then(() => {
    const snap = getSnap();
    if (!pid || !snap) return;
    if (sbLastSaved.get(pid) === snap) {
      // 마지막 '성공' 저장본과 동일 참조 = 사용자가 그 상태로 되돌린 것(예: 블록 취소의 통짜 복원).
      // 이때 남아 있는 실패 보류분(sbPending)은 낡은 스냅샷 — 지우지 않으면 재진입 시 취소한 블록이 부활한다.
      sbPending.delete(pid);
      return;
    }
    return api.saveStoryboard(pid, snap).then(
      () => { sbLastSaved.set(pid, snap); sbPending.delete(pid); },
      (err) => { sbPending.set(pid, snap); throw err; },   // 실패 = 완료 아님 — 스냅샷 보관 후 전파
    );
  });
  sbSaveChain = run.catch(() => {});   // 체인은 실패해도 살아있게, 실패는 호출자에 전파
  return run;
}

const withoutLayoutRow = (block) => {
  const { layoutRowId: _layoutRowId, ...single } = block;
  return single;
};

const SCOPE_LABELS = { all: '전부', bg: '배경만', pose: '포즈만' };
const WORN_CUT_TYPES = new Set(['styling', 'horizon', 'mirror']);
const exampleCategoryFor = (cut) => cut === 'product' ? 'product' : (cut === 'horizon' ? 'horizon' : 'styling');
const exampleThumbFor = (catalogs, exampleId, cut) => (
  (catalogs?.genExamples || []).find((example) => example.id === exampleId)?.thumb
  || Placeholder.photo(exampleId, exampleCategoryFor(cut), 240, 320)
);

const byRankThenId = (left, right) => (
  (Number(left.rank) || 0) - (Number(right.rank) || 0)
  || (String(left.id) < String(right.id) ? -1 : String(left.id) > String(right.id) ? 1 : 0)
);

export function selectGenerationExamples(catalog, { cutType, shot, clothingType, gender }) {
  const matched = (catalog || []).filter((example) => (
    example?.cutType === cutType
    && example?.shot === shot
    && (cutType === 'product' ? example?.gender == null : example?.gender === gender)
    && Array.isArray(example?.applicableClothingTypes)
    && example.applicableClothingTypes.includes(clothingType)
  ));
  const mixAxis = cutType === 'styling' ? 'mood'
    : cutType === 'product' && shot === 'detail' ? 'detailSubject'
      : null;
  if (!mixAxis) return [...matched].sort(byRankThenId).slice(0, 6);

  const buckets = new Map();
  for (const example of matched) {
    const key = String(example[mixAxis] || '');
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(example);
  }
  const orderedBuckets = [...buckets.entries()]
    .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
    .map(([, examples]) => examples.sort(byRankThenId));
  const mixed = [];
  for (let rankIndex = 0; mixed.length < 6; rankIndex += 1) {
    let added = false;
    for (const examples of orderedBuckets) {
      if (examples[rankIndex]) {
        mixed.push(examples[rankIndex]);
        added = true;
        if (mixed.length === 6) break;
      }
    }
    if (!added) break;
  }
  return mixed;
}

function exampleGenderFromAnalysis(analysis, catalogs) {
  const allowed = new Set(['women', 'men']);
  const modelId = analysis?.selectedModelId || analysis?.selected_model_id;
  const models = [...(catalogs?.models || []), ...(analysis?.models || [])];
  const modelGender = models.find((model) => model.id === modelId)?.gender;
  if (allowed.has(modelGender)) return modelGender;
  const fitGender = analysis?.fitProfile?.gender;
  if (allowed.has(fitGender)) return fitGender;
  const targets = (analysis?.targetGenders || []).filter((value) => allowed.has(value));
  return targets.length === 1 ? targets[0] : null;
}

function OuterClosureIcon({ state }) {
  const edge = state === 'closed'
    ? <path d="M24 18v32" />
    : state === 'partial'
      ? <path d="M18 18l6 10 6-10M24 28v22" />
      : <path d="M18 18l6 16 6-16M18 34l-3 16M30 34l3 16" />;
  return (
    <svg className="outer-closure-icon" viewBox="0 0 48 56" aria-hidden="true">
      <path d="M17 8l-9 7 5 12 4-4-2 27h18l-2-27 4 4 5-12-9-7-7 7z" />
      {edge}
      {state !== 'open' && <><circle cx="24" cy="32" r="1" /><circle cx="24" cy="39" r="1" /><circle cx="24" cy="46" r="1" /></>}
    </svg>
  );
}

function referenceFeedbackPatch(block, changes, catalogs) {
  if (!block) return changes;
  const exampleId = Object.prototype.hasOwnProperty.call(changes, 'exampleId') ? changes.exampleId : block.exampleId;
  const refScope = Object.prototype.hasOwnProperty.call(changes, 'refScope') ? changes.refScope : (block.refScope || 'all');
  // 같은 공간 묶음 컷은 서버 계약(normalize_spec)이 범위를 'pose' 로 강제 — 프론트 표시도 동일 규칙
  const spaceGroupId = Object.prototype.hasOwnProperty.call(changes, 'spaceGroupId') ? changes.spaceGroupId : block.spaceGroupId;
  const effScope = spaceGroupId ? 'pose' : refScope;
  const next = { ...changes };
  if (exampleId && effScope === 'all') {
    if (block.baseThumb == null) next.baseThumb = block.thumb;
    next.thumb = exampleThumbFor(catalogs, exampleId, changes.cutType ?? block.cutType);
  } else if (block.baseThumb != null) {
    next.thumb = block.baseThumb;
  }
  return next;
}

function StoryboardCard({ block, catalogs, colorOpts, matchClothing, clothingType, spaceTag, selected, locked, gripDrag, onSelect, onDuplicate, onDelete, onUp, onDown }) {
  const isMine = block.source === 'mine';
  const colorIds = (block.colorIds && block.colorIds.length) ? block.colorIds : (block.colorId ? [block.colorId] : []);
  const cols = colorIds.map((id) => colorOpts.find((c) => c.id === id)).filter(Boolean);
  const poseEdited = !!block.pose && block.pose !== 'auto';
  const matchEdited = Array.isArray(block.matchIds) && block.matchIds.length > 0;
  const matchThumb = matchEdited ? ((matchClothing || []).find((m) => m.id === block.matchIds[0])?.thumb) : null;
  // 레퍼런스 범위 표시 — 전부=썸네일 교체(referenceFeedbackPatch), 포즈만/배경만=미니 이미지 (매칭 의류와 같은 패턴)
  const refMiniScope = block.exampleId
    ? (block.spaceGroupId ? 'pose' : (block.refScope === 'pose' || block.refScope === 'bg' ? block.refScope : null))
    : null;
  const refMiniThumb = refMiniScope ? exampleThumbFor(catalogs, block.exampleId, block.cutType) : null;
  const refMiniLabel = refMiniScope === 'bg' ? '배경' : '포즈';
  const isProduct = block.cutType === 'product';
  const dirLabel = isProduct
    ? (catalogs.productDirections.find((d) => d.value === block.direction)?.label || '앞면')
    : (catalogs.directions.find((d) => d.value === block.direction)?.label || '—');
  const shotLabel = isProduct
    ? (catalogs.productShotTypes.find((s) => s.value === block.shot)?.label || '고스트샷')
    : (catalogs.shotTypes.find((s) => s.value === block.shot)?.label || '—');
  const closureOptions = catalogs.outerClosureStates || [];
  const closureValue = closureOptions.some((option) => option.value === block.outerClosureState) ? block.outerClosureState : 'open';
  const closureLabel = closureOptions.find((option) => option.value === closureValue)?.label || '전체 열림';
  const showOuterClosure = clothingType === 'outer' && block.source === 'ai' && WORN_CUT_TYPES.has(block.cutType);
  return (
    <div className={`sb-card${selected ? ' on' : ''}${locked ? ' locked' : ''}`} onClick={onSelect}>
      <div className="sb-cardface">
        <span className="sb-grip" title="드래그로 순서 변경" onClick={(e) => e.stopPropagation()} {...(gripDrag || {})}>
          <svg width="14" height="20" viewBox="0 0 14 20" aria-hidden="true"><g fill="currentColor"><circle cx="4" cy="4" r="1.7" /><circle cx="10" cy="4" r="1.7" /><circle cx="4" cy="10" r="1.7" /><circle cx="10" cy="10" r="1.7" /><circle cx="4" cy="16" r="1.7" /><circle cx="10" cy="16" r="1.7" /></g></svg>
        </span>
        <div className="thumb"><img src={block.thumb} alt="" /></div>
        <div className="sb-textcol">
          <div className="bk">{isMine ? '내 이미지' : block.title}
            {/* 같은 공간에서 이어 찍는 컷 묶음 표시 (spaceGroupId, ADR-0004) */}
            {!isMine && spaceTag && <span className="sb-space" title="같은 공간에서 이어 찍는 컷이에요">공간 {spaceTag}</span>}
          </div>
          {!isMine && (
            <div className="sb-reveal sb-detail-rows">
              {block.cutType ? (
                <>
                  {/* mirror 생성 레시피는 방향 개념이 없다 (ADR-0004) — 행 자체를 숨김 */}
                  {block.cutType !== 'mirror' && <div className="sb-detail">방향: {dirLabel}</div>}
                  <div className="sb-detail">샷 종류: {shotLabel}</div>
                  {showOuterClosure && <div className="sb-detail">아우터 열림 정도: {closureLabel}</div>}
                </>
              ) : <div className="sb-detail muted">사진 목적 미설정</div>}
            </div>
          )}
          {!isMine && block.cutType && cols.length > 0 && (
            <div className="sb-reveal sb-cfoot">
              {cols.map((c, i) => <span key={i} className="sb-cdot" style={{ background: c.hex }} title={c.label} />)}
            </div>
          )}
        </div>
        {(poseEdited || matchEdited || refMiniThumb) && (
          <div className="sb-eimgs">
            {poseEdited && <figure className="sb-eimg"><img src={block.poseThumb} alt="" /><figcaption>포즈</figcaption></figure>}
            {refMiniThumb && <figure className="sb-eimg"><img src={refMiniThumb} alt="" /><figcaption>{refMiniLabel}</figcaption></figure>}
            {matchEdited && matchThumb && <figure className="sb-eimg"><img src={matchThumb} alt="" /><figcaption>매칭 의류</figcaption></figure>}
          </div>
        )}
      </div>
      <div className="sb-actions" onClick={(e) => e.stopPropagation()}>
        <IconButton name="chevUp" size="sm" title="위로" onClick={onUp} />
        <IconButton name="chevDown" size="sm" title="아래로" onClick={onDown} />
        <IconButton name="copy" size="sm" title="복제" onClick={onDuplicate} />
        <IconButton name="trash" size="sm" title="삭제" onClick={onDelete} />
      </div>
    </div>
  );
}

function previewRowsForSection(items) {
  const rows = [];
  for (let pos = 0; pos < items.length;) {
    const first = items[pos];
    const rowId = first.b.layoutRowId;
    if (rowId && first.b.source !== 'mine') {
      let end = pos + 1;
      while (end < items.length && items[end].b.source !== 'mine' && items[end].b.layoutRowId === rowId) end += 1;
      if (end - pos > 1) {
        rows.push(items.slice(pos, end).map(({ b }) => b));
        pos = end;
        continue;
      }
    }
    rows.push([first.b]);
    pos += 1;
  }
  return rows;
}

function PagePreviewRail({ sections, selectedId, onHover, onSelect }) {
  return (
    <aside className="sb-preview-rail" aria-label="페이지 미리보기">
      <div className="sb-preview-head">
        <div className="sb-preview-title">페이지 미리보기</div>
        <div className="sb-preview-sub">이미지 구성</div>
      </div>
      <div className="sb-preview-page">
        {sections.map((section) => (
          <div key={`${section.id}:${section.start}`} className="sb-preview-section" role="group" aria-label={`${section.title} 이미지 구성`}>
            {previewRowsForSection(section.items).map((row, rowIndex) => (
              <div key={row[0]?.layoutRowId || row[0]?.id || rowIndex} className="sb-preview-row"
                style={{ '--sb-preview-cols': row.length }}>
                {row.map((block) => {
                  const thumb = block.thumb || (block.source === 'mine' ? block.ownImages?.[0] : null);
                  return (
                    <button key={block.id} type="button" data-preview-id={block.id}
                      className={`sb-preview-mini${block.id === selectedId ? ' is-selected' : ''}`}
                      aria-label={block.title || (block.source === 'mine' ? '내 이미지' : '컷')}
                      onMouseEnter={() => onHover(block.id)} onMouseLeave={() => onHover(null)}
                      onFocus={() => onHover(block.id)} onBlur={() => onHover(null)}
                      onClick={() => onSelect(block.id)}>
                      {thumb && <img src={thumb} alt="" />}
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}

const SECTION_ORDER = new Map(SECTION_ROLE_OPTIONS.map((option, index) => [option.value, index]));

/* 섹션은 블록이 0장이 되어도 사라지지 않는다. 빈 밴드의 start는 다음 섹션
   첫 블록 앞(없으면 맨 끝)이라 그 자리에서 새 블록을 다시 만들 수 있다. */
function deriveFixedSections(blocks) {
  const runs = deriveSections(blocks);
  return SECTION_ROLE_OPTIONS.flatMap((option, order) => {
    const existing = runs.filter((section) => section.role === option.value);
    if (existing.length) return existing;
    const nextIndex = blocks.findIndex((block) => (SECTION_ORDER.get(block.sectionRole) ?? 99) > order);
    return [{
      id: `empty:${option.value}`,
      title: option.label,
      role: option.value,
      layout: 'stack',
      custom: false,
      samePlace: false,
      spaceGroupId: null,
      start: nextIndex < 0 ? blocks.length : nextIndex,
      items: [],
    }];
  });
}

function dragGroupFor(blocks, id) {
  const index = blocks.findIndex((b) => b.id === id);
  if (index < 0) return null;
  const block = blocks[index];
  if (!block.layoutRowId) return { indexes: [index], ids: [id], items: [block] };
  const indexes = [];
  blocks.forEach((candidate, i) => {
    if (candidate.layoutRowId === block.layoutRowId && candidate.sectionId === block.sectionId) indexes.push(i);
  });
  const contiguous = indexes.length > 1 && indexes.every((value, i) => i === 0 || value === indexes[i - 1] + 1);
  if (!contiguous) return { indexes: [index], ids: [id], items: [block] };
  return { indexes, ids: indexes.map((i) => blocks[i].id), items: indexes.map((i) => blocks[i]) };
}

/* underline tab navigation for 컷 종류 — sliding indicator */
export function UnderlineTabs({ options, value, onChange }) {
  const ref = React.useRef(null);
  const [line, setLine] = React.useState({ left: 0, width: 0 });
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    const active = el.querySelector('.utab.on');
    if (active) setLine({ left: active.offsetLeft, width: active.offsetWidth });
  }, [value]);
  // initial measure after first paint
  React.useEffect(() => {
    const el = ref.current; if (!el) return;
    requestAnimationFrame(() => { const a = el.querySelector('.utab.on'); if (a) setLine({ left: a.offsetLeft, width: a.offsetWidth }); });
  }, []);
  return (
    <div className="utabs" ref={ref} style={{ '--ul-left': line.left + 'px', '--ul-width': line.width + 'px' }}>
      {options.map((o) => (
        <button key={o.value} className={`utab${value === o.value ? ' on' : ''}`} onClick={() => onChange(o.value)}>{o.label}</button>
      ))}
    </div>
  );
}

/* 대상 색상 — colored circles only (from product input) */
export function ColorDots({ colorOpts, value, onChange }) {
  return (
    <div className="color-dots">
      {colorOpts.map((c) => (
        <button key={c.id} className={`color-dot${value === c.id ? ' on' : ''}`} title={c.label} onClick={() => onChange(c.id)}>
          <span className="cd-fill" style={{ background: c.hex }} />
        </button>
      ))}
    </div>
  );
}

/* 샷 필터 아이콘 — 크롭 모양 픽토그램. 상의=위쪽, 하의=아래쪽 크롭
   (생성예시 수집 버킷 규칙과 동일 — 상의 미디움=머리~허리, 하의 미디움=다리~허리) */
function ShotIcon({ cut, shot, clothingType }) {
  if (cut === 'product') {
    return (
      <svg viewBox="16 6 68 72" preserveAspectRatio="xMidYMid meet" aria-hidden="true">
        <g transform={shot === 'detail' ? 'scale(1.35) translate(-13 -13)' : undefined}>
          <rect className="si" x="34" y="32" width="32" height="38" rx="6" />
          <rect className="si" x="24" y="32" width="12" height="17" rx="5" />
          <rect className="si" x="64" y="32" width="12" height="17" rx="5" />
        </g>
      </svg>
    );
  }
  const vbTop = { full: '18 0 64 106', medium: '18 0 64 54' };
  const vbBottom = { full: '18 0 64 106', medium: '18 34 64 68' };
  const vb = (clothingType === 'bottom' ? vbBottom : vbTop)[shot] || vbTop.full;
  return (
    <svg viewBox={vb} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
      <circle className="si" cx="50" cy="16" r="9" />
      <rect className="si" x="37" y="28" width="26" height="33" rx="10" />
      <rect className="si" x="39" y="61" width="9.5" height="42" rx="4.5" />
      <rect className="si" x="51.5" y="61" width="9.5" height="42" rx="4.5" />
      {cut === 'mirror' && <rect className="si" x="44" y="9" width="12" height="19" rx="2.5" />}
    </svg>
  );
}

/* 분위기 예시 — 갤러리가 주인공 (B+C안 확정, ADR-0004):
   · 샷 종류 = 갤러리의 아이콘 필터 타일 (설정과 같은 shot 필드를 바꾼다)
   · 생성예시 셀 선택 = 촬영 연출만 참고 — 예시 속 옷·신발·액세서리는 제외하고 exampleId로 생성 입력에 포함
   · 내 사진(refImages) = '+ 타일'로 갤러리에 통합 — 점선 테두리·배지, 분위기(조명·색감)만 참고
   · 카드가 사이드/뒷면이어도 선택한 예시의 전체 연출을 참고하되, 카드의 촬영 방향은 유지
   refs/exampleId 는 제어형 — 콘티는 블록이, 에디터 AI 패널은 패널 상태가 소유 (계약 §3.4/§6). */
export function MoodGuide({ catalogs, cut, direction, shot, onShotChange, shotOptions = null, clothingType = 'top', gender = null, exampleId, onExampleChange, refs = [], onRefsChange, onPickRef, refScope = 'all', onRefScopeChange, inSpace = false }) {
  const shotOpts = shotOptions || (cut === 'product' ? catalogs.productShotTypes
    : catalogs.shotTypes);
  const shotVal = shotOpts.some((s) => s.value === shot) ? shot : shotOpts[0].value;
  const examples = React.useMemo(() => selectGenerationExamples(catalogs.genExamples, {
    cutType: cut, shot: shotVal, clothingType, gender,
  }), [catalogs.genExamples, cut, shotVal, clothingType, gender]);
  const selectedExample = examples.find((example) => example.id === exampleId) || null;
  const moodOnly = (cut === 'styling' || cut === 'horizon') && !!direction && direction !== 'front';
  useEffect(() => {
    if (!exampleId || !onExampleChange) return;
    const published = selectedExample?.variants || [];
    if (!selectedExample || (inSpace && !published.includes('pose'))) {
      onExampleChange(null);
      return;
    }
    if (inSpace && refScope !== 'pose' && onRefScopeChange) {
      onRefScopeChange('pose');
      return;
    }
    if (!inSpace && (cut === 'product' || moodOnly) && refScope !== 'all' && onRefScopeChange) {
      onRefScopeChange('all');
      return;
    }
    if (cut !== 'product' && !published.includes(refScope) && onRefScopeChange) {
      onRefScopeChange('all');
    }
  }, [exampleId, selectedExample, inSpace, cut, moodOnly, refScope, onExampleChange, onRefScopeChange]);
  const unavailableReason = (scope) => scope === 'pose'
    ? '이 예시는 아직 포즈 전용 자산이 없어요'
    : '이 예시는 아직 배경 전용 자산이 없어요';
  return (
    <div className="insp-sec">
      {/* 같은 공간 묶음 안에서는 배경 기준이 묶음에 있으므로 예시는 '포즈 예시'로 강등 (P5 확정) */}
      <div className="sb-exhead"><label className="lbl">{cut === 'product' ? '생성 예시' : inSpace ? '포즈 예시' : '분위기 예시'}</label><span className="sb-exhint">내 사진은 이 프로젝트에서만</span></div>
      {onShotChange && (
        <div className="shot-tiles">
          {shotOpts.map((s) => (
            <button key={s.value} type="button" className={`shot-tile${shotVal === s.value ? ' on' : ''}`} onClick={() => onShotChange(s.value)}>
              <ShotIcon cut={cut} shot={s.value} clothingType={clothingType} />{s.label}
            </button>
          ))}
        </div>
      )}
      <div className={`sb-exgrid${moodOnly ? ' moodonly' : ''}`}>
        {examples.length === 0 && (
          <div className="sb-exempty">이 조건의 생성예시는 준비 중이에요</div>
        )}
        {examples.map((e) => {
          const on = exampleId === e.id;
          const variants = Array.isArray(e.variants) ? e.variants : [];
          const inSpaceDisabled = inSpace && !variants.includes('pose');
          // 레퍼런스 범위 — 호버 오버레이에서 선택. 시리즈 안은 '포즈만' 고정, 제품·비정면(moodOnly)은 범위 개념 없음.
          const scopeChoices = !onRefScopeChange || moodOnly || cut === 'product' ? null
            : inSpace ? [{ v: 'pose', l: '포즈만', disabled: !variants.includes('pose') }]
              : [
                { v: 'all', l: '전부', disabled: !variants.includes('all') },
                { v: 'bg', l: '배경만', disabled: !variants.includes('bg') },
                { v: 'pose', l: '포즈만', disabled: !variants.includes('pose') },
              ];
          const pick = (scope) => {
            if (!onExampleChange) return;
            if (!variants.includes(scope)) return;
            if (on && (refScope || 'all') === scope) { onExampleChange(null); return; }   // 같은 선택 재클릭 = 해제
            onExampleChange(e.id);
            if (onRefScopeChange) onRefScopeChange(scope);
          };
          const defaultScope = cut === 'product' || moodOnly ? 'all'
            : inSpace ? 'pose'
              : variants.includes(refScope || 'all') ? (refScope || 'all') : 'all';
          return (
            <button key={e.id} type="button" disabled={inSpaceDisabled}
              title={inSpaceDisabled ? unavailableReason('pose') : undefined}
              className={`sb-excell${on ? ' sel' : ''}${inSpaceDisabled ? ' unavailable' : ''}`}
              onClick={() => { if (on) onExampleChange?.(null); else pick(defaultScope); }}>
              <img src={e.thumb} alt="" />{on && <span className="ck"><Icon name="check" size={11} /></span>}
              {on && scopeChoices && <span className="sb-exscope">{SCOPE_LABELS[refScope || 'all'] || '전부'}</span>}
              {scopeChoices && (
                /* 오버레이 배경 클릭은 셀 기본 선택으로 통과(기존 클릭 선택 유지) — 버튼 클릭만 범위 지정 */
                <span className="sb-exov">
                  <span className="sb-exov-t">레퍼런스 범위</span>
                  <span className="sb-exov-b">
                    {scopeChoices.map((c) => (
                      <span key={c.v} role="button" tabIndex={c.disabled ? -1 : 0}
                        aria-disabled={c.disabled || undefined}
                        title={c.disabled ? unavailableReason(c.v) : undefined}
                        className={`sb-exov-btn${on && (refScope || 'all') === c.v ? ' on' : ''}${c.disabled ? ' unavailable' : ''}`}
                        onClick={(ev) => { ev.stopPropagation(); if (!c.disabled) pick(c.v); }}
                        onKeyDown={(ev) => { if (!c.disabled && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); ev.stopPropagation(); pick(c.v); } }}>
                        {c.l}
                      </span>
                    ))}
                  </span>
                </span>
              )}
            </button>
          );
        })}
        {refs.map((r, i) => (
          <span className="sb-excell up" key={'u' + i} title="분위기(조명·색감)만 참고해요. 옷과 모델은 바뀌지 않아요.">
            <img src={r?.url || r} alt="" /><span className="upb">내 사진</span>
            <button type="button" className="rm" onClick={() => onRefsChange && onRefsChange(refs.filter((_, j) => j !== i))}><Icon name="x" size={11} /></button>
          </span>
        ))}
        {onRefsChange && (
          <button type="button" className="sb-excell uptile" onClick={async () => {
            // 업로드({assetId,url}) — objectURL 이 아니라 서버 asset 이어야 생성에 실제 첨부된다(refAssetIds).
            const picked = await (onPickRef ? onPickRef() : api.pickRefImage(useAppStore.getState().projectId));
            if (picked) onRefsChange([...refs, picked]); // 취소(null)면 무시
          }}>
            <span className="plus">+</span>내 사진
          </button>
        )}
      </div>
      {moodOnly && <div className="sb-exnote">예시의 <b>포즈·구도·분위기</b>를 참고하되, 촬영 방향은 {direction === 'side' ? '사이드' : '뒷면'}로 유지해요.</div>}
      {/* 레퍼런스 범위 (P5 확정, 전부|포즈만|배경만) — 같은 공간 묶음은 포즈 고정, 제품 생성 레시피는 범위 개념 없음 */}
      {!moodOnly && exampleId && inSpace && (
        <div className="sb-exnote pick"><b>포즈만 참고해요</b> — 배경은 같은 공간 묶음의 기준을 따라요.</div>
      )}
      {!moodOnly && exampleId && !inSpace && cut === 'product' && (
        <div className="sb-exnote pick"><b>이 예시처럼 생성돼요</b> — 옷만 우리 걸로 교체</div>
      )}
      {!moodOnly && exampleId && !inSpace && cut !== 'product' && (
        refScope === 'pose'
          ? <div className="sb-exnote">포즈만 참고해요. 배경에 맞지 않으면 자세와 구도가 자연스럽게 조정될 수 있어요.</div>
          : refScope === 'bg'
            ? <div className="sb-exnote">배경·분위기만 참고해요. 포즈는 이 옷과 장소에 어울리게 새로 잡혀요.</div>
            : <div className="sb-exnote pick"><b>이 예시처럼 생성돼요</b> — 상품·매칭 의류와 우리 모델로 교체</div>
      )}
    </div>
  );
}

function Inspector({ block, catalogs, colorOpts, detailColorOpts, clothingType, exampleGender, hasDetailImage, mode, onMode, onChange, matchClothing, dirty, warn, onDone, onRevert, onAddMine, onImgDrag, onCancelNew, isNew }) {
  const doneRef = useRef(null);
  useEffect(() => { if (warn && doneRef.current) doneRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, [warn]);

  if (!block) return (
    <div className="surface inspector empty-insp">
      <EmptyState icon="layout" title="블록을 선택해 수정하세요" desc="좌측에서 수정하고싶은 카드를 선택하거나 아래 버튼으로 내 이미지를 추가하세요." />
      <button className="mine-add-big" onClick={onAddMine}><Icon name="upload" size={20} />내 이미지 업로드</button>
    </div>
  );

  const closureOptions = catalogs.outerClosureStates || [];
  const purposeOptions = contentTemplatesForSection(block.sectionRole, { hasDetailImage });
  const onPurpose = (role) => {
    if (!role) return; // 사진 목적은 필수 단일 선택 — 활성 칩 재클릭으로 해제하지 않는다.
    onChange((current) => {
      const purposePatch = blockPatchForContentRole(current, role, { clothingType });
      const feedback = referenceFeedbackPatch(current, purposePatch, catalogs);
      const nextColorOpts = role === CONTENT_ROLES.DETAIL ? detailColorOpts : colorOpts;
      const colorId = nextColorOpts.some((color) => color.id === current.colorId)
        ? current.colorId : nextColorOpts[0]?.id;
      const category = purposePatch.cutType === 'product' ? 'product' : purposePatch.cutType === 'horizon' ? 'horizon' : 'styling';
      return { ...feedback, colorId, baseThumb: null, thumb: Placeholder.photo(`purpose_${current.id}_${role}`, category, 240, 320) };
    });
  };

  // 내 이미지 = 직접 삽입 흐름 (PRD 8.8) — no AI options
  const isMine = block.source === 'mine';
  if (isMine) {
    return (
      <div className="surface inspector">
        <div className="sec-title" style={{ fontSize: 15, marginBottom: 6 }}>내 이미지</div>
        <div className="insp-note" style={{ marginBottom: 14 }}><Icon name="info" size={14} />내 이미지는 가지고 있는 이미지를 그대로 삽입해요. AI 생성 옵션은 적용되지 않습니다.</div>
        {(block.ownImages || []).length > 0 && (
          <div className="thumb-grid cols3" style={{ marginBottom: 12 }}>
            {block.ownImages.map((src, i) => (
              <div className="tg-cell mine-drag" key={i} draggable
                onDragStart={(e) => { e.dataTransfer.effectAllowed = 'copy'; e.dataTransfer.setData('text/mineimg', src); onImgDrag && onImgDrag(src); }}
                onDragEnd={() => onImgDrag && onImgDrag(null)} title="블록 사이로 끌어 넣기">
                <img src={src} alt="" />
                <button className="rm" onClick={() => onChange({ ownImages: block.ownImages.filter((_, j) => j !== i) })}><Icon name="x" size={11} /></button>
              </div>
            ))}
          </div>
        )}
        <button className="ref-upload" onClick={async () => onChange({ ownImages: [...(block.ownImages || []), await api.pickAnyImage()] })}>
          <Icon name="upload" size={16} />로컬에서 이미지 업로드
        </button>
      </div>
    );
  }

  // 매칭 의류 detail editor (AI cuts only) — 포즈 편집은 폐기(2026-07-10): 포즈는 생성예시(레퍼런스 범위)가 담당
  if (mode === 'edit') {
    return (
      <div className="surface inspector insp-edit-panel">
        <div className="insp-edit-head">
          <Button variant="quiet" size="sm" icon="arrowLeft" onClick={() => onMode('props')}>뒤로 가기</Button>
        </div>
        <div className="sec-title" style={{ fontSize: 15, margin: '2px 0 14px' }}>{block.title} · 매칭 의류 편집</div>
        {matchClothing && (
          <div className="insp-sec">
            <label className="lbl">매칭 의류<span className="opt" style={{ fontWeight: 400, color: 'var(--fg-3)', marginLeft: 6 }}>착용컷에 함께</span></label>
            <div className="match-grid" style={{ marginTop: 9 }}>
              {matchClothing.map((m) => {
                const on = (block.matchIds || []).includes(m.id);
                return (
                  <button key={m.id} className={`match-cell${on ? ' on' : ''}`} onClick={() => {
                    const cur = new Set(block.matchIds || []); on ? cur.delete(m.id) : cur.add(m.id); onChange({ matchIds: [...cur] });
                  }}><img src={m.thumb} alt={m.name} /><span className="ml">{m.name}{on && <Icon name="check" size={12} />}</span></button>
                );
              })}
            </div>
          </div>
        )}
        <div className="insp-note"><Icon name="info" size={14} />변경 사항은 다음 생성 단계에서 적용돼요.</div>
      </div>
    );
  }

  const isProduct = block.cutType === 'product';
  const isMirror = block.cutType === 'mirror';
  const isDetail = block.contentRole === CONTENT_ROLES.DETAIL;
  const productShotOptions = isDetail
    ? catalogs.productShotTypes.filter((option) => option.value === 'detail')
    : catalogs.productShotTypes.filter((option) => option.value !== 'detail');
  const showOuterClosure = clothingType === 'outer' && block.source === 'ai' && WORN_CUT_TYPES.has(block.cutType);
  const outerClosureState = closureOptions.some((option) => option.value === block.outerClosureState) ? block.outerClosureState : 'open';
  return (
    <div className="surface inspector">
      <div className="insp-sec">
        <label className="lbl">{sectionTitle(block.sectionRole)}에서 이 사진의 역할</label>
        <Chips options={purposeOptions} value={block.contentRole} onChange={onPurpose} />
        <p className="hint" style={{ marginTop: 8 }}>{purposeOptions.find((option) => option.value === block.contentRole)?.description}</p>
      </div>
      {isNew && (
        <div className="insp-newmeta">
          <span>추가 위치: {sectionTitle(block.sectionRole)} · 생성 시 크레딧 {catalogs.creditCosts?.storyboardPerCut ?? 1}</span>
          <button className="insp-cancel-new" onClick={onCancelNew}><Icon name="trash" size={13} />이 블록 취소</button>
        </div>
      )}

      {!block.cutType ? (
        <>
          <div className="insp-empty-hint"><Icon name="arrowUp" size={15} />사진의 역할을 먼저 선택하면 세부 설정이 나타나요.</div>
          {/* 새 컷 컨텍스트 — 어디에 추가되는지·비용, 그리고 흔적 없는 취소 (P6) */}
          <div className="insp-newmeta">
            <span>{block.sectionTitle ? `추가 위치: ${block.sectionTitle}` : '새 컷'} · 생성 시 크레딧 {catalogs.creditCosts?.storyboardPerCut ?? 1}</span>
            {onCancelNew && <button className="insp-cancel-new" onClick={onCancelNew}><Icon name="trash" size={13} />이 블록 취소</button>}
          </div>
        </>
      ) : (
        <>
      {/* 분위기 예시가 주인공 — 샷 종류는 갤러리의 아이콘 필터, 방향은 아래로 강등 (B+C안, ADR-0004) */}
      <MoodGuide catalogs={catalogs} cut={block.cutType} direction={block.direction} shot={block.shot}
        shotOptions={isProduct ? productShotOptions : null}
        onShotChange={(v) => onChange((current) => referenceFeedbackPatch(current, { shot: v, exampleId: null }, catalogs))} clothingType={clothingType} gender={exampleGender}
        exampleId={block.exampleId || null}
        onExampleChange={(v) => onChange((current) => referenceFeedbackPatch(current, {
          exampleId: v, refScope: !v ? 'all' : (current.spaceGroupId ? 'pose' : (current.refScope || 'all')),
        }, catalogs))}
        refScope={block.refScope || 'all'} onRefScopeChange={(v) => onChange((current) => referenceFeedbackPatch(current, { refScope: v }, catalogs))} inSpace={!!block.spaceGroupId}
        refs={(block.refImages || []).map((u, i) => ({ url: u?.url || u, assetId: u?.assetId || (block.refAssetIds || [])[i] }))}
        onRefsChange={(r) => onChange({
          refImages: r.map((x) => x?.url || x),                        // 표시용 URL
          refAssetIds: r.map((x) => x?.assetId).filter(Boolean),       // 서버 첨부용 asset id (계약 §6)
        })} />

      {/* 방향 — mirror 생성 레시피는 방향 개념 없음 (ADR-0004) */}
      {!isMirror && !isDetail && (
        <div className="insp-sec"><label className="lbl">방향</label>
          <Chips options={isProduct ? catalogs.productDirections : catalogs.directions}
            value={(isProduct ? catalogs.productDirections : catalogs.directions).some((d) => d.value === block.direction) ? block.direction : 'front'}
            onChange={(v) => onChange({ direction: v })} /></div>
      )}

      {showOuterClosure && (
        <div className="insp-sec outer-closure-field">
          <div className="lbl" id={`outer-closure-label-${block.id}`}>아우터 열림 정도</div>
          <div className="outer-closure-options" role="radiogroup" aria-labelledby={`outer-closure-label-${block.id}`}>
            {closureOptions.map((option) => {
              const on = outerClosureState === option.value;
              return (
                <label key={option.value} className={`outer-closure-option${on ? ' on' : ''}`}>
                  <input type="radio" name={`outer-closure-${block.id}`} value={option.value}
                    checked={on} onChange={() => onChange({ outerClosureState: option.value })} />
                  <OuterClosureIcon state={option.value} />
                  <span>{option.label}</span>
                </label>
              );
            })}
          </div>
          <p className="outer-closure-hint">이 컷에서 아우터의 앞부분을 얼마나 열지 정해요.</p>
        </div>
      )}

      <div className="insp-divider" />

      <div className="insp-sec"><label className="lbl">대상 색상</label>
        <ColorDots colorOpts={isDetail ? detailColorOpts : colorOpts}
          value={block.colorId} onChange={(v) => onChange({ colorId: v })} /></div>

      {/* 매칭 의류가 없으면 편집 패널이 빈 화면이 되므로 진입 자체를 막는다 */}
      {WORN_CUT_TYPES.has(block.cutType) && Array.isArray(matchClothing) && matchClothing.length > 0 && (
        <button className="insp-detail-btn" onClick={() => onMode('edit')}>
          <Icon name="settings" size={17} />매칭 의류 편집
        </button>
      )}

      {/* 추가 옵션 — 이미지별 얼굴 노출 / 앵글 (PRD 6.8, 9.x). mirror 레시피는 얼굴 기본 '폰으로 가림', 앵글 없음 (ADR-0004) */}
      <details className="insp-extra">
        <summary><Icon name="chevDown" size={15} />추가 옵션</summary>
        <div className="insp-sec" style={{ marginTop: 12 }}><label className="lbl">모델 얼굴</label>
          <Chips options={isMirror
            ? [{ value: 'hide', label: '폰으로 가림' }, { value: 'show', label: '노출' }]
            : [{ value: 'same', label: '동일' }, { value: 'show', label: '노출' }, { value: 'hide', label: '비노출' }]}
            value={isMirror ? (block.faceExposure === 'show' ? 'show' : 'hide') : (block.faceExposure || 'same')}
            onChange={(v) => onChange({ faceExposure: v })} /></div>
        {!isMirror && <div className="insp-sec"><label className="lbl">앵글</label>
          <Chips options={[{ value: 'same', label: '동일' }, { value: 'low', label: '로우' }, { value: 'high', label: '하이' }]}
            value={block.angle || 'same'} onChange={(v) => onChange({ angle: v })} /></div>}
      </details>
        </>
      )}

      <div ref={doneRef}>
        {warn && <div className="insp-warn">수정 완료를 먼저 눌러주세요</div>}
        {dirty && (
          <div className="insp-done-row">
            <button className="insp-revert" onClick={onRevert}><Icon name="undo" size={16} />원래대로</button>
            <button className="insp-done pulse" onClick={onDone}><Icon name="check" size={16} />수정 완료</button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Storyboard() {
  const navigate = useNavigate();
  const [blocks, setBlocks] = useState(null);
  const [catalogs, setCatalogs] = useState(null);
  const [matchClothing, setMatchClothing] = useState(null);
  const [colorOpts, setColorOpts] = useState([]);
  const [detailColorOpts, setDetailColorOpts] = useState([]);
  const [clothingType, setClothingType] = useState('top'); // 샷 필터 아이콘·예시 크롭용 (상의=위/하의=아래)
  const [exampleGender, setExampleGender] = useState(null);
  const [hasDetailImage, setHasDetailImage] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [splitOpen, setSplitOpen] = useState(false); // 한 번이라도 카드를 열면 좌/우 분할 유지
  const [mode, setMode] = useState('props');
  const [dirty, setDirty] = useState(false);
  const [dragId, setDragId] = useState(null);
  const [dragOver, setDragOver] = useState(null);
  const [dragOverSec, setDragOverSec] = useState(null); // 호버 중인 드롭 대상 섹션 — 하이라이트와 드롭이 같은 신호를 쓴다
  const [dragMine, setDragMine] = useState(null);
  const [collapsed, setCollapsed] = useState(() => new Set()); // 접힌 섹션 id (UI 전용, 저장 안 함)
  const [warn, setWarn] = useState(false);
  const [previewHoverId, setPreviewHoverId] = useState(null);
  const snapRef = useRef(null);
  const newSeq = useRef(0);
  const cardRefs = useRef(new Map());
  // 보드 스크롤 ↔ 프리뷰 중심 동기화 — 뷰포트 중앙에 가장 가까운 카드의 미니가 레일 중앙 근처에 오도록
  const scrollRaf = useRef(null);
  useEffect(() => {
    const onScroll = (e) => {
      // 허용 목록: 보드가 실제로 움직이는 스크롤만 동기화 트리거로 —
      // 솔로 모드 = window(document) 스크롤, 분할 모드 = 카드 칼럼(.sb-scroll-l).
      // 인스펙터(.insp-col)·프리뷰 레일 등 다른 스크롤 컨테이너는 무시(스냅백·헛동기화 방지).
      const t = e.target;
      const fromBoard = t === document || (t instanceof Element && !!t.closest('.sb-scroll-l'));
      if (!fromBoard) return;
      if (scrollRaf.current) return;
      scrollRaf.current = requestAnimationFrame(() => {
        scrollRaf.current = null;
        const rail = document.querySelector('.sb-preview-rail'); if (!rail || rail.scrollHeight <= rail.clientHeight) return;
        const center = window.innerHeight / 2;
        let bestId = null, bestD = Infinity;
        cardRefs.current.forEach((el, id) => {
          if (!el || !el.isConnected) return;
          const r = el.getBoundingClientRect();
          const d = Math.abs((r.top + r.bottom) / 2 - center);
          if (d < bestD) { bestD = d; bestId = id; }
        });
        if (bestId == null) return;
        const mini = rail.querySelector(`[data-preview-id="${bestId}"]`);
        if (!mini) return;
        const mr = mini.getBoundingClientRect(); const rr = rail.getBoundingClientRect();
        rail.scrollTop = rail.scrollTop + (mr.top - rr.top) - rail.clientHeight / 2 + mr.height / 2;
      });
    };
    // capture: true — 스크롤은 버블링되지 않으므로, 분할 화면의 카드 칼럼(.sb-scroll-l 자체 스크롤)과
    // 솔로 화면의 window 스크롤을 모두 캡처 단계에서 받는다. (창 리스너만으로는 내부 컨테이너를 놓침)
    window.addEventListener('scroll', onScroll, { passive: true, capture: true });
    return () => { window.removeEventListener('scroll', onScroll, { capture: true }); if (scrollRaf.current) cancelAnimationFrame(scrollRaf.current); };
  }, []);
  const toast = useToast();
  // 카피라이팅 토글 = 플로우 선택값 (store → patchProject 동기화, ADR-0002)
  const projectId = useAppStore((s) => s.projectId);
  const copyOn = useAppStore((s) => s.copywriting);
  const setCopyOn = useAppStore((s) => s.setCopywriting);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)

  useEffect(() => {
    (async () => {
      await useAppStore.getState().loadProject();
      const pid = useAppStore.getState().projectId;
      if (!pid) { navigate('/create/input', { replace: true }); return; }  // 콜드 진입(복원 불가) → 입력
      pidRef.current = pid;   // 이 인스턴스의 저장 대상 고정 (프로젝트 경계)
      await sbSaveIdle();     // 직전 인스턴스의 비행 중 저장(이탈 플러시)이 착지한 뒤에 읽는다 — 스테일 로드 방지
      const [b, c, m, p, a] = await Promise.all([
        api.getStoryboard(pid), api.getCatalogs(), api.getMatchClothing(pid),
        api.getProduct(pid), api.getAnalysis(pid),
      ]);
      // 직전 이탈 저장 실패분 복원 — 단, "서버가 우리가 마지막으로 알던 상태 그대로"일 때만.
      // 서버가 변했다면 다른 탭/기기의 더 새로운 저장이므로 보관분을 폐기하고 서버본을 따른다(침묵 덮어쓰기 금지).
      let pending = sbPending.get(pid);
      const baseline = sbLastSaved.get(pid);
      // 1순위: 보관분이 서버와 내용 동일 = '실패'로 기록됐지만 실제로 착지했던 저장(응답 유실).
      //        기준선 일치 여부와 무관하게 최우선 정리 — 안 하면 불필요한 복원·재저장 루프에 빠진다.
      if (pending && sbStable(b) === sbStable(pending)) { sbPending.delete(pid); pending = null; }
      const serverUnchanged = baseline != null && sbStable(b) === sbStable(baseline);
      const usePending = !!pending && serverUnchanged;
      if (pending && !usePending) {
        // 진짜 충돌(서버가 보관분·기준선과 다른 제3의 내용) — 폐기하되 침묵하지 않는다
        sbPending.delete(pid);
        toast.push('다른 곳에서 저장된 최신 콘티를 불러왔어요 — 이전에 저장 못 한 변경은 반영되지 않았어요');
      }
      if (usePending) sbSkipFirstSave.current = false;   // 복원분은 미저장 상태 — 첫 자동저장 생략 없이 즉시 재시도
      else sbLastSaved.set(pid, b);   // 이번 로드의 서버 상태를 기준선으로 기록 — 다음 복원 판별의 비교 대상
      const sourceBlocks = usePending ? pending : b;
      const productHasDetail = hasDetailSource(p);
      const initBlocks = ensureSections(sourceBlocks, { hasDetailImage: productHasDetail }).map((block) => ({
        ...block, ...referenceFeedbackPatch(block, {}, c),
      }));
      const normalized = sbStable(initBlocks) !== sbStable(sourceBlocks);
      setCollapsed(new Set(deriveSections(initBlocks).map((s) => s.id)));   // 진입 기본 상태 = 모든 섹션 접힘 (사용자 확정)
      setBlocks(initBlocks); setCatalogs(c); setMatchClothing(m); setClothingType(p.clothingType || 'top');
      setExampleGender(exampleGenderFromAnalysis(a, c)); setHasDetailImage(productHasDetail);
      if (normalized) sbSkipFirstSave.current = false; // v2 계약 정규화 결과를 자동저장한다.
      const allColorOpts = (p.colors || []).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexFor(col) }));
      const opts = allColorOpts.filter((_option, index) => (p.colors[index].images || []).length || p.colors[index].isBase);
      setDetailColorOpts(allColorOpts.length ? allColorOpts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
      setColorOpts(opts.length ? opts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
    })();
  }, []);
  // 콘티 편집 자동저장 — Editor 와 동일 패턴(1.5s debounce). generate 클릭 전 이탈해도 콘티 유실 없음.
  const saveTimer = useRef(null);
  const latestBlocks = useRef(null);
  const pidRef = useRef(null);   // 이 인스턴스가 로드한 프로젝트 — 플러시가 스토어의 "현재" id(새 프로젝트로 바뀌었을 수 있음)를 쓰지 않게 고정
  const pendingRowRestore = useRef(null);   // 새 블록 삽입이 가른 행 { blockId, rowId, memberIds } — 취소 시 복원용
  const saveNow = (pid) => sbSaveNow(pid, () => latestBlocks.current);
  useEffect(() => { latestBlocks.current = blocks; }, [blocks]);
  const sbSkipFirstSave = useRef(true);
  useEffect(() => {
    if (blocks == null || !projectId) return;
    if (sbSkipFirstSave.current) { sbSkipFirstSave.current = false; return; }  // 최초 로드분은 저장 생략(불필요 dirty 방지)
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { saveNow(projectId).catch(() => {}); }, 1500);
    return () => clearTimeout(saveTimer.current);
  }, [blocks, projectId]);
  // 언마운트 시 보류 자동저장 플러시 — '이전' 등 이탈 직전 1.5s 안의 변경 유실 방지.
  // saveNow 체인을 타므로: 비행 중 저장 뒤에 줄서고(순서 보장), 변경 없으면 안 쏘고, 실패분은 재시도된다.
  useEffect(() => () => {
    clearTimeout(saveTimer.current);
    saveNow(pidRef.current).catch(() => {});   // 이 인스턴스가 로드했던 프로젝트로만 저장 (경계 고정)
  }, []);
  // 컬러 비교 자격 상실(색 통일·시리즈 편입 등) 시 즉시 세로로 강등 — 무효 레이아웃이 저장·조립에 남지 않게.
  // 주의: 훅은 아래 로딩 early-return 위에 있어야 한다 (훅 개수 불변 규칙).
  // autoDemoteTrail: 이 effect 가 만든 배열 → 원본 계보. 삭제-undo 의 "변경 없음" 판정이
  // 자동 강등(계보 위)과 사용자 레이아웃 변경(계보 밖)을 필드가 아니라 **행위자**로 구분하게 한다.
  const autoDemoteTrail = useRef(new WeakMap());
  useEffect(() => {
    if (!blocks) return;
    for (const s of deriveSections(blocks)) {
      if (s.layout !== 'colorCompare') continue;
      const cset = new Set(s.items.filter(({ b }) => b.source !== 'mine' && b.colorId).map(({ b }) => b.colorId));
      if (s.samePlace || cset.size < 2) {
        setBlocks((bs) => {
          const next = patchSection(bs, s.id, { sectionLayout: 'stack' });
          autoDemoteTrail.current.set(next, bs);
          return next;
        });
        return;
      }
    }
  }, [blocks]);
  if (!blocks || !catalogs) return <div className="wizard wide">{doneBlocked && <DoneGuardModal />}<div className="surface"><Skeleton h={400} /></div></div>;

  const selected = blocks.find((b) => b.id === selectedId);
  const isMineSel = selected && selected.source === 'mine';
  const patch = (id, changes) => {
    setBlocks((bs) => {
      const current = bs.find((b) => b.id === id);
      const p = typeof changes === 'function' ? changes(current) : changes;
      const oldRowId = current?.layoutRowId;
      return bs.map((b) => {
        if (b.id === id) {
          const updated = { ...b, ...p };
          return p.source === 'mine' ? withoutLayoutRow(updated) : updated;
        }
        // 내 이미지로 전환한 컷은 행에 남을 수 없으므로 기존 행도 함께 해제한다.
        return p.source === 'mine' && oldRowId && b.layoutRowId === oldRowId ? withoutLayoutRow(b) : b;
      });
    });
    // 사진 목적이 정해지는 순간 placeholder가 생성 대상이 되면 레이아웃 배타 규칙을 다시 적용한다.
    const cur0 = blocks.find((x) => x.id === id);
    const applied = typeof changes === 'function' ? changes(cur0) : changes;
    if (applied && 'cutType' in applied && applied.cutType && cur0 && !cur0.cutType) setBlocks((bs) => normalizeBoard(bs));
    const b = cur0;
    if (!b || b.source !== 'mine' || typeof changes === 'function' || ('source' in changes)) setDirty(true);
  };
  const selectCard = (id) => {
    if (selectedId === id) { finishEdit(); return; }      // click again → deselect
    const cur = blocks.find((b) => b.id === selectedId);
    const curLocked = selectedId && dirty && cur && cur.source !== 'mine';   // 내 이미지는 잠그지 않음
    if (curLocked) { setWarn(true); return; }
    const target = blocks.find((b) => b.id === id);
    snapRef.current = target ? { ...target } : null;
    setSelectedId(id); setMode('props'); setDirty(false); setWarn(false); setSplitOpen(true);
  };
  const finishEdit = () => { pendingRowRestore.current = null; setSelectedId(null); setMode('props'); setDirty(false); setWarn(false); snapRef.current = null; };
  const revertEdit = () => {
    if (snapRef.current) {
      const snap = snapRef.current;
      // 구조 필드(섹션·공간)는 현재값 유지 — 편집 중 이동 후 '원래대로'가 옛 소속을 되살려
      // 같은 섹션 id가 비연속으로 쪼개지는 것 방지. 되돌림은 인스펙터 소유 필드만.
      setBlocks((bs) => bs.map((b) => b.id === snap.id ? {
        ...snap,
        sectionId: b.sectionId, sectionTitle: b.sectionTitle, sectionLayout: b.sectionLayout,
        sectionRole: b.sectionRole, taxonomyVersion: b.taxonomyVersion,
        sectionCustom: b.sectionCustom, spaceGroupId: b.spaceGroupId, spaceVariation: b.spaceVariation,
        layoutRowId: b.layoutRowId, layoutRowVersion: b.layoutRowVersion,
      } : b));
    }
    setSelectedId(null); setMode('props'); setDirty(false); setWarn(false); snapRef.current = null;
  };
  const duplicate = (id) => setBlocks((bs) => {
    const i = bs.findIndex((b) => b.id === id); if (i < 0) return bs;
    const group = dragGroupFor(bs, id);
    const copy = { ...withoutLayoutRow(bs[i]), id: uid('blk') };
    const n = [...bs];
    // 행 안에 복제본을 끼워 넣어 기존 행의 연속성을 깨지 않도록 행 바로 뒤에 단일 컷으로 둔다.
    n.splice((group?.indexes[group.indexes.length - 1] ?? i) + 1, 0, copy);
    // 컷 수가 변한 섹션의 레이아웃 위생 — 삽입·이동 경로와 동일 규칙 (예: 2컷 twoColumn 에 복제 → 강등/재배치)
    return normalizeBoard(n);
  });
  const remove = (id) => {
    const idx = blocks.findIndex((b) => b.id === id); const removed = blocks[idx];
    const rowId = removed?.layoutRowId;
    const undoBlock = removed ? withoutLayoutRow(removed) : removed;
    // undo 정본 = 삭제 전 보드 통짜 스냅샷 — normalizeBoard 가 삭제 시 레이아웃을 강등하므로
    // 재삽입+재정규화만으론 원래 레이아웃·행 구성이 복원되지 않는다(addBlock 취소와 동일 패턴).
    const preDelete = blocks;
    let postDelete = null;   // 삭제 직후 상태 — identity 가 그대로일 때만 통짜 복원 유효
    setBlocks((bs) => {
      postDelete = normalizeBoard(bs.filter((b) => b.id !== id).map((b) => {
        // 삭제 규칙: 한 멤버가 사라지면 남은 파트너 전원의 행 id를 내려 모두 일반 단일 카드로 돌린다.
        // normalizeBoard: 컷 수가 줄어든 섹션의 레이아웃 위생(예: 3컷 threeColumn 에서 1개 삭제 → 스테일 레이아웃 해소)
        return rowId && b.layoutRowId === rowId ? withoutLayoutRow(b) : b;
      }));
      return postDelete;
    });
    if (selectedId === id) finishEdit();
    toast.push('블록을 삭제했어요', { undo: () => setBlocks((bs) => {
      if (!undoBlock) return bs;
      // "삭제 직후 그대로" 판정 = 현재 보드가 postDelete 이거나 자동 강등 effect 가 postDelete
      // 로부터 만들어 온 계보(autoDemoteTrail) 위에 있을 때만 — 사용자 조작(레이아웃 칩 포함)은
      // 계보에 없으므로 자동으로 폴백. 판정되면 스냅샷 통짜 복원(레이아웃·행·소속까지 원복,
      // 복원 후 effect 가 preDelete 기준으로 재평가).
      let cur = bs, unchanged = false;
      for (let hop = 0; cur && hop < 8; hop += 1) {
        if (cur === postDelete) { unchanged = true; break; }
        cur = autoDemoteTrail.current.get(cur);
      }
      if (unchanged) return preDelete;
      // 그 사이 실제 조작이 있었으면 폴백: 재삽입 후 이웃 섹션 재채택 + 공통 위생
      const n = [...bs]; n.splice(Math.min(idx, n.length), 0, undoBlock);
      return normalizeBoard(adoptSection(n, undoBlock.id));
    }) });
  };
  // 이동 후 adoptSection — 섹션 경계를 넘으면 이웃 섹션을 채택하고 대상 섹션을 '직접 구성' 처리
  const moveBlock = (id, dir) => setBlocks((bs) => {
    const i = bs.findIndex((b) => b.id === id); const j = i + dir;
    if (i < 0 || j < 0 || j >= bs.length) return bs;
    const rowId = bs[i].layoutRowId;
    // 행 내부 화살표 이동은 멤버 순서만 바꾸고, 행 밖으로 빼는 이동은 행 전체를 단일 컷으로 해제한다.
    const base = rowId && bs[j].layoutRowId !== rowId
      ? bs.map((b) => b.layoutRowId === rowId ? withoutLayoutRow(b) : b)
      : [...bs];
    const n = [...base]; [n[i], n[j]] = [n[j], n[i]];
    return normalizeBoard(adoptSection(n, id));
  });
  const addBlock = (idx, targetSid, targetRole = null) => {
    newSeq.current += 1;
    const targetHost = blocks.find((b) => b.sectionId === targetSid);
    const host = targetHost || (!targetRole ? blocks[Math.max(0, Math.min(idx - 1, blocks.length - 1))] : null);
    const sectionRole = targetRole || host?.sectionRole || SECTION_ROLES.BENEFIT;
    const firstPurpose = contentTemplatesForSection(sectionRole, { hasDetailImage })[0]?.value || CONTENT_ROLES.HERO;
    const purposePatch = blockPatchForContentRole(null, firstPurpose, { clothingType });
    // 추가 위치의 대표 사진 목적을 바로 적용한다. 인스펙터에서 다른 목적으로 바꿀 수 있다.
    const nb = { id: uid('blk'), sectionRole, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION, colorId: colorOpts[0]?.id || 'col1',
      pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
      ...purposePatch,
      thumb: Placeholder.photo('new' + Date.now(), purposePatch.cutType === 'product' ? 'product' : purposePatch.cutType === 'horizon' ? 'horizon' : 'styling', 240, 320), poseThumb: Placeholder.pose('stand'), poseLabel: 'AI 자동' };
    setBlocks((bs) => {
      const m = [...bs]; m.splice(idx, 0, nb);
      let out = adoptSection(m, nb.id, targetSid, sectionRole);             // 이웃/명시된 섹션 소속으로 삽입
      // 빈 보드 등 이웃이 없으면 기본 섹션 부여 — 무소속(unsupported state) 블록 방지
      out = out.map((b) => b.id === nb.id && !b.sectionId
        ? { ...b, sectionId: uid('sec'), sectionTitle: sectionTitle(sectionRole), sectionLayout: 'stack' } : b);
      // 같은 공간 시리즈 섹션에의 '추가'는 명시 액션 = 시리즈 가입 — adoptSection 의 자동가입 금지 규칙은
      // '이동'용이다. 미가입 상태로 두면 deriveSections 가 시리즈를 해제해 SPACE 연속성·포즈 범위 계약이 깨진다.
      {
        const sid = out.find((b) => b.id === nb.id)?.sectionId;
        const peers = out.filter((b) => b.id !== nb.id && b.sectionId === sid);
        const g = peers.length && peers.every((b) => b.spaceGroupId && b.spaceGroupId === peers[0].spaceGroupId)
          ? peers[0] : null;
        if (g) out = out.map((b) => (b.id === nb.id
          ? { ...b, spaceGroupId: g.spaceGroupId, spaceVariation: g.spaceVariation ?? null } : b));
      }
      out = normalizeBoard(out);   // 행 한가운데 삽입·컷 수 변경에 따른 강등 등 공통 위생 (삽입 경로 공통 규칙)
      // 취소-복원 = 삽입 전 배열 통짜 보관 — 행 id 뿐 아니라 레이아웃 강등·소속까지 원상 복구.
      // "삽입 직후 상태 그대로"일 때만 유효(이후 어떤 조작이든 snapshot identity 가 바뀌어 자동 무효화).
      pendingRowRestore.current = { blockId: nb.id, preInsert: bs, snapshot: out };
      snapRef.current = { ...out.find((b) => b.id === nb.id) };             // '원래대로' 스냅샷은 소속 부여 후 기준 (섹션 유실 방지)
      return out;
    });
    setSelectedId(nb.id); setMode('props'); setDirty(false); setWarn(false); setSplitOpen(true);
    toast.push('블록을 추가했어요', { icon: 'plus' });
  };
  const mineBlock = (src, n) => ({
    id: uid('blk'), sectionRole: SECTION_ROLES.BENEFIT, contentRole: CONTENT_ROLES.CUSTOM, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
    title: '내 이미지', source: 'mine', cutType: null, colorId: colorOpts[0]?.id || 'col1',
    ownImages: [src], thumb: src, pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
    poseThumb: Placeholder.pose('stand'), poseLabel: '-',
  });
  const addMineBlock = async (idx) => {
    const src = await api.pickAnyImage();
    const nb = mineBlock(src, (newSeq.current += 1));
    // adoptSection — 화면(즉시)과 재진입(ensureSections 상속)이 같은 소속이 되도록 삽입 시점에 확정
    setBlocks((bs) => {
      const m = [...bs]; m.splice(idx == null ? m.length : idx, 0, nb);
      const adopted = adoptSection(m, nb.id);
      return adopted.find((b) => b.id === nb.id)?.sectionId ? adopted : ensureSections(adopted);
    });
    setSelectedId(nb.id); setMode('props'); setDirty(false); setSplitOpen(true);
    toast.push('내 이미지 블록을 추가했어요', { icon: 'plus' });
  };
  // drag-to-reorder blocks (with drop indicator)
  const onDragStart = (id) => (e) => {
    e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/blk', id);
    // 고스트 = 그립 점이 아니라 블록 카드 전체 — 잡은 지점 기준 오프셋 유지
    const node = cardRefs.current.get(id);
    if (node && e.dataTransfer.setDragImage) {
      const r = node.getBoundingClientRect();
      e.dataTransfer.setDragImage(node, Math.max(0, e.clientX - r.left), Math.max(0, e.clientY - r.top));
    }
    setDragId(id);
  };
  const onDragEnd = () => { setDragId(null); setDragOver(null); setDragOverSec(null); };
  const onDropAt = (idx, targetSid, targetRole = null) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const img = e.dataTransfer.getData('text/mineimg') || dragMine;
    setDragOver(null); setDragOverSec(null);
    if (img) { setDragMine(null); insertMineAt(idx, img, targetSid, targetRole); return; }   // 내 이미지를 새 블록으로 삽입
    const id = e.dataTransfer.getData('text/blk') || dragId; setDragId(null); if (!id) return;
    setBlocks((bs) => {
      const from = bs.findIndex((b) => b.id === id); if (from < 0) return bs;
      let to = idx; if (from < idx) to -= 1;
      // 드래그는 잡은 카드 "한 장"만 옮긴다 — 카드가 개별로 보이므로 행 전체 이동은 예상 밖 동작.
      // 행 멤버를 끌어내면 그 행은 해제하고, 남의 행 사이에 끼어들며 깨진 행 계약은 normalizeRows 가 일괄 정리한다.
      const movedRowId = bs[from].layoutRowId;
      const dissolve = (arr) => (movedRowId ? arr.map((b) => (b.layoutRowId === movedRowId ? withoutLayoutRow(b) : b)) : arr);
      if (to === from) {
        // 위치 불변 — 단, 다른 섹션 몸통(경계 드롭라인)에 놓였다면 소속만 바꾼다.
        if (!targetSid || bs[from].sectionId === targetSid) return bs;  // 진짜 제자리 드롭만 무시
        return normalizeBoard(dissolve(adoptSection(bs, id, targetSid, targetRole)));
      }
      const m = [...bs]; const [it] = m.splice(from, 1); m.splice(to, 0, it);
      return normalizeBoard(dissolve(adoptSection(m, id, targetSid, targetRole)));
    });
  };
  const insertMineAt = (idx, src, targetSid, targetRole = null) => {
    const nb = mineBlock(src, (newSeq.current += 1));
    // normalizeRows — 행 한가운데 끼어들면 그 행 계약을 해제 (드래그 이동과 동일 규칙)
    setBlocks((bs) => {
      const m = [...bs]; m.splice(idx, 0, nb);
      const adopted = adoptSection(m, nb.id, targetSid, targetRole);
      return normalizeBoard(adopted.find((b) => b.id === nb.id)?.sectionId ? adopted : ensureSections(adopted));
    });
    toast.push('내 이미지를 블록으로 넣었어요', { icon: 'plus' });
  };
  /* 섹션 접기/펼치기 (UI 전용) */
  const toggleSec = (id) => setCollapsed((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  /* 섹션 레이아웃 변경 — 멤버 전체 patch + 직접 구성 표시 */
  const setSecLayout = (sec, v) => {
    // 활성 칩도 다시 적용할 수 있어야 layoutRowId 없는 레거시 보드를 명시적으로 마이그레이션할 수 있다.
    setBlocks((bs) => patchSection(bs, sec.id, { sectionLayout: v, sectionCustom: true }));
  };
  const locked = !!selectedId && dirty && !isMineSel;
  const draggedBlock = dragId ? blocks.find((b) => b.id === dragId) : null;
  // 공간 그룹 라벨 — 보드 등장 순서대로 A, B, … (spaceGroupId → 표시용)
  const spaceLabels = {};
  blocks.forEach((b) => {
    if (b.spaceGroupId && !(b.spaceGroupId in spaceLabels)) spaceLabels[b.spaceGroupId] = String.fromCharCode(65 + Object.keys(spaceLabels).length);
  });
  const cardEl = ({ b: block, i }, sec) => {
    const isDragging = block.id === dragId;
    const crossSectionCardDrag = !!draggedBlock && draggedBlock.sectionId !== sec.id;
    return (
      <React.Fragment key={block.id}>
        <div className={`sb-dropline${dragOver === i && dragOverSec === sec.id ? ' on' : ''}${dragMine ? ' armed' : ''}`}
          onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); e.stopPropagation(); setDragOver(i); setDragOverSec(sec.id); } }}
          onDrop={onDropAt(i, sec.id, sec.role)} />
        <div ref={(node) => { if (node) cardRefs.current.set(block.id, node); else cardRefs.current.delete(block.id); }}
          className={`sb-drag${isDragging ? ' dragging' : ''}${previewHoverId === block.id ? ' preview-hover' : ''}`}
          onDragOver={(e) => {
            if (dragId || dragMine) {
              e.preventDefault(); e.stopPropagation();
              // 다른 섹션의 카드 몸통 = 섹션 끝 fallback 드롭 — 표시선도 실제 위치(섹션 끝)와 일치시킨다
              if (crossSectionCardDrag) { setDragOver(sec.start + sec.items.length); setDragOverSec(sec.id); return; }
              const r = e.currentTarget.getBoundingClientRect();
              setDragOver(e.clientY < r.top + r.height / 2 ? i : i + 1); setDragOverSec(sec.id);
            }
          }}
          onDrop={(e) => {
            if (crossSectionCardDrag) return; // 다른 섹션 카드 몸통은 섹션 끝 fallback 으로 버블링
            if (dragId || dragMine) onDropAt(dragOver == null ? i + 1 : dragOver, sec.id, sec.role)(e);
          }}>
          <StoryboardCard block={block} catalogs={catalogs} colorOpts={colorOpts} matchClothing={matchClothing} clothingType={clothingType}
            spaceTag={block.spaceGroupId && !sec.samePlace ? spaceLabels[block.spaceGroupId] : null}
            selected={block.id === selectedId} locked={locked && block.id !== selectedId}
            gripDrag={{ draggable: true, onDragStart: onDragStart(block.id), onDragEnd }}
            onSelect={() => selectCard(block.id)} onUp={() => moveBlock(block.id, -1)} onDown={() => moveBlock(block.id, 1)}
            onDuplicate={() => duplicate(block.id)} onDelete={() => remove(block.id)} />
        </div>
        <button className="sb-insert" onClick={() => addBlock(i + 1, sec.id, sec.role)} title="여기에 블록 추가">
          <span className="sb-insert-line" /><span className="sb-insert-pill"><Icon name="plus" size={15} />블록 추가</span><span className="sb-insert-line" />
        </button>
      </React.Fragment>
    );
  };
  /* 섹션 밴드 — depth=1. 헤더(접기·제목·컷수·레이아웃) + 기존 카드/드롭라인(전역 인덱스 유지) */
  const sections = deriveFixedSections(blocks);
  // 하이라이트 = 드롭과 동일한 단일 출처(dragOverSec) — 모든 드롭 지점이 자기 대상 섹션 id를 명시하므로 추론 없음.
  const hotSecId = dragOverSec;
  const list = (
    <div className="sb-cards">
      <div className="sb-list">
        {sections.map((sec) => {
          const isCol = collapsed.has(sec.id);
          const previewRevealsSection = isCol && sec.items.some(({ b }) => b.id === previewHoverId);
          const sectionOpen = !isCol || previewRevealsSection;
          const sectionEnd = sec.start + sec.items.length;
          // 레이아웃 칩 — 연속 AI 컷 수에 따라 제공. 컬러 비교만 별도 자격제(색상 2+, 시리즈 제외).
          // http 모드는 서버 조립(M-02)이 아직 소비하지 않아 전체 숨김("배선된 칩만" 규칙) — 설계문서 §4-7.
          const layoutUiOn = (import.meta.env.VITE_API_MODE ?? 'mock') !== 'http';
          const aiItems = sec.items.filter(({ b }) => b.source !== 'mine'); // 컷 수 정의는 normalizeSectionLayouts·patchSection 과 동일 (placeholder 포함)
          const colorSet = new Set(aiItems.filter(({ b }) => b.colorId).map(({ b }) => b.colorId));
          const cmpOk = !sec.samePlace && colorSet.size >= 2;
          const offeredGridLayout = aiItems.length === 2 ? 'twoColumn'
            : aiItems.length === 3 ? 'threeColumn'
              : aiItems.length === 4 ? 'grid2x2'
                : aiItems.length >= 5 ? 'twoColumn' : null;
          // 제공 옵션은 현재 컷 수로 매번 계산하되, 활성 상태는 저장된 layout 그대로 비교해 미제공 값을 강제로 바꾸지 않는다.
          const chipsOn = layoutUiOn && aiItems.length >= 2;   // 1컷 섹션은 '세로 1열' 표기 자체가 무의미 — 숨김
          const isCmp = sec.layout === 'colorCompare';
          const layoutCtl = chipsOn ? (

                  <span className="sb-layctl" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()} title="이 섹션이 상세페이지에 놓이는 방식">
                    <span className="sb-lay-l">레이아웃 설정</span>
                    <span className="sb-laychips">
                    <button className={`sb-lay${sec.layout === 'stack' ? ' on' : ''}`} onClick={() => setSecLayout(sec, 'stack')}>
                      <span className="pg pgs"><i /><i /></span>세로 1열
                    </button>
                    {offeredGridLayout === 'twoColumn' && (
                      <button className={`sb-lay${sec.layout === 'twoColumn' ? ' on' : ''}`} onClick={() => setSecLayout(sec, 'twoColumn')}>
                        <span className="pg pg2"><i /><i /></span>2단
                      </button>
                    )}
                    {offeredGridLayout === 'threeColumn' && (
                      <button className={`sb-lay${sec.layout === 'threeColumn' ? ' on' : ''}`} onClick={() => setSecLayout(sec, 'threeColumn')}>
                        <span className="pg pg3"><i /><i /><i /></span>3단
                      </button>
                    )}
                    {offeredGridLayout === 'grid2x2' && (
                      <button className={`sb-lay${sec.layout === 'grid2x2' ? ' on' : ''}`} onClick={() => setSecLayout(sec, 'grid2x2')}>
                        <span className="pg pg4"><i /><i /><i /><i /></span>2×2단
                      </button>
                    )}
                    {(cmpOk || isCmp) && (
                      <button className={`sb-lay${isCmp ? ' on' : ''}`} onClick={() => setSecLayout(sec, 'colorCompare')}>
                        <span className="pg pgc">{[...colorSet].slice(0, 2).map((cid) => { const c = colorOpts.find((x) => x.id === cid); return <i key={cid} style={c ? { background: c.hex } : undefined} />; })}</span>컬러 비교
                      </button>
                    )}
                  </span>
                  </span>
          ) : null;
          return (
            <React.Fragment key={sec.id + ':' + sec.start}>
            <section className={`sb-sec${sectionOpen ? ' open' : ''}${hotSecId === sec.id ? ' hot' : ''}${sec.custom ? ' edited' : ''}`}
              onDragOver={(e) => {
                if (!draggedBlock) return;
                if (draggedBlock.sectionId === sec.id) { setDragOver(null); setDragOverSec(null); return; }
                e.preventDefault(); setDragOver(sectionEnd); setDragOverSec(sec.id);
              }}
              onDrop={(e) => {
                if (draggedBlock && draggedBlock.sectionId !== sec.id) onDropAt(sectionEnd, sec.id, sec.role)(e);
              }}>
              {sectionOpen && (
              <div className="sb-sec-h" onClick={() => toggleSec(sec.id)} role="button" tabIndex={0} aria-expanded={sectionOpen}
                onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleSec(sec.id); } }}
                title={sectionOpen ? '접기' : '펼치기'}>
                <Icon name={sectionOpen ? 'chevDown' : 'chevRight'} size={15} />
                <span className="sb-sec-t">{sec.title}</span>
                <span className="sb-sec-n">{sec.items.length}컷</span>
                {layoutCtl}
              </div>
              )}
              {isCmp && layoutUiOn && sectionOpen && (
                <div className="sb-swrail" title="색상별 컷을 나란히 비교하는 레이아웃으로 생성돼요">
                  {[...colorSet].map((cid) => { const c = colorOpts.find((x) => x.id === cid); return c ? <span key={cid} className="sb-cdot" style={{ background: c.hex }} title={c.label} /> : null; })}
                  <span className="sb-swrail-t">색상별로 나란히 비교돼요</span>
                </div>
              )}
              {!sectionOpen ? (
                /* 히어로 덱(2026-07-11) — 접힌 섹션은 이미지가 밴드 전체를 차지, 이름·컷수·레이아웃은 이미지 우측 상단.
                   칩(button)을 품으므로 button 중첩 금지 → div role="button" */
                <div className="sb-deck-hero" role="button" tabIndex={0} aria-expanded={false} title="펼치기"
                  onClick={() => toggleSec(sec.id)}
                  onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggleSec(sec.id); } }}
                  onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); e.stopPropagation(); setDragOver(sectionEnd); setDragOverSec(sec.id); } }}
                  onDrop={(e) => { if (dragId || dragMine) onDropAt(sectionEnd, sec.id, sec.role)(e); }}>
                  <span className="sb-deck-fan">
                    {sec.items.slice(0, 3).map(({ b }, k) => (
                      <img key={k} src={b.thumb || (b.ownImages || [])[0]} alt="" style={{ zIndex: 3 - k }} />
                    ))}
                  </span>
                  <span className="sb-deck-meta">
                    <span className="sb-deck-top">
                      <span className="sb-deck-name">{sec.title}</span>
                      <span className="sb-deck-count">{sec.items.length}컷</span>
                      {layoutCtl}
                    </span>
                    <span className="sb-deck-hint">눌러서 펼쳐보기</span>
                  </span>
                </div>
              ) : (
                <>
                  <button className="sb-insert" onClick={() => addBlock(sec.start, sec.id, sec.role)} title="여기에 블록 추가">
                    <span className="sb-insert-line" /><span className="sb-insert-pill"><Icon name="plus" size={15} />블록 추가</span><span className="sb-insert-line" />
                  </button>
                  {sec.items.map((item) => cardEl(item, sec))}
                  {/* 섹션 꼬리 표시선 — 교차 드래그의 "섹션 끝" 드롭 위치를 섹션 안에서 점등.
                      경계 인덱스(sectionEnd)가 다음 섹션 첫 드롭라인과 겹치는 문제의 시각적 해소. */}
                  <div className={`sb-dropline${dragOver === sectionEnd && dragOverSec === sec.id ? ' on' : ''}${dragMine ? ' armed' : ''}`}
                    onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); e.stopPropagation(); setDragOver(sectionEnd); setDragOverSec(sec.id); } }}
                    onDrop={onDropAt(sectionEnd, sec.id, sec.role)} />
                </>
              )}
            </section>
            {/* 섹션 사이 hover 인서트 — 위 섹션 끝에 블록 추가 */}
            {sec.items.length > 0 && (
              <button className="sb-insert sb-insert-gap" onClick={() => addBlock(sectionEnd, sec.id, sec.role)} title="여기에 블록 추가">
                <span className="sb-insert-line" /><span className="sb-insert-pill"><Icon name="plus" size={15} />블록 추가</span><span className="sb-insert-line" />
              </button>
            )}
            </React.Fragment>
          );
        })}
        {/* 맨 아래 전역 드롭라인 — 마지막 섹션 id를 명시해 하이라이트·드롭이 같은 대상을 가리키게 (스테일 잔상·암묵 추론 제거) */}
        <div className={`sb-dropline${dragOver === blocks.length && sections.length === 0 ? ' on' : ''}${dragMine ? ' armed' : ''}`}
          onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); e.stopPropagation(); setDragOver(blocks.length); setDragOverSec(sections.length ? sections[sections.length - 1].id : null); } }}
          onDrop={onDropAt(blocks.length, sections.length ? sections[sections.length - 1].id : undefined, sections.at(-1)?.role)} />
      </div>
    </div>
  );

  const inspector = <Inspector block={selected} catalogs={catalogs} colorOpts={colorOpts} detailColorOpts={detailColorOpts} clothingType={clothingType} exampleGender={exampleGender} hasDetailImage={hasDetailImage} mode={mode} onMode={setMode}
    onChange={(p) => patch(selectedId, p)} matchClothing={matchClothing} dirty={dirty && !isMineSel} warn={warn} onDone={finishEdit} onRevert={revertEdit} onAddMine={addMineBlock}
    isNew={pendingRowRestore.current?.blockId === selectedId}
    onImgDrag={(v) => { setDragMine(v); if (v == null) { setDragOver(null); setDragOverSec(null); } }}
    onCancelNew={() => {
      // 취소 = 삽입 전 상태로: 블록 제거 + 이 삽입이 갈랐던 행 복원 (normalizeRows 가 인접성 재검증)
      const id = selectedId;
      const restore = pendingRowRestore.current; pendingRowRestore.current = null;
      // 스테일 방지: 삽입 이후 보드가 조금이라도 바뀌었으면(배치 재적용·드래그 등) 낡은 행 구성을 복원하지 않는다
      const valid = restore && restore.blockId === id && latestBlocks.current === restore.snapshot;
      setBlocks((bs) => valid ? restore.preInsert : normalizeBoard(bs.filter((b) => b.id !== id)));
      finishEdit();
      toast.push('블록을 취소했어요');
    }} />;

  const previewRail = <PagePreviewRail sections={sections} selectedId={selectedId} onHover={setPreviewHoverId}
    onSelect={(id) => {
      const sectionId = blocks.find((b) => b.id === id)?.sectionId;
      setCollapsed((current) => {
        if (!sectionId || !current.has(sectionId)) return current;
        const next = new Set(current); next.delete(sectionId); return next;
      });
      selectCard(id);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        cardRefs.current.get(id)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }));
    }} />;

  let body;
  if (!splitOpen) {
    // 처음 진입 — 카드들만 가운데 정렬, 우측 패널 없음
    body = (
      <div className="storyboard-solo-layout">
        {previewRail}
        <div className="sb-solo">
          {list}
          <button className="mine-add-solo" onClick={() => addMineBlock()}><Icon name="upload" size={17} />내 이미지 업로드</button>
        </div>
      </div>
    );
  } else {
    // 카드를 한 번이라도 열었으면 — 좌/우 분할(간격 좁게) 유지, 선택 없으면 우측에 빈 상태(내 이미지 업로드)
    body = <div className="storyboard-layout tight">{previewRail}<div className="sb-scroll-l">{list}</div><div className="insp-col">{inspector}</div></div>;
  }

  const cutCount = blocks.length;
  // 크레딧은 AI 생성 컷에만 — 내 이미지 블록은 생성 작업이 없어 제외 (계약 §6)
  const aiCount = blocks.filter((b) => b.source !== 'mine').length;
  const mineCount = cutCount - aiCount;
  const generate = async () => {
    // 방어: UI disabled 와 별개로 함수 자체도 게이트 — 다른 호출 경로가 생겨도 미설정 블록 생성 불가
    if (blocks.length === 0) return;
    if (blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))) { toast.push('사진 역할이 정해지지 않은 블록이 있어요'); return; }
    // 생성 입력은 서버가 저장된 콘티에서 읽는다 — CTA 에서 반드시 저장 (frontend_state_model §5).
    // 같은 직렬 체인 경유: 비행 중 자동저장 뒤에 줄서서 최신 스냅샷이 마지막에 반영됨을 보장.
    // 실패는 throw 로 전파돼 기존처럼 네비게이션이 중단된다.
    await saveNow(projectId);
    navigate('/create/generating');
  };
  return (
    <div className="wizard wide sb-page">
      {doneBlocked && <DoneGuardModal />}
      <PageHead title="상세페이지 초안 구성" sub="지금 보이는 이미지들은 예시입니다. 느낌만을 보고 필요한 컷은 수정하며 상세페이지를 생성해보세요." />
      <div className={`sb-count-head${splitOpen ? ' is-split' : ''}`}>
        구성컷: <strong>{cutCount}개</strong>
      </div>
      {body}

      {/* document-flow bottom action bar */}
      <div className="sb-actionbar">
        <div className="sb-ab-inner">
          <button className="btn btn-ghost" onClick={() => navigate('/create/mannequin')}><Icon name="arrowLeft" size={17} />이전</button>
          <div className="sb-ab-count">AI 생성 {aiCount}컷 · 셀러 사진 {mineCount}컷</div>
          <div className="sb-ab-copy">
            <Toggle on={copyOn} onChange={setCopyOn} />
            <div><div className="sec-title" style={{ fontSize: 14 }}>카피라이팅 {copyOn ? 'ON' : 'OFF'}</div>
              <div className="hint" style={{ marginTop: 1 }}>AI가 카피를 자동으로 넣어요</div></div>
          </div>
          <button className="btn btn-primary btn-lg sb-ab-go btn-glowring" onClick={generate}
            disabled={blocks.length === 0 || blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))}
            title={blocks.length === 0 ? '컷을 1개 이상 구성해주세요'
              : blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType)) ? '사진 역할이 정해지지 않은 블록이 있어요' : undefined}>
            <Icon name="sparkles" size={18} />상세페이지 생성하기 <Icon name="arrowRight" size={17} /> {aiCount * (catalogs.creditCosts?.storyboardPerCut ?? 1)} 크레딧
          </button>
        </div>
      </div>
    </div>
  );
}

export default Storyboard;
