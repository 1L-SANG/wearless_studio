/* =============================================================
   mannequin/Mannequin.jsx — 마네킹컷 생성·선택·조정 (PRD §7).
   생성 대기(진행바) → A/B 후보 + 히스토리 선택 → 세부 조정(세션 2회)
   → 전부 재생성(확인 모달) → 구성 방식 미니 카드 → 콘티보드.
   ============================================================= */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { CREDIT_COSTS, LIMITS } from '@/lib/limits.js';
import { Button } from '@/components/Button.jsx';
import { Icon } from '@/components/Icon.jsx';
import { Modal } from '@/components/Modal.jsx';
import { ProgressBar } from '@/components/Progress.jsx';
import { PageHead } from '@/features/shell/PageHead.jsx';
import { WizardCTA } from '@/features/shell/WizardCTA.jsx';
import { useToast } from '@/components/Toast.jsx';
import styles from './Mannequin.module.css';

const LENGTHS = ['더 짧게', '현재', '더 길게'];
const FITS = ['더 슬림하게', '현재', '더 여유있게'];

function genStepLabel(p) {
  if (p < 30) return '의류 정보 분석 중';
  if (p < 60) return '핏·주름 정리하는 중';
  if (p < 95) return '마네킹컷 생성 중';
  return '결과를 확인하는 중';
}

export function Mannequin() {
  const navigate = useNavigate();
  const toast = useToast();
  const mannequins = useAppStore((s) => s.mannequins);
  const selectedId = useAppStore((s) => s.selectedMannequinId);
  const setMannequins = useAppStore((s) => s.setMannequins);
  const selectMannequin = useAppStore((s) => s.selectMannequin);
  const adjustCount = useAppStore((s) => s.adjustCount);
  const incAdjust = useAppStore((s) => s.incAdjust);
  const composeMode = useAppStore((s) => s.composeMode);
  const setComposeMode = useAppStore((s) => s.setComposeMode);
  const spendCredits = useAppStore((s) => s.spendCredits);
  const catalogs = useAppStore((s) => s.catalogs);
  const analysis = useAppStore((s) => s.analysis);
  const product = useAppStore((s) => s.product);

  const [phase, setPhase] = useState(mannequins.length ? 'ready' : 'loading');
  const [progress, setProgress] = useState(0);
  const [lengthSel, setLengthSel] = useState('현재');
  const [fitSel, setFitSel] = useState('현재');
  const [busy, setBusy] = useState(false);
  const [matchOpen, setMatchOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  const [regenOpen, setRegenOpen] = useState(false);

  useEffect(() => {
    if (mannequins.length) { setPhase('ready'); return; }
    let alive = true;
    setPhase('loading');
    api.generateMannequins({ onProgress: (p) => alive && setProgress(p) })
      .then((list) => { if (alive) { setMannequins(list); setPhase('ready'); } });
    return () => { alive = false; };
  }, [mannequins.length, setMannequins]);

  const composeModes = catalogs?.composeModes || [];
  const colorCount = product?.colors?.length ?? 1;
  const mainMatch = (analysis?.matchClothing || []).find((m) => m.selected && m.selOrder === 1);
  const adjustsLeft = LIMITS.mannequinAdjustMax - adjustCount;
  const hasChange = lengthSel !== '현재' || fitSel !== '현재';
  const canAdjust = hasChange && adjustsLeft > 0 && !busy;
  const currentMode = composeModes.find((m) => m.value === composeMode);

  const byCand = (c) => mannequins.filter((m) => m.candidate === c).sort((a, b) => a.version - b.version);
  const activeOf = (c) => mannequins.find((m) => m.candidate === c && m.id === selectedId) || byCand(c).slice(-1)[0];

  const doAdjust = async () => {
    if (!canAdjust) return;
    setBusy(true); setProgress(0);
    try {
      const created = await api.adjustMannequin({
        baseId: selectedId,
        fit: fitSel !== '현재' ? fitSel : undefined,
        length: lengthSel !== '현재' ? lengthSel : undefined,
        onProgress: setProgress,
      });
      setMannequins([...mannequins, created]);
      selectMannequin(created.id);
      incAdjust();
      spendCredits(CREDIT_COSTS.mannequinAdjust);
      setLengthSel('현재'); setFitSel('현재');
      toast?.push('조정한 마네킹컷이 추가됐어요.', { icon: 'check' });
    } finally { setBusy(false); }
  };

  const doRegen = async () => {
    if (adjustsLeft <= 0) { setRegenOpen(false); return; } // 재생성도 조정 횟수를 소모 (PRD §7.5)
    setRegenOpen(false); setBusy(true); setProgress(0);
    try {
      const list = await api.regenerateMannequins({ onProgress: setProgress });
      setMannequins(list);
      incAdjust();
      spendCredits(CREDIT_COSTS.mannequinGenerate);
      toast?.push('마네킹컷을 다시 생성했어요.', { icon: 'refresh' });
    } finally { setBusy(false); }
  };

  if (phase === 'loading') {
    return (
      <div className={styles.wrap}>
        <PageHead title="마네킹컷을 만들고 있어요" sub="의류의 핏과 실루엣 기준을 잡는 중이에요." />
        <div className={styles.loadingCard}>
          <ProgressBar value={progress} label={genStepLabel(progress)} sub="생성 마지막 단계에서 잠시 멈춘 것처럼 보일 수 있어요." />
          <div className={styles.loadingCands}>
            <div className={styles.loadingCand}><Icon name="person" size={28} /></div>
            <div className={styles.loadingCand}><Icon name="person" size={28} /></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.wrap}>
      <PageHead title="원하는 느낌으로 구현된 이미지를 선택해주세요" sub="핏과 총기장을 조정해 의류 재현도를 높여보세요." />

      <div className={styles.layout}>
        {/* ---- left: A/B candidates ---- */}
        <section className={styles.card}>
          <h2 className={styles.cardTitle}>마네킹컷 선택</h2>
          <p className={styles.cardSub}>선택한 컷이 실제 착용컷 생성의 기준이 돼요.</p>
          <div className={styles.cands}>
            {['A', 'B'].map((c) => {
              const active = activeOf(c);
              if (!active) return null;
              const isSel = selectedId === active.id;
              return (
                <div key={c} className={styles.cand}>
                  <button type="button" className={`${styles.candImg} ${isSel ? styles.candSel : ''}`} onClick={() => selectMannequin(active.id)}>
                    <img src={active.src} alt={`마네킹 ${c}`} />
                    {isSel && <span className={styles.selBadge}><Icon name="check" size={13} />선택됨</span>}
                  </button>
                  <div className={styles.candLabel}>마네킹 {c} · {active.fitLabel} / {active.lengthLabel}</div>
                  <div className={styles.history}>
                    {byCand(c).map((v) => (
                      <button key={v.id} type="button" className={`${styles.histThumb} ${selectedId === v.id ? styles.histOn : ''}`} title={`v${v.version}`} onClick={() => selectMannequin(v.id)}>
                        <img src={v.src} alt={`버전 ${v.version}`} />
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* ---- right: adjust + compose mode ---- */}
        <aside className={styles.side}>
          <section className={styles.card}>
            <div className={styles.sideHead}>
              <h2 className={styles.cardTitle}>세부 조정</h2>
              <span className={styles.adjBadge}>조정 가능 횟수 {adjustsLeft}/{LIMITS.mannequinAdjustMax}</span>
            </div>

            <div className={styles.adjGroup}>
              <div className={styles.adjMain}><Icon name="shirt" size={15} />메인 의류</div>
              <span className={styles.adjLabel}>총기장</span>
              <div className={styles.segRow}>
                {LENGTHS.map((l) => (
                  <button key={l} type="button" className={`${styles.seg} ${lengthSel === l ? styles.segOn : ''}`} onClick={() => setLengthSel(l)}>{l}</button>
                ))}
              </div>
              <span className={styles.adjLabel}>핏</span>
              <div className={styles.segRow}>
                {FITS.map((f) => (
                  <button key={f} type="button" className={`${styles.seg} ${fitSel === f ? styles.segOn : ''}`} onClick={() => setFitSel(f)}>{f}</button>
                ))}
              </div>
            </div>

            {mainMatch && (
              <div className={styles.matchAdjust}>
                <button type="button" className={styles.matchToggle} onClick={() => setMatchOpen((v) => !v)}>
                  <span><Icon name="layers" size={14} />매칭 의류 · {mainMatch.name}</span>
                  <Icon name={matchOpen ? 'chevUp' : 'chevDown'} size={16} />
                </button>
                {matchOpen && (
                  <div className={styles.matchBody}>
                    <span className={styles.adjLabel}>총기장 / 핏은 메인 조정과 함께 적용돼요.</span>
                  </div>
                )}
              </div>
            )}

            <Button variant="primary" block disabled={!canAdjust} onClick={doAdjust}>
              {busy ? `조정 중… ${progress}%` : `의류 조정하기 · ${CREDIT_COSTS.mannequinAdjust} 크레딧`}
            </Button>
            <Button variant="ghost" block icon="refresh" disabled={busy || adjustsLeft <= 0} onClick={() => setRegenOpen(true)}>
              마네킹컷 전부 재생성 · {CREDIT_COSTS.mannequinGenerate} 크레딧
            </Button>
            {adjustsLeft <= 0 && <p className={styles.adjNote}>이번 세션의 조정 횟수를 모두 사용했어요.</p>}
          </section>

          {/* compose mode mini card */}
          <section className={styles.card}>
            <h2 className={styles.cardTitle}>상세페이지 구성</h2>
            <div className={styles.modeCard}>
              <div className={styles.modeTop}>
                <Icon name="layout" size={16} />
                <span className={styles.modeName}>{currentMode?.label}</span>
                {currentMode?.recommended && <span className={styles.modeRec}>추천</span>}
              </div>
              <p className={styles.modeDesc}>{currentMode?.desc}</p>
              <div className={styles.modeFlow}>
                {(currentMode?.flow || []).map((f, i) => <span key={i} className={styles.flowChip}>{f}</span>)}
              </div>
              <Button variant="ghost" size="sm" block onClick={() => setModeOpen(true)}>구성 방식 변경</Button>
            </div>
          </section>
        </aside>
      </div>

      <WizardCTA>
        <Button variant="quiet" icon="arrowLeft" onClick={() => navigate('/create/input')}>이전</Button>
        <Button variant="primary" size="lg" iconRight="arrowRight" disabled={busy} onClick={() => navigate('/create/storyboard')}>콘티보드로</Button>
      </WizardCTA>

      {/* ---- compose mode chooser ---- */}
      {modeOpen && (
        <Modal onClose={() => setModeOpen(false)}>
          <h3 className={styles.modalTitle}>구성 방식 선택</h3>
          <div className={styles.modeList}>
            {composeModes.map((m) => {
              const disabled = m.value === 'extended' && colorCount < 2;
              return (
                <button
                  key={m.value}
                  type="button"
                  disabled={disabled}
                  className={`${styles.modeOption} ${composeMode === m.value ? styles.modeOptionOn : ''}`}
                  onClick={() => { setComposeMode(m.value); setModeOpen(false); }}
                >
                  <div className={styles.modeOptTop}>
                    <span className={styles.modeName}>{m.label}</span>
                    {m.recommended && <span className={styles.modeRec}>추천</span>}
                    <span className={styles.modeCount}>{m.count}컷</span>
                  </div>
                  <p className={styles.modeDesc}>{m.desc}</p>
                  {disabled && <p className={styles.modeDisabled}>색상이 2개 이상일 때 선택할 수 있어요.</p>}
                </button>
              );
            })}
          </div>
        </Modal>
      )}

      {/* ---- regenerate confirm ---- */}
      {regenOpen && (
        <Modal onClose={() => setRegenOpen(false)}>
          <h3 className={styles.modalTitle}>마네킹컷을 전부 다시 생성할까요?</h3>
          <p className={styles.modalBody}>지금까지 선택하고 조정한 결과가 새 컷으로 바뀔 수 있어요. 재생성은 조정 횟수와 크레딧 {CREDIT_COSTS.mannequinGenerate}을 사용해요.</p>
          <div className={styles.modalActions}>
            <Button variant="quiet" onClick={() => setRegenOpen(false)}>취소</Button>
            <Button variant="primary" onClick={doRegen}>재생성</Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

export default Mannequin;
