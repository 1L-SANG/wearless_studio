/* =============================================================
   features/payments — 토스 결제 결과 (/payments/success · /payments/fail)
   successUrl 로 돌아오면 쿼리(paymentKey·orderId·amount)를 서버 승인에 넘긴다.
   **금액은 서버가 주문 스냅샷과 대조**하므로 이 값이 조작돼도 크레딧은 늘지 않는다.
   승인 성공 시 계정/크레딧 쿼리를 무효화해 잔액을 즉시 갱신한다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useSearchParams, Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAppStore } from '@/store/useAppStore.js';
import { Button, Icon } from '@/components/ui.jsx';
import { clearCreditReturn, readCreditReturn } from '@/lib/creditReturn.js';
import { authorizeFlowContinuation } from '@/lib/flowSession.js';

export function PaymentSuccess() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const qc = useQueryClient();
  // 잔액 표시의 단일 소스는 스토어(§6) — 승인 응답의 available 을 그대로 반영한다.
  const syncCredits = useAppStore((a) => a.syncCredits);
  const [state, setState] = useState({ status: 'confirming' });
  const once = useRef(false);   // StrictMode 이중 마운트로 승인이 두 번 나가지 않게
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
    const paymentKey = params.get('paymentKey');
    const orderId = params.get('orderId');
    const amount = Number(params.get('amount'));
    if (!paymentKey || !orderId || !Number.isFinite(amount)) {
      setState({ status: 'error', message: '결제 정보가 올바르지 않아요.' });
      return;
    }
    api.confirmTossPayment({ paymentKey, orderId, amount })
      .then((res) => {
        syncCredits(res.available);                                  // 헤더 잔액 즉시 반영
        qc.invalidateQueries({ queryKey: ['creditHistory'] });       // 사용 내역 갱신
        const resume = readCreditReturn(useAppStore.getState().projectId);
        if (resume) {
          clearCreditReturn();
          authorizeFlowContinuation(resume.projectId, resume.path);
        }
        if (!mounted.current || currentPath.current !== '/payments/success') return;
        setState({ status: 'done', credits: res.credits, available: res.available });
        if (resume) {
          navigate(resume.path, {
            replace: true,
            state: resume.action ? { creditResume: resume } : null,
          });
        }
      })
      .catch((e) => {
        if (mounted.current && currentPath.current === '/payments/success') {
          setState({ status: 'error', message: e?.message || '결제 승인에 실패했어요.' });
        }
      });
  }, [navigate, params, qc, syncCredits]);

  if (state.status === 'confirming') {
    return <Shell icon="refresh" title="결제를 확인하고 있어요" desc="잠시만 기다려 주세요." />;
  }
  if (state.status === 'error') {
    return (
      <Shell icon="alert" title="결제 승인에 실패했어요" desc={state.message}>
        <Link to="/pricing"><Button variant="primary">요금제로 돌아가기</Button></Link>
      </Shell>
    );
  }
  return (
    <Shell
      icon="coins"
      title="충전이 완료됐어요"
      desc={`크레딧 ${Number(state.credits || 0).toLocaleString('ko-KR')}이 추가됐어요.`
        + (state.available != null ? ` 현재 잔액 ${Number(state.available).toLocaleString('ko-KR')}.` : '')}
    >
      <Link to="/credits/history"><Button variant="ghost">사용 내역 보기</Button></Link>
      <Link to="/library"><Button variant="primary">작업 계속하기</Button></Link>
    </Shell>
  );
}

export function PaymentFail() {
  const [params] = useSearchParams();
  const code = params.get('code');
  const message = params.get('message') || '결제가 완료되지 않았어요.';
  return (
    <Shell icon="alert" title="결제가 취소됐어요" desc={code ? `${message} (${code})` : message}>
      <Link to="/pricing"><Button variant="primary">다시 시도하기</Button></Link>
    </Shell>
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

export default PaymentSuccess;
