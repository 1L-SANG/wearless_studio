/* =============================================================
   features/pricing — 요금제 (/pricing)
   구독 / 추가구매(top-up)를 connected-tabs 로 전환해 카드 표시.
   구독은 준비 중, 추가구매는 토스 결제창으로 연결한다.
   데이터: api.getPricingPlans() (http → /v1/pricing-plans, mock 폴백).
   ============================================================= */
import { useState } from 'react';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Icon, Skeleton, EmptyState, ErrorState } from '@/components/ui.jsx';
import s from './Pricing.module.css';

const won = (n) => '₩' + Number(n).toLocaleString('ko-KR');
// 공개 클라이언트 키(테스트). 없으면 결제 버튼을 비활성 — 키 없이 결제창을 띄우면 런타임에 깨진다.
const TOSS_CLIENT_KEY = import.meta.env.VITE_TOSS_CLIENT_KEY;

// 랜딩 PricingSection 및 요금제 정본 §5(v9). 가격과 지급량은 API 값을 사용한다.
const PLAN_DETAILS = {
  starter: {
    features: ['기본모델 2명 무료 제공', '마네킹컷 1회 무료 수정 가능', '에디터 기능 제공', '무제한 다운로드 가능'],
  },
  seller: {
    baseCredits: 16000,
    bonusNote: '2,000 크레딧 추가 증정',
    features: ['Starter의 모든 기능 제공', '모든 AI 모델 50% 할인', '매칭의류 커스텀 업로드 가능', '충전할 때마다 크레딧 5% 보너스'],
  },
  pro: {
    baseCredits: 32000,
    bonusNote: '6,000 크레딧 추가 증정',
    features: ['Seller의 모든 기능 제공', '마네킹컷 2회 무료 수정 가능', '모든 AI 모델 무료 제공', '충전할 때마다 크레딧 10% 보너스'],
  },
};

export function Pricing() {
  const [tab, setTab] = useState('subscription'); // 'subscription' | 'topup'
  const [buying, setBuying] = useState(null);     // 결제창 여는 중인 planCode
  const [payError, setPayError] = useState('');
  const account = useAppStore((a) => a.account);
  const currentPlan = (account?.plan || '').toLowerCase();

  // 추가구매: 서버가 주문(금액 스냅샷)을 만들고 → 그 값 그대로 토스 결제창을 연다.
  // 성공/실패는 리다이렉트로 돌아와 /payments/success|fail 이 승인·안내를 담당한다.
  async function buyTopup(planCode) {
    setPayError('');
    setBuying(planCode);
    try {
      const order = await api.createTossCheckout(planCode);
      const { loadTossPayments } = await import('@tosspayments/tosspayments-sdk');
      const toss = await loadTossPayments(TOSS_CLIENT_KEY);
      const payment = toss.payment({ customerKey: order.customerKey });
      await payment.requestPayment({
        method: 'CARD',
        amount: { currency: 'KRW', value: order.amount },
        orderId: order.orderId,
        orderName: order.orderName,
        successUrl: `${window.location.origin}/payments/success`,
        failUrl: `${window.location.origin}/payments/fail`,
      });
    } catch (e) {
      // 사용자가 결제창을 닫은 경우도 여기로 온다 — 조용히 버튼만 되돌린다.
      const code = e?.code || '';
      if (code !== 'USER_CANCEL' && code !== 'PAY_PROCESS_CANCELED') {
        setPayError(e?.message || '결제를 시작하지 못했어요.');
      }
      setBuying(null);
    }
  }

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
        <p className={s.sub}>상세페이지 한 개에 13,000원. 사진 10장 기준이에요.</p>
      </div>

      <div className={s.tabs} role="group" aria-label="요금제 유형">
        <button type="button" aria-pressed={recurring} className={`${s.tab}${recurring ? ' ' + s.active : ''}`} onClick={() => setTab('subscription')}>구독</button>
        <button type="button" aria-pressed={!recurring} className={`${s.tab}${!recurring ? ' ' + s.active : ''}`} onClick={() => setTab('topup')}>추가 구매</button>
      </div>

      {payError && <div className={`surface ${s.payError}`} role="alert">{payError}</div>}
      <p className={s.tabDesc}>
        {recurring
          ? '매달 자동으로 크레딧이 충전되는 정기 구독이에요.'
          : '구독 크레딧이 부족할 때, 한 번만 결제해 바로 충전하는 1회 상품이에요.'}
      </p>
      <div className={s.billingNotice}>
        <p>
          {recurring
            ? '구독은 해지할 때까지 매달 자동 결제돼요. 구독 크레딧은 결제 주기가 끝나면 소멸하고 이월되지 않아요.'
            : '추가 구매 크레딧은 소멸하지 않아요.'}
          {' '}
          {/* 전자상거래법 §17②5호(디지털콘텐츠 제공 개시 시 철회 제한)는 같은 조 제6항의
              '명확한 표시'가 있어야 적용된다. 이 문장이 그 표시다 — 결제 전에 보여야 하고
              강조를 빼면 안 된다(환불정책 제4조 제2항). */}
          <strong>크레딧을 한 건이라도 사용하면 청약철회(환불)가 되지 않아요.</strong>
          {' 결제 후 7일 안에 한 건도 쓰지 않았다면 전액 환불받을 수 있어요. '}
          <a href={WEARLESS_LEGAL_URLS.refund}>환불 정책</a>
        </p>
      </div>

      {isLoading && (
        <div className={s.grid}>{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} h={190} r={16} />)}</div>
      )}
      {isError && <div className="surface"><ErrorState desc="요금제를 불러오지 못했어요." onRetry={refetch} /></div>}
      {!isLoading && !isError && shown.length === 0 && (
        <div className="surface"><EmptyState icon="coins" title="요금제를 준비 중이에요" desc="잠시 후 다시 확인해 주세요." /></div>
      )}

      {!isLoading && !isError && shown.length > 0 && (
        <div className={`${s.grid}${recurring ? '' : ' ' + s.topupGrid}`}>
          {shown.map((p) => {
            const isCurrent = recurring && p.code === currentPlan;
            const credits = Number(p.credits).toLocaleString('ko-KR');
            const details = recurring && Object.hasOwn(PLAN_DETAILS, p.code) ? PLAN_DETAILS[p.code] : null;
            return (
              <div key={p.id} className={`${s.card}${isCurrent ? ' ' + s.current : ''}${recurring ? '' : ' ' + s.topupCard}`}>
                {recurring && p.code === 'seller' && <span className={s.popular}>MOST POPULAR</span>}
                {isCurrent && <span className={s.badge}>이용 중</span>}
                <span className={`${s.kind}${recurring ? '' : ' ' + s.kindTopup}`}>
                  {!recurring && <Icon name="coins" size={13} />}
                  {recurring ? '정기 구독' : '1회 충전'}
                </span>
                <h3 className={s.name}>{p.name}</h3>
                {recurring ? (
                  <>
                    <div className={s.priceRow}>
                      <span className={s.price}>{won(p.price)}</span>
                      <span className={s.unit}>/ 월</span>
                    </div>
                    <div className={s.creditSection}>
                      <div className={s.creditLine}>
                        {details?.baseCredits && <>
                          <s className={s.baseCredits}>{details.baseCredits.toLocaleString('ko-KR')}</s>
                          <span className={s.creditArrow} aria-hidden="true">→</span>
                        </>}
                        <span className={s.creditAmount}>
                          <span className={details?.bonusNote ? s.bonusCredits : undefined}>{credits}</span>
                          {details?.bonusNote && <em className={s.bonusNote}>{details.bonusNote}</em>}
                        </span>
                        <span className={s.creditUnit}>크레딧</span>
                      </div>
                    </div>
                    {details && <ul className={s.features}>
                      {details.features.map((feature) => <li key={feature}>
                        <span className={s.featureCheck} aria-hidden="true"><Icon name="check" size={12} stroke={3} /></span>
                        <span>{feature}</span>
                      </li>)}
                    </ul>}
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
                  {recurring ? (
                    // 정기구독(빌링키)은 이번 범위 밖 — 기존 '준비 중' 유지
                    <div className={s.buttonRing}>
                      <button type="button" className={`${s.purchaseButton} ${s.subscriptionButton}`} disabled title="결제 연동 준비 중">
                        {isCurrent ? '이용 중' : '구독하기'} {!isCurrent && '(준비 중)'}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button" className={s.purchaseButton}
                      disabled={!TOSS_CLIENT_KEY || buying !== null}
                      title={TOSS_CLIENT_KEY ? undefined : '결제 키가 설정되지 않았어요'}
                      onClick={() => buyTopup(p.code)}
                    >
                      {buying === p.code ? '결제창 여는 중…' : '구매하기'}
                      {!TOSS_CLIENT_KEY && ' (준비 중)'}
                    </button>
                  )}
                  <p className={s.purchaseConsent}>
                    결제하면 <a href={WEARLESS_LEGAL_URLS.terms}>이용약관</a>과 <a href={WEARLESS_LEGAL_URLS.refund}>환불 정책</a>에 동의하는 것으로 봐요.
                  </p>
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
