/* =============================================================
   features/product-input — ① 제품 정보 입력 (PRD §5)
   Ported verbatim from reference/prototype/features/product-input.jsx.
   Only change: ES imports/exports; onNext → React Router navigate.
   Markup, classNames, inline styles, real file upload unchanged.
   ============================================================= */
import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { uid } from '@/lib/ids.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, Button, IconButton, Skeleton, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { AnalysisForm, AnalysisSkeleton, isMatchRecommendationPatch } from '@/features/analysis/AnalysisForm.jsx';

// human-readable file size
const fmtSize = (b) => b == null ? '' : b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB';

function ColorSwatchPicker({ swatchColors, value, onChange }) {
  return (
    <div className="color-pick">
      <div className="color-pick-head">
        <label className="lbl">색상 선택</label>
        <span className="hint">이 색상의 이름을 골라주세요</span>
      </div>
      <div className="swatch-grid">
        {swatchColors.map((s) => {
          const on = value === s.id;
          return (
            <button key={s.id} className={`swatch${on ? ' on' : ''}`} onClick={() => onChange(s.id)}>
              <span className="swatch-dot" style={{ background: s.hex }} />
              {s.label}
              {on && <Icon name="check" size={13} className="check" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// build file metas from a FileList (drag-drop / picker), capping to the room left
const filesToMetas = (fileList, room) => {
  const imgs = [...fileList].filter((f) => f.type && f.type.startsWith('image/'));
  return imgs.slice(0, Math.max(0, room)).map((f) => ({ src: URL.createObjectURL(f), name: f.name, size: f.size, type: f.type || 'image', lastModified: f.lastModified }));
};
const fileExt = (im) => (im.type && im.type.split('/')[1] ? im.type.split('/')[1].toUpperCase() : 'IMG');

// small file-meta caption shown over an uploaded image (name · size · type) — requested feature
function MetaCap({ im }) {
  return (
    <span className="img-cap">
      <span className="img-cap-name" title={im.name}>{im.name || '이미지'}</span>
      <span className="img-cap-sub">{fmtSize(im.size)} · {fileExt(im)}</span>
    </span>
  );
}

// add target that ALSO accepts drag-drop + click-to-pick (keeps original .tile.add / .up-empty styles)
function AddDrop({ className, slot, room, onAddFiles, children }) {
  const [over, setOver] = useState(false);
  const inputRef = useRef(null);
  const disabled = room <= 0;
  const take = (fileList) => { const metas = filesToMetas(fileList, room); if (metas.length) onAddFiles(slot, metas); };
  return (
    <button type="button" className={`${className}${over ? ' over' : ''}`} disabled={disabled}
      onClick={() => inputRef.current && inputRef.current.click()}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); if (!disabled) take(e.dataTransfer.files); }}>
      <input ref={inputRef} type="file" accept="image/*" multiple hidden
        onChange={(e) => { take(e.target.files); e.target.value = ''; }} />
      {children}
    </button>
  );
}

function ColorImageGroup({ group, catalogs, swatchColors, onAddFiles, onRemove, onRename, onRemoveGroup, onPickColor }) {
  const base = group.isBase;
  const used = group.images.length;
  const chosen = (swatchColors || []).find((s) => s.id === group.swatchId);
  // color indicator (dot + label); gray "색상 미정" until a swatch is picked
  const colorInd = (
    <span className="color-ind" title={chosen ? chosen.label : '색상 미정'}>
      <span className={`color-ind-dot${chosen ? '' : ' undecided'}`} style={{ background: chosen ? chosen.hex : '#d4d4d8' }} />
      <span className={`color-ind-label${chosen ? '' : ' undecided'}`}>{chosen ? chosen.label : '색상 미정'}</span>
    </span>
  );
  const slotLabel = (s) => (catalogs.angleLabels && catalogs.angleLabels[s]) || s;
  const MAX = 6;
  const tiles = (s, small) => {
    const imgs = group.images.filter((im) => im.slot === s);
    return (
      <div className="slot-tiles">
        {imgs.map((im) => (
          <div className={`tile${small ? ' sm' : ''}`} key={im.id}>
            <img src={im.src} alt="" onError={(e) => { e.currentTarget.style.opacity = 0; }} />
            <button className="rm" onClick={() => onRemove(im.id)}><Icon name="x" size={12} /></button>
            <MetaCap im={im} />
          </div>
        ))}
        <AddDrop className={`tile add${small ? ' sm' : ''}`} slot={s} room={MAX - used} onAddFiles={onAddFiles}>
          <span className="add-ico"><Icon name="imagePlus" size={small ? 24 : 26} /></span>
          <span className="add-cap"><span>이미지를</span><span>업로드해주세요</span></span>
        </AddDrop>
      </div>
    );
  };
  // 2×2 angle "wells" — all four angles at a glance, images stack inside each
  const wellSlot = (s) => (
    <div className="slot-well" key={s}>
      <div className="slot-well-head"><span className="swh-label">{slotLabel(s)}{s === 'Front' && <span className="req-star">*</span>}</span></div>
      {tiles(s, true)}
    </div>
  );
  return (
    <div className="color-group">
      {!base && (
        <div className="color-group-head">
          <div className="ttl">
            <span className="color-swatch" style={{ background: chosen ? chosen.hex : '#e9e7ec' }} />
            <div className="sec-title" style={{ fontSize: 15 }}>{chosen ? chosen.label : group.name || '색상'}</div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <IconButton name="trash" size="sm" onClick={onRemoveGroup} title="색상 삭제" />
          </div>
        </div>
      )}

      {base ? (
        <>
          <div className="color-bar">{colorInd}</div>
          <div className="slot-wells">{catalogs.angleSlots.map(wellSlot)}</div>
        </>
      ) : (
        <div className="slot-tiles">
          {group.images.map((im) => (
            <div className="tile" key={im.id}>
              <img src={im.src} alt="" onError={(e) => { e.currentTarget.style.opacity = 0; }} />
              <button className="rm" onClick={() => onRemove(im.id)}><Icon name="x" size={12} /></button>
              <MetaCap im={im} />
            </div>
          ))}
          <AddDrop className="tile add" slot="Front" room={3 - used} onAddFiles={onAddFiles}>
            <Icon name="plus" size={16} />{used === 0 ? '정면 필수' : '추가'}
          </AddDrop>
        </div>
      )}

      {used > 0 && (
        <ColorSwatchPicker swatchColors={swatchColors} value={group.swatchId} onChange={onPickColor} />
      )}

      {!base && <p className="cap-note">정면 사진 필수 · 색상당 최대 3장 · 현재 {used}장</p>}
    </div>
  );
}

export function ProductInput() {
  const navigate = useNavigate();
  const [product, setProduct] = useState(null);
  const [catalogs, setCatalogs] = useState(null);
  const [phase, setPhase] = useState('input');   // input | analyzing | done
  const [analysis, setAnalysis] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const projectId = useAppStore((s) => s.projectId);
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)
  const toast = useToast();

  useEffect(() => {
    (async () => {
      // 직접 URL 진입까지 포함해 projectId 를 보장한다 (frontend_state_model.md §4).
      // 읽기 전용 loadProject 만 사용 — 새 project 생성(reseed)은 TopNav·보관함의
      // 명시적 '새 제작' 액션만 담당한다 (StrictMode 이중 실행에도 멱등).
      await useAppStore.getState().loadProject();
      const pid = useAppStore.getState().projectId;
      const [p, c] = await Promise.all([api.getProduct(pid), api.getCatalogs()]);
      const fresh = { ...p, name: '', colors: [{ ...p.colors[0], swatchId: undefined, images: [] }] };
      setProduct(fresh); setCatalogs(c);
    })();
  }, []);

  if (!product || !catalogs) return <div className="wizard">{doneBlocked && <DoneGuardModal />}<div className="surface"><Skeleton h={420} /></div></div>;

  const set = (patch) => setProduct((p) => ({ ...p, ...patch }));
  // add real uploaded files (drag-drop / picker) with name/size/type meta (PRD §5.5)
  const addImageFiles = (colorId, slot, metas) => setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, images: [...c.images, ...metas.map((m) => ({ id: uid('img'), slot, label: slot, ...m }))] } : c) }));
  const removeImage = (colorId, imgId) => setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, images: c.images.filter((im) => im.id !== imgId) } : c) }));
  const renameColor = (colorId, name) => setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, name } : c) }));
  const setColor = (colorId, swatchId) => setProduct((p) => ({ ...p, colors: p.colors.map((c) => c.id === colorId ? { ...c, swatchId } : c) }));
  const addColor = () => setProduct((p) => p.colors.length >= 3 ? p : ({ ...p, colors: [...p.colors, { id: uid('col'), name: '', isBase: false, images: [] }] }));
  const removeColor = (colorId) => setProduct((p) => ({ ...p, colors: p.colors.filter((c) => c.id !== colorId) }));

  const hasFront = product.colors.some((c) => c.images.some((im) => im.slot === 'Front'));
  const hasName = !!(product.name && product.name.trim());
  const canDone = hasFront && phase === 'input';
  const locked = phase !== 'input';

  // 입력 완료 → analyze inline (skeleton below) → fill analysis form below
  const submit = async () => {
    setPhase('analyzing');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    const a = await api.analyzeProduct(projectId, {});
    setAnalysis(a);
    // 상품명이 비어 있으면 AI가 임의로 지어준다 → 요약 카드에 표시됨
    const finalName = (product.name && product.name.trim()) ? product.name.trim() : (a.suggestedName || '새 상품');
    if (!product.name || !product.name.trim()) set({ name: finalName });
    // persist the user's input (name + 색상/이미지) into the create flow so the
    // downstream steps (mannequin / storyboard / editor) read what was entered,
    // not the seed (mock/api.saveProduct → DB.product). [data-flow fix]
    await api.saveProduct(projectId, { ...product, name: finalName, uploadComplete: true });
    setPhase('done');
    toast.push('AI 분석을 완료했어요', { icon: 'sparkles' });
  };

  const nameCard = (
    <div className="surface">
      <div className="sec-head">
        <div><div className="sec-title">상품명</div></div>
      </div>
      <input className="field" value={product.name} placeholder="예: 소프트 골지 라운드 니트"
        disabled={locked} onChange={(e) => set({ name: e.target.value })} />
    </div>
  );

  const allUploaded = product.colors.flatMap((c) => c.images);
  const imgCount = product.colors[0] ? product.colors[0].images.length : 0;
  const images = (
    <div className="surface pi-images">
      <div className="sec-head">
        <div className="ttl" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="sec-title" style={{ whiteSpace: 'nowrap' }}>상품 이미지</div>
          <span className="pill pill-soft">{imgCount}장</span>
        </div>
      </div>
      <div className="sec-sub" style={{ marginTop: -6, marginBottom: 16 }}>각도별로 한 장 이상 올리면 더 정확한 상세페이지가 만들어져요. 앞면은 필수예요.</div>
      {product.colors.map((c) => (
        <ColorImageGroup key={c.id} group={c} catalogs={catalogs} swatchColors={catalogs.swatchColors}
          onAddFiles={(slot, metas) => addImageFiles(c.id, slot, metas)} onRemove={(id) => removeImage(c.id, id)}
          onRename={(n) => renameColor(c.id, n)} onRemoveGroup={() => removeColor(c.id)} onPickColor={(sid) => setColor(c.id, sid)} />
      ))}
      {!locked && (
        <div style={{ marginTop: 16 }}>
          <Button variant="quiet" icon="plus" onClick={addColor} disabled={product.colors.length >= 3}>색상 추가</Button>
          {product.colors.length >= 3 && <p className="hint" style={{ marginTop: 8 }}>색상은 최대 3개까지 추가할 수 있어요.</p>}
        </div>
      )}
    </div>
  );

  // 입력 섹션: 상품명 + 이미지를 한 카드로
  const inputSection = <div className="merged-card">{nameCard}{images}</div>;

  const wide = phase !== 'input';

  // after 입력 완료, the input collapses into a compact summary above the analysis
  const allImages = product.colors.flatMap((c) => c.images);
  const colorCount = product.colors.filter((c) => c.images.length).length;
  const summaryCard = (
    <div className="surface pi-summary">
      <div className="pi-summary-row">
        <div className="pi-summary-thumbs">
          {allImages.slice(0, 5).map((im) => <img key={im.id} src={im.src} alt="" />)}
          {allImages.length > 5 && <span className="more">+{allImages.length - 5}</span>}
        </div>
        <div className="pi-summary-meta">
          <div className="sec-title" style={{ fontSize: 15 }}>{product.name || '상품 이미지'}</div>
          <div className="hint" style={{ marginTop: 3 }}>이미지 {allImages.length}장 · 색상 {colorCount || 1}</div>
        </div>
        <button className="btn btn-quiet btn-sm" onClick={() => setExpanded((e) => !e)}>
          {expanded ? '접기' : '펼치기'}<Icon name={expanded ? 'chevUp' : 'chevDown'} size={15} />
        </button>
      </div>
      {expanded && <div className="pi-summary-body">{inputSection}</div>}
    </div>
  );

  return (
    <div className={`wizard${wide ? ' wide' : ''}`}>
      {doneBlocked && <DoneGuardModal />}
      <PageHead
        title="의류 이미지를 올려주세요"
        sub={<>사진 몇장만으로 경험해보세요.<br />부족한 정보는 AI 분석 후 직접 확인하고 보정할 수 있어요.</>}
      />

      {phase === 'input' ? inputSection : summaryCard}

      {phase === 'input' && (
        <>
          <WizardCTA>
            <Button variant="primary" size="lg" icon="check" disabled={!canDone} onClick={submit}>입력 완료</Button>
          </WizardCTA>
          {!canDone && <p className="hint" style={{ textAlign: 'right', marginTop: 8 }}>앞면 이미지를 1장 이상 올리면 입력을 완료할 수 있어요.</p>}
        </>
      )}

      <div className="af-anchor" />
      {phase === 'analyzing' && <AnalysisSkeleton />}
      {phase === 'done' && (
        <div className="pi-reveal">
          <AnalysisForm inline analysis={analysis} catalogs={catalogs}
            onChange={(patch) => {
              // 후보 목록은 서버 소유 — 추천 갱신 패치뿐 아니라 선택 토글 응답도
              // 서버 머지 결과로 동기화해 묵은 후보가 로컬에 남지 않게 한다.
              const syncMatch = isMatchRecommendationPatch(patch) || 'matchClothing' in patch;
              setAnalysis((a) => ({ ...a, ...patch }));
              api.saveAnalysis(projectId, patch).then((saved) => {
                if (syncMatch) setAnalysis((a) => ({ ...a, matchClothing: saved.matchClothing }));
              });
            }}
            onNext={() => navigate('/create/mannequin')} />
        </div>
      )}
    </div>
  );
}

export default ProductInput;
