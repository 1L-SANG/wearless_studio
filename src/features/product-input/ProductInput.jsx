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
import { isGenerationRelevantAnalysisPatch, useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { saveProductDraft, loadDraft, clearDraft, hasPendingDraft } from '@/lib/draftStore.js';
import { syncDraftToBackend } from '@/lib/draftSync.js';
import { Icon, Button, IconButton, ErrorState, Skeleton, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA, useDoneGuard, DoneGuardModal } from '@/features/shell/shell.jsx';
import { AnalysisForm, AnalysisSkeleton, AnalysisProgress, isMatchRecommendationPatch } from '@/features/analysis/AnalysisForm.jsx';

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
  const [loadError, setLoadError] = useState('');
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [phase, setPhase] = useState('input');   // input | analyzing | done
  // 분석 결과 도착 신호 — 화면 전환은 대기 연출(AnalysisProgress)이 잔여 단계를 완주한 뒤
  // onFinished 로 수행한다 (애니메이션 끝 ≈ 전환, 2026-07-13 A안 결정).
  const [analysisReady, setAnalysisReady] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [analysisProjectId, setAnalysisProjectId] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const { session, loading: authLoading, openLogin } = useAuth();
  const doneBlocked = useDoneGuard();   // 생성 완료 후 초안 재진입 제한 (PRD §10.17)
  const toast = useToast();

  // 분석 CTA — 마네킹부터는 로그인 필요. 서버 분석을 마친 로그인 사용자는 바로 이동한다.
  // 로컬 분석 결과는 먼저 IndexedDB 에 보관한다. 미로그인이면 로그인 모달을 띄우고, 이미
  // 로그인한 상태(로그인 복귀 후 동기화 실패·다른 탭 로그인 포함)면 여기서 백엔드 동기화를
  // 재시도한 뒤 이동한다. analysisProjectId 를 로그인 여부의 대용값으로 쓰지 않는다.
  const redirectingRef = useRef(false);
  const analysisSaveChainRef = useRef(Promise.resolve());
  const goToMannequin = async () => {
    if (redirectingRef.current) return; // 더블클릭/재진입 가드 (blob 추출 await 중)
    redirectingRef.current = true;
    try {
      // 직전 입력 이벤트의 PATCH가 getAnalysis보다 늦게 도착하는 레이스를 막는다. 모든 분석 저장을
      // 입력 순서대로 직렬화하고, 확정은 현재 큐까지만 기다린 뒤 이동/재생성을 시작한다.
      await analysisSaveChainRef.current;
      // 이벤트 시점에만 읽어 분석 편집마다 ProductInput 전체가 다시 렌더되지 않게 한다.
      const routeState = useAppStore.getState().generationRelevantEditsDirty
        ? { refreshForEdits: true }
        : undefined;
      if (session && analysisProjectId) {
        navigate('/create/mannequin', { state: routeState });
        return;
      }
      const { failed } = await saveProductDraft(product, analysis);
      if (failed) toast.push(`일부 사진(${failed}장)을 임시 저장하지 못했어요.`, { icon: 'alertTri' });
      if (session) {
        const draft = await loadDraft();
        if (!draft?.product) throw new Error('저장된 입력 내용을 다시 불러오지 못했어요. 다시 시도해 주세요.');
        const { projectId } = await syncDraftToBackend(draft);
        useAppStore.getState().adoptProject(projectId);
        setAnalysisProjectId(projectId);
        await clearDraft().catch(() => {});
        navigate('/create/mannequin', { state: routeState });
        return;
      }
      openLogin('/create/mannequin');
    } catch (error) {
      toast.push(error?.message || '입력 내용을 서버에 저장하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    } finally {
      redirectingRef.current = false;
    }
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoadError('');
      // cold input 은 라우트 계층이 stale flow 를 먼저 비운다. 같은 탭에서 돌아온 input 만
      // 현재 project 를 읽고, project 가 없으면 null 계약의 클라이언트 시드 템플릿을 쓴다.
      const { projectId: currentProjectId, projectPersisted } = useAppStore.getState();
      const editingProjectId = projectPersisted && currentProjectId ? currentProjectId : null;
      const [p, c, existingAnalysis] = await Promise.all([
        api.getProduct(editingProjectId),
        api.getCatalogs(),
        editingProjectId ? api.getAnalysis(editingProjectId) : Promise.resolve(null),
      ]);
      if (!alive) return;
      setCatalogs(c);

      // 같은 탭에서 마네킹/후속 단계로 갔다가 input 으로 돌아온 경우에는 현재 프로젝트를
      // 편집한다. cold input 은 라우트 계층이 먼저 beginProject 해서 여기까지 stale id가 오지 않는다.
      if (editingProjectId && existingAnalysis) {
        setProduct(p);
        setAnalysis(existingAnalysis);
        setAnalysisProjectId(editingProjectId);
        setPhase('done');
        return;
      }

      // 로그인 실패/취소/브라우저 뒤로가기(카카오→←뒤로→구글)·새로고침으로 입력 화면에 돌아오면
      // 페이지가 새로고침돼 입력이 사라진다 → 리다이렉트 직전 저장해 둔 draft(입력+분석)를 복원한다
      // (사진 blob→objectURL 재생성, imageId 매칭). 단 '이 탭 세션'에 저장한 경우만
      // (hasPendingDraft=sessionStorage) — 같은 탭은 복원되고, 공용 브라우저의 다른 사용자
      // (다른 탭/세션)에겐 복원되지 않아 입력이 누출되지 않는다. draft 는 새 제작·로그아웃 때 정리.
      const draft = hasPendingDraft() ? await loadDraft().catch(() => null) : null;
      if (!alive) return;
      if (draft?.product) {
        const urlById = {};
        for (const ph of draft.photos || []) {
          try { urlById[ph.imageId] = URL.createObjectURL(ph.blob); } catch { /* skip */ }
        }
        const restored = {
          ...draft.product,
          colors: (draft.product.colors || []).map((col) => ({
            ...col,
            images: (col.images || []).map((im) => ({ ...im, src: urlById[im.id] || im.src })),
          })),
        };
        setProduct(restored);
        // 분석 결과 복원 → 분석 폼(done)으로 바로. 단 정면 사진이 추출 실패로 빠졌으면 입력
        // 단계로 둬서 '정면 필수' 검증이 재업로드를 강제하게 한다(검증 우회 방지).
        // 정면 판정은 product 메타데이터가 아니라 실제 저장된 photo blob(photos[]) 기준 — 더 안전.
        const restoredHasFront = (draft.photos || []).some((p) => p.slot === 'Front');
        if (draft.analysis && restoredHasFront) { setAnalysis(draft.analysis); setPhase('done'); }
        return;
      }

      const fresh = { ...p, name: '', colors: [{ ...p.colors[0], swatchId: undefined, images: [] }] };
      setProduct(fresh);
    })().catch((error) => {
      if (alive) setLoadError(error?.message || '입력 화면을 불러오지 못했어요. 다시 시도해 주세요.');
    });
    return () => { alive = false; };
  }, [loadAttempt]);

  if (loadError) return (
    <div className="wizard">
      {doneBlocked && <DoneGuardModal />}
      <div className="surface">
        <ErrorState desc={loadError} onRetry={() => setLoadAttempt((n) => n + 1)} />
      </div>
    </div>
  );
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
  const canDone = hasFront && phase === 'input' && !authLoading;
  const locked = phase !== 'input';

  // AI 분석하기 → analyze inline (skeleton below) → fill analysis form below
  const submit = async () => {
    if (authLoading) return;
    setAnalysisReady(false);
    setPhase('analyzing');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    try {
      // 보관함 프로젝트(서버 행)는 바로 이 시점에 생성한다 — '상세페이지 제작' 진입이 아니라
      // AI 분석을 시작할 때. createProject 는 토큰이 필요하므로 로그인 사용자만 생성하고,
      // 비로그인 공개 분석은 서버 project 없이 진행(프로젝트 생성은 로그인 후 단계가 담당).
      const pid = session ? await useAppStore.getState().ensureProject() : null;
      // 인증 상태가 이후 바뀌어도 이 분석·편집은 시작할 때 선택한 backend/project에 고정한다.
      setAnalysisProjectId(pid);
      // 사진을 서버에 먼저 올리고(images[].id=asset id) 상품을 저장한다 — http 분석 워커는
      // 저장된 products.colors 를 읽으므로, 분석보다 반드시 앞서야 한다(순서 뒤집히면 no_product_images).
      // mock 모드에선 uploadProductPhotos·saveProduct 가 인메모리 no-op 이라 동작 동일.
      const uploaded = await api.uploadProductPhotos(pid, product);
      const enteredName = (product.name && product.name.trim()) ? product.name.trim() : null;
      // 저장은 이 단계가 실제로 만든 것만 — colors(asset id)·업로드 완료·입력한 이름. measurements 등은
      // getProduct 가 아직 mock seed 라 통째로 보내면 가짜 실측이 실서버로 샌다(seed 누출 차단).
      await api.saveProduct(pid, {
        colors: uploaded.colors, uploadComplete: true, ...(enteredName ? { name: enteredName } : {}),
      });
      const a = await api.analyzeProduct(pid, {});
      setAnalysis(a);
      // 상품명이 비어 있으면 AI가 임의로 지어준다 → 요약 카드에 표시됨 + 서버에도 반영
      const finalName = enteredName || a.suggestedName || '새 상품';
      if (!enteredName) {
        set({ name: finalName });
        await api.saveProduct(pid, { name: finalName });
      }
      // 즉시 전환하지 않는다 — 대기 연출이 잔여 단계를 빠르게 완주한 뒤 onFinished 에서 전환.
      setAnalysisReady(true);
    } catch (e) {
      // http 모드에서 분석 실패(네트워크·서버 에러)해도 스피너에 고착되지 않게 — 입력으로 복귀 + 안내.
      setPhase('input');
      toast.push(e?.message || '분석에 실패했어요. 잠시 후 다시 시도해 주세요.', { icon: 'alert' });
    }
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

  // after AI analysis starts, the input collapses into a compact summary above the analysis
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
            <Button variant="primary" size="lg" icon="check" disabled={!canDone} onClick={submit}>AI 분석하기</Button>
          </WizardCTA>
          {!hasFront && <p className="hint" style={{ textAlign: 'right', marginTop: 8 }}>앞면 이미지를 1장 이상 올리면 입력을 완료할 수 있어요.</p>}
          {hasFront && authLoading && <p className="hint" style={{ textAlign: 'right', marginTop: 8 }}>로그인 상태를 확인하고 있어요.</p>}
        </>
      )}

      <div className="af-anchor" />
      {phase === 'analyzing' && (
        <>
          <AnalysisProgress
            photoSrc={product.colors?.[0]?.images?.[0]?.src}
            done={analysisReady}
            onFinished={() => { setPhase('done'); toast.push('AI 분석을 완료했어요', { icon: 'sparkles' }); }} />
          <AnalysisSkeleton />
        </>
      )}
      {phase === 'done' && (
        <div className="pi-reveal">
          <AnalysisForm inline analysis={analysis} catalogs={catalogs}
            onChange={(patch) => {
              // 후보 목록은 서버 소유 — 추천 갱신 패치뿐 아니라 선택 토글 응답도
              // 서버 머지 결과로 동기화해 묵은 후보가 로컬에 남지 않게 한다.
              const syncMatch = isMatchRecommendationPatch(patch) || 'matchClothing' in patch;
              if (isGenerationRelevantAnalysisPatch(patch)) {
                useAppStore.getState().markGenerationRelevantEdits();
              }
              setAnalysis((a) => ({ ...a, ...patch }));
              analysisSaveChainRef.current = analysisSaveChainRef.current
                .then(() => api.saveAnalysis(analysisProjectId, patch))
                .then((saved) => {
                  if (syncMatch) setAnalysis((a) => ({ ...a, matchClothing: saved.matchClothing }));
                })
                .catch((error) => {
                  toast.push(error?.message || '분석 수정 내용을 저장하지 못했어요.', { icon: 'alertTri' });
                });
            }}
            onNext={goToMannequin} />
        </div>
      )}
    </div>
  );
}

export default ProductInput;
