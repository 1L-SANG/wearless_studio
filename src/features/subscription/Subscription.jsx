/* =============================================================
   features/subscription — 정기결제(빌링) 화면 3종
   계획서 docs/plans/2026-09-09-toss-billing-subscription.md

   · /subscription/success — 결제창이 돌려준 authKey 로 구독을 연다(서버가 빌링키 발급)
   · /subscription/fail    — 카드 인증 실패 안내
   · /subscription         — 상태·다음 결제일·카드 관리·해지

   **이 화면의 존재 이유는 해지 확인창이다.** 크레딧이 이월되는 정책이라 오래 구독한
   사람일수록 해지 시 한 번에 사라지는 양이 크다(Seller 3개월이면 54,000). 그래서
   해지 버튼은 곧장 API 를 부르지 않고, 서버가 알려준 소멸 예정 수량·날짜를 먼저 보여준다.

   빌링키는 프런트에 오지 않는다 — 카드사·끝 4자리만 표시용으로 받는다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { seoulDate } from '@/lib/datetime.js';
import { Button, Icon, Skeleton, ErrorState } from '@/components/ui.jsx';
import s from './Subscription.module.css';

const TOSS_CLIENT_KEY = import.meta.env.VITE_TOSS_CLIENT_KEY;
const num = (n) => Number(n || 0).toLocaleString('ko-KR');

const STATUS_LABEL = {
  active: '이용 중',
  past_due: '결제 실패',
  canceled: '해지 예약됨',
};

/** 카드 등록 결제창을 연다. 성공하면 successUrl 로 authKey·customerKey 가 붙어 돌아온다. */
async function openBillingAuth({ userId, next }) {
  const { loadTossPayments } = await import('@tosspayments/tosspayments-sdk');
  const toss = await loadTossPayments(TOSS_CLIENT_KEY);
  const payment = toss.payment({ customerKey: userId });
  await payment.requestBillingAuth({
    method: 'CARD',
    successUrl: `${window.location.origin}${next}`,
    failUrl: `${window.location.origin}/subscription/fail`,
  });
}

/* ---------------------------------------------------------------- 시작(성공 복귀) */

export function SubscriptionSuccess() {
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const qc = useQueryClient();
  const syncCredits = useAppStore((a) => a.syncCredits);
  const [state, setState] = useState({ status: 'starting' });
  const once = useRef(false);          // StrictMode 이중 마운트로 구독이 두 번 열리지 않게
  const mounted = useRef(true);
  const currentPath = useRef(pathname);
  currentPath.current = pathname;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (once.current) return;
    once.current = true;
    const authKey = params.get('authKey');
    const customerKey = params.get('customerKey');
    const planCode = params.get('plan');
    // 카드 교체 복귀는 plan 없이 온다 — 구독을 새로 열면 안 되고 카드만 갈아끼운다.
    const mode = params.get('mode') === 'card' ? 'card' : 'start';
    if (!authKey || !customerKey || (mode === 'start' && !planCode)) {
      setState({ status: 'error', message: '카드 등록 정보가 올바르지 않아요.' });
      return;
    }
    const call = mode === 'card'
      ? api.replaceSubscriptionCard({ authKey, customerKey })
      : api.startSubscription({ authKey, customerKey, planCode });
    call
      .then((res) => {
        if (res.available != null) syncCredits(res.available);
        qc.invalidateQueries({ queryKey: ['subscription'] });
        qc.invalidateQueries({ queryKey: ['creditHistory'] });
        if (!mounted.current || currentPath.current !== '/subscription/success') return;
        setState({ status: 'done', mode, credits: res.credits, planCode: res.planCode });
      })
      .catch((e) => {
        if (mounted.current && currentPath.current === '/subscription/success') {
          setState({ status: 'error', message: e?.message || '구독을 시작하지 못했어요.' });
        }
      });
  }, [params, qc, syncCredits]);

  if (state.status === 'starting') {
    return <Shell icon="refresh" title="구독을 여는 중이에요" desc="잠시만 기다려 주세요." />;
  }
  if (state.status === 'error') {
    return (
      <Shell icon="alert" title="구독을 시작하지 못했어요" desc={state.message}>
        <Link to="/pricing"><Button variant="primary">요금제로 돌아가기</Button></Link>
      </Shell>
    );
  }
  if (state.mode === 'card') {
    return (
      <Shell icon="refresh" title="카드를 바꿨어요" desc="다음 결제부터 새 카드로 결제돼요.">
        <Link to="/subscription"><Button variant="primary">구독 관리로</Button></Link>
      </Shell>
    );
  }
  return (
    <Shell
      icon="coins"
      title="구독이 시작됐어요"
      desc={`크레딧 ${num(state.credits)}이 충전됐어요. 매달 같은 날 자동으로 충전돼요.`}
    >
      <Link to="/subscription"><Button variant="ghost">구독 관리</Button></Link>
      <Link to="/library"><Button variant="primary">작업 시작하기</Button></Link>
    </Shell>
  );
}

/* ---------------------------------------------------------------- 시작(실패 복귀) */

export function SubscriptionFail() {
  const [params] = useSearchParams();
  const code = params.get('code');
  const message = params.get('message') || '카드 등록이 완료되지 않았어요.';
  return (
    <Shell icon="alert" title="카드 등록이 취소됐어요" desc={code ? `${message} (${code})` : message}>
      <Link to="/pricing"><Button variant="primary">다시 시도하기</Button></Link>
    </Shell>
  );
}

/* ---------------------------------------------------------------- 구독 관리 */

export function SubscriptionManage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [actionError, setActionError] = useState('');

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['subscription'],
    queryFn: () => api.getMySubscription(),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['subscription'] });
  const onError = (e) => setActionError(e?.message || '요청을 처리하지 못했어요.');

  const cancel = useMutation({
    mutationFn: () => api.cancelSubscription(),
    onSuccess: () => { setConfirming(false); invalidate(); },
    onError,
  });
  const resume = useMutation({
    mutationFn: () => api.resumeSubscription(),
    onSuccess: invalidate,
    onError,
  });

  async function changeCard() {
    setActionError('');
    try {
      await openBillingAuth({
        userId: session?.user?.id,
        next: '/subscription/success?mode=card',
      });
    } catch (e) {
      const code = e?.code || '';
      if (code !== 'USER_CANCEL' && code !== 'PAY_PROCESS_CANCELED') onError(e);
    }
  }

  if (isLoading) return <div className="wizard"><Skeleton h={220} r={16} /></div>;
  if (isError) {
    return (
      <div className="wizard">
        <div className="surface"><ErrorState desc="구독 정보를 불러오지 못했어요." onRetry={refetch} /></div>
      </div>
    );
  }
  if (data.status === 'none') {
    return (
      <Shell icon="coins" title="이용 중인 구독이 없어요" desc="요금제를 고르면 매달 크레딧이 충전돼요.">
        <Link to="/pricing"><Button variant="primary">요금제 보기</Button></Link>
      </Shell>
    );
  }

  const expiring = data.expiring || { credits: 0, expiresAt: null };

  return (
    <div className="wizard">
      <div className={s.head}>
        <h1 className={s.title}>구독 관리</h1>
      </div>

      {actionError && <div className="surface" role="alert" style={{ marginBottom: 12 }}>{actionError}</div>}

      {/* 결제 실패 배너 — 유예 중에도 크레딧은 쓸 수 있다는 사실을 반드시 함께 밝힌다.
          이 문장이 없으면 사용자는 서비스가 멈춘 줄 알고 이탈한다. */}
      {(data.status === 'past_due' || data.cardNeedsUpdate) && (
        <div className={s.alert} role="alert">
          <Icon name="alert" size={16} />
          <div>
            <strong>결제가 실패했어요.</strong>{' '}
            {data.graceUntil
              ? `${seoulDate(data.graceUntil)}까지 카드를 바꾸면 구독이 이어져요.`
              : '카드를 다시 등록해 주세요.'}
            {' '}남은 크레딧은 그때까지 그대로 쓸 수 있어요.
          </div>
        </div>
      )}

      <div className="surface">
        <dl className={s.rows}>
          <div className={s.row}>
            <dt>상태</dt>
            <dd>{STATUS_LABEL[data.status] || data.status}</dd>
          </div>
          <div className={s.row}>
            <dt>요금제</dt>
            <dd>{data.planCode}</dd>
          </div>
          {data.scheduledPlanCode && (
            <div className={s.row}>
              <dt>다음 주기 요금제</dt>
              <dd>{data.scheduledPlanCode}부터 적용돼요</dd>
            </div>
          )}
          <div className={s.row}>
            <dt>{data.status === 'canceled' ? '이용 종료일' : '다음 결제일'}</dt>
            <dd>{seoulDate(data.nextBillingAt || data.currentPeriodEnd)}</dd>
          </div>
          <div className={s.row}>
            <dt>결제 카드</dt>
            <dd>{data.card?.brand ? `${data.card.brand} ····${data.card.last4}` : '등록된 카드 없음'}</dd>
          </div>
          <div className={s.row}>
            <dt>구독 크레딧</dt>
            <dd>{num(expiring.credits)}</dd>
          </div>
        </dl>

        <div className={s.actions}>
          <Button variant="ghost" onClick={changeCard} disabled={!TOSS_CLIENT_KEY}>카드 변경</Button>
          {data.status === 'canceled' ? (
            <Button variant="primary" onClick={() => resume.mutate()} disabled={resume.isPending}>
              해지 취소
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => { setActionError(''); setConfirming(true); }}>
              구독 해지
            </Button>
          )}
        </div>
      </div>

      {data.status === 'canceled' && (
        <div className={s.note}>
          {seoulDate(data.currentPeriodEnd)}까지 그대로 쓸 수 있어요. 그날
          구독 크레딧 <strong>{num(expiring.credits)}</strong>이 이월분까지 모두 소멸해요.
        </div>
      )}

      {confirming && (
        <div className={s.modal} role="dialog" aria-modal="true" aria-labelledby="cancel-title">
          <div className={s.modalCard}>
            <h2 id="cancel-title" className={s.modalTitle}>구독을 해지할까요?</h2>
            {/* 계획서 §0.1 — 소멸 예정 수량·날짜를 숫자로 보여주지 않으면 환불 분쟁이 난다. */}
            <p className={s.warn}>
              해지해도 <strong>{seoulDate(data.currentPeriodEnd)}</strong>까지는 그대로 쓸 수 있어요.
              그날 구독 크레딧 <strong>{num(expiring.credits)}</strong>이
              {expiring.expiresAt ? ` ${seoulDate(expiring.expiresAt)}에` : ''} 모두 사라져요.
              <strong> 이월된 크레딧도 함께 소멸해요.</strong>
            </p>
            <p className={s.modalSub}>추가 구매한 크레딧은 소멸하지 않아요.</p>
            <div className={s.actions}>
              <Button variant="ghost" onClick={() => setConfirming(false)}>돌아가기</Button>
              <Button variant="primary" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
                {cancel.isPending ? '해지하는 중…' : '해지할게요'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Shell({ icon, title, desc, children }) {
  return (
    <div className="wizard">
      <div className="surface" style={{ textAlign: 'center', padding: '48px 24px' }}>
        <Icon name={icon} size={28} />
        <h1 style={{ margin: '12px 0 6px', fontSize: 22 }}>{title}</h1>
        <p style={{ color: 'var(--fg-2, #4a4a45)', marginBottom: 20 }}>{desc}</p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center' }}>{children}</div>
      </div>
    </div>
  );
}

export default SubscriptionManage;
