/* =============================================================
   generating/Generating.jsx — 생성 대기 페이지 (PRD §9).
   전체 진행바 + 단계 체크리스트 + 상단 구성요소 pill. 완료 시
   자동으로 에디터로 진입한다.
   ============================================================= */
import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { ProgressBar, Checklist } from '@/components/Progress.jsx';
import { PageHead } from '@/features/shell/PageHead.jsx';
import styles from './Generating.module.css';

export function Generating() {
  const navigate = useNavigate();
  const storyboard = useAppStore((s) => s.storyboard);
  const composeMode = useAppStore((s) => s.composeMode);
  const catalogs = useAppStore((s) => s.catalogs);
  const [progress, setProgress] = useState(0);
  const [steps, setSteps] = useState([]);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api.generateDetailPage({ onProgress: setProgress, onStep: setSteps })
      .then(() => setTimeout(() => navigate('/editor/new'), 500));
  }, [navigate]);

  const pills = storyboard.length
    ? [...new Set(storyboard.map((b) => b.title))]
    : (catalogs?.composeModes?.find((m) => m.value === composeMode)?.flow || []);

  return (
    <div className={styles.wrap}>
      <PageHead title="상세페이지를 만들고 있어요" sub="콘티에서 확정한 컷과 카피를 바탕으로 생성 중이에요." />

      <section className={styles.card}>
        {pills.length > 0 && (
          <div className={styles.pills}>
            {pills.map((p, i) => <span key={i} className={styles.pill}>{p}</span>)}
          </div>
        )}
        <ProgressBar value={progress} label="상세페이지 생성 중" />
        <div className={styles.checklist}>
          <Checklist items={steps} />
        </div>
      </section>
    </div>
  );
}

export default Generating;
