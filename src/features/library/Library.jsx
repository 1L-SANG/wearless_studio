/* =============================================================
   features/library — 보관함 (PRD §4)
   Ported verbatim from reference/prototype/features/library.jsx.
   Only change: ES imports; onNew/onOpen wired to router + store.
   ============================================================= */
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Button, Skeleton, EmptyState, ErrorState } from '@/components/ui.jsx';

export function Library() {
  const navigate = useNavigate();
  const beginProject = useAppStore((s) => s.beginProject);
  // 서버 project(보관함 행)는 AI 분석 시작 때 생성 — '새 상세페이지' 클릭만으로 빈 행 생성 방지.
  const onNew = async () => { await beginProject(); navigate('/create/input'); };
  const onOpen = (it) => navigate(`/editor/${it.id}`);

  // 서버 상태는 TanStack Query 캐시로 (frontend_state_model §8-7).
  // staleTime 0 → 보관함 진입(마운트)마다 refetch. 생성/편집/완료가 다른 화면에서
  // 일어나므로 전역 staleTime(60s) 캐시를 쓰면 생성분 빠진 stale 목록이 보인다.
  // 캐시는 즉시 렌더하고 백그라운드로 갱신 → 항상 최신 + 깜빡임 없음.
  const { data: items = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['library'],
    queryFn: () => api.getLibrary({}),
    staleTime: 0,
  });
  const phase = isLoading ? 'loading' : isError ? 'error' : 'ready';

  const statusPill = (s) => s === 'done' ? <span className="pill pill-soft st"><span className="dot dot-done" />완료</span>
    : s === 'generating' ? <span className="pill pill-soft st"><span className="dot dot-busy" />생성 중</span>
    : <span className="pill pill-soft st">초안</span>;

  // 표시 파생 — 저장값은 ISO updatedAt (계약 ProjectSummary, §7-12 해소)
  const timeAgo = (it) => {
    if (it.status === 'generating') return '진행 중';
    const m = Math.max(1, Math.round((Date.now() - new Date(it.updatedAt).getTime()) / 60000));
    if (m < 60) return `${m}분 전`;
    const h = Math.round(m / 60);
    if (h < 24) return `${h}시간 전`;
    const d = Math.round(h / 24);
    return d === 1 ? '어제' : `${d}일 전`;
  };

  return (
    <div className="wizard wide" style={{ paddingTop: 28 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 22 }}>
        <div><h1 style={{ fontFamily: 'var(--font-display)', fontSize: 28, color: 'var(--fg-1)' }}>보관함</h1>
          <p className="sec-sub" style={{ marginTop: 6 }}>지금까지 만든 상세페이지를 모아봤어요.</p></div>
      </div>

      {phase === 'loading' && (
        <div className="lib-grid">{Array.from({ length: 8 }).map((_, i) => <div key={i}><Skeleton h={240} r={12} /></div>)}</div>
      )}
      {phase === 'error' && <div className="surface"><ErrorState desc="보관함을 불러오지 못했어요." onRetry={refetch} /></div>}
      {phase === 'ready' && items.length === 0 && (
        <div className="surface"><EmptyState icon="library" title="아직 만든 상세페이지가 없어요"
          desc="상품 사진 몇 장이면 첫 상세페이지를 만들 수 있어요." action={<Button variant="primary" icon="plus" onClick={onNew}>첫 상세페이지 만들기</Button>} /></div>
      )}
      {phase === 'ready' && items.length > 0 && (
        <div className="lib-grid">
          {items.map((it) => (
            <div className="lib-card" key={it.id} onClick={() => onOpen(it)}>
              <div className="lib-cover"><img src={it.cover || undefined} alt={it.title} /><div className="st">{statusPill(it.status)}</div></div>
              <div className="lib-info"><div className="t">{it.title}</div>
                <div className="m"><span>블록 {it.blockCount}</span><span>·</span><span>{timeAgo(it)}</span></div></div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default Library;
