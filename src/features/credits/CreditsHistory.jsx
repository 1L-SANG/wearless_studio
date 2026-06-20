/* =============================================================
   features/credits — 크레딧 사용 내역 (/credits/history)
   표시 전용(계약 §6): 크레딧 증감 원장을 **최신순 평면 나열**(프로젝트 그룹 X).
   증가(충전·환불)는 성공 틴트 배경 + 초록, 사용은 기본. 환불 등 쓰기 UI는 PG 단계.
   데이터: api.getCreditHistory() (http → /v1/credits/history, mock 폴백)
         + api.getLibrary() 로 projectId → 제목(있을 때만 메타에 표시).
   ============================================================= */
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, Skeleton, EmptyState, ErrorState } from '@/components/ui.jsx';
import s from './CreditsHistory.module.css';

const ACTION_LABEL = {
  mannequinGenerate: '마네킹 생성',
  'mannequinGenerate.release': '예약 해제',
  grant_subscription: '구독 크레딧 충전',
  grant_topup: '추가 구매',
  expire_subscription: '구독 크레딧 만료',
  refund_request: '환불 요청',
  refund_approved: '환불 승인',
  refund_rejected: '환불 거부',
};
const labelFor = (k) => ACTION_LABEL[k] || k;
const iconFor = (delta) => (delta > 0 ? 'coins' : 'sparkles');
const fmtDate = (iso) =>
  new Date(iso).toLocaleString('ko-KR', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });

export function CreditsHistory() {
  const account = useAppStore((a) => a.account);

  const { data: history = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['creditHistory'],
    queryFn: () => api.getCreditHistory(),
    staleTime: 0,
  });
  const { data: library = [] } = useQuery({ queryKey: ['library'], queryFn: () => api.getLibrary({}), staleTime: 0 });
  const titleMap = useMemo(() => Object.fromEntries(library.map((p) => [p.id, p.title])), [library]);

  const rows = useMemo(
    () => [...history].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt)),
    [history],
  );

  return (
    <div className="wizard wide" style={{ paddingTop: 28 }}>
      <div className={s.head}>
        <h1 className={s.title}>크레딧 사용 내역</h1>
        <p className={s.sub}>
          크레딧이 언제 충전되고 사용됐는지 최신순으로 모아봤어요.
          {account && <> 현재 남은 크레딧 <strong>{account.credits}</strong>.</>}
        </p>
      </div>

      {isLoading && (
        <div className={s.list}>{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} h={60} r={12} />)}</div>
      )}
      {isError && <div className="surface"><ErrorState desc="사용 내역을 불러오지 못했어요." onRetry={refetch} /></div>}
      {!isLoading && !isError && rows.length === 0 && (
        <div className="surface"><EmptyState icon="image" title="아직 사용 내역이 없어요" desc="마네킹·상세페이지를 만들면 크레딧 사용 내역이 여기에 쌓여요." /></div>
      )}

      {!isLoading && !isError && rows.length > 0 && (
        <div className={s.list}>
          {rows.map((e) => {
            const gain = e.delta > 0;
            const project = e.projectId ? titleMap[e.projectId] : null;
            return (
              <div key={e.id} className={`${s.row}${gain ? ' ' + s.gain : ''}`}>
                <span className={s.left}>
                  <span className={s.icon}><Icon name={iconFor(e.delta)} size={17} /></span>
                  <span style={{ minWidth: 0 }}>
                    <div className={s.label}>{labelFor(e.actionKey)}</div>
                    <div className={s.meta}>{project ? `${project} · ` : ''}{fmtDate(e.createdAt)}</div>
                  </span>
                </span>
                <span className={`${s.amount}${gain ? ' ' + s.gain : ''}`}>
                  {gain ? `+${e.delta}` : e.delta}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default CreditsHistory;
