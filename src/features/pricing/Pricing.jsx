/* =============================================================
   features/pricing — 요금제 (/pricing)
   구독 / 추가구매(top-up)를 connected-tabs 로 전환해 카드 표시.
   구독은 빌링키(정기결제), 추가구매는 1회 결제 — 둘 다 토스 결제창으로 연결한다.
   구독 흐름은 features/subscription 이 이어받는다(계획서 2026-09-09-toss-billing-subscription).
   데이터: api.getPricingPlans() (http → /v1/pricing-plans, mock 폴백).

   ▶ 이 라우트는 **공개다**(App.jsx 에서 RequireAuth 밖). 랜딩(wearless.kr)의 요금제
     '선택' 이 여기로 사람을 보내므로 로그인 없이도 가격이 보여야 한다. 그래서 이 파일은
     세션 없는 렌더를 정상 경로로 취급한다:
       · 카탈로그는 공개 엔드포인트라 그냥 뜬다.
       · account 는 비어 있다 → currentPlan 이 '' 이라 '이용 중' 강조만 빠진다.
       · **결제 버튼만 로그인을 요구한다** — 누르면 결제창 대신 로그인 모달을 열고,
         로그인 뒤 이 화면으로 돌려보낸다(openLogin('/pricing')).
     결제 자체를 공개로 푼 게 아니다. 공개된 건 '얼마인가' 뿐이다.
   ============================================================= */
import { useState } from 'react';
import { WEARLESS_LEGAL_URLS } from '@/lib/legalLinks.js';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Icon, Skeleton, EmptyState, ErrorState } from '@/components/ui.jsx';
import { TOSS_BILLING_CLIENT_KEY, TOSS_CLIENT_KEY } from '@/lib/tossKeys.js';
import s from './Pricing.module.css';

const won = (n) => '₩' + Number(n).toLocaleString('ko-KR');
// 공개 클라이언트 키(테스트). 없으면 결제 버튼을 비활성 — 키 없이 결제창을 띄우면 런타임에 깨진다.
// 충전과 구독은 계약 MID 가 달라 클라이언트 키도 다르다 — lib/tossKeys.js 참고.
// 하나로 쓰면 둘 중 하나가 INVALID_API_KEY / NOT_SUPPORTED_METHOD 로 깨진다.

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
  const { session, openLogin } = useAuth();
  // 비로그인 방문자(랜딩에서 넘어온 사람)는 가격까지만 본다. 복귀 목표를 이 화면으로 심어
  // 로그인 뒤 요금제로 돌아오게 한다 — 기본값(/create/input)으로 두면 결제하러 로그인한
  // 사람이 입력 화면에 떨어져 요금제를 다시 찾아 들어와야 한다.
  const requireLogin = () => openLogin('/pricing');

  // 추가구매: 서버가 주문(금액 스냅샷)을 만들고 → 그 값 그대로 토스 결제창을 연다.
  // 성공/실패는 리다이렉트로 돌아와 /payments/success|fail 이 승인·안내를 담당한다.
  async function buyTopup(planCode) {
    // 세션 없이 여기 오면 createTossCheckout 이 401 로 떨어져 "결제를 시작하지 못했어요" 만
    // 남는다 — 원인(로그인 안 함)도 다음 행동도 안 보인다. 결제창 대신 로그인 모달을 연다.
    if (!session) { requireLogin(); return; }
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

  // 구독: 토스 결제창에서 카드를 등록(빌링키 인증)하고 successUrl 로 돌아온다.
  // 실제 빌링키 발급·첫 결제는 서버가 authKey 로 처리한다 — **클라이언트는 금액을 모른다**.
  async function subscribe(planCode) {
    if (!session) { requireLogin(); return; }
    setPayError('');
    setBuying(planCode);
    try {
      const { loadTossPayments } = await import('@tosspayments/tosspayments-sdk');
      // 자동결제 MID 의 클라이언트 키여야 한다 — 일반결제 키로 부르면 NOT_SUPPORTED_METHOD.
      const toss = await loadTossPayments(TOSS_BILLING_CLIENT_KEY);
      const payment = toss.payment({ customerKey: session.user.id });
      await payment.requestBillingAuth({
        method: 'CARD',
        successUrl: `${window.location.origin}/subscription/success?plan=${encodeURIComponent(planCode)}`,
        failUrl: `${window.location.origin}/subscription/fail`,
      });
    } catch (e) {
      // 사용자가 창을 닫은 경우도 여기로 온다 — 조용히 버튼만 되돌린다.
      const code = e?.code || '';
      if (code !== 'USER_CANCEL' && code !== 'PAY_PROCESS_CANCELED') {
        setPayError(e?.message || '카드 등록을 시작하지 못했어요.');
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
          {/* 이월 정책(2026-09-09). 이 문장은 계약 고지다 — 코드 동작과 반드시 같아야 한다.
              구독 유지 중에는 이월, 해지하면 주기 종료일에 이월분까지 전부 소멸. */}
          {recurring
            ? '구독은 해지할 때까지 매달 자동 결제돼요. 구독 크레딧은 구독을 유지하는 동안 다음 달로 이월되지만, 해지하면 결제 주기가 끝나는 날 이월분까지 모두 소멸해요.'
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
                    // 구독과 로그인 버튼을 같은 무지개 링 안에 표시한다.
                    <div className={s.buttonRing}>
                      {!session ? (
                        <button type="button" className={`${s.purchaseButton} ${s.subscriptionButton}`} onClick={requireLogin}>
                          로그인하고 시작하기
                        </button>
                      ) : (
                        <button
                          type="button" className={`${s.purchaseButton} ${s.subscriptionButton}`}
                          disabled={isCurrent || !TOSS_BILLING_CLIENT_KEY || buying !== null}
                          title={TOSS_BILLING_CLIENT_KEY ? undefined : '결제 키가 설정되지 않았어요'}
                          onClick={() => subscribe(p.code)}
                        >
                          {isCurrent ? '이용 중'
                            : (buying === p.code ? '카드 등록 창 여는 중…' : '구독하기')}
                        </button>
                      )}
                    </div>
                  ) : (
                    <div className={!session ? s.buttonRing : undefined}>
                      <button
                        type="button" className={s.purchaseButton}
                        disabled={session ? (!TOSS_CLIENT_KEY || buying !== null) : false}
                        title={!session || TOSS_CLIENT_KEY ? undefined : '결제 키가 설정되지 않았어요'}
                        onClick={() => buyTopup(p.code)}
                      >
                        {!session ? '로그인하고 구매하기' : (buying === p.code ? '결제창 여는 중…' : '구매하기')}
                        {session && !TOSS_CLIENT_KEY && ' (준비 중)'}
                      </button>
                    </div>
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
