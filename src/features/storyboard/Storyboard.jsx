/* =============================================================
   features/storyboard — ⑤ 콘티보드 (PRD §8)
   blocks 는 "서버 상태의 working copy" 패턴: 진입 시 fetch → 로컬 편집
   → 생성 CTA 에서 saveStoryboard 로 한 번에 저장 (frontend_state_model §4).
   사용자는 sectionRole(핵심 장점/핏·코디/제품 확인), 컷 종류와 생성예시를 다룬다.
   contentRole은 섹션·카드 위치·선택한 컷에서 정하는 내부 생성값이다.
   카피라이팅 토글은 store(copywriting) → patchProject 동기화.
   UnderlineTabs/ColorDots/MoodGuide/hexFor are exported for the editor.
   ============================================================= */
import React, { useState, useEffect, useLayoutEffect, useRef } from 'react';
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
  cutTypeOptionsForSection,
  defaultContentRoleForSection,
  hasDetailSource,
  normalizedRecipePatch,
  poseExampleDirectionCompatible,
  sectionRoleForContentRole,
  sectionTitle,
} from '@/lib/storyboardTaxonomy.js';
import {
  assignGenerationExamples,
  isGenerationCombinationPublic,
  selectGenerationExamples,
  storedExampleConditionStatus,
} from '@/lib/generationExamples.js';
import {
  inferStoryboardSpaceSet,
  spaceSetGroupId,
  storyboardSpaceSetsFor,
  withStoryboardSpaceSetExamples,
} from '@/lib/storyboardSpaceSetCatalog.js';
import { genderForClothingType } from '@/lib/productGender.js';
import {
  dissolveSpaceSet,
  groupConsecutiveSpaceRuns,
  insertSpaceSet,
  moveBlockWithSpaceMembership,
  moveSpaceSetRun,
  replaceSpaceSetRun,
} from '@/lib/storyboardSpaceSets.js';
import { normalizePlaceType } from '@/lib/storyboardEntryPlacement.js';
import { renderGroups } from '@/lib/storyboardRenderGroups.js';
import { prewarmImages } from '@/lib/imagePrewarm.js';


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
function sbSaveNow(pid, getSnap, options = {}) {
  const run = sbSaveChain.catch(() => {}).then(() => {
    const snap = getSnap();
    if (!pid || !snap) return;
    if (sbLastSaved.get(pid) === snap) {
      // 마지막 '성공' 저장본과 동일 참조 = 사용자가 그 상태로 되돌린 것(예: 블록 취소의 통짜 복원).
      // 이때 남아 있는 실패 보류분(sbPending)은 낡은 스냅샷 — 지우지 않으면 재진입 시 취소한 블록이 부활한다.
      sbPending.delete(pid);
      return;
    }
    return api.saveStoryboard(pid, snap, options).then(
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
// 서버 게이트와 함께 켠다. dev에서는 검증용으로 열고 production은 명시적 Vite 플래그로 공개한다.
const BG_EXAMPLES_ENABLED = Boolean(import.meta.env?.DEV)
  || import.meta.env?.VITE_GENEXAMPLE_BG_ENABLED === 'true';
const WORN_CUT_TYPES = new Set(['styling', 'horizon', 'mirror']);
const FIT_ROLE_BY_CUT_TYPE = Object.freeze({
  styling: CONTENT_ROLES.COORDINATION,
  horizon: CONTENT_ROLES.FIT,
  mirror: CONTENT_ROLES.REAL_WEAR,
});
const exampleCategoryFor = (cut) => cut === 'product' ? 'product' : (cut === 'horizon' ? 'horizon' : 'styling');
const exampleThumbFor = (catalogs, exampleId, cut) => (
  (catalogs?.genExamples || []).find((example) => example.id === exampleId)?.thumb
  || Placeholder.photo(exampleId, exampleCategoryFor(cut), 240, 320)
);
const blockHasCompatiblePoseExample = (block, catalogs) => {
  if (!block?.exampleId) return true;
  const example = (catalogs?.genExamples || []).find((item) => item.id === block.exampleId);
  return !!example && (example.variants || []).includes('pose')
    && poseExampleDirectionCompatible(example, { cutType: block.cutType, direction: block.direction });
};



function exampleGenderFromAnalysis(analysis, catalogs, clothingType) {
  if (clothingType === 'dress') return genderForClothingType(clothingType, []);
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

const REF_SCOPE_META = Object.freeze({
  all: { label: '전부', className: 'all', copy: '이 예시의 연출 전체 참고' },
  pose: { label: '포즈', className: 'pose', copy: '이 예시의 포즈만 참고' },
  bg: { label: '배경', className: 'bg', copy: '이 예시의 배경만 참고' },
});

const cutNumber = (index, total) => String(index).padStart(2, '0') + '/' + String(total).padStart(2, '0');

function cardLabels(block, catalogs) {
  const isProduct = block.cutType === 'product';
  const direction = isProduct
    ? (catalogs.productDirections.find((item) => item.value === block.direction)?.label || '앞면')
    : (catalogs.directions.find((item) => item.value === block.direction)?.label || '—');
  const shot = isProduct
    ? (catalogs.productShotTypes.find((item) => item.value === block.shot)?.label || '고스트샷')
    : (catalogs.shotTypes.find((item) => item.value === block.shot)?.label || '—');
  return { direction, shot, isProduct };
}

function StoryboardCaption({ block, catalogs, colorOpts, matchClothing, clothingType }) {
  if (block.source === 'mine') return <div className="sb-canvas-caption mine">내 사진</div>;

  const colors = ((block.colorIds && block.colorIds.length) ? block.colorIds : [block.colorId])
    .map((id) => colorOpts.find((color) => color.id === id))
    .filter(Boolean);
  const example = block.exampleId
    ? (catalogs.genExamples || []).find((item) => item.id === block.exampleId)
    : null;
  const scope = block.spaceGroupId ? 'pose' : (block.refScope || 'all');
  const scopeMeta = REF_SCOPE_META[scope] || REF_SCOPE_META.all;
  const referenceThumb = block.exampleId ? exampleThumbFor(catalogs, block.exampleId, block.cutType) : null;
  const match = Array.isArray(block.matchIds) && block.matchIds.length
    ? (matchClothing || []).find((item) => item.id === block.matchIds[0])
    : null;
  const { direction, shot, isProduct } = cardLabels(block, catalogs);
  const scopeAll = !block.spaceGroupId && scope === 'all';
  const directionDiffers = scopeAll && !!example?.direction && !!block.direction && example.direction !== block.direction;
  const shotDiffers = scopeAll && !!example?.shot && !!block.shot && example.shot !== block.shot;
  const closureOptions = catalogs.outerClosureStates || [];
  const closure = closureOptions.find((option) => option.value === block.outerClosureState)?.label || '전체 열림';
  const showClosure = clothingType === 'outer' && WORN_CUT_TYPES.has(block.cutType);

  return (
    <div className="sb-canvas-caption">
      <span className="sb-caption-line">
        <span className="sb-caption-values">
          {block.cutType !== 'mirror' && (
            <span className={directionDiffers ? 'sb-val-changed' : undefined}>{direction}</span>
          )}
          {block.cutType !== 'mirror' && <span aria-hidden="true"> · </span>}
          <span className={shotDiffers ? 'sb-val-changed' : undefined}>{shot}</span>
          {showClosure && <span title="아우터 열림 정도"> · {closure}</span>}
        </span>
      </span>
      <span className="sb-caption-line sb-caption-meta">
        {colors.map((color) => (
          <span key={color.id} className="sb-caption-dot" style={{ background: color.hex }} title={color.label} />
        ))}
        {colors.length > 0 && (
          <span className="sb-caption-color" title={colors.map((color) => color.label).join(', ')}>
            {colors[0].label}{colors.length > 1 ? ` 외 ${colors.length - 1}` : ''}
          </span>
        )}
        {!isProduct && match?.thumb && (
          <span className="sb-match-chip" title="매칭 의류 착용">
            <img src={match.thumb} alt="" /><span>매칭</span>
          </span>
        )}
        {referenceThumb && (
          <span className={'sb-ref-chip ' + scopeMeta.className}>
            <img src={referenceThumb} alt="" /><span>{scopeMeta.label}</span>
            <span className="sb-ref-pop">
              <img src={referenceThumb} alt="" />
              <b>{scopeMeta.copy}</b>
            </span>
          </span>
        )}
      </span>
    </div>
  );
}

function StoryboardCardActions({ gripDrag, onDuplicate, onDelete }) {
  return (
    <span className="sb-canvas-actions" onClick={(event) => event.stopPropagation()}>
      <span className="sb-canvas-grip" title="드래그로 순서 변경" {...gripDrag} aria-label="드래그로 순서 변경">⠿</span>
      <button type="button" title="복제" aria-label="복제" onClick={onDuplicate}>⧉</button>
      <button type="button" title="삭제" aria-label="삭제" onClick={onDelete}>✕</button>
    </span>
  );
}

function StoryboardMedia({ block, index, total, gripDrag, onDuplicate, onDelete }) {
  const missing = block.source !== 'mine' && !block.exampleId;
  return (
    <>
      <span className="sb-canvas-number">{cutNumber(index, total)}</span>
      {block.source === 'mine' && <span className="sb-mine-badge">내 사진</span>}
      {missing ? (
        <span className="sb-missing-body">
          생성예시를 배정하지<br />못했어요
          <i>카드를 열어 다시 시도</i>
        </span>
      ) : (
        <img src={block.thumb || block.ownImages?.[0]} alt="" loading="lazy" decoding="async" />
      )}
      <StoryboardCardActions gripDrag={gripDrag} onDuplicate={onDuplicate} onDelete={onDelete} />
    </>
  );
}

function StoryboardCard({
  item, total, catalogs, colorOpts, matchClothing, clothingType,
  selected, locked, gripDrag, onSelect, onDuplicate, onDelete, addControl,
}) {
  const { block, index } = item;
  const missing = block.source !== 'mine' && !block.exampleId;
  return (
    <div className={'sb-canvas-card' + (locked ? ' locked' : '')}>
      <div
        className={'sb-cutcard' + (selected ? ' selected' : '') + (missing ? ' missing' : '')}
        role="button"
        tabIndex={0}
        onClick={onSelect}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect();
          }
        }}
      >
        <StoryboardMedia
          block={block}
          index={index}
          total={total}
          gripDrag={gripDrag}
          onDuplicate={onDuplicate}
          onDelete={onDelete}
        />
      </div>
      <StoryboardCaption
        block={block}
        catalogs={catalogs}
        colorOpts={colorOpts}
        matchClothing={matchClothing}
        clothingType={clothingType}
      />
      {addControl}
    </div>
  );
}

function StoryboardFrame({
  items, total, catalogs, colorOpts, matchClothing, clothingType,
  selectedId, locked, dragFor, onSelect, onDuplicate, onDelete, addControl,
}) {
  return (
    <div className="sb-frame">
      <span className="sb-frame-tag">한 프레임 구성 · 2컷</span>
      <div className="sb-frame-box">
        {items.map((item) => {
          const missing = item.block.source !== 'mine' && !item.block.exampleId;
          return (
            <div
              key={item.block.id}
              className={'sb-frame-half' + (item.block.id === selectedId ? ' selected' : '') + (missing ? ' missing' : '') + (locked && item.block.id !== selectedId ? ' locked' : '')}
              role="button"
              tabIndex={0}
              onClick={() => onSelect(item.block.id)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onSelect(item.block.id);
                }
              }}
            >
              <StoryboardMedia
                block={item.block}
                index={item.index}
                total={total}
                gripDrag={dragFor(item.block.id)}
                onDuplicate={() => onDuplicate(item.block.id)}
                onDelete={() => onDelete(item.block.id)}
              />
            </div>
          );
        })}
      </div>
      <div className="sb-frame-captions">
        {items.map((item) => (
          <StoryboardCaption
            key={item.block.id}
            block={item.block}
            catalogs={catalogs}
            colorOpts={colorOpts}
            matchClothing={matchClothing}
            clothingType={clothingType}
          />
        ))}
      </div>
      {addControl}
    </div>
  );
}

function StoryboardStack({ group, total, onOpen }) {
  const previews = group.items.slice(0, 3);
  return (
    <div className="sb-stack-wrap">
      <button type="button" className={'sb-stack' + (previews.length ? '' : ' empty')} onClick={onOpen}>
        {previews.length ? previews.map((item, stackIndex) => (
          <span key={item.block.id} className="sb-stack-cut">
            {stackIndex === 0 && <span className="sb-canvas-number">{cutNumber(item.index, total)}</span>}
            <img src={item.block.thumb || item.block.ownImages?.[0]} alt="" loading="lazy" decoding="async" />
          </span>
        )) : <span className="sb-stack-empty">＋ 컷 추가</span>}
        <span className="sb-stack-count">{group.items.length}컷</span>
      </button>
    </div>
  );
}

function frameUnits(items) {
  const units = [];
  for (let index = 0; index < items.length;) {
    const rowId = items[index].block.layoutRowId;
    if (rowId) {
      let end = index + 1;
      while (end < items.length && items[end].block.layoutRowId === rowId) end += 1;
      if (end - index === 2) {
        units.push({ kind: 'frame', items: items.slice(index, end) });
        index = end;
        continue;
      }
    }
    units.push({ kind: 'card', items: [items[index]] });
    index += 1;
  }
  return units;
}

function canvasUnits(items) {
  const units = [];
  for (let index = 0; index < items.length;) {
    const spaceGroupId = items[index].block.spaceGroupId;
    if (spaceGroupId) {
      let end = index + 1;
      while (end < items.length && items[end].block.spaceGroupId === spaceGroupId
        && items[end].block.sectionId === items[index].block.sectionId) end += 1;
      units.push({ kind: 'tray', spaceGroupId, items: items.slice(index, end) });
      index = end;
      continue;
    }
    let end = index + 1;
    while (end < items.length && !items[end].block.spaceGroupId) end += 1;
    units.push(...frameUnits(items.slice(index, end)));
    index = end;
  }
  return units;
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
        <button key={o.value} disabled={o.disabled} title={o.disabled ? (o.disabledReason || '이 조건은 아직 서비스에 공개되지 않았어요') : undefined}
          className={`utab${value === o.value ? ' on' : ''}`} onClick={() => onChange(o.value)}>{o.label}</button>
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

function ExampleThumb({ example }) {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setAttempt(0); setFailed(false); }, [example?.thumb]);
  if (failed) return (
    <span className="sb-exthumb-error">썸네일을 불러오지 못했어요
      <span role="button" tabIndex={0} onClick={(event) => {
        event.stopPropagation(); setAttempt((value) => value + 1); setFailed(false);
      }} onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); event.stopPropagation(); setAttempt((value) => value + 1); setFailed(false);
        }
      }}>다시 시도</span>
    </span>
  );
  const separator = String(example?.thumb || '').includes('?') ? '&' : '?';
  return <img src={`${example.thumb}${attempt ? `${separator}retry=${attempt}` : ''}`} alt="" onError={() => setFailed(true)} />;
}

function ShotSegment({
  options, value, onChange, cut, clothingType, gender, isOptionPublished = null,
}) {
  return (
    <div className="seg sb-shot-seg" data-idx={Math.max(0, options.findIndex((option) => option.value === value))} aria-label={cut === 'product' ? '제품컷 형식' : '샷 종류'}>
      {options.map((option) => {
        const published = isOptionPublished
          ? isOptionPublished(option.value)
          : isGenerationCombinationPublic({
            cutType: cut, shot: option.value, clothingType, gender,
          });
        return (
          <button key={option.value} type="button" className={value === option.value ? 'on' : ''}
            disabled={!published} aria-pressed={value === option.value}
            title={!published ? '이 조건은 아직 서비스에 공개되지 않았어요' : undefined}
            onClick={() => onChange(option.value)}>{option.label}</button>
        );
      })}
    </div>
  );
}

function SpaceMemberStrip({ set, siblings, currentId }) {
  if (!set) return null;
  const summary = set.setType === 'horizon-rotation'
    ? `${siblings.length}컷으로 정면·옆면·뒷면을 이어 봐요`
    : set.setType === 'horizon-sequence'
      ? `${siblings.length}컷의 호리존 연속 예시예요`
      : `${siblings.length}컷이 같은 공간에서 이어져요`;
  return (
    <div className={`sb-space-strip tone-${set.tone}`}>
      <div className="sb-space-strip-thumbs" aria-hidden="true">
        {siblings.map((sibling) => (
          <span key={sibling.id} className={sibling.id === currentId ? 'current' : ''}>
            <img src={sibling.thumb} alt="" />
          </span>
        ))}
      </div>
      <div className="sb-space-strip-copy">
        <strong>{set.setType === 'styling' ? '📍' : set.setType ? '↻' : '•'} {set.name}</strong>
        <span>{summary}</span>
        <small>세트 변경은 보드의 띠에서</small>
      </div>
    </div>
  );
}

function SpaceSetGallery({ mode, error, onChoose, onClose, gender, clothingType }) {
  const replacing = mode === 'replace';
  const spaceSets = storyboardSpaceSetsFor({ gender, clothingType });
  return (
    <div className="surface inspector sb-set-picker">
      <div className="sb-set-picker-head">
        <div>
          <div className="sec-title">{replacing ? '촬영 세트 변경' : '촬영 세트 추가'}</div>
          <p>{replacing
            ? '고르면 공간과 구성 컷 전체가 한 번에 바뀌어요.'
            : '세트 카드 하나에 공간과 어울리는 컷 구성이 함께 들어 있어요.'}</p>
        </div>
        <button type="button" className="sb-set-picker-close" onClick={onClose} aria-label="촬영 세트 갤러리 닫기"><Icon name="x" size={16} /></button>
      </div>
      <div className="sb-set-grid">
        {spaceSets.map((set) => (
          <button key={set.id} type="button" className={`sb-set-card tone-${set.tone}`} onClick={() => onChoose(set)}>
            <span className="sb-set-polaroids" aria-hidden="true">
              {set.members.map((member, index) => (
                <span key={member.exampleId || `${member.direction}:${member.shot}:${index}`} className={`sb-set-polaroid p${index + 1}`}>
                  {member.thumb
                    ? <img src={member.thumb} alt="" />
                    : <span className={member.shot === 'medium' ? 'figure medium' : 'figure'} />}
                </span>
              ))}
            </span>
            <strong>{set.name}</strong>
            <small>{set.compositionLabel}</small>
          </button>
        ))}
        {!spaceSets.length && <div className="sb-set-empty">이 상품에 맞는 촬영 세트를 준비 중이에요.</div>}
      </div>
      {error && <div className="sb-save-error">{error}</div>}
    </div>
  );
}


/* 분위기 예시 — 갤러리가 주인공 (B+C안 확정, ADR-0004):
   · 샷 종류 = 갤러리 헤더 세그먼트 (설정과 같은 shot 필드를 바꾼다)
   · 생성예시 셀 선택 = 촬영 연출만 참고 — 예시 속 옷·신발·액세서리는 제외하고 exampleId로 생성 입력에 포함
   · 내 사진(refImages) = '+ 타일'로 갤러리에 통합 — 점선 테두리·배지, 분위기(조명·색감)만 참고
   · 카드가 사이드/뒷면이어도 선택한 예시의 전체 연출을 참고하되, 카드의 촬영 방향은 유지
   refs/exampleId 는 제어형 — 콘티는 블록이, 에디터 AI 패널은 패널 상태가 소유 (계약 §3.4/§6). */
export function MoodGuide({ catalogs, cut, direction, shot, onShotChange, shotOptions = null, clothingType = 'top', gender = null, exampleId, onExampleChange, onCycleExample, refs = [], onRefsChange, onPickRef, refScope = 'all', onRefScopeChange, inSpace = false, onUseMine = null }) {
  const shotOpts = shotOptions || (cut === 'product' ? catalogs.productShotTypes
    : catalogs.shotTypes);
  const shotVal = shotOpts.some((s) => s.value === shot) ? shot : shotOpts[0].value;
  const examples = React.useMemo(() => selectGenerationExamples(catalogs.genExamples, {
    cutType: cut,
    shot: shotVal,
    clothingType,
    gender,
    spaceGroupId: inSpace ? 'inspector' : null,
    direction,
    includeSetOnly: inSpace,
    appendSetOnly: !inSpace && cut !== 'product',
  }), [catalogs.genExamples, cut, shotVal, clothingType, gender, inSpace, direction]);
  const selectedExample = (catalogs.genExamples || []).find((example) => example.id === exampleId) || null;
  const moodOnly = !inSpace && (cut === 'styling' || cut === 'horizon') && !!direction && direction !== 'front';
  const conditionStatus = !exampleId ? null : storedExampleConditionStatus(selectedExample, {
    cutType: cut, clothingType, gender,
  });
  const selectedPoseCompatible = (selectedExample?.variants || []).includes('pose')
    && poseExampleDirectionCompatible(selectedExample, {
      cutType: selectedExample?.cutType || cut,
      direction,
    });
  const selectedStatus = conditionStatus === 'valid'
    && (
      (inSpace && !selectedPoseCompatible)
      || (!inSpace && refScope === 'pose' && !selectedPoseCompatible)
    )
    ? 'changed' : conditionStatus;
  const cycleExamples = inSpace ? examples.filter((example) => (
    (example.variants || []).includes('pose')
    && poseExampleDirectionCompatible(example, { cutType: example.cutType || cut, direction })
  )) : examples;
  const cycle = () => {
    if (cycleExamples.length <= 1) return;
    const current = cycleExamples.findIndex((example) => example.id === exampleId);
    const next = cycleExamples[(current + 1 + cycleExamples.length) % cycleExamples.length];
    if (onCycleExample) Promise.resolve(onCycleExample(next.id)).catch(() => {});
    else onExampleChange?.(next.id);
  };
  const selectFirstAvailable = () => {
    const available = inSpace ? cycleExamples : examples;
    if (available[0]) onExampleChange?.(available[0].id);
  };
  const unavailableReason = (scope) => scope === 'pose'
    ? '이 예시는 아직 포즈 전용 자산이 없어요'
    : '이 예시는 아직 배경 전용 자산이 없어요';
  const poseDirectionReason = (example) => {
    const label = { front: '정면', back: '뒷면', side: '사이드' }[example?.direction] || '다른 방향';
    return `이 예시의 포즈는 ${label} 전용이에요`;
  };
  return (
    <div className="insp-sec">
      {/* 같은 공간 묶음 안에서는 배경 기준이 묶음에 있으므로 예시는 '포즈 예시'로 강등 (P5 확정) */}
      <div className="sb-exhead">
        <label className="lbl">{cut === 'product' ? '생성 예시' : inSpace ? '포즈 예시' : '분위기 예시'}</label>
        {onShotChange
          ? <ShotSegment options={shotOpts} value={shotVal} onChange={onShotChange}
            cut={cut} clothingType={clothingType} gender={gender}
            isOptionPublished={cut !== 'product' ? (candidateShot) => selectGenerationExamples(
              catalogs.genExamples,
              {
                cutType: cut,
                shot: candidateShot,
                clothingType,
                gender,
                spaceGroupId: inSpace ? 'inspector' : null,
                direction,
                includeSetOnly: inSpace,
                appendSetOnly: !inSpace,
              },
            ).length > 0 : null} />
          : <span className="sb-exhint">내 사진은 이 프로젝트에서만</span>}
      </div>
      {inSpace && cut !== 'product' && (
        <div className="sb-exnote-blue">아래 생성예시의 포즈만 이용하여 변경할 수 있습니다</div>
      )}
      {exampleId && !inSpace && selectedStatus !== 'valid' && (
        <div className="sb-current-example has-error">
          <div className="sb-example-error">{selectedExample ? '조건이 바뀌어 예시를 다시 골라주세요' : '저장된 예시를 불러오지 못했어요'}</div>
          {examples.length > 0 && (
            <button type="button" className="sb-example-retry" onClick={selectFirstAvailable}>다시 선택</button>
          )}
        </div>
      )}
      {exampleId && inSpace && (
        <div className={`sb-current-example${selectedStatus !== 'valid' ? ' has-error' : ''}`}>
          <div className="sb-current-title">현재 선택</div>
          {selectedExample ? (
            <>
              <div className="sb-current-thumb">
                <ExampleThumb example={selectedExample} />
              </div>
              {selectedStatus === 'changed' && <div className="sb-example-error">조건이 바뀌어 예시를 다시 골라주세요</div>}
            </>
          ) : <div className="sb-example-error">저장된 예시를 불러오지 못했어요</div>}
          {selectedStatus !== 'valid' && examples.length > 0 && (
            <button type="button" className="sb-example-retry" onClick={selectFirstAvailable}>다시 선택</button>
          )}
          <button type="button" className="sb-cycle-example" disabled={cycleExamples.length <= 1}
            title={cycleExamples.length <= 1 ? '이 조건에는 다른 예시가 없어요' : undefined}
            onClick={cycle}>다른 예시 보기</button>
        </div>
      )}
      <div className={`sb-exgrid${moodOnly ? ' moodonly' : ''}`}>
        {examples.length === 0 && (
          <div className="sb-exempty">
            {isGenerationCombinationPublic({ cutType: cut, shot: shotVal, clothingType, gender })
              ? '이 조건의 생성예시를 불러오지 못했어요' : '이 조건은 아직 서비스에 공개되지 않았어요'}
            <button type="button" onClick={() => globalThis.location?.reload()}>다시 시도</button>
          </div>
        )}
        {examples.map((e) => {
          const on = exampleId === e.id;
          const variants = Array.isArray(e.variants) ? e.variants : [];
          const poseCompatible = poseExampleDirectionCompatible(e, { cutType: e.cutType || cut, direction });
          const poseDisabled = !variants.includes('pose') || !poseCompatible;
          const poseDisabledReason = !variants.includes('pose')
            ? unavailableReason('pose') : poseDirectionReason(e);
          const inSpaceDisabled = inSpace && poseDisabled;
          // 레퍼런스 범위 — 호버 오버레이에서 선택. 시리즈 안은 호환되는 '포즈만'으로 고정.
          const scopeChoices = !onRefScopeChange || cut === 'product' ? null
            : inSpace ? [{ v: 'pose', l: '포즈만', disabled: poseDisabled, reason: poseDisabledReason }]
              : [
                { v: 'all', l: '전부', disabled: !variants.includes('all') },
                ...(BG_EXAMPLES_ENABLED
                  ? [{ v: 'bg', l: '배경만', disabled: !variants.includes('bg') }]
                  : []),
                { v: 'pose', l: '포즈만', disabled: poseDisabled, reason: poseDisabledReason },
              ];
          const pick = (scope) => {
            if (!onExampleChange) return;
            if (!variants.includes(scope)) return;
            if (scope === 'pose' && !poseCompatible) return;
            onExampleChange(e.id);
            if (onRefScopeChange) onRefScopeChange(scope);
          };
          const defaultScope = cut === 'product' || moodOnly ? 'all'
            : inSpace ? 'pose'
              : variants.includes(refScope || 'all')
                && ((refScope || 'all') !== 'pose' || poseCompatible)
                ? (refScope || 'all') : 'all';
          return (
            <React.Fragment key={e.id}>
              <button type="button" disabled={inSpaceDisabled}
                title={inSpaceDisabled ? poseDisabledReason : undefined}
                className={`sb-excell${on ? ' sel' : ''}${inSpaceDisabled ? ' unavailable' : ''}`}
                onClick={() => pick(defaultScope)}>
                <ExampleThumb example={e} />
                {on && <span className="ck"><Icon name="check" size={11} /></span>}
                {on && scopeChoices && <span className="sb-exscope">{SCOPE_LABELS[refScope || 'all'] || '전부'}</span>}
                {scopeChoices && (
                  /* 오버레이 배경 클릭은 셀 기본 선택으로 통과(기존 클릭 선택 유지) — 버튼 클릭만 범위 지정 */
                  <span className="sb-exov">
                    <span className="sb-exov-t">레퍼런스 범위</span>
                    <span className="sb-exov-b">
                      {scopeChoices.map((c) => (
                        <span key={c.v} role="button" tabIndex={c.disabled ? -1 : 0}
                          aria-disabled={c.disabled || undefined}
                          title={c.disabled ? (c.reason || unavailableReason(c.v)) : undefined}
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
            </React.Fragment>
          );
        })}
        {refs.map((r, i) => (
          <span className={`sb-excell up${onUseMine ? ' usable' : ''}`} key={'u' + i}
            title={onUseMine ? '누르면 이 컷을 이 사진으로 바꿔요' : '분위기(조명·색감)만 참고해요. 옷과 모델은 바뀌지 않아요.'}
            onClick={onUseMine ? () => onUseMine(r) : undefined}
            onKeyDown={onUseMine ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onUseMine(r);
              }
            } : undefined}
            role={onUseMine ? 'button' : undefined}
            tabIndex={onUseMine ? 0 : undefined}>
            <img src={r?.url || r} alt="" /><span className="upb">내 사진</span>
            <button type="button" className="rm" onClick={(ev) => { ev.stopPropagation(); onRefsChange && onRefsChange(refs.filter((_, j) => j !== i)); }}><Icon name="x" size={11} /></button>
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
      {/* 레퍼런스 범위 (P5 확정, 전부|포즈만|배경만) — 같은 공간 묶음은 포즈 고정, 제품 생성 레시피는 범위 개념 없음 */}
      {exampleId && !inSpace && cut !== 'product' && refScope === 'pose' && (
        <div className="sb-exnote">포즈의 좌우와 비대칭을 그대로 따르고, 프레이밍은 현재 샷을 따라요.</div>
      )}
      {exampleId && !inSpace && cut !== 'product' && refScope === 'bg' && (
        <div className="sb-exnote">배경·분위기만 참고해요. 포즈는 이 옷과 장소에 어울리게 새로 잡혀요.</div>
      )}
    </div>
  );
}

function Inspector({ block, catalogs, colorOpts, detailColorOpts, clothingType, exampleGender, hasDetailImage, mode, onMode, onChange, onAtomicChange, requestedRecipe, onCancelRequestedRecipe, matchClothing, spaceContext, onDissolveSpaceSet, onDuplicate, onDelete, dirty, warn, onDone, onRevert, onAddMine, onImgDrag, onCancelNew, isNew }) {
  const doneRef = useRef(null);
  const [matchOpen, setMatchOpen] = useState(false);
  const [pendingRecipe, setPendingRecipe] = useState(null);
  const [pendingChoice, setPendingChoice] = useState(null);
  const [pendingError, setPendingError] = useState(null);
  const [pendingSaving, setPendingSaving] = useState(false);
  useEffect(() => { setMatchOpen(false); }, [block?.id]);
  useEffect(() => {
    // block과 requestedRecipe가 둘 다 null이면 undefined===undefined로 참이 되어
    // null.cutType을 읽다 죽는다(같은 카드 재클릭=선택 해제 시 백지 화면의 원인, 2026-07-29)
    setPendingRecipe(requestedRecipe && block && requestedRecipe.blockId === block.id
      ? { cutType: requestedRecipe.cutType, shot: requestedRecipe.shot } : null);
    setPendingChoice(null); setPendingError(null); setPendingSaving(false);
  }, [block?.id, requestedRecipe?.blockId, requestedRecipe?.cutType, requestedRecipe?.shot]);
  useEffect(() => { if (warn && doneRef.current) doneRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, [warn]);

  if (!block) return (
    <div className="surface inspector empty-insp">
      <EmptyState icon="layout" title="블록을 선택해 수정하세요" desc="좌측에서 수정하고싶은 카드를 선택하거나 아래 버튼으로 내 이미지를 추가하세요." />
      <button className="mine-add-big" onClick={onAddMine}><Icon name="upload" size={20} />내 이미지 업로드</button>
    </div>
  );

  const closureOptions = catalogs.outerClosureStates || [];
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

  const isProduct = block.cutType === 'product';
  const isMirror = block.cutType === 'mirror';
  const isDetail = block.contentRole === CONTENT_ROLES.DETAIL;
  const effectiveSectionRole = requestedRecipe?.sectionRole || block.sectionRole;
  const pendingInSpace = !!block.spaceGroupId && !requestedRecipe;
  const productShotOptions = catalogs.productShotTypes
    .filter((option) => hasDetailImage || option.value !== 'detail');
  const hasSelectableExamples = (cutType, shot) => selectGenerationExamples(
    catalogs.genExamples,
    {
      cutType,
      shot,
      clothingType,
      gender: exampleGender,
      appendSetOnly: cutType !== 'product',
    },
  ).length > 0;
  const cutTypeOptions = cutTypeOptionsForSection(effectiveSectionRole).map((option) => {
    const shots = option.value === 'product'
      ? productShotOptions.map((item) => item.value)
      : catalogs.shotTypes.map((item) => item.value);
    return {
      ...option,
      disabled: !shots.some((shot) => hasSelectableExamples(option.value, shot)),
    };
  });
  const onCutTypeChange = (cutType) => {
    if (block.cutType === cutType) { setPendingRecipe(null); return; }
    const availableShots = cutType === 'product' ? productShotOptions : catalogs.shotTypes;
    const shot = availableShots.find((option) => option.value === block.shot
      && hasSelectableExamples(cutType, option.value))?.value
      || availableShots.find((option) => hasSelectableExamples(cutType, option.value))?.value
      || block.shot;
    setPendingError(null);
    setPendingChoice(null);
    setPendingRecipe({ cutType, shot });
  };
  const onShotChange = (shot) => {
    if (block.cutType === 'product') {
      if (block.shot === shot) { setPendingRecipe(null); return; }
      setPendingError(null);
      setPendingChoice(null);
      setPendingRecipe({ cutType: 'product', shot });
      return;
    }
    onChange((current) => {
      if (!current.spaceGroupId || !current.exampleId) {
        return {
          shot,
          exampleSelectionOrigin: current.exampleId ? 'user' : null,
        };
      }
      return { shot, refScope: 'pose', exampleSelectionOrigin: 'user' };
    });
  };
  const commitPendingRecipe = async (exampleId) => {
    if (!pendingRecipe || pendingSaving) return;
    const example = (catalogs.genExamples || []).find((item) => item.id === exampleId);
    if (!example) return;
    const current = block;
    const nextRole = pendingRecipe.cutType === 'product'
      ? (pendingRecipe.shot === 'detail' ? CONTENT_ROLES.DETAIL : CONTENT_ROLES.PRODUCT_OVERVIEW)
      : effectiveSectionRole === SECTION_ROLES.FIT
        ? FIT_ROLE_BY_CUT_TYPE[pendingRecipe.cutType]
        : [CONTENT_ROLES.HERO, CONTENT_ROLES.BENEFIT].includes(current.contentRole)
          ? current.contentRole : defaultContentRoleForSection(effectiveSectionRole);
    const recipePatch = normalizedRecipePatch({
      ...current,
      source: 'ai',
      cutType: pendingRecipe.cutType,
      shot: pendingRecipe.shot,
    }, nextRole, { hasDetailImage });
    const nextColorOpts = nextRole === CONTENT_ROLES.DETAIL ? detailColorOpts : colorOpts;
    const colorId = nextColorOpts.some((color) => color.id === current.colorId)
      ? current.colorId : nextColorOpts[0]?.id;
    const changes = referenceFeedbackPatch(current, {
      ...recipePatch,
      source: 'ai',
      shot: pendingRecipe.shot,
      colorId,
      pose: 'auto',
      poseLabel: 'AI 자동',
      angle: 'same',
      exampleId,
      baseThumb: current.baseThumb ?? current.thumb,
      exampleSelectionOrigin: 'user',
      refScope: pendingInSpace ? 'pose' : 'all',
      outerClosureState: clothingType === 'outer' && WORN_CUT_TYPES.has(pendingRecipe.cutType)
        ? (closureOptions.some((option) => option.value === current.outerClosureState)
          ? current.outerClosureState : 'open')
        : null,
      ...(pendingRecipe.cutType === 'product' ? { matchIds: [], faceExposure: null } : {}),
    }, catalogs);
    setPendingChoice(exampleId);
    setPendingSaving(true);
    setPendingError(null);
    try {
      await onAtomicChange(changes, { pickerOwnsError: true });
      setPendingRecipe(null);
      setPendingChoice(null);
    } catch {
      setPendingError('변경 내용을 저장하지 못했어요');
    } finally {
      setPendingSaving(false);
    }
  };
  const onGenerationExampleChange = (exampleId) => onChange((current) => {
    const changes = {
      exampleId,
      baseThumb: current.baseThumb ?? current.thumb,
      exampleSelectionOrigin: exampleId ? 'user' : null,
      refScope: current.spaceGroupId ? 'pose' : (current.refScope || 'all'),
    };
    return referenceFeedbackPatch(current, changes, catalogs);
  });
  const onCycleGenerationExample = (exampleId) => onAtomicChange(referenceFeedbackPatch(block, {
    exampleId,
    exampleSelectionOrigin: 'user',
    refScope: block.spaceGroupId ? 'pose' : (block.refScope || 'all'),
  }, catalogs), { retryAtomic: true });
  const onDirectionChange = (direction) => onChange((current) => {
    if (!current.exampleId) return { direction };
    if (!current.spaceGroupId && current.refScope !== 'pose') return { direction };
    const example = (catalogs.genExamples || []).find((item) => item.id === current.exampleId);
    const compatible = (example?.variants || []).includes('pose')
      && poseExampleDirectionCompatible(example, {
        cutType: current.cutType,
        direction,
      });
    if (compatible) {
      return {
        direction,
        ...(current.spaceGroupId ? { refScope: 'pose' } : {}),
      };
    }
    return {
      direction,
      refScope: current.spaceGroupId ? 'pose' : 'all',
      exampleId: null,
      exampleSelectionOrigin: null,
      thumb: current.baseThumb || current.thumb,
      baseThumb: null,
    };
  });
  const showOuterClosure = clothingType === 'outer' && block.source === 'ai' && WORN_CUT_TYPES.has(block.cutType);
  const outerClosureState = closureOptions.some((option) => option.value === block.outerClosureState) ? block.outerClosureState : 'open';
  return (
    <div className="surface inspector">
      {spaceContext && (
        <SpaceMemberStrip set={spaceContext.set} siblings={spaceContext.siblings} currentId={block.id} />
      )}
      {isNew && (
        <div className="insp-newmeta">
          <span>추가 위치: {sectionTitle(block.sectionRole)} · 생성 시 크레딧 {catalogs.creditCosts?.storyboardPerCut ?? 1}</span>
          <button className="insp-cancel-new" onClick={onCancelNew}><Icon name="trash" size={13} />이 블록 취소</button>
        </div>
      )}

      {!block.cutType ? (
        <>
          <div className="insp-empty-hint"><Icon name="info" size={15} />이 이미지의 생성 설정을 준비하지 못했어요. 블록을 취소하고 다시 추가해주세요.</div>
          {/* 새 컷 컨텍스트 — 어디에 추가되는지·비용, 그리고 흔적 없는 취소 (P6) */}
          <div className="insp-newmeta">
            <span>{block.sectionTitle ? `추가 위치: ${block.sectionTitle}` : '새 컷'} · 생성 시 크레딧 {catalogs.creditCosts?.storyboardPerCut ?? 1}</span>
            {onCancelNew && <button className="insp-cancel-new" onClick={onCancelNew}><Icon name="trash" size={13} />이 블록 취소</button>}
          </div>
        </>
      ) : (
        <>
      <div className={`insp-sec${spaceContext ? ' sb-cut-locked' : ''}`}>
        <div className="sb-cut-label-row"><label className="lbl">컷 종류</label>
          {spaceContext && (
            <details className="sb-space-more">
              <summary aria-label="촬영 세트 메뉴">⋯</summary>
              <button type="button" onClick={onDissolveSpaceSet}>세트 전체 풀기</button>
            </details>
          )}</div>
        <UnderlineTabs
          options={spaceContext ? cutTypeOptions.map((option) => ({
            ...option, disabled: true, disabledReason: '촬영 세트를 푼 뒤 바꿀 수 있어요',
          })) : cutTypeOptions}
          value={pendingRecipe?.cutType || block.cutType}
          onChange={spaceContext ? () => {} : onCutTypeChange} />
        {spaceContext && (
          <div className="sb-lock-note"><Icon name="lock" size={13} />세트에 묶인 동안 고정돼요. 풀려면 위의 ⋯ 메뉴를 여세요.</div>
        )}
      </div>

      {pendingRecipe ? (
        <div className="sb-pending-recipe">
          <div className="insp-note"><Icon name="info" size={14} />새 컷의 예시를 먼저 골라주세요. 선택하면 컷·샷·예시가 함께 바뀌어요.</div>
          {requestedRecipe && <button type="button" className="insp-cancel-new" onClick={() => {
            setPendingRecipe(null);
            onCancelRequestedRecipe?.();
          }}>섹션 이동 취소</button>}
          <MoodGuide catalogs={catalogs} cut={pendingRecipe.cutType}
            direction={pendingRecipe.cutType === 'mirror' ? null : block.direction} shot={pendingRecipe.shot}
            shotOptions={pendingRecipe.cutType === 'product' ? productShotOptions : null}
            onShotChange={(shot) => setPendingRecipe((current) => ({ ...current, shot }))}
            clothingType={clothingType} gender={exampleGender}
            exampleId={pendingChoice} onExampleChange={commitPendingRecipe}
            refScope={pendingInSpace ? 'pose' : 'all'} inSpace={pendingInSpace} />
          {pendingError && <div className="sb-save-error">{pendingError}
            <button type="button" disabled={!pendingChoice || pendingSaving}
              onClick={() => commitPendingRecipe(pendingChoice)}>다시 시도</button>
          </div>}
        </div>
      ) : (
        <>
          <MoodGuide onUseMine={(ref) => onChange({
            source: 'mine', title: '내 이미지', cutType: null, contentRole: CONTENT_ROLES.CUSTOM,
            ownImages: [ref?.url || ref], thumb: ref?.url || ref,
            exampleId: null, exampleSelectionOrigin: null, refScope: null,
            spaceGroupId: null, spaceVariation: null,
          })} catalogs={catalogs} cut={block.cutType}
            direction={block.direction} shot={block.shot}
            shotOptions={isProduct ? productShotOptions : null}
            onShotChange={onShotChange} clothingType={clothingType} gender={exampleGender}
            exampleId={block.exampleId || null}
            onExampleChange={onGenerationExampleChange} onCycleExample={onCycleGenerationExample}
            refScope={block.refScope || 'all'} onRefScopeChange={(value) => onChange((current) => referenceFeedbackPatch(current, {
              refScope: value,
              exampleSelectionOrigin: current.exampleId ? 'user' : null,
            }, catalogs))} inSpace={!!block.spaceGroupId}
            refs={(block.refImages || []).map((value, index) => ({ url: value?.url || value, assetId: value?.assetId || (block.refAssetIds || [])[index] }))}
            onRefsChange={(references) => onChange({
              refImages: references.map((value) => value?.url || value),
              refAssetIds: references.map((value) => value?.assetId).filter(Boolean),
            })} />
        </>
      )}

      {/* 방향 — mirror 생성 레시피는 방향 개념 없음 (ADR-0004) */}
      {!isMirror && !isDetail && (
        <div className="insp-sec" style={{ marginBottom: 12 }}><label className="lbl">방향</label>
          <Chips options={isProduct ? catalogs.productDirections : catalogs.directions}
            value={(isProduct ? catalogs.productDirections : catalogs.directions).some((d) => d.value === block.direction) ? block.direction : 'front'}
            onChange={onDirectionChange} /></div>
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

      <div className="insp-sec"><label className="lbl">대상 색상</label>
        <ColorDots colorOpts={isDetail ? detailColorOpts : colorOpts}
          value={block.colorId} onChange={(v) => onChange({ colorId: v })} /></div>

      {/* 매칭 의류가 없으면 편집 패널이 빈 화면이 되므로 진입 자체를 막는다 */}
      {WORN_CUT_TYPES.has(block.cutType) && Array.isArray(matchClothing) && matchClothing.length > 0 && (
        <>
          <button className={`insp-detail-btn${matchOpen ? ' open' : ''}`} onClick={() => setMatchOpen((v) => !v)}>
            <Icon name="settings" size={17} />매칭 의류 편집
          </button>
          {matchOpen && (
            <div className="sb-match-inline">
              <div className="match-grid">
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
        </>
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
  const [dragSpaceGroupId, setDragSpaceGroupId] = useState(null);
  const [dragOver, setDragOver] = useState(null);
  const [dragOverSec, setDragOverSec] = useState(null); // 호버 중인 드롭 대상 섹션 — 하이라이트와 드롭이 같은 신호를 쓴다
  const [dragOverSpaceGroupId, setDragOverSpaceGroupId] = useState(null);
  const [dragMine, setDragMine] = useState(null);
  const [setPicker, setSetPicker] = useState(null);
  const [setPickerError, setSetPickerError] = useState(null);
  const [openGroupKeys, setOpenGroupKeys] = useState([]); // 펼쳐 둔 렌더 그룹들 (다중 허용 · UI 전용)
  const [insertMenuKey, setInsertMenuKey] = useState(null);
  const [warn, setWarn] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [loadRetry, setLoadRetry] = useState(0);
  const [saveError, setSaveError] = useState(null);
  const [pendingSectionMove, setPendingSectionMove] = useState(null);
  const [atomicSaving, setAtomicSaving] = useState(false);
  const atomicSavingRef = useRef(false);
  const atomicRetryRef = useRef(null);
  const directSaveSnapshots = useRef(new WeakSet());
  const saveRetryOptions = useRef({});
  const snapRef = useRef(null);
  const newSeq = useRef(0);
  const cardRefs = useRef(new Map());
  const toast = useToast();
  // 카피라이팅 토글 = 플로우 선택값 (store → patchProject 동기화, ADR-0002)
  const projectId = useAppStore((s) => s.projectId);
  const copyOn = useAppStore((s) => s.copywriting);
  const setCopyOn = useAppStore((s) => s.setCopywriting);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)

  useEffect(() => {
    (async () => {
      try {
        setLoadError(null);
      await useAppStore.getState().loadProject();
      const pid = useAppStore.getState().projectId;
      if (!pid) { navigate('/create/input', { replace: true }); return; }  // 콜드 진입(복원 불가) → 입력
      pidRef.current = pid;   // 이 인스턴스의 저장 대상 고정 (프로젝트 경계)
      await sbSaveIdle();     // 직전 인스턴스의 비행 중 저장(이탈 플러시)이 착지한 뒤에 읽는다 — 스테일 로드 방지
      const [b, c, m, p, a] = await Promise.all([
        api.getStoryboard(pid), api.getCatalogs(), api.getMatchClothing(pid),
        api.getProduct(pid), api.getAnalysis(pid),
      ]);
      const hydratedCatalogs = withStoryboardSpaceSetExamples(c);
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
      if (!usePending) sbLastSaved.set(pid, b);   // 이번 로드의 서버 상태를 기준선으로 기록
      const sourceBlocks = usePending ? pending : b;
      const productHasDetail = hasDetailSource(p);
      const resolvedGender = exampleGenderFromAnalysis(
        a,
        hydratedCatalogs,
        p.clothingType,
      );
      const normalizedBlocks = (ensureSections(sourceBlocks, { hasDetailImage: productHasDetail }).map((block) => ({
        ...block, ...referenceFeedbackPatch(block, {}, hydratedCatalogs),
      })));
      const normalized = sbStable(normalizedBlocks) !== sbStable(sourceBlocks);
      const assignment = assignGenerationExamples(normalizedBlocks, {
        catalog: hydratedCatalogs.genExamples,
        product: p,
        gender: resolvedGender,
      });
      const initBlocks = assignment.blocks;
      setOpenGroupKeys([]);
      setBlocks(initBlocks); setCatalogs(hydratedCatalogs); setMatchClothing(m); setClothingType(p.clothingType || 'top');
      setExampleGender(resolvedGender); setHasDetailImage(productHasDetail);
      if (normalized || assignment.changed || usePending) {
        const autoAssignmentOnly = assignment.assignedIds.length > 0
          && assignment.protectedIds.length === 0 && !normalized && !usePending;
        try {
          await sbSaveNow(pid, () => initBlocks, { autoAssignment: autoAssignmentOnly });
          saveRetryOptions.current = {};
          setSaveError(null);
        } catch {
          saveRetryOptions.current = { autoAssignment: autoAssignmentOnly };
          setSaveError('변경 내용을 저장하지 못했어요');
        }
      }
      const allColorOpts = (p.colors || []).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexFor(col) }));
      const opts = allColorOpts.filter((_option, index) => (p.colors[index].images || []).length || p.colors[index].isBase);
      setDetailColorOpts(allColorOpts.length ? allColorOpts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
      setColorOpts(opts.length ? opts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
      } catch {
        setLoadError('생성예시 카탈로그를 불러오지 못했어요');
      }
    })();
  }, [loadRetry]);
  /* 보드 썸네일 선캐싱 — 트리거 ① 보드 내용이 확정/변경될 때마다(진입·컷 추가·예시 교체).
     이미 데운 URL은 모듈 캐시가 걸러내므로 재실행이 중복 요청을 만들지 않는다. */
  useEffect(() => {
    if (!blocks || !catalogs) return undefined;
    return prewarmImages(blocks.flatMap((block) => [
      block.thumb,
      block.ownImages?.[0],
      block.exampleId ? exampleThumbFor(catalogs, block.exampleId, block.cutType) : null,
    ]));
  }, [blocks, catalogs]);

  // 콘티 편집 자동저장 — Editor 와 동일 패턴(1.5s debounce). generate 클릭 전 이탈해도 콘티 유실 없음.
  const saveTimer = useRef(null);
  const latestBlocks = useRef(null);
  const pidRef = useRef(null);   // 이 인스턴스가 로드한 프로젝트 — 플러시가 스토어의 "현재" id(새 프로젝트로 바뀌었을 수 있음)를 쓰지 않게 고정
  const pendingRowRestore = useRef(null);   // 새 블록 삽입이 가른 행 { blockId, rowId, memberIds } — 취소 시 복원용
  const saveNow = (pid) => sbSaveNow(pid, () => latestBlocks.current);
  useLayoutEffect(() => {
    latestBlocks.current = blocks;
    if (blocks != null) saveRetryOptions.current = {};
  }, [blocks]);
  const sbSkipFirstSave = useRef(true);
  useEffect(() => {
    if (blocks == null || !projectId) return;
    if (sbSkipFirstSave.current) { sbSkipFirstSave.current = false; return; }  // 최초 로드분은 저장 생략(불필요 dirty 방지)
    if (directSaveSnapshots.current.has(blocks)) {
      directSaveSnapshots.current.delete(blocks);
      return;
    }
    saveRetryOptions.current = {};
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveNow(projectId).then(() => setSaveError(null)).catch(() => setSaveError('변경 내용을 저장하지 못했어요'));
    }, 1500);
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
  if (loadError) return (
    <div className="wizard wide"><div className="surface sb-load-error">
      <div>{loadError}</div>
      <button type="button" className="btn btn-primary" onClick={() => setLoadRetry((value) => value + 1)}>다시 시도</button>
    </div></div>
  );
  if (!blocks || !catalogs) return <div className="wizard wide">{doneBlocked && <DoneGuardModal />}<div className="surface"><Skeleton h={400} /></div></div>;

  const selected = blocks.find((b) => b.id === selectedId);
  const isMineSel = selected && selected.source === 'mine';
  const patch = (id, changes) => {
    if (atomicSavingRef.current) return;
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
    // 내부 생성 레시피가 복구돼 placeholder가 생성 대상이 되면 레이아웃 배타 규칙을 다시 적용한다.
    const cur0 = blocks.find((x) => x.id === id);
    const applied = typeof changes === 'function' ? changes(cur0) : changes;
    if (applied && 'cutType' in applied && applied.cutType && cur0 && !cur0.cutType) setBlocks((bs) => normalizeBoard(bs));
    const b = cur0;
    if (!b || b.source !== 'mine' || typeof changes === 'function' || ('source' in changes)) setDirty(true);
  };
  const atomicPatch = async (id, changes, { retryAtomic = false, pickerOwnsError = false } = {}) => {
    if (atomicSavingRef.current) throw new Error('storyboard_atomic_save_in_progress');
    atomicSavingRef.current = true;
    setAtomicSaving(true);
    setSaveError(null);
    const previous = blocks;
    const move = pendingSectionMove?.blockId === id ? pendingSectionMove : null;
    let staged = previous;
    if (move) {
      const from = staged.findIndex((block) => block.id === id);
      if (from >= 0) {
        let to = Math.max(0, Math.min(move.index, staged.length));
        if (from < to) to -= 1;
        const movedRowId = staged[from].layoutRowId;
        const moved = [...staged];
        const [item] = moved.splice(from, 1);
        moved.splice(Math.min(to, moved.length), 0, item);
        const dissolved = movedRowId
          ? moved.map((block) => block.layoutRowId === movedRowId ? withoutLayoutRow(block) : block)
          : moved;
        staged = adoptSection(dissolved, id, move.targetSid, move.targetRole);
      }
    }
    const next = normalizeBoard(staged.map((block) => (
      block.id === id ? { ...block, ...changes } : block
    )));
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    try {
      await sbSaveNow(projectId, () => next);
      if (move) setPendingSectionMove(null);
      atomicRetryRef.current = null;
      saveRetryOptions.current = {};
      setSaveError(null);
      setDirty(true);
    } catch (error) {
      if (sbPending.get(projectId) === next) sbPending.delete(projectId);
      atomicRetryRef.current = retryAtomic ? { previous, next } : null;
      directSaveSnapshots.current.add(previous);
      setBlocks(previous);
      saveRetryOptions.current = {};
      if (!pickerOwnsError) setSaveError('변경 내용을 저장하지 못했어요');
      throw error;
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  };
  const atomicBoardChange = async (buildNext, { nextSelectedId = null } = {}) => {
    if (atomicSavingRef.current) throw new Error('storyboard_atomic_save_in_progress');
    atomicSavingRef.current = true;
    setAtomicSaving(true);
    setSaveError(null);
    const previous = blocks;
    const built = buildNext(previous);
    const next = normalizeBoard(built);
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    try {
      await sbSaveNow(projectId, () => next);
      atomicRetryRef.current = null;
      saveRetryOptions.current = {};
      setSaveError(null);
      setDirty(true);
      if (nextSelectedId) {
        const target = next.find((block) => block.id === nextSelectedId);
        snapRef.current = target ? { ...target } : null;
        setSelectedId(nextSelectedId);
        setMode('props');
        setSplitOpen(true);
      }
      return next;
    } catch (error) {
      if (sbPending.get(projectId) === next) sbPending.delete(projectId);
      atomicRetryRef.current = { previous, next };
      directSaveSnapshots.current.add(previous);
      setBlocks(previous);
      saveRetryOptions.current = {};
      throw error;
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  };
  const selectCard = (id) => {
    if (atomicSavingRef.current) return;
    setSetPicker(null); setSetPickerError(null);
    if (selectedId === id) { finishEdit(); return; }      // click again → deselect
    const cur = blocks.find((b) => b.id === selectedId);
    const curLocked = selectedId && dirty && cur && cur.source !== 'mine';   // 내 이미지는 잠그지 않음
    if (curLocked) { setWarn(true); return; }
    setPendingSectionMove(null);
    const target = blocks.find((b) => b.id === id);
    snapRef.current = target ? { ...target } : null;
    setSelectedId(id); setMode('props'); setDirty(false); setWarn(false); setSplitOpen(true);
  };
  const finishEdit = () => { pendingRowRestore.current = null; setPendingSectionMove(null); setSelectedId(null); setMode('props'); setDirty(false); setWarn(false); snapRef.current = null; };
  const revertEdit = () => {
    if (snapRef.current) {
      const snap = snapRef.current;
      // 구조 필드(섹션·공간)는 현재값 유지 — 편집 중 이동 후 '원래대로'가 옛 소속을 되살려
      // 같은 섹션 id가 비연속으로 쪼개지는 것 방지. 되돌림은 인스펙터 소유 필드만.
      setBlocks((bs) => normalizeBoard(bs.map((b) => b.id === snap.id ? {
        ...snap,
        sectionId: b.sectionId, sectionTitle: b.sectionTitle, sectionLayout: b.sectionLayout,
        sectionRole: b.sectionRole, taxonomyVersion: b.taxonomyVersion,
        sectionCustom: b.sectionCustom, spaceGroupId: b.spaceGroupId, spaceVariation: b.spaceVariation,
        layoutRowId: b.layoutRowId, layoutRowVersion: b.layoutRowVersion,
      } : b)));
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
      postDelete = normalizeBoard((bs.filter((b) => b.id !== id).map((b) => {
        // 삭제 규칙: 한 멤버가 사라지면 남은 파트너 전원의 행 id를 내려 모두 일반 단일 카드로 돌린다.
        // normalizeBoard: 컷 수가 줄어든 섹션의 레이아웃 위생(예: 3컷 threeColumn 에서 1개 삭제 → 스테일 레이아웃 해소)
        return rowId && b.layoutRowId === rowId ? withoutLayoutRow(b) : b;
      })));
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
  const addBlock = async (idx, targetSid, targetRole = null, targetSpaceGroupId = null, targetRenderGroupKey = null) => {
    newSeq.current += 1;
    const targetHost = blocks.find((b) => b.sectionId === targetSid);
    const host = targetHost || (!targetRole ? blocks[Math.max(0, Math.min(idx - 1, blocks.length - 1))] : null);
    const sectionRole = targetRole || host?.sectionRole || SECTION_ROLES.BENEFIT;
    // 역할 칩 없이도 모든 내부 생성법을 계속 추가할 수 있게, 삽입 지점과
    // 가장 가까운 같은 섹션의 AI 이미지가 쓰는 생성법을 물려받는다.
    const isAdjacentCandidate = (block) => block
      && block.source !== 'mine'
      && (!targetSid || block.sectionId === targetSid)
      && sectionRoleForContentRole(block.contentRole) === sectionRole;
    let adjacent = null;
    for (let distance = 1; distance <= blocks.length && !adjacent; distance += 1) {
      const before = blocks[idx - distance];
      const after = blocks[idx + distance - 1];
      adjacent = [before, after].find(isAdjacentCandidate) || null;
    }
    const renderPurpose = targetRenderGroupKey === 'studio' ? CONTENT_ROLES.FIT
      : targetRenderGroupKey === 'styling' ? CONTENT_ROLES.COORDINATION
        : targetRenderGroupKey === 'product' ? CONTENT_ROLES.PRODUCT_OVERVIEW
          : null;
    const firstPurpose = renderPurpose || adjacent?.contentRole || defaultContentRoleForSection(sectionRole);
    const purposePatch = blockPatchForContentRole(null, firstPurpose, { clothingType });
    const initialColorOpts = firstPurpose === CONTENT_ROLES.DETAIL ? detailColorOpts : colorOpts;
    // 내부 역할은 현재 섹션의 안전한 기본값으로 시작하고, normalizeBoard가
    // 핵심 장점 첫 카드의 hero 여부를 실제 카드 순서에 맞춰 다시 확정한다.
    const nb = { id: uid('blk'), sectionRole, taxonomyVersion: STORYBOARD_TAXONOMY_VERSION, colorId: initialColorOpts[0]?.id || 'col1',
      pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
      ...purposePatch,
      thumb: Placeholder.photo('new' + Date.now(), purposePatch.cutType === 'product' ? 'product' : purposePatch.cutType === 'horizon' ? 'horizon' : 'styling', 240, 320), poseThumb: Placeholder.pose('stand'), poseLabel: 'AI 자동' };
    const m = [...blocks]; m.splice(idx, 0, nb);
      let out = adoptSection(m, nb.id, targetSid, sectionRole);             // 이웃/명시된 섹션 소속으로 삽입
      // 빈 보드 등 이웃이 없으면 기본 섹션 부여 — 무소속(unsupported state) 블록 방지
      out = out.map((b) => b.id === nb.id && !b.sectionId
        ? { ...b, sectionId: uid('sec'), sectionTitle: sectionTitle(sectionRole), sectionLayout: 'stack' } : b);
      // 같은 공간 시리즈 섹션에의 '추가'는 명시 액션 = 시리즈 가입 — adoptSection 의 자동가입 금지 규칙은
      // '이동'용이다. 미가입 상태로 두면 deriveSections 가 시리즈를 해제해 SPACE 연속성·포즈 범위 계약이 깨진다.
      {
        const sid = out.find((b) => b.id === nb.id)?.sectionId;
        const peers = out.filter((b) => b.id !== nb.id && b.sectionId === sid);
        const explicitGroup = targetSpaceGroupId
          ? peers.find((block) => block.spaceGroupId === targetSpaceGroupId)
          : null;
        const g = explicitGroup || (peers.length && peers.every((b) => b.spaceGroupId && b.spaceGroupId === peers[0].spaceGroupId)
          ? peers[0] : null);
        if (g) out = out.map((b) => (b.id === nb.id
          ? { ...b, spaceGroupId: g.spaceGroupId, spaceVariation: g.spaceVariation ?? 'subtle', refScope: 'pose' } : b));
      }
      out = normalizeBoard(out);   // 행 한가운데 삽입·컷 수 변경에 따른 강등 등 공통 위생 (삽입 경로 공통 규칙)
      // 취소-복원 = 삽입 전 배열 통짜 보관 — 행 id 뿐 아니라 레이아웃 강등·소속까지 원상 복구.
      // "삽입 직후 상태 그대로"일 때만 유효(이후 어떤 조작이든 snapshot identity 가 바뀌어 자동 무효화).
    const assignment = assignGenerationExamples(out, {
      catalog: catalogs.genExamples,
      product: { clothingType },
      gender: exampleGender,
      onlyBlockIds: [nb.id],
    });
    const next = assignment.blocks;
    pendingRowRestore.current = { blockId: nb.id, preInsert: blocks, snapshot: next };
    snapRef.current = { ...next.find((b) => b.id === nb.id) };             // '원래대로' 스냅샷은 소속 부여 후 기준 (섹션 유실 방지)
    directSaveSnapshots.current.add(next);
    setBlocks(next);
    saveRetryOptions.current = {};
    try {
      await sbSaveNow(projectId, () => next);
      saveRetryOptions.current = {};
      setSaveError(null);
    } catch {
      setSaveError('변경 내용을 저장하지 못했어요');
    }
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
  const onSpaceDragStart = (spaceGroupId) => (e) => {
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/space-group', spaceGroupId);
    setDragSpaceGroupId(spaceGroupId);
  };
  const onDragEnd = () => {
    setDragId(null); setDragSpaceGroupId(null); setDragOver(null); setDragOverSec(null); setDragOverSpaceGroupId(null);
  };
  const renderKeyForBlockId = (id) => renderGroups(blocks)
    .find((group) => group.items.some((item) => item.block.id === id))?.key;
  const onDropAt = (idx, targetSid, targetRole = null, targetSpaceGroupId = null, targetGroupKey = null) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    const img = e.dataTransfer.getData('text/mineimg') || dragMine;
    const draggedGroup = e.dataTransfer.getData('text/space-group') || dragSpaceGroupId;
    setDragOver(null); setDragOverSec(null); setDragOverSpaceGroupId(null);
    if (draggedGroup) {
      setDragSpaceGroupId(null);
      if (targetSpaceGroupId) return;
      const members = blocks.filter((block) => block.spaceGroupId === draggedGroup);
      if (targetGroupKey && renderKeyForBlockId(members[0]?.id) !== targetGroupKey) {
        toast.push('같은 그룹 안에서만 순서를 바꿀 수 있어요');
        return;
      }
      const memberIds = new Set(members.map((member) => member.id));
      const allAllowed = !targetRole || members.every((block) => cutTypeOptionsForSection(targetRole)
        .some((option) => option.value === block.cutType));
      if (!allAllowed) { toast.push('이 섹션에는 세트 구성을 그대로 옮길 수 없어요'); return; }
      setBlocks((current) => {
        let moved = moveSpaceSetRun(current, draggedGroup, idx);
        for (const member of members) moved = adoptSection(moved, member.id, targetSid, targetRole);
        return normalizeBoard(moved.map((block) => memberIds.has(block.id)
          ? { ...block, spaceGroupId: draggedGroup, spaceVariation: block.spaceVariation || 'subtle', refScope: 'pose' }
          : block));
      });
      return;
    }
    if (img) {
      setDragMine(null);
      if (targetSpaceGroupId) { toast.push('내 이미지는 촬영 세트 밖에 추가해주세요'); return; }
      insertMineAt(idx, img, targetSid, targetRole);
      return;
    }   // 내 이미지를 새 블록으로 삽입
    const id = e.dataTransfer.getData('text/blk') || dragId; setDragId(null); if (!id) return;
    const moving = blocks.find((block) => block.id === id);
    if (moving && targetGroupKey && renderKeyForBlockId(id) !== targetGroupKey) {
      toast.push('같은 그룹 안에서만 순서를 바꿀 수 있어요');
      return;
    }
    const cutAllowed = !targetRole || moving?.source === 'mine'
      || cutTypeOptionsForSection(targetRole).some((option) => option.value === moving?.cutType);
    if (moving && !cutAllowed) {
      const targetContentRole = defaultContentRoleForSection(targetRole);
      const targetRecipe = blockPatchForContentRole(moving, targetContentRole, { clothingType });
      setPendingSectionMove({
        blockId: id, index: idx, targetSid, targetRole,
        cutType: targetRecipe.cutType, shot: targetRecipe.shot, sectionRole: targetRole,
      });
      snapRef.current = { ...moving };
      setSelectedId(id); setMode('props'); setDirty(false); setWarn(false); setSplitOpen(true);
      toast.push('새 섹션에 맞는 컷 예시를 먼저 골라주세요');
      return;
    }
    const needsPoseReselection = !!targetSpaceGroupId && moving?.spaceGroupId !== targetSpaceGroupId
      && !blockHasCompatiblePoseExample(moving, catalogs);
    setBlocks((current) => {
      const moved = moveBlockWithSpaceMembership(current, id, idx, {
        targetSpaceGroupId,
        isPoseCompatible: (block) => blockHasCompatiblePoseExample(block, catalogs),
      });
      let adopted = adoptSection(moved, id, targetSid, targetRole);
      if (targetSpaceGroupId) {
        const targetMember = moved.find((block) => block.spaceGroupId === targetSpaceGroupId);
        adopted = adopted.map((block) => block.id === id ? {
          ...block, spaceGroupId: targetSpaceGroupId,
          spaceVariation: targetMember?.spaceVariation || 'subtle', refScope: 'pose',
        } : block);
      }
      return normalizeBoard(adopted);
    });
    if (needsPoseReselection) toast.push('이 공간에 맞는 포즈 예시를 다시 골라주세요');
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
  /* 렌더 그룹 아코디언 (UI 전용) */
  // 다중 열기 — 한 섹션을 펼쳐도 이미 펼친 섹션은 그대로 둔다(생성 전 전체 점검 동선).
  const toggleRenderGroup = (key) => setOpenGroupKeys((current) => (
    current.includes(key) ? current.filter((k) => k !== key) : [...current, key]
  ));
  const openRenderGroup = (key) => setOpenGroupKeys((current) => (
    current.includes(key) ? current : [...current, key]
  ));
  /* 섹션 레이아웃 변경 — 멤버 전체 patch + 직접 구성 표시 */
  const setSecLayout = (sec, v) => {
    // 활성 칩도 다시 적용할 수 있어야 layoutRowId 없는 레거시 보드를 명시적으로 마이그레이션할 수 있다.
    setBlocks((bs) => patchSection(bs, sec.id, { sectionLayout: v, sectionCustom: true }));
  };
  const openSetPicker = (picker) => {
    setSetPickerError(null);
    setSetPicker(picker);
    setSplitOpen(true);
  };
  const chooseSpaceSet = async (set) => {
    if (!setPicker) return;
    setSetPickerError(null);
    const groupId = spaceSetGroupId(set.id, uid('sg'));
    try {
      if (setPicker.mode === 'replace') {
        const currentMembers = blocks.filter((block) => block.spaceGroupId === setPicker.spaceGroupId);
        const newIds = set.members.map((_member, index) => currentMembers[index]?.id || uid('blk'));
        await atomicBoardChange((current) => {
          const replaced = replaceSpaceSetRun(current, setPicker.spaceGroupId, set, {
            spaceGroupId: groupId,
            makeId: (_member, index) => newIds[index],
            setSelectionOrigin: 'user',
          });
          const normalized = normalizeBoard(replaced);
          return assignGenerationExamples(normalized, {
            catalog: catalogs.genExamples,
            product: { clothingType },
            gender: exampleGender,
            onlyBlockIds: newIds,
          }).blocks;
        }, { nextSelectedId: newIds[0] });
      } else {
        const memberIds = set.members.map(() => uid('blk'));
        const memberIdSet = new Set(memberIds);
        const targetHost = blocks.find((block) => block.sectionId === setPicker.targetSid)
          || blocks[Math.max(0, Math.min(setPicker.index - 1, blocks.length - 1))];
        const template = targetHost || {
          sectionRole: setPicker.targetRole,
          taxonomyVersion: STORYBOARD_TAXONOMY_VERSION,
          colorId: colorOpts[0]?.id || 'col1',
          matchIds: [], faceExposure: 'same', angle: 'same', refImages: [], refAssetIds: [],
          thumb: Placeholder.photo('new-set', 'styling', 240, 320),
          poseThumb: Placeholder.pose('stand'),
        };
        await atomicBoardChange((current) => {
          let inserted = insertSpaceSet(current, setPicker.index, set, template, {
            spaceGroupId: groupId,
            makeId: (_member, index) => memberIds[index],
            setSelectionOrigin: 'user',
          });
          for (const id of memberIds) inserted = adoptSection(inserted, id, setPicker.targetSid, setPicker.targetRole);
          inserted = inserted.map((block) => memberIdSet.has(block.id) ? {
            ...block, spaceGroupId: groupId, spaceVariation: set.spaceVariation || 'subtle', refScope: 'pose',
          } : block);
          const normalized = normalizeBoard(inserted);
          return assignGenerationExamples(normalized, {
            catalog: catalogs.genExamples,
            product: { clothingType },
            gender: exampleGender,
            onlyBlockIds: memberIds,
          }).blocks;
        }, { nextSelectedId: memberIds[0] });
      }
      setSetPicker(null);
      setDirty(false);
      toast.push(setPicker.mode === 'replace' ? '촬영 세트를 변경했어요' : '촬영 세트를 추가했어요', { icon: 'plus' });
    } catch {
      setSetPickerError('촬영 세트를 저장하지 못했어요. 다시 시도해주세요.');
    }
  };
  const dissolveSpaceGroup = async (spaceGroupId) => {
    if (!spaceGroupId) return;
    try {
      await atomicBoardChange((current) => dissolveSpaceSet(current, spaceGroupId));
      setDirty(false);
      toast.push('촬영 세트를 풀었어요');
    } catch {
      setSaveError('변경 내용을 저장하지 못했어요');
    }
  };
  const dissolveSelectedSpaceSet = () => dissolveSpaceGroup(selected?.spaceGroupId);
  const locked = !!selectedId && dirty && !isMineSel;
  const boardGroups = renderGroups(blocks);
  const sections = deriveFixedSections(blocks);
  const draggedBlock = dragId ? blocks.find((block) => block.id === dragId) : null;
  const draggedSpaceBlock = dragSpaceGroupId ? blocks.find((block) => block.spaceGroupId === dragSpaceGroupId) : null;
  const draggedBlockGroupKey = draggedBlock ? renderKeyForBlockId(draggedBlock.id) : null;
  const draggedSpaceGroupKey = draggedSpaceBlock ? renderKeyForBlockId(draggedSpaceBlock.id) : null;

  const sectionForGroup = (group) => {
    const sectionId = group.items[0]?.block.sectionId;
    if (sectionId) {
      const exact = sections.find((section) => section.id === sectionId);
      if (exact) return exact;
    }
    const role = group.key === 'hooking'
      ? SECTION_ROLES.BENEFIT
      : group.key === 'product' ? SECTION_ROLES.PRODUCT : SECTION_ROLES.FIT;
    return sections.find((section) => section.role === role) || {
      id: 'empty:' + role,
      role,
      layout: 'stack',
      items: [],
      start: group.items[0]?.index ? group.items[0].index - 1 : blocks.length,
    };
  };

  const insertControl = (idx, group, targetSpaceGroupId = null) => {
    const section = sectionForGroup(group);
    const menuKey = group.key + ':' + idx + ':' + (targetSpaceGroupId || 'single');
    const menuOpen = insertMenuKey === menuKey;
    const canAcceptDrag = (!draggedBlockGroupKey || draggedBlockGroupKey === group.key)
      && (!draggedSpaceGroupKey || draggedSpaceGroupKey === group.key)
      && !(dragSpaceGroupId && targetSpaceGroupId);
    const lineOn = dragOver === idx && dragOverSec === section.id
      && dragOverSpaceGroupId === targetSpaceGroupId;
    return (
      <span
        className={'sb-addzone' + (targetSpaceGroupId ? ' in-tray' : '') + (lineOn ? ' drop-on' : '')}
        onDragOver={(event) => {
          if (!(dragId || dragMine || dragSpaceGroupId) || !canAcceptDrag) return;
          event.preventDefault();
          event.stopPropagation();
          setDragOver(idx);
          setDragOverSec(section.id);
          setDragOverSpaceGroupId(targetSpaceGroupId);
        }}
        onDrop={onDropAt(idx, section.id, section.role, targetSpaceGroupId, group.key)}
      >
        <span className="sb-addzone-bar" />
        <button
          type="button"
          className="sb-addzone-plus"
          aria-label="이 위치에 컷 추가"
          aria-expanded={menuOpen}
          onClick={(event) => {
            event.stopPropagation();
            setInsertMenuKey((current) => current === menuKey ? null : menuKey);
          }}
        >＋</button>
        {menuOpen && (
          <span className="sb-add-pop" onClick={(event) => event.stopPropagation()}>
            {targetSpaceGroupId ? (
              <button type="button" onClick={() => {
                setInsertMenuKey(null);
                addBlock(idx, section.id, section.role, targetSpaceGroupId, group.key);
              }}>＋ 이 공간에 컷 추가</button>
            ) : (
              <>
                <button type="button" onClick={() => {
                  setInsertMenuKey(null);
                  addBlock(idx, section.id, section.role, null, group.key);
                }}>＋ 개별 컷</button>
                {section.role !== SECTION_ROLES.PRODUCT && (
                  <button type="button" onClick={() => {
                    setInsertMenuKey(null);
                    openSetPicker({
                      mode: 'add',
                      index: idx,
                      targetSid: section.id,
                      targetRole: section.role,
                    });
                  }}>＋ 촬영 세트</button>
                )}
              </>
            )}
          </span>
        )}
      </span>
    );
  };

  const layoutControlForGroup = (group) => {
    const section = sectionForGroup(group);
    const aiItems = section.items.filter(({ b }) => b.source !== 'mine');
    if ((import.meta.env.VITE_API_MODE ?? 'mock') === 'http' || aiItems.length < 2) return null;
    const colorSet = new Set(aiItems.filter(({ b }) => b.colorId).map(({ b }) => b.colorId));
    const comparisonAllowed = !section.samePlace && colorSet.size >= 2;
    const offered = aiItems.length === 2 ? 'twoColumn'
      : aiItems.length === 3 ? 'threeColumn'
        : aiItems.length === 4 ? 'grid2x2'
          : aiItems.length >= 5 ? 'twoColumn' : null;
    return (
      <div className="sb-group-layout" aria-label="레이아웃 설정">
        <span>레이아웃 설정</span>
        <button type="button" className={section.layout === 'stack' ? 'on' : undefined}
          onClick={() => setSecLayout(section, 'stack')}>세로 1열</button>
        {offered && (
          <button type="button" className={section.layout === offered ? 'on' : undefined}
            onClick={() => setSecLayout(section, offered)}>
            {offered === 'twoColumn' ? '2단' : offered === 'threeColumn' ? '3단' : '2×2단'}
          </button>
        )}
        {(comparisonAllowed || section.layout === 'colorCompare') && (
          <button type="button" className={section.layout === 'colorCompare' ? 'on' : undefined}
            onClick={() => setSecLayout(section, 'colorCompare')}>컬러 비교</button>
        )}
      </div>
    );
  };

  const registerUnitRef = (node, items) => {
    items.forEach((item) => {
      if (node) cardRefs.current.set(item.block.id, node);
      else cardRefs.current.delete(item.block.id);
    });
  };

  const renderUnit = (unit, group, targetSpaceGroupId = null) => {
    const section = sectionForGroup(group);
    const lastItem = unit.items[unit.items.length - 1];
    const addControl = insertControl(lastItem.index, group, targetSpaceGroupId);
    if (unit.kind === 'frame') {
      return (
        <div
          key={'frame:' + unit.items[0].block.layoutRowId}
          ref={(node) => registerUnitRef(node, unit.items)}
          className={'sb-grid-unit sb-frame-unit sb-drag' + (unit.items.some((item) => item.block.id === dragId) ? ' dragging' : '')}
        >
          <StoryboardFrame
            items={unit.items}
            total={blocks.length}
            catalogs={catalogs}
            colorOpts={colorOpts}
            matchClothing={matchClothing}
            clothingType={clothingType}
            selectedId={selectedId}
            locked={locked}
            dragFor={(id) => ({ draggable: true, onDragStart: onDragStart(id), onDragEnd })}
            onSelect={selectCard}
            onDuplicate={duplicate}
            onDelete={remove}
            addControl={addControl}
          />
        </div>
      );
    }

    const item = unit.items[0];
    const block = item.block;
    return (
      <div
        key={block.id}
        ref={(node) => registerUnitRef(node, [item])}
        className={'sb-grid-unit sb-drag' + (block.id === dragId ? ' dragging' : '')}
      >
        <StoryboardCard
          item={item}
          total={blocks.length}
          catalogs={catalogs}
          colorOpts={colorOpts}
          matchClothing={matchClothing}
          clothingType={clothingType}
          selected={block.id === selectedId}
          locked={locked && block.id !== selectedId}
          gripDrag={{ draggable: true, onDragStart: onDragStart(block.id), onDragEnd }}
          onSelect={() => selectCard(block.id)}
          onDuplicate={() => duplicate(block.id)}
          onDelete={() => remove(block.id)}
          addControl={addControl}
        />
      </div>
    );
  };

  const renderTray = (unit, group) => {
    const set = inferStoryboardSpaceSet(unit.spaceGroupId);
    const place = normalizePlaceType(set.placeType, set.setType);
    const placeClass = place === 'cafe' ? 'cafe'
      : place === 'nature' ? 'nature'
        : place === 'studio' ? 'studio' : 'other';
    const label = set.setType === 'horizon-rotation' || set.setType === 'horizon-sequence'
      ? '↻ ' + set.name + ' · ' + unit.items.length + '컷'
      : '📍 ' + set.name + ' · ' + unit.items.length + '컷';
    return (
      <div key={'tray:' + unit.spaceGroupId} className={'sb-tray place-' + placeClass}>
        <div
          className="sb-tray-head"
          draggable
          onDragStart={onSpaceDragStart(unit.spaceGroupId)}
          onDragEnd={onDragEnd}
        >
          <strong>{label}</strong>
          <button type="button" className="sb-tray-swap" onClick={(event) => {
            event.stopPropagation();
            if (locked) {
              setWarn(true);
              return;
            }
            const first = unit.items[0].block;
            snapRef.current = { ...first };
            setSelectedId(first.id);
            setMode('props');
            setDirty(false);
            openSetPicker({ mode: 'replace', spaceGroupId: unit.spaceGroupId });
          }}>촬영 세트 변경</button>
          <details className="sb-tray-more" onClick={(event) => event.stopPropagation()}>
            <summary aria-label="세트 메뉴">⋯</summary>
            <span>
              <button type="button" onClick={() => dissolveSpaceGroup(unit.spaceGroupId)}>세트 전체 풀기</button>
            </span>
          </details>
        </div>
        <div className="sb-tray-grid">
          {frameUnits(unit.items).map((trayUnit) => renderUnit(trayUnit, group, unit.spaceGroupId))}
        </div>
      </div>
    );
  };

  const allGroupKeys = boardGroups.map((group) => group.key);
  const allOpen = allGroupKeys.length > 0 && allGroupKeys.every((key) => openGroupKeys.includes(key));

  const list = (
    <div className="sb-canvas-board">
      <div className="sb-board-tools">
        <button
          type="button"
          className="sb-board-tool"
          onClick={() => setOpenGroupKeys(allOpen ? [] : allGroupKeys)}
        >
          {allOpen ? '전체 접기' : '전체 펼치기'}
        </button>
      </div>
      {boardGroups.map((group) => {
        const open = openGroupKeys.includes(group.key);
        const range = group.items.length
          ? String(group.items[0].index).padStart(2, '0') + '–'
            + String(group.items[group.items.length - 1].index).padStart(2, '0')
            + ' / ' + blocks.length
          : '0컷';
        const groupSection = sectionForGroup(group);
        return (
          <section
            key={group.key}
            className={'sb-deck' + (open ? ' open' : '') + (dragOverSec === groupSection.id ? ' hot' : '')}
            onPointerEnter={() => {
              if (open || !catalogs) return;   // 펼치기 직전 신호 — 아직 안 데운 것만 앞당겨 받는다
              prewarmImages(group.items.flatMap(({ block }) => [
                block.thumb,
                block.ownImages?.[0],
                block.exampleId ? exampleThumbFor(catalogs, block.exampleId, block.cutType) : null,
              ]), { concurrency: 6 });
            }}
          >
            <button
              type="button"
              className="sb-deck-header"
              aria-expanded={open}
              onClick={() => toggleRenderGroup(group.key)}
            >
              <span className="sb-deck-chevron">▾</span>
              <span className="sb-deck-index">{group.title}</span>
              <span className="sb-deck-label">{group.label}</span>
              <span className="sb-deck-range">{range}</span>
            </button>
            <div className="sb-stack-collapse">
              <div>
                <StoryboardStack group={group} total={blocks.length} onOpen={() => openRenderGroup(group.key)} />
              </div>
            </div>
            <div className="sb-deck-collapse">
              <div>
                {layoutControlForGroup(group)}
                <div className="sb-canvas-grid">
                  {canvasUnits(group.items).map((unit) => (
                    unit.kind === 'tray' ? renderTray(unit, group) : renderUnit(unit, group)
                  ))}
                  <button
                    type="button"
                    className="sb-ghost-card"
                    onClick={() => {
                      const section = sectionForGroup(group);
                      const index = group.items.length
                        ? group.items[group.items.length - 1].index
                        : section.start;
                      addBlock(index, section.id, section.role, null, group.key);
                    }}
                  >＋ 컷 추가</button>
                </div>
              </div>
            </div>
          </section>
        );
      })}
    </div>
  );


  const selectedSpaceRun = selected?.spaceGroupId
    ? groupConsecutiveSpaceRuns(blocks).find((run) => run.kind === 'space'
      && run.items.some((block) => block.id === selected.id)) : null;
  const selectedSpaceSiblings = selectedSpaceRun?.items || [];
  const selectedSpaceContext = selectedSpaceRun ? {
    siblings: selectedSpaceSiblings,
    set: inferStoryboardSpaceSet(selectedSpaceRun.spaceGroupId),
  } : null;
  const inspector = setPicker ? (
    <SpaceSetGallery mode={setPicker.mode} error={setPickerError} onChoose={chooseSpaceSet}
      gender={exampleGender} clothingType={clothingType}
      onClose={() => { setSetPicker(null); setSetPickerError(null); }} />
  ) : <Inspector key={selectedId} block={selected} catalogs={catalogs} colorOpts={colorOpts} detailColorOpts={detailColorOpts} clothingType={clothingType} exampleGender={exampleGender} hasDetailImage={hasDetailImage} mode={mode} onMode={setMode}
    onChange={(p) => patch(selectedId, p)} onAtomicChange={(p, options) => atomicPatch(selectedId, p, options)} requestedRecipe={pendingSectionMove}
    onCancelRequestedRecipe={() => setPendingSectionMove(null)} matchClothing={matchClothing}
    spaceContext={selectedSpaceContext} onDissolveSpaceSet={dissolveSelectedSpaceSet}
    onDuplicate={() => duplicate(selectedId)} onDelete={() => remove(selectedId)}
    dirty={dirty && !isMineSel} warn={warn} onDone={finishEdit} onRevert={revertEdit} onAddMine={addMineBlock}
    isNew={pendingRowRestore.current?.blockId === selectedId}
    onImgDrag={(v) => { setDragMine(v); if (v == null) { setDragOver(null); setDragOverSec(null); setDragOverSpaceGroupId(null); } }}
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

  const body = (
    <div className={'sb-canvas-shell' + (splitOpen ? ' inspector-open' : '')}>
      <div className="sb-canvas-main" onClick={() => setInsertMenuKey(null)}>
        {list}
        <button className="mine-add-solo" onClick={() => addMineBlock()}>
          <Icon name="upload" size={17} />내 이미지 업로드
        </button>
      </div>
      {splitOpen && <div className="insp-col">{inspector}</div>}
    </div>
  );
  const cutCount = blocks.length;
  // 크레딧은 AI 생성 컷에만 — 내 이미지 블록은 생성 작업이 없어 제외 (계약 §6)
  const aiCount = blocks.filter((b) => b.source !== 'mine').length;
  const mineCount = cutCount - aiCount;
  const retryFailedSave = async () => {
    let atomicRetry = atomicRetryRef.current;
    if (atomicRetry && latestBlocks.current !== atomicRetry.previous) {
      atomicRetryRef.current = null;
      atomicRetry = null;
    }
    if (!atomicRetry) {
      try {
        await sbSaveNow(projectId, () => latestBlocks.current, saveRetryOptions.current);
        saveRetryOptions.current = {};
        setSaveError(null);
      } catch {
        setSaveError('변경 내용을 저장하지 못했어요');
      }
      return;
    }
    if (atomicSavingRef.current) return;
    atomicSavingRef.current = true;
    setAtomicSaving(true);
    directSaveSnapshots.current.add(atomicRetry.next);
    setBlocks(atomicRetry.next);
    try {
      await sbSaveNow(projectId, () => atomicRetry.next);
      atomicRetryRef.current = null;
      setSaveError(null);
      setDirty(true);
    } catch {
      if (sbPending.get(projectId) === atomicRetry.next) sbPending.delete(projectId);
      directSaveSnapshots.current.add(atomicRetry.previous);
      setBlocks(atomicRetry.previous);
      setSaveError('변경 내용을 저장하지 못했어요');
    } finally {
      atomicSavingRef.current = false;
      setAtomicSaving(false);
    }
  };
  const generate = async () => {
    // 방어: UI disabled 와 별개로 함수 자체도 게이트 — 다른 호출 경로가 생겨도 미설정 블록 생성 불가
    if (blocks.length === 0) return;
    if (blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType))) { toast.push('생성 설정을 준비하지 못한 이미지가 있어요'); return; }
    // 생성 입력은 서버가 저장된 콘티에서 읽는다 — CTA 에서 반드시 저장 (frontend_state_model §5).
    // 같은 직렬 체인 경유: 비행 중 자동저장 뒤에 줄서서 최신 스냅샷이 마지막에 반영됨을 보장.
    // 실패는 throw 로 전파돼 기존처럼 네비게이션이 중단된다.
    await saveNow(projectId);
    navigate('/create/generating');
  };
  return (
    <div className={`wizard wide sb-page${atomicSaving ? ' is-atomic-saving' : ''}`}
      aria-busy={atomicSaving || undefined}
      onClickCapture={atomicSaving ? (event) => { event.preventDefault(); event.stopPropagation(); } : undefined}
      onDragStartCapture={atomicSaving ? (event) => { event.preventDefault(); event.stopPropagation(); } : undefined}>
      {doneBlocked && <DoneGuardModal />}
      <PageHead title="상세페이지 초안 구성" sub="지금 보이는 이미지들은 예시입니다. 느낌만을 보고 필요한 컷은 수정하며 상세페이지를 생성해보세요." />
      {saveError && <div className="sb-save-error">{saveError}
        <button type="button" onClick={retryFailedSave}>다시 시도</button>
      </div>}
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
              : blocks.some((b) => b.source !== 'mine' && (!b.contentRole || !b.cutType)) ? '생성 설정을 준비하지 못한 이미지가 있어요' : undefined}>
            <Icon name="sparkles" size={18} />상세페이지 생성하기 <Icon name="arrowRight" size={17} /> {aiCount * (catalogs.creditCosts?.storyboardPerCut ?? 1)} 크레딧
          </button>
        </div>
      </div>
    </div>
  );
}

export default Storyboard;
