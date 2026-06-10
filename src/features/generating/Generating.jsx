/* =============================================================
   features/generating — ⑥ 생성 대기 (PRD §9)
   Ported verbatim from reference/prototype/features/generating.jsx.
   Only change: ES imports; onDone → navigate('/editor/new').
   ============================================================= */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { ProgressBar, Checklist } from '@/components/ui.jsx';
import { PageHead } from '@/features/shell/shell.jsx';

export function Generating() {
  const navigate = useNavigate();
  const [progress, setProgress] = useState(0);
  const [steps, setSteps] = useState([]);
  const composition = ['후킹', '셀링포인트', '스타일링컷', '호리존컷', '제품컷'];

  useEffect(() => {
    api.generateDetailPage({ onProgress: setProgress, onStep: setSteps }).then(() => setTimeout(() => navigate('/editor/new'), 600));
  }, []);

  const running = steps.find((s) => s.status === 'running');
  const current = running ? running.label + '을 만들고 있어요' : progress >= 100 ? '상세페이지를 조립했어요' : '준비하는 중이에요';

  return (
    <div className="wizard">
      <PageHead title="상세페이지를 생성하고 있어요" sub="콘티에 맞춰 이미지와 카피를 함께 만들고 있습니다." />
      <div className="surface gen-center">
        <ProgressBar value={progress} label={current} />
        <div className="comp-pills">
          {composition.map((c) => <span className="flow-pill" key={c}>{c}</span>)}
        </div>
      </div>
      <div className="surface">
        <div className="sec-title" style={{ fontSize: 15, marginBottom: 6 }}>생성 진행 상황</div>
        <Checklist items={steps.map((s) => ({ key: s.key, label: s.label, status: s.status }))} />
      </div>
    </div>
  );
}

export default Generating;
