/* =============================================================
   features/pricing — 요금제 (/pricing)
   표시 전용(계약 §6): 구독 / 추가구매(top-up)를 connected-tabs 로 전환해 카드 표시.
   현재 이용 중인 구독 플랜을 강조. 실제 구매/결제는 PG 연동 단계 — 버튼은 "준비 중".
   데이터: api.getPricingPlans() (http → /v1/pricing-plans, mock 폴백).
   ============================================================= */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Button, Icon, Skeleton, EmptyState, ErrorState } from '@/components/ui.jsx';
import s from './Pricing.module.css';

const won = (n) => '₩' + Number(n).toLocaleString('ko-KR');

export function Pricing() {
  const [tab, setTab] = useState('subscription'); // 'subscription' | 'topup'
  const account = useAppStore((a) => a.account);
  const currentPlan = (account?.plan || '').toLowerCase();

  const { data: plans = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['pricingPlans'],
    queryFn: () => api.getPricingPlans(),
    staleTime: 5 * 60 * 1000,
  });

  const shown = plans.filter((p) => p.kind === tab);
  const recurring = tab === 'subscription';

  return (
    <div className="wizard wide">
      <div className={s.head}>
        <h1 className={s.title}>요금제</h1>
        <p className={s.sub}>매달 크레딧이 충전되는 구독을 고르고, 부족하면 추가로 구매할 수 있어요.</p>
      </div>

      <div className={s.tabs} role="tablist">
        <button className={`${s.tab}${tab === 'subscription' ? ' ' + s.active : ''}`} onClick={() => setTab('subscription')}>구독</button>
        <button className={`${s.tab}${tab === 'topup' ? ' ' + s.active : ''}`} onClick={() => setTab('topup')}>추가 구매</button>
      </div>
      <p className={s.tabDesc}>
        {recurring
          ? '매달 자동으로 크레딧이 충전되는 정기 구독이에요.'
          : '구독 크레딧이 부족할 때, 한 번만 결제해 바로 충전하는 1회 상품이에요.'}
      </p>

      {isLoading && (
        <div className={s.grid}>{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} h={190} r={16} />)}</div>
      )}
      {isError && <div className="surface"><ErrorState desc="요금제를 불러오지 못했어요." onRetry={refetch} /></div>}
      {!isLoading && !isError && shown.length === 0 && (
        <div className="surface"><EmptyState icon="coins" title="요금제를 준비 중이에요" desc="잠시 후 다시 확인해 주세요." /></div>
      )}

      {!isLoading && !isError && shown.length > 0 && (
        <div className={s.grid}>
          {shown.map((p) => {
            const isCurrent = recurring && p.code === currentPlan;
            const credits = p.credits.toLocaleString('ko-KR');
            return (
              <div key={p.id} className={`${s.card}${isCurrent ? ' ' + s.current : ''}${recurring ? '' : ' ' + s.topupCard}`}>
                {isCurrent && <span className={s.badge}>이용 중</span>}
                <span className={`${s.kind}${recurring ? '' : ' ' + s.kindTopup}`}>
                  <Icon name={recurring ? 'refresh' : 'coins'} size={13} />
                  {recurring ? '정기 구독' : '1회 충전'}
                </span>
                <h3 className={s.name}>{p.name}</h3>
                {recurring ? (
                  <>
                    <div className={s.priceRow}>
                      <span className={s.price}>{won(p.price)}</span>
                      <span className={s.unit}>/ 월</span>
                    </div>
                    <p className={s.credits}>크레딧 <strong>{credits}</strong> 매달 충전</p>
                  </>
                ) : (
                  <>
                    <div className={s.priceRow}>
                      <span className={s.creditBig}>+{credits}</span>
                      <span className={s.unit}>크레딧</span>
                    </div>
                    <p className={s.credits}>{won(p.price)} · 1회 결제</p>
                  </>
                )}
                <div className={s.cta}>
                  <Button variant={isCurrent ? 'ghost' : 'primary'} block disabled title="결제 연동 준비 중">
                    {isCurrent ? '이용 중' : recurring ? '구독하기' : '구매하기'} {!isCurrent && '(준비 중)'}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default Pricing;
