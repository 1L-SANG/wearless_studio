/* =============================================================
   features/storyboard — ⑤ 콘티보드 (PRD §8)
   blocks 는 "서버 상태의 working copy" 패턴: 진입 시 fetch → 로컬 편집
   → 생성 CTA 에서 saveStoryboard 로 한 번에 저장 (frontend_state_model §4).
   컷 분류: cutType(styling|horizon|product) + source(ai|mine) — ADR-0003.
   카피라이팅 토글은 store(copywriting) → patchProject 동기화.
   UnderlineTabs/ColorDots/MoodGuide/hexFor are exported for the editor.
   ============================================================= */
import React, { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { uid } from '@/lib/ids.js';
import { Placeholder } from '@/mock/placeholders.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, IconButton, Button, Chips, ThumbGrid, EmptyState, Skeleton, Toggle, useToast } from '@/components/ui.jsx';
import { PageHead, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';

const COLOR_HEX = {
  white: '#ffffff', ivory: '#f3eee1', beige: '#d8c4a3', brown: '#7a5230', black: '#15141a',
  gray: '#9a9aa1', navy: '#1f2a44', blue: '#2a5db0', green: '#3f7a4f', red: '#c0392b', pink: '#e3a7b8', yellow: '#e7c75c',
  '블랙': '#15141a', '아이보리': '#f3eee1', '화이트': '#ffffff', '베이지': '#d8c4a3',
};
export const hexFor = (c) => COLOR_HEX[c.swatchId] || COLOR_HEX[c.name] || '#d8d6dc';

function StoryboardCard({ block, catalogs, colorOpts, matchClothing, spaceTag, selected, locked, gripDrag, onSelect, onDuplicate, onDelete, onUp, onDown }) {
  const isMine = block.source === 'mine';
  const colorIds = (block.colorIds && block.colorIds.length) ? block.colorIds : (block.colorId ? [block.colorId] : []);
  const cols = colorIds.map((id) => colorOpts.find((c) => c.id === id)).filter(Boolean);
  const poseEdited = !!block.pose && block.pose !== 'auto';
  const matchEdited = Array.isArray(block.matchIds) && block.matchIds.length > 0;
  const matchThumb = matchEdited ? ((matchClothing || []).find((m) => m.id === block.matchIds[0])?.thumb) : null;
  const isProduct = block.cutType === 'product';
  const dirLabel = isProduct
    ? (catalogs.productDirections.find((d) => d.value === block.direction)?.label || '앞면')
    : (catalogs.directions.find((d) => d.value === block.direction)?.label || '—');
  const shotLabel = isProduct
    ? (catalogs.productShotTypes.find((s) => s.value === block.shot)?.label || '고스트컷')
    : (catalogs.shotTypes.find((s) => s.value === block.shot)?.label || '—');
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
                  {/* 거울샷은 방향 개념이 없다 (ADR-0004) — 행 자체를 숨김 */}
                  {block.cutType !== 'mirror' && <div className="sb-detail">방향: {dirLabel}</div>}
                  <div className="sb-detail">샷 종류: {shotLabel}</div>
                </>
              ) : <div className="sb-detail muted">컷 종류 미설정</div>}
            </div>
          )}
          {!isMine && block.cutType && cols.length > 0 && (
            <div className="sb-reveal sb-cfoot">
              {cols.map((c, i) => <span key={i} className="sb-cdot" style={{ background: c.hex }} title={c.label} />)}
            </div>
          )}
        </div>
        {(poseEdited || matchEdited) && (
          <div className="sb-eimgs">
            {poseEdited && <figure className="sb-eimg"><img src={block.poseThumb} alt="" /><figcaption>포즈</figcaption></figure>}
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
        {shot === 'hanger' && <path d="M50 16 q0 -6 5 -6 M32 32 L50 18 L68 32" fill="none" stroke="currentColor" strokeWidth="3" opacity=".5" />}
        <g transform={shot === 'flatlay' ? 'rotate(8 50 52)' : undefined}>
          <rect className="si" x="34" y="32" width="32" height="38" rx="6" />
          <rect className="si" x="24" y="32" width="12" height="17" rx="5" />
          <rect className="si" x="64" y="32" width="12" height="17" rx="5" />
        </g>
      </svg>
    );
  }
  const vbTop = { full: '18 0 64 106', knee: '18 0 64 80', medium: '18 0 64 54', close: '26 2 48 36' };
  const vbBottom = { full: '18 0 64 106', knee: '18 30 64 56', medium: '18 34 64 68', close: '30 54 40 34' };
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
   · 생성예시 셀 선택 = "예시 그대로, 옷·모델만 교체" — exampleId로 생성 입력에 포함
   · 내 사진(refImages) = '+ 타일'로 갤러리에 통합 — 점선 테두리·배지, 분위기(조명·색감)만 참고
   · 예시는 전부 정면 대역(band)이라 방향과 무관 — 사이드/뒷면이면 분위기만 반영 안내
   refs/exampleId 는 제어형 — 콘티는 블록이, 에디터 AI 패널은 패널 상태가 소유 (계약 §3.4/§6). */
export function MoodGuide({ catalogs, cut, direction, shot, onShotChange, clothingType = 'top', exampleId, onExampleChange, refs = [], onRefsChange }) {
  const cat = cut === 'product' ? 'product' : (cut === 'horizon' ? 'horizon' : 'styling'); // mirror는 styling 계열 플레이스홀더 (ADR-0004)
  const shotOpts = cut === 'product' ? catalogs.productShotTypes
    : cut === 'mirror' ? catalogs.shotTypes.filter((s) => s.value === 'full' || s.value === 'knee')
      : catalogs.shotTypes;
  const shotVal = shotOpts.some((s) => s.value === shot) ? shot : shotOpts[0].value;
  const examples = React.useMemo(() => Array.from({ length: 6 }, (_, i) => {
    const seed = `ex_${cut || 'x'}_${clothingType}_${shotVal || 'x'}_${i}`;
    return { id: seed, thumb: Placeholder.photo(seed, cat, 240, 320) };
  }), [cut, clothingType, shotVal, cat]);
  const moodOnly = (cut === 'styling' || cut === 'horizon') && !!direction && direction !== 'front';
  return (
    <div className="insp-sec">
      <div className="sb-exhead"><label className="lbl">분위기 예시</label><span className="sb-exhint">내 사진은 이 프로젝트에서만</span></div>
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
        {examples.map((e) => {
          const on = exampleId === e.id;
          return (
            <button key={e.id} type="button" className={`sb-excell${on ? ' sel' : ''}`}
              onClick={() => onExampleChange && onExampleChange(on ? null : e.id)}>
              <img src={e.thumb} alt="" />{on && <span className="ck"><Icon name="check" size={11} /></span>}
            </button>
          );
        })}
        {refs.map((r, i) => (
          <span className="sb-excell up" key={'u' + i} title="분위기(조명·색감)만 참고해요. 옷과 모델은 바뀌지 않아요.">
            <img src={r} alt="" /><span className="upb">내 사진</span>
            <button type="button" className="rm" onClick={() => onRefsChange && onRefsChange(refs.filter((_, j) => j !== i))}><Icon name="x" size={11} /></button>
          </span>
        ))}
        {onRefsChange && (
          <button type="button" className="sb-excell uptile" onClick={async () => onRefsChange([...refs, await api.pickAnyImage()])}>
            <span className="plus">+</span>내 사진
          </button>
        )}
      </div>
      {moodOnly && <div className="sb-exnote">방향이 {direction === 'side' ? '사이드' : '뒷면'}라 예시의 <b>분위기(조명·톤)만</b> 적용돼요.</div>}
      {!moodOnly && exampleId && <div className="sb-exnote pick"><b>이 예시처럼 생성돼요</b> — 옷과 모델만 우리 걸로 교체</div>}
    </div>
  );
}

function Inspector({ block, catalogs, colorOpts, clothingType, mode, onMode, onChange, matchClothing, dirty, warn, onDone, onRevert, onAddMine, onImgDrag }) {
  const doneRef = useRef(null);
  useEffect(() => { if (warn && doneRef.current) doneRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' }); }, [warn]);

  if (!block) return (
    <div className="surface inspector empty-insp">
      <EmptyState icon="layout" title="블록을 선택해 수정하세요" desc="좌측에서 수정하고싶은 카드를 선택하거나 아래 버튼으로 내 이미지를 추가하세요." />
      <button className="mine-add-big" onClick={onAddMine}><Icon name="upload" size={20} />내 이미지 업로드</button>
    </div>
  );

  // 컷 종류 탭 = catalogs.cutTypes + '내 이미지'(source 전환) 합성 (계약 §5)
  const cutTabs = [...catalogs.cutTypes, { value: 'mine', label: '내 이미지' }];
  const tabValue = block.source === 'mine' ? 'mine' : (block.cutType || '');
  // 컷 종류 전환 시 방향·샷을 대상 컷의 유효 옵션으로 정규화 — 어느 방향의 전환이든 계약 위반 값이 남지 않는다 (ADR-0004)
  const onTab = (v) => {
    if (v === 'mine') return onChange({ source: 'mine', cutType: null, exampleId: null });
    if (v === 'mirror') {
      // 거울샷: 방향 없음, 샷 full/knee, 얼굴 기본 '폰으로 가림', 포즈 자동
      return onChange({
        source: 'ai', cutType: 'mirror', direction: null, exampleId: null,
        shot: block.shot === 'knee' ? 'knee' : 'full',
        faceExposure: block.faceExposure === 'show' ? 'show' : 'hide',
        angle: 'same', pose: 'auto', poseLabel: 'AI 자동',
      });
    }
    if (v === 'product') {
      return onChange({
        source: 'ai', cutType: 'product', exampleId: null,
        direction: block.direction === 'back' ? 'back' : 'front',
        shot: ['ghost', 'hanger', 'flatlay'].includes(block.shot) ? block.shot : 'ghost',
      });
    }
    // styling · horizon
    return onChange({
      source: 'ai', cutType: v, exampleId: null,
      direction: ['front', 'back', 'side'].includes(block.direction) ? block.direction : 'front',
      shot: ['full', 'knee', 'medium', 'close'].includes(block.shot) ? block.shot : 'full',
    });
  };

  // 내 이미지 = 직접 삽입 흐름 (PRD 8.8) — no AI options
  const isMine = block.source === 'mine';
  if (isMine) {
    return (
      <div className="surface inspector">
        <div className="sec-title" style={{ fontSize: 15, marginBottom: 6 }}>내 이미지</div>
        <div className="insp-sec"><label className="lbl">컷 종류</label>
          <UnderlineTabs options={cutTabs} value={tabValue} onChange={onTab} /></div>
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

  // pose + 매칭 의류 detail editor (AI cuts only)
  if (mode === 'edit') {
    const poseItems = catalogs.poses;
    return (
      <div className="surface inspector insp-edit-panel">
        <div className="insp-edit-head">
          <Button variant="quiet" size="sm" icon="arrowLeft" onClick={() => onMode('props')}>뒤로 가기</Button>
        </div>
        <div className="sec-title" style={{ fontSize: 15, margin: '2px 0 14px' }}>{block.title} · 포즈·매칭 의류 편집</div>
        {block.cutType === 'mirror' ? (
          /* 거울샷 포즈는 거울 셀피 구도로 자동 고정 (ADR-0004) */
          <div className="insp-note" style={{ marginBottom: 14 }}><Icon name="info" size={14} />거울샷 포즈는 거울 셀피 구도로 자동 연출돼요.</div>
        ) : (
          <div className="insp-sec"><label className="lbl">포즈 변경</label>
            <ThumbGrid items={poseItems} value={block.pose || 'auto'} onChange={(v) => {
              const it = poseItems.find((p) => p.id === v); onChange({ pose: v, poseLabel: it?.label || 'AI 자동', poseThumb: it?.thumb || block.poseThumb });
            }} labels /></div>
        )}
        {matchClothing && (
          <div className="insp-sec">
            <label className="lbl">매칭 의류<span className="opt" style={{ fontWeight: 400, color: 'var(--fg-3)', marginLeft: 6 }}>스타일링에 함께</span></label>
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
  return (
    <div className="surface inspector">
      <div className="insp-sec"><label className="lbl">컷 종류</label>
        <UnderlineTabs options={cutTabs} value={tabValue} onChange={onTab} /></div>

      {!block.cutType ? (
        <div className="insp-empty-hint"><Icon name="arrowUp" size={15} />컷 종류를 먼저 선택하면 세부 설정이 나타나요.</div>
      ) : (
        <>
      {/* 분위기 예시가 주인공 — 샷 종류는 갤러리의 아이콘 필터, 방향은 아래로 강등 (B+C안, ADR-0004) */}
      <MoodGuide catalogs={catalogs} cut={block.cutType} direction={block.direction} shot={block.shot}
        onShotChange={(v) => onChange({ shot: v, exampleId: null })} clothingType={clothingType}
        exampleId={block.exampleId || null} onExampleChange={(v) => onChange({ exampleId: v })}
        refs={block.refImages || []} onRefsChange={(r) => onChange({ refImages: r })} />

      {/* 방향 — 거울샷은 방향 개념 없음 (ADR-0004) */}
      {!isMirror && (
        <div className="insp-sec"><label className="lbl">방향</label>
          <Chips options={isProduct ? catalogs.productDirections : catalogs.directions}
            value={(isProduct ? catalogs.productDirections : catalogs.directions).some((d) => d.value === block.direction) ? block.direction : 'front'}
            onChange={(v) => onChange({ direction: v })} /></div>
      )}

      <div className="insp-divider" />

      <div className="insp-sec"><label className="lbl">대상 색상</label>
        <ColorDots colorOpts={colorOpts} value={block.colorId} onChange={(v) => onChange({ colorId: v })} /></div>

      <button className="insp-detail-btn" onClick={() => onMode('edit')}>
        <Icon name="settings" size={17} />포즈·매칭 의류 편집
      </button>

      {/* 추가 옵션 — 컷별 얼굴 노출 / 앵글 (PRD 6.8, 9.x). 거울샷은 얼굴 기본 '폰으로 가림', 앵글 없음 (ADR-0004) */}
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
  const [clothingType, setClothingType] = useState('top'); // 샷 필터 아이콘·예시 크롭용 (상의=위/하의=아래)
  const [selectedId, setSelectedId] = useState(null);
  const [splitOpen, setSplitOpen] = useState(false); // 한 번이라도 카드를 열면 좌/우 분할 유지
  const [mode, setMode] = useState('props');
  const [dirty, setDirty] = useState(false);
  const [dragId, setDragId] = useState(null);
  const [dragOver, setDragOver] = useState(null);
  const [dragMine, setDragMine] = useState(null);
  const [warn, setWarn] = useState(false);
  const snapRef = useRef(null);
  const newSeq = useRef(0);
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
      const [b, c, m, p] = await Promise.all([api.getStoryboard(pid), api.getCatalogs(), api.getMatchClothing(), api.getProduct(pid)]);
      setBlocks(b); setCatalogs(c); setMatchClothing(m); setClothingType(p.clothingType || 'top');
      const opts = (p.colors || []).filter((col) => col.images.length || col.isBase).map((col) => ({ id: col.id, label: col.name || '색상', hex: hexFor(col) }));
      setColorOpts(opts.length ? opts : [{ id: 'col1', label: '기본', hex: '#15141a' }]);
    })();
  }, []);
  if (!blocks || !catalogs) return <div className="wizard wide">{doneBlocked && <DoneGuardModal />}<div className="surface"><Skeleton h={400} /></div></div>;

  const selected = blocks.find((b) => b.id === selectedId);
  const isMineSel = selected && selected.source === 'mine';
  const patch = (id, p) => { setBlocks((bs) => bs.map((b) => b.id === id ? { ...b, ...p } : b)); const b = blocks.find((x) => x.id === id); if (!b || b.source !== 'mine' || ('source' in p)) setDirty(true); };
  const selectCard = (id) => {
    if (selectedId === id) { finishEdit(); return; }      // click again → deselect
    const cur = blocks.find((b) => b.id === selectedId);
    const curLocked = selectedId && dirty && cur && cur.source !== 'mine';   // 내 이미지는 잠그지 않음
    if (curLocked) { setWarn(true); return; }
    const target = blocks.find((b) => b.id === id);
    snapRef.current = target ? { ...target } : null;
    setSelectedId(id); setMode('props'); setDirty(false); setWarn(false); setSplitOpen(true);
  };
  const finishEdit = () => { setSelectedId(null); setMode('props'); setDirty(false); setWarn(false); snapRef.current = null; };
  const revertEdit = () => {
    if (snapRef.current) { const snap = snapRef.current; setBlocks((bs) => bs.map((b) => b.id === snap.id ? { ...snap } : b)); }
    setSelectedId(null); setMode('props'); setDirty(false); setWarn(false); snapRef.current = null;
  };
  const duplicate = (id) => setBlocks((bs) => { const i = bs.findIndex((b) => b.id === id); const copy = { ...bs[i], id: uid('blk') }; const n = [...bs]; n.splice(i + 1, 0, copy); return n; });
  const remove = (id) => {
    const idx = blocks.findIndex((b) => b.id === id); const removed = blocks[idx];
    setBlocks((bs) => bs.filter((b) => b.id !== id));
    if (selectedId === id) finishEdit();
    toast.push('블록을 삭제했어요', { undo: () => setBlocks((bs) => { const n = [...bs]; n.splice(idx, 0, removed); return n; }) });
  };
  const moveBlock = (id, dir) => setBlocks((bs) => { const i = bs.findIndex((b) => b.id === id); const j = i + dir; if (j < 0 || j >= bs.length) return bs; const n = [...bs]; [n[i], n[j]] = [n[j], n[i]]; return n; });
  const addBlock = (idx) => {
    newSeq.current += 1;
    // 새 블록 — source 'ai', 컷 종류는 미설정(null)에서 시작 (계약 §3.4)
    const nb = { id: uid('blk'), kind: 'info', title: '새로운 블록', source: 'ai', cutType: null, colorId: colorOpts[0]?.id || 'col1',
      pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [],
      thumb: Placeholder.photo('new' + Date.now(), 'styling', 240, 320), poseThumb: Placeholder.pose('stand'), poseLabel: 'AI 자동' };
    setBlocks((bs) => { const m = [...bs]; m.splice(idx, 0, nb); return m; });
    snapRef.current = { ...nb };
    setSelectedId(nb.id); setMode('props'); setDirty(false); setWarn(false); setSplitOpen(true);   // new block IS selected, but empty (no cut type)
    toast.push('블록을 추가했어요', { icon: 'plus' });
  };
  const mineBlock = (src, n) => ({
    id: uid('blk'), kind: 'info', title: `새 블록 (${n})`, source: 'mine', cutType: null, colorId: colorOpts[0]?.id || 'col1',
    ownImages: [src], thumb: src, pose: 'auto', matchIds: [], faceExposure: 'same', angle: 'same', refImages: [],
    poseThumb: Placeholder.pose('stand'), poseLabel: '-',
  });
  const addMineBlock = async (idx) => {
    const src = await api.pickAnyImage();
    const nb = mineBlock(src, (newSeq.current += 1));
    setBlocks((bs) => { const m = [...bs]; m.splice(idx == null ? m.length : idx, 0, nb); return m; });
    setSelectedId(nb.id); setMode('props'); setDirty(false); setSplitOpen(true);
    toast.push('내 이미지 블록을 추가했어요', { icon: 'plus' });
  };
  // drag-to-reorder blocks (with drop indicator)
  const onDragStart = (id) => (e) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/blk', id); setDragId(id); };
  const onDragEnd = () => { setDragId(null); setDragOver(null); };
  const onDropAt = (idx) => (e) => {
    e.preventDefault();
    const img = e.dataTransfer.getData('text/mineimg') || dragMine;
    setDragOver(null);
    if (img) { setDragMine(null); insertMineAt(idx, img); return; }   // 내 이미지를 새 블록으로 삽입
    const id = e.dataTransfer.getData('text/blk') || dragId; setDragId(null); if (!id) return;
    setBlocks((bs) => { const from = bs.findIndex((b) => b.id === id); if (from < 0) return bs; const m = [...bs]; const [it] = m.splice(from, 1); let to = idx; if (from < idx) to -= 1; m.splice(to, 0, it); return m; });
  };
  const insertMineAt = (idx, src) => {
    const nb = mineBlock(src, (newSeq.current += 1));
    setBlocks((bs) => { const m = [...bs]; m.splice(idx, 0, nb); return m; });
    toast.push('내 이미지를 블록으로 넣었어요', { icon: 'plus' });
  };

  const locked = !!selectedId && dirty && !isMineSel;
  // 공간 그룹 라벨 — 보드 등장 순서대로 A, B, … (spaceGroupId → 표시용)
  const spaceLabels = {};
  blocks.forEach((b) => {
    if (b.spaceGroupId && !(b.spaceGroupId in spaceLabels)) spaceLabels[b.spaceGroupId] = String.fromCharCode(65 + Object.keys(spaceLabels).length);
  });
  const cardEl = (b, i) => (
    <React.Fragment key={b.id}>
      <div className={`sb-dropline${dragOver === i ? ' on' : ''}${dragMine ? ' armed' : ''}`} onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); setDragOver(i); } }} onDrop={onDropAt(i)} />
      <div className={`sb-drag${dragId === b.id ? ' dragging' : ''}`}
        onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); const r = e.currentTarget.getBoundingClientRect(); setDragOver(e.clientY < r.top + r.height / 2 ? i : i + 1); } }}
        onDrop={(e) => { if (dragId || dragMine) onDropAt(dragOver == null ? i + 1 : dragOver)(e); }}>
        <StoryboardCard block={b} catalogs={catalogs} colorOpts={colorOpts} matchClothing={matchClothing}
          spaceTag={b.spaceGroupId ? spaceLabels[b.spaceGroupId] : null}
          selected={b.id === selectedId} locked={locked && b.id !== selectedId}
          gripDrag={{ draggable: true, onDragStart: onDragStart(b.id), onDragEnd }}
          onSelect={() => selectCard(b.id)} onUp={() => moveBlock(b.id, -1)} onDown={() => moveBlock(b.id, 1)}
          onDuplicate={() => duplicate(b.id)} onDelete={() => remove(b.id)} />
      </div>
    </React.Fragment>
  );
  const list = (
    <div className="sb-cards">
      <div className="sb-list">
        {blocks.map((b, i) => (
          <React.Fragment key={b.id}>
            {cardEl(b, i)}
            <button className="sb-insert" onClick={() => addBlock(i + 1)} title="여기에 블록 추가">
              <span className="sb-insert-line" /><span className="sb-insert-pill"><Icon name="plus" size={15} />블록 추가</span><span className="sb-insert-line" />
            </button>
          </React.Fragment>
        ))}
        <div className={`sb-dropline${dragOver === blocks.length ? ' on' : ''}${dragMine ? ' armed' : ''}`} onDragOver={(e) => { if (dragId || dragMine) { e.preventDefault(); setDragOver(blocks.length); } }} onDrop={onDropAt(blocks.length)} />
      </div>
    </div>
  );

  const inspector = <Inspector block={selected} catalogs={catalogs} colorOpts={colorOpts} clothingType={clothingType} mode={mode} onMode={setMode}
    onChange={(p) => patch(selectedId, p)} matchClothing={matchClothing} dirty={dirty && !isMineSel} warn={warn} onDone={finishEdit} onRevert={revertEdit} onAddMine={addMineBlock} onImgDrag={setDragMine} />;

  let body;
  if (!splitOpen) {
    // 처음 진입 — 카드들만 가운데 정렬, 우측 패널 없음
    body = (
      <div className="sb-solo">
        {list}
        <button className="mine-add-solo" onClick={() => addMineBlock()}><Icon name="upload" size={17} />내 이미지 업로드</button>
      </div>
    );
  } else {
    // 카드를 한 번이라도 열었으면 — 좌/우 분할(간격 좁게) 유지, 선택 없으면 우측에 빈 상태(내 이미지 업로드)
    body = <div className="storyboard-layout tight"><div className="sb-scroll-l">{list}</div><div className="insp-col">{inspector}</div></div>;
  }

  const cutCount = blocks.length;
  // 크레딧은 AI 생성 컷에만 — 내 이미지 블록은 생성 작업이 없어 제외 (계약 §6)
  const aiCount = blocks.filter((b) => b.source !== 'mine').length;
  const mineCount = cutCount - aiCount;
  const generate = async () => {
    // 생성 입력은 서버가 저장된 콘티에서 읽는다 — CTA 에서 반드시 저장 (frontend_state_model §5)
    await api.saveStoryboard(projectId, blocks);
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

      {/* fixed bottom action bar */}
      <div className="sb-actionbar">
        <div className="sb-ab-inner">
          <button className="btn btn-ghost" onClick={() => navigate('/create/mannequin')}><Icon name="arrowLeft" size={17} />이전</button>
          <div className="sb-ab-count">AI 생성 {aiCount}컷 · 셀러 사진 {mineCount}컷</div>
          <div className="sb-ab-copy">
            <Toggle on={copyOn} onChange={setCopyOn} />
            <div><div className="sec-title" style={{ fontSize: 14 }}>카피라이팅 {copyOn ? 'ON' : 'OFF'}</div>
              <div className="hint" style={{ marginTop: 1 }}>AI가 카피를 자동으로 넣어요</div></div>
          </div>
          <button className="btn btn-primary btn-lg sb-ab-go btn-glowring" onClick={generate}>
            <Icon name="sparkles" size={18} />상세페이지 생성하기 <Icon name="arrowRight" size={17} /> {aiCount * (catalogs.creditCosts?.storyboardPerCut ?? 1)} 크레딧
          </button>
        </div>
      </div>
    </div>
  );
}

export default Storyboard;
