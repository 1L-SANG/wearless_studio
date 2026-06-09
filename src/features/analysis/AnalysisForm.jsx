/* =============================================================
   analysis/AnalysisForm.jsx — AI 분석 결과 확인/보정 (PRD §6).
   기본 정보 → 소재 → 실측 → 강조 특징 → 모델 → 매칭 의류.
   Edits persist to the store (patchAnalysis) so downstream steps
   (mannequin, storyboard) read the confirmed analysis.
   ============================================================= */
import { useState } from 'react';
import { useAppStore } from '@/store/useAppStore.js';
import { api } from '@/lib/api/index.js';
import { LIMITS } from '@/lib/limits.js';
import { Button } from '@/components/Button.jsx';
import { Icon } from '@/components/Icon.jsx';
import { Chips } from '@/components/Form.jsx';
import { WizardCTA } from '@/features/shell/WizardCTA.jsx';
import styles from './Analysis.module.css';

export function AnalysisForm({ onNext }) {
  const analysis = useAppStore((s) => s.analysis);
  const catalogs = useAppStore((s) => s.catalogs);
  const patchAnalysis = useAppStore((s) => s.patchAnalysis);

  const [matDraft, setMatDraft] = useState(null); // { name, ratio } | null
  const [pointDraft, setPointDraft] = useState('');

  if (!analysis || !catalogs) return null;

  const set = (patch) => patchAnalysis(patch);
  const subCats = catalogs.subCategories?.[analysis.clothingType] || [];
  const measSchema = catalogs.measurementSchema?.[analysis.clothingType] || [];
  const materials = analysis.materials || [];
  const matSum = materials.reduce((n, m) => n + (Number(m.ratio) || 0), 0);
  const points = analysis.sellingPoints || [];
  const aiPoints = analysis.aiSuggestedPoints || [];
  const pointTotal = points.length + aiPoints.length;
  const matchSelected = (analysis.matchClothing || []).filter((m) => m.selected).sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0));

  /* ---- materials ---- */
  const addMaterial = () => {
    const name = (matDraft?.name || '').trim();
    const ratio = Number(matDraft?.ratio) || 0;
    if (!name) return;
    set({ materials: [...materials, { name, ratio }] });
    setMatDraft(null);
  };
  const removeMaterial = (i) => set({ materials: materials.filter((_, j) => j !== i) });

  /* ---- measurements ---- */
  const setMeas = (key, raw) => {
    const value = raw === '' ? null : Number(raw);
    const next = (analysis.measurements || []).map((m) => (m.key === key ? { ...m, value } : m));
    set({ measurements: next });
  };
  const measByKey = (key) => (analysis.measurements || []).find((m) => m.key === key);

  /* ---- selling points ---- */
  const addPoint = () => {
    const t = pointDraft.trim();
    if (t && pointTotal < LIMITS.sellingPointMax && !points.includes(t) && !aiPoints.includes(t)) {
      set({ sellingPoints: [...points, t] });
      setPointDraft('');
    }
  };
  const removePoint = (p) => set({ sellingPoints: points.filter((x) => x !== p) });
  const removeAiPoint = (p) => set({ aiSuggestedPoints: aiPoints.filter((x) => x !== p) });

  /* ---- match clothing (max 2, 메인/서브) ---- */
  const toggleMatch = (id) => {
    const list = analysis.matchClothing || [];
    const cur = list.find((m) => m.id === id);
    let next;
    if (cur?.selected) {
      next = list.map((m) => (m.id === id ? { ...m, selected: false, selOrder: undefined } : m));
    } else {
      if (matchSelected.length >= LIMITS.matchClothingMax) return;
      const order = matchSelected.length + 1;
      next = list.map((m) => (m.id === id ? { ...m, selected: true, selOrder: order } : m));
    }
    // renumber remaining selected by current order
    const reSel = next.filter((m) => m.selected).sort((a, b) => (a.selOrder || 0) - (b.selOrder || 0));
    next = next.map((m) => { const idx = reSel.findIndex((x) => x.id === m.id); return idx >= 0 ? { ...m, selOrder: idx + 1 } : m; });
    set({ matchClothing: next });
  };

  const proceed = () => { api.saveAnalysis(analysis); onNext?.(); };

  return (
    <>
      <div className={styles.head}>
        <h2 className={styles.headTitle}>AI가 상품 정보를 분석했어요</h2>
        <p className={styles.headSub}>틀린 부분이 있으면 직접 수정해주세요.</p>
      </div>

      <section className={styles.card}>
        {/* 기본 정보 */}
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>기본 정보</h3>
          <div className={styles.field}><span className={styles.fieldLabel}>의류 종류</span>
            <Chips options={catalogs.clothingTypes} value={analysis.clothingType} onChange={(v) => set({ clothingType: v, subCategory: '' })} />
          </div>
          {subCats.length > 0 && (
            <div className={styles.field}><span className={styles.fieldLabel}>세부 카테고리</span>
              <Chips options={subCats} value={analysis.subCategory} onChange={(v) => set({ subCategory: v })} />
            </div>
          )}
          <div className={styles.field}><span className={styles.fieldLabel}>대상 성별</span>
            <Chips options={catalogs.genders} value={analysis.targetGenders} onChange={(v) => set({ targetGenders: v })} multi />
          </div>
          <div className={styles.field}><span className={styles.fieldLabel}>핏</span>
            <Chips options={catalogs.fits} value={analysis.fit} onChange={(v) => set({ fit: v })} />
          </div>
        </div>

        <div className={styles.divider} />

        {/* 소재 */}
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>소재</h3>
          <p className={styles.sectionHint}>혼용률을 입력해주세요. 합계 100%를 권장해요.{matSum > 100 && <span className={styles.warn}> 합계가 {matSum}%로 100%를 넘었어요.</span>}</p>
          <div className={styles.matRow}>
            {materials.map((m, i) => (
              <span key={i} className={styles.matChip}>{m.name}<i>{m.ratio}%</i><button type="button" onClick={() => removeMaterial(i)} aria-label="삭제"><Icon name="x" size={12} /></button></span>
            ))}
            {matDraft ? (
              <span className={styles.matDraft}>
                <input className={styles.matName} placeholder="소재명" value={matDraft.name} autoFocus onChange={(e) => setMatDraft({ ...matDraft, name: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && addMaterial()} />
                <input className={styles.matRatio} placeholder="%" inputMode="numeric" value={matDraft.ratio} onChange={(e) => setMatDraft({ ...matDraft, ratio: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && addMaterial()} />
                <button type="button" className={styles.matAddOk} onClick={addMaterial}><Icon name="check" size={14} /></button>
              </span>
            ) : (
              <button type="button" className={styles.addChip} onClick={() => setMatDraft({ name: '', ratio: '' })}><Icon name="plus" size={13} />소재 추가</button>
            )}
          </div>
        </div>

        <div className={styles.divider} />

        {/* 실측 정보 */}
        <div className={styles.section}>
          <div className={styles.sectionHeadRow}>
            <h3 className={styles.sectionTitle}>실측 정보</h3>
            <label className={styles.checkbox}>
              <input type="checkbox" checked={analysis.measurementsUnknown} onChange={(e) => set({ measurementsUnknown: e.target.checked })} />
              실측 모름
            </label>
          </div>
          <p className={styles.sectionHint}>실측은 AI가 추정하지 않아요. 단위 cm. 아는 항목만 입력해도 돼요.</p>
          {!analysis.measurementsUnknown && (
            <div className={styles.measGrid}>
              {measSchema.map((key) => {
                const m = measByKey(key);
                return (
                  <div key={key} className={styles.measItem}>
                    <span className={styles.measLabel}>{key}</span>
                    <div className={styles.measInputWrap}>
                      <input className={styles.measInput} inputMode="numeric" placeholder="—" value={m?.value ?? ''} onChange={(e) => setMeas(key, e.target.value)} />
                      <span className={styles.measUnit}>cm</span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className={styles.divider} />

        {/* 강조하고 싶은 특징 */}
        <div className={styles.section}>
          <div className={styles.sectionHeadRow}>
            <h3 className={styles.sectionTitle}>강조하고 싶은 특징</h3>
            <span className={styles.counter}>{pointTotal}/{LIMITS.sellingPointMax}개</span>
          </div>
          <div className={styles.pointRow}>
            {aiPoints.map((p) => (
              <span key={p} className={`${styles.point} ${styles.pointAi}`}><span className={styles.aiBadge}>AI 제안</span>{p}<button type="button" onClick={() => removeAiPoint(p)} aria-label="삭제"><Icon name="x" size={12} /></button></span>
            ))}
            {points.map((p) => (
              <span key={p} className={styles.point}>{p}<button type="button" onClick={() => removePoint(p)} aria-label="삭제"><Icon name="x" size={12} /></button></span>
            ))}
            {pointTotal < LIMITS.sellingPointMax && (
              <input className={styles.pointInput} placeholder="특징 입력 후 Enter" value={pointDraft} onChange={(e) => setPointDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addPoint(); } }} onBlur={addPoint} />
            )}
          </div>
        </div>

        <div className={styles.divider} />

        {/* 모델 선택 */}
        <div className={styles.section}>
          <h3 className={styles.sectionTitle}>모델 선택</h3>
          <div className={styles.cardList}>
            {(analysis.models || []).map((m) => (
              <button key={m.id} type="button" className={`${styles.pickCard} ${analysis.selectedModelId === m.id ? styles.pickOn : ''}`} onClick={() => set({ selectedModelId: m.id })}>
                <img src={m.thumb} alt={m.name} />
                <span className={styles.pickName}>{m.name}</span>
                {m.recommended && <span className={styles.recStar}><Icon name="star" size={13} fill="currentColor" />추천</span>}
                {analysis.selectedModelId === m.id && <span className={styles.pickCheck}><Icon name="check" size={14} /></span>}
              </button>
            ))}
          </div>
        </div>

        <div className={styles.divider} />

        {/* 매칭 의류 */}
        <div className={styles.section}>
          <div className={styles.sectionHeadRow}>
            <h3 className={styles.sectionTitle}>매칭 의류</h3>
            <span className={styles.counter}>{matchSelected.length}/{LIMITS.matchClothingMax}개</span>
          </div>
          <p className={styles.sectionHint}>스타일링컷에서 함께 입힐 의류를 골라주세요.</p>
          <div className={styles.cardList}>
            {(analysis.matchClothing || []).map((m) => (
              <button key={m.id} type="button" className={`${styles.pickCard} ${m.selected ? styles.pickOn : ''}`} onClick={() => toggleMatch(m.id)}>
                <img src={m.thumb} alt={m.name} />
                <span className={styles.pickName}>{m.name}</span>
                {m.selected && <span className={styles.roleBadge}>{m.selOrder === 1 ? '메인' : '서브'}</span>}
              </button>
            ))}
          </div>
        </div>
      </section>

      <WizardCTA>
        <Button variant="primary" size="lg" iconRight="arrowRight" onClick={proceed}>마네킹컷으로</Button>
      </WizardCTA>
    </>
  );
}

export default AnalysisForm;
