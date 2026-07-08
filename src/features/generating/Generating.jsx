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
import { ProgressBar, Checklist, useToast } from '@/components/ui.jsx';
import { PageHead } from '@/features/shell/shell.jsx';
import { preloadEditor } from '@/features/editor/lazyEditor.js';

export function Generating() {
  const navigate = useNavigate();
  const toast = useToast();
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
      if (!pid) { navigate('/create/input', { replace: true }); return; }  // 콜드 진입(복원 불가) → 입력
      // 이미 생성 완료된 프로젝트 — 재생성 없이 에디터로 (PRD §10.17, 서버도 동일 규칙으로 멱등)
      const project = await api.getProject(pid);
      if (cancelled) return;
      if (project.status === 'done') { navigate(`/editor/${pid}`, { replace: true }); return; }
      try {
        const { credits } = await api.generateDetailPage(pid, { onProgress: setProgress, onStep: setSteps });
        useAppStore.getState().syncCredits(credits);
      } catch (e) {
        // 전체 실패(실서버) — done 오염 없이 콘티로 되돌린다. 실패 컷은 미차감 (계약 §6)
        if (!cancelled) { toast.push(e?.message || '상세페이지 생성에 실패했어요. 다시 시도해 주세요.', { icon: 'x' }); navigate('/create/storyboard', { replace: true }); }
        return;
      }
      if (cancelled) return;
      setTimeout(() => navigate(`/editor/${pid}`), 600);
    })();
    return () => { cancelled = true; };
  }, []);

  // 서버 progress 는 체크포인트(15→65→85→100)로 띄엄띄엄 온다. 그 사이(특히 컷 생성 15→65)를
  // 완만히 채워 바가 멈춘 것처럼 보이지 않게 한다(서버값이 바닥, 다음 체크포인트 직전까지만 크리프).
  const [shown, setShown] = useState(0);
  useEffect(() => {
    setShown((s) => Math.max(s, progress));
    const id = setInterval(() => setShown((s) => {
      const ceil = progress >= 85 ? 99 : progress >= 65 ? 82 : progress >= 15 ? 60 : 13;
      return s < ceil ? Math.min(ceil, s + 1) : s;
    }), 700);
    return () => clearInterval(id);
  }, [progress]);
  const p = Math.max(progress, shown);

  const running = steps.find((s) => s.status === 'running');
  const current = running ? running.label + '을 만들고 있어요' : p >= 100 ? '상세페이지를 조립했어요' : '준비하는 중이에요';

  return (
    <div className="wizard">
      <PageHead title="상세페이지를 생성하고 있어요" sub="콘티에 맞춰 이미지와 카피를 함께 만들고 있습니다." />
      <div className="surface gen-center">
        <ProgressBar value={p} label={current} />
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
