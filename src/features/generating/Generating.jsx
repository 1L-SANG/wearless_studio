/* =============================================================
   features/generating — ⑥ 생성 대기 (PRD §9)
   생성 입력은 전부 서버 상태(저장된 콘티 + project 선택값)에서 읽는다.
   크레딧 봉투 { data, credits } 의 잔액을 syncCredits 로 반영하고,
   완료 후 /editor/:projectId 로 진입한다 (frontend_state_model §5).
   FaceMarket: 생성 전 서버 verify 게이트가 라이선스를 검사 — 해지/만료면 409 →
   블로킹 패널로 멈춘다(장면⑤). 성공 시 온체인 정산 영수증(getJobSettlement)을 띄운다(장면③).
   ============================================================= */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { getJobSettlement } from '@/lib/api/facemarket.js';
import { useAppStore } from '@/store/useAppStore.js';
import { ProgressBar, Checklist, Button, Icon, useToast } from '@/components/ui.jsx';
import { PageHead, WizardCTA } from '@/features/shell/shell.jsx';
import { preloadEditor } from '@/features/editor/lazyEditor.js';

// 정산 영수증 조회. 404 는 '정산 없음'(비 FaceMarket 잡·체인 미기록)의 확정 신호라 즉시 null 로 끝내
// 공용 경로(라이선스 없는 셀러)를 지연시키지 않는다. 워커는 잡 완료 전 정산을 기록하므로 정상 경로는
// 첫 조회에 성공한다. 일시 오류(네트워크·5xx)만 짧게 재시도. 끝내 없으면 null → 영수증 없이 진행(graceful).
async function tryGetReceipt(jobId) {
  for (let i = 0; i < 3; i++) {
    try { return await getJobSettlement(jobId); }
    catch (e) {
      if (e?.status === 404) return null;   // 정산 없음 — 재시도 불필요
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  return null;
}

const won = (n) => `₩${Number(n || 0).toLocaleString('ko-KR')}`;
const shortHash = (h) => (h ? (h.length > 18 ? `${h.slice(0, 10)}…${h.slice(-6)}` : h) : '—');

export function Generating() {
  const navigate = useNavigate();
  const toast = useToast();
  const [progress, setProgress] = useState(0);
  const [steps, setSteps] = useState([]);
  const [pid, setPid] = useState(null);
  const [blocked, setBlocked] = useState(null);   // 409 라이선스 차단 메시지(한국어) — 장면⑤
  const [receipt, setReceipt] = useState(null);   // 온체인 정산 영수증 — 장면③
  const [stillRunning, setStillRunning] = useState(false);  // 대기 상한 초과 — 서버는 계속 도는 중
  const [checking, setChecking] = useState(false);          // '완성됐는지 확인하기' 진행 중
  const composition = ['후킹', '스타일링', '스튜디오', '의류 확인'];

  useEffect(() => {
    preloadEditor();
    // StrictMode 이중 실행 시 생성·차감이 두 번 나가지 않게, 소모 호출 전에 취소 확인
    let cancelled = false;
    (async () => {
      await useAppStore.getState().loadProject();
      if (cancelled) return;
      const projectId = useAppStore.getState().projectId;
      if (!projectId) { navigate('/create/input', { replace: true }); return; }  // 콜드 진입(복원 불가) → 입력
      setPid(projectId);
      // 이미 생성 완료된 프로젝트 — 재생성 없이 에디터로 (PRD §10.17, 서버도 동일 규칙으로 멱등)
      const project = await api.getProject(projectId);
      if (cancelled) return;
      if (project.status === 'done') { navigate(`/editor/${projectId}`, { replace: true }); return; }
      let credits;
      let jobId;
      try {
        const res = await api.generateDetailPage(projectId, { onProgress: setProgress, onStep: setSteps });
        credits = res.credits;
        jobId = res.jobId;
        useAppStore.getState().syncCredits(credits);
      } catch (e) {
        if (cancelled) return;
        // 장면⑤ — 얼굴 라이선스 차단(409): 되돌리지 않고 블로킹 패널로 명확히 멈춘다(재생성 재차단 신호).
        if (e?.status === 409) { setBlocked(e.message || '이 모델의 얼굴 라이선스를 사용할 수 없어요.'); return; }
        // 대기 상한 초과는 **실패가 아니다** — 서버 잡은 계속 돌고 결과도 저장된다. 콘티로
        // 되돌리면 사용자는 "실패했다"고 읽고 다시 누르는데, 서버는 같은 활성 잡에 합류시켜
        // 또 기다리다 또 튕긴다(2026-08-05 실측 사고). 되돌리지 말고 상태를 정직하게 알린다.
        if (e?.code === 'job_timeout') { setStillRunning(true); return; }
        // 그 외 전체 실패(실서버) — done 오염 없이 콘티로 되돌린다. 실패 컷은 미차감 (계약 §6)
        toast.push(e?.message || '상세페이지 생성에 실패했어요. 다시 시도해 주세요.', { icon: 'x' });
        navigate('/create/storyboard', { replace: true });
        return;
      }
      if (cancelled) return;
      // 장면③ — 온체인 정산 영수증. 있으면 표시하고 자동 이동을 보류(셀러가 확인 후 편집으로).
      const r = jobId ? await tryGetReceipt(jobId) : null;
      if (cancelled) return;
      if (r) { setReceipt(r); return; }
      setTimeout(() => navigate(`/editor/${projectId}`), 600);
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

  // 장면⑤ — 라이선스 차단 블로킹 패널 (모든 훅 실행 이후 조건부 렌더)
  if (blocked) {
    return (
      <div className="wizard narrow">
        <PageHead title="상세페이지를 생성할 수 없어요" sub="얼굴 라이선스 확인에서 막혔어요." />
        <div className="surface fm-blocked">
          <div className="fm-blocked-icon"><Icon name="alertCircle" size={28} /></div>
          <p className="fm-blocked-msg">{blocked}</p>
          <p className="fm-blocked-hint">다른 모델을 선택하거나 라이선스 상태를 확인한 뒤 다시 시도해 주세요.</p>
          <Button variant="primary" block onClick={() => navigate('/create/storyboard', { replace: true })}>콘티로 돌아가기</Button>
        </div>
      </div>
    );
  }

  // 완성 여부를 **서버에 물어본 뒤에만** 에디터로 보낸다. 아직이면 그 자리에 머문다.
  // 미완성 프로젝트로 에디터에 들어가면 editor-blocks 가 빈 배열이고, 에디터의 자동 저장이
  // 1.5초 뒤 그 빈 배열을 PUT 해버린다(`Editor.jsx` 의 blocks==null 가드는 [] 를 막지 못하고,
  // 이탈 플러시도 [] 가 truthy 라 그대로 나간다). 그 사이 생성이 끝나면 만들어진 상세페이지가
  // 지워진다. 기존에는 status==='done' 경로로만 에디터에 들어갔기에 없던 위험이다.
  const checkDone = async () => {
    if (checking || !pid) return;
    setChecking(true);
    try {
      const project = await api.getProject(pid);
      if (project?.status === 'done') { navigate(`/editor/${pid}`); return; }
      toast.push('아직 만들고 있어요. 조금 뒤에 다시 확인해 주세요.', { icon: 'clock' });
    } catch {
      toast.push('상태를 확인하지 못했어요. 잠시 후 다시 시도해 주세요.', { icon: 'x' });
    } finally {
      setChecking(false);
    }
  };

  // 대기 상한 초과 — 실패 화면이 아니다. 서버는 계속 만들고 있고 결과도 저장된다.
  // "다시 시도" 버튼을 두지 않는 것은 의도다: 활성 잡이 있으면 서버가 같은 잡에 합류시키므로
  // 다시 눌러도 새로 만들어지지 않는다. 확인만 다시 하게 한다.
  if (stillRunning) {
    return (
      <div className="wizard narrow">
        <PageHead title="아직 만들고 있어요" sub="화면만 기다리기를 멈췄고, 생성은 계속 진행 중이에요." />
        <div className="surface fm-blocked">
          <div className="fm-blocked-icon"><Icon name="clock" size={28} /></div>
          <p className="fm-blocked-msg">
            평소보다 오래 걸리고 있어요. 완성되면 보관함에 그대로 저장돼요.
          </p>
          <p className="fm-blocked-hint">
            지금 다시 눌러도 새로 만들어지지 않아요 — 만들고 있던 그 작업을 이어서 기다립니다.
            잠시 뒤 아래 버튼으로 확인해 주세요.
          </p>
          <Button variant="primary" block disabled={checking} onClick={checkDone}>
            {checking ? '확인하는 중이에요…' : '완성됐는지 확인하기'}
          </Button>
          <Button variant="ghost" block onClick={() => navigate('/create/storyboard', { replace: true })}>
            콘티로 돌아가기
          </Button>
        </div>
      </div>
    );
  }

  // 장면③ — 온체인 정산 영수증(70/20/10 분배 · txHash · chainId · vcId)
  if (receipt) {
    const total = receipt.totalAmount || 0;
    const pct = (n) => (total ? Math.round((Number(n || 0) / total) * 100) : 0);
    return (
      <div className="wizard narrow">
        <PageHead title="상세페이지 생성이 완료됐어요" sub="얼굴 라이선스 사용료가 온체인에 정산됐어요." />
        <div className="surface fm-receipt">
          <div className="fm-receipt-head">
            <span className="fm-receipt-badge"><Icon name="check" size={13} />정산 완료</span>
            <span className="fm-receipt-total">{won(total)}</span>
          </div>
          <div className="fm-split">
            <div className="fm-split-row"><span>모델 정산</span><span className="fm-split-pct">{pct(receipt.modelAmount)}%</span><span>{won(receipt.modelAmount)}</span></div>
            <div className="fm-split-row"><span>플랫폼</span><span className="fm-split-pct">{pct(receipt.platformAmount)}%</span><span>{won(receipt.platformAmount)}</span></div>
            <div className="fm-split-row"><span>운영</span><span className="fm-split-pct">{pct(receipt.opsAmount)}%</span><span>{won(receipt.opsAmount)}</span></div>
          </div>
          <div className="fm-chain">
            <div className="fm-chain-row"><span>체인 ID</span><code>{receipt.chainId || '—'}</code></div>
            <div className="fm-chain-row"><span>Tx</span><code>{shortHash(receipt.txHash)}</code></div>
            <div className="fm-chain-row"><span>VC ID</span><code>{shortHash(receipt.vcId)}</code></div>
            <div className="fm-chain-row"><span>상태</span><span className="fm-chain-status">{receipt.chainStatus === 'confirmed' ? '온체인 확정' : (receipt.chainStatus || '기록됨')}</span></div>
          </div>
        </div>
        <WizardCTA>
          <Button variant="primary" size="lg" iconRight="arrowRight" onClick={() => navigate(`/editor/${pid}`)}>상세페이지 편집하기</Button>
        </WizardCTA>
      </div>
    );
  }

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
