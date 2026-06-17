/* =============================================================
   features/generating — ⑥ 생성 대기 (PRD §9)
   생성 입력은 전부 서버 상태(저장된 콘티 + project 선택값)에서 읽는다.
   크레딧 봉투 { data, credits } 의 잔액을 syncCredits 로 반영하고,
   완료 후 /editor/:projectId 로 진입한다 (frontend_state_model §5).
   ============================================================= */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { ProgressBar, Checklist } from '@/components/ui.jsx';
import { PageHead } from '@/features/shell/shell.jsx';
import { preloadEditor } from '@/features/editor/lazyEditor.js';

export function Generating() {
  const navigate = useNavigate();
  const [progress, setProgress] = useState(0);
  const [steps, setSteps] = useState([]);
  const composition = ['후킹', '셀링포인트', '스타일링컷', '호리존컷', '제품컷'];

  useEffect(() => {
    preloadEditor();
    // StrictMode 이중 실행 시 생성·차감이 두 번 나가지 않게, 소모 호출 전에 취소 확인
    let cancelled = false;
    (async () => {
      await useAppStore.getState().loadProject();
      if (cancelled) return;
      const pid = useAppStore.getState().projectId;
      // 이미 생성 완료된 프로젝트 — 재생성 없이 에디터로 (PRD §10.17, 서버도 동일 규칙으로 멱등)
      const project = await api.getProject(pid);
      if (cancelled) return;
      if (project.status === 'done') { navigate(`/editor/${pid}`, { replace: true }); return; }
      const { credits } = await api.generateDetailPage(pid, { onProgress: setProgress, onStep: setSteps });
      useAppStore.getState().syncCredits(credits);
      if (cancelled) return;
      setTimeout(() => navigate(`/editor/${pid}`), 600);
    })();
    return () => { cancelled = true; };
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
