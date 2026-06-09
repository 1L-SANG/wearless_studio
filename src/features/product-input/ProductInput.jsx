/* =============================================================
   product-input/ProductInput.jsx — 제품 정보 입력 + 인라인 AI 분석.
   (PRD §5, §6, §13.1) input + analysis are a single route. On
   "입력 완료" the input collapses to a summary card and analysis
   runs inline below, then the AnalysisForm renders in place.
   ============================================================= */
import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { Placeholder } from '@/mock/placeholders.js';
import { useAppStore } from '@/store/useAppStore.js';
import { LIMITS } from '@/lib/limits.js';
import { Button } from '@/components/Button.jsx';
import { Icon } from '@/components/Icon.jsx';
import { Field } from '@/components/Form.jsx';
import { Skeleton, ErrorState } from '@/components/States.jsx';
import { PageHead } from '@/features/shell/PageHead.jsx';
import { useToast } from '@/components/Toast.jsx';
import { UploadWell } from './UploadWell.jsx';
import { AnalysisForm } from '@/features/analysis/AnalysisForm.jsx';
import styles from './ProductInput.module.css';

const BASE_SLOTS = ['Front', 'Back', 'Detail', 'Fit'];

function mockUpload(slot, label) {
  const id = Math.random().toString(36).slice(2, 8);
  const src = slot === 'Detail'
    ? Placeholder.detail('up' + id, 300, 400)
    : Placeholder.photo('up' + id, slot === 'Fit' ? 'styling' : 'horizon', 300, 400);
  return {
    id: 'img_' + id, slot, label, src,
    file: { name: `product_${(slot || 'img').toLowerCase()}_${id}.jpg`, size: `${(0.4 + Math.random() * 2.6).toFixed(1)} MB`, type: 'JPG' },
  };
}

export function ProductInput() {
  const navigate = useNavigate();
  const toast = useToast();
  const catalogs = useAppStore((s) => s.catalogs);
  const loadCatalogs = useAppStore((s) => s.loadCatalogs);
  const setProduct = useAppStore((s) => s.setProduct);
  const setAnalysis = useAppStore((s) => s.setAnalysis);

  useEffect(() => { loadCatalogs(); }, [loadCatalogs]);

  const [name, setName] = useState('');
  const [base, setBase] = useState({ Front: null, Back: null, Detail: null, Fit: null });
  const [extras, setExtras] = useState([]); // { id, colorId, images:[] }
  const [phase, setPhase] = useState('input'); // input | analyzing | error | analysis
  const [expanded, setExpanded] = useState(true);
  const [progress, setProgress] = useState(0);

  const angleLabels = catalogs?.angleLabels || {};
  const swatches = catalogs?.swatchColors || [];

  const imageCount = useMemo(
    () => Object.values(base).filter(Boolean).length + extras.reduce((n, c) => n + c.images.length, 0),
    [base, extras],
  );
  const colorCount = 1 + extras.length;
  const canComplete = !!base.Front;
  const firstThumb = base.Front?.src || base.Back?.src || null;

  /* ---- base color wells ---- */
  const addBase = (slot) => setBase((b) => ({ ...b, [slot]: mockUpload(slot, angleLabels[slot] || slot) }));
  const removeBase = (slot) => setBase((b) => ({ ...b, [slot]: null }));

  /* ---- extra colors ---- */
  const addExtraColor = () => {
    if (extras.length >= LIMITS.additionalColorMax) return;
    setExtras((e) => [...e, { id: 'col_' + Math.random().toString(36).slice(2, 7), colorId: null, images: [] }]);
  };
  const removeExtraColor = (id) => setExtras((e) => e.filter((c) => c.id !== id));
  const pickSwatch = (id, colorId) => setExtras((e) => e.map((c) => (c.id === id ? { ...c, colorId } : c)));
  const addExtraImage = (id) => setExtras((e) => e.map((c) => (
    c.id === id && c.images.length < LIMITS.additionalColorMaxImages
      ? { ...c, images: [...c.images, mockUpload('Front', '정면')] }
      : c
  )));
  const removeExtraImage = (id, imgId) => setExtras((e) => e.map((c) => (
    c.id === id ? { ...c, images: c.images.filter((im) => im.id !== imgId) } : c
  )));

  /* ---- complete → analyze inline ---- */
  const runAnalysis = () => {
    setPhase('analyzing');
    setProgress(0);
    api.analyzeProduct({ onProgress: setProgress })
      .then((analysis) => { setAnalysis(analysis); setPhase('analysis'); })
      .catch((err) => { setPhase('error'); toast?.push(err.message || '분석에 실패했어요.', { icon: 'alertCircle' }); });
  };

  const onComplete = () => {
    if (!canComplete) return;
    const baseImages = BASE_SLOTS.map((s) => base[s]).filter(Boolean);
    const colors = [
      { id: 'col1', name: '기준 색상', isBase: true, isMain: true, images: baseImages },
      ...extras.map((c) => ({
        id: c.id,
        name: swatches.find((s) => s.id === c.colorId)?.label || '색상 미정',
        isBase: false,
        images: c.images,
      })),
    ];
    setProduct({ id: 'prd_' + Date.now(), name: name.trim(), clothingType: 'top', colors, measurements: [], measurementsUnknown: false, uploadComplete: true });
    setExpanded(false);
    runAnalysis();
  };

  return (
    <div className={styles.wrap}>
      <PageHead title="의류 이미지를 올려주세요" />

      {/* ---- summary card (after 입력 완료) ---- */}
      {phase !== 'input' && (
        <div className={styles.summary}>
          <div className={styles.summaryThumb}>{firstThumb ? <img src={firstThumb} alt="" /> : <Icon name="image" size={20} />}</div>
          <div className={styles.summaryInfo}>
            <div className={styles.summaryName}>{name.trim() || '상품명 미입력'}</div>
            <div className={styles.summaryMeta}>이미지 {imageCount}장 · 색상 {colorCount}개</div>
          </div>
          <Button variant="quiet" size="sm" icon={expanded ? 'chevUp' : 'chevDown'} onClick={() => setExpanded((v) => !v)}>
            {expanded ? '접기' : '펼치기'}
          </Button>
        </div>
      )}

      {/* ---- input section ---- */}
      {(phase === 'input' || expanded) && (
        <section className={styles.card}>
          <div className={styles.block}>
            <h2 className={styles.blockTitle}>상품명</h2>
            <Field placeholder="예: 소프트 골지 라운드 니트" value={name} onChange={(e) => setName(e.target.value)} />
          </div>

          <div className={styles.divider} />

          <div className={styles.block}>
            <div className={styles.blockHeadRow}>
              <h2 className={styles.blockTitle}>상품 이미지</h2>
              <span className={styles.counter}>{imageCount}장</span>
            </div>
            <p className={styles.blockHint}>각도별로 한 장 이상 올리면 더 정확한 상세페이지가 만들어져요. 앞면은 필수예요.</p>

            <div className={styles.colorHead}><span className={styles.colorDot} />기준 색상</div>
            <div className={styles.wellGrid}>
              {BASE_SLOTS.map((slot) => (
                <UploadWell
                  key={slot}
                  label={angleLabels[slot] || slot}
                  required={slot === 'Front'}
                  image={base[slot]}
                  onAdd={() => addBase(slot)}
                  onRemove={() => removeBase(slot)}
                />
              ))}
            </div>

            {/* extra colors */}
            {extras.map((c) => {
              const sw = swatches.find((s) => s.id === c.colorId);
              return (
                <div key={c.id} className={styles.extraColor}>
                  <div className={styles.extraHead}>
                    <div className={styles.swatchPicker}>
                      <span className={styles.colorChip} data-empty={!sw} style={sw ? { '--sw': sw.hex } : undefined} />
                      <span className={styles.extraName}>{sw?.label || '색상 미정'}</span>
                    </div>
                    <button type="button" className={styles.extraRemove} onClick={() => removeExtraColor(c.id)}><Icon name="x" size={14} />색상 삭제</button>
                  </div>
                  <div className={styles.swatchRow}>
                    {swatches.map((s) => (
                      <button
                        key={s.id}
                        type="button"
                        className={`${styles.swatch} ${c.colorId === s.id ? styles.swatchOn : ''}`}
                        style={{ '--sw': s.hex }}
                        title={s.label}
                        onClick={() => pickSwatch(c.id, s.id)}
                      />
                    ))}
                  </div>
                  <div className={styles.extraWells}>
                    {c.images.map((im) => (
                      <UploadWell key={im.id} label="정면" image={im} onRemove={() => removeExtraImage(c.id, im.id)} />
                    ))}
                    {c.images.length < LIMITS.additionalColorMaxImages && (
                      <UploadWell label="정면" required={c.images.length === 0} image={null} onAdd={() => addExtraImage(c.id)} />
                    )}
                  </div>
                </div>
              );
            })}

            {extras.length < LIMITS.additionalColorMax && (
              <Button variant="ghost" size="sm" icon="plus" onClick={addExtraColor}>추가 색상</Button>
            )}
          </div>
        </section>
      )}

      {/* ---- complete CTA (input phase only) ---- */}
      {phase === 'input' && (
        <div className={styles.cta}>
          <Button variant="primary" size="lg" disabled={!canComplete} onClick={onComplete}>입력 완료</Button>
          {!canComplete && <p className={styles.ctaHint}>앞면 이미지를 한 장 이상 올리면 분석을 시작할 수 있어요.</p>}
        </div>
      )}

      {/* ---- analysis loading skeleton (PRD §5.5) ---- */}
      {phase === 'analyzing' && (
        <section className={styles.card}>
          <div className={styles.analyzing}>
            <Icon name="loader" size={18} className="spin" />
            <span>AI가 상품을 분석하고 있어요… {progress}%</span>
          </div>
          <div className={styles.skelRows}>
            <Skeleton w="40%" h={22} />
            <Skeleton h={60} />
            <Skeleton h={60} />
            <Skeleton w="60%" h={60} />
          </div>
        </section>
      )}

      {phase === 'error' && (
        <section className={styles.card}>
          <ErrorState desc="분석 서버에 일시적인 문제가 발생했어요." onRetry={runAnalysis} />
        </section>
      )}

      {/* ---- inline analysis form (PRD §6) ---- */}
      {phase === 'analysis' && (
        <AnalysisForm onNext={() => navigate('/create/mannequin')} />
      )}
    </div>
  );
}

export default ProductInput;
