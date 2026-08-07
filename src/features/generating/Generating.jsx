/* =============================================================
   features/generating — ⑥ 생성 시작(starter) → 에디터 통합 대기.
   대기 UI는 Editor 가 소유한다(editor_wait_dev_spec v2 — 완료 시 화면 전환 없이
   같은 캔버스에서 이어서 편집, 2026-08-03 오너 결정 #4). 이 라우트는 콘티의
   [생성하기]가 부르는 진입점으로 남아: 잡을 시작시키고 곧장 /editor/{pid}로 보낸다.
   잡 수명은 store.detailPageJob 소유 — 리다이렉트 후에도 폴링·리본이 산다.
   ============================================================= */
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';

export function Generating() {
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await useAppStore.getState().loadProject();
      if (cancelled) return;
      const pid = useAppStore.getState().projectId;
      if (!pid) { navigate('/create/input', { replace: true }); return; }
      const project = await api.getProject(pid).catch(() => null);
      if (cancelled) return;
      // 이미 완료된 프로젝트는 재생성 없이 에디터로(PRD §10.17, 서버도 멱등).
      // 아니면 생성을 시작만 하고 에디터로 — 대기·채움·완료는 전부 에디터 안에서.
      if (project?.status !== 'done') useAppStore.getState().startDetailPageGeneration(pid);
      navigate(`/editor/${pid}`, { replace: true });
    })();
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return <div className="route-loading">상세페이지 생성을 시작하고 있어요</div>;
}

export default Generating;
