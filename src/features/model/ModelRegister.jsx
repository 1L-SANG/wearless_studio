import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Icon } from '@/components/ui.jsx';
import {
  cancelEnrollment,
  completeEnrollment,
  createEnrollment,
  createLivenessSession,
  deleteEnrollmentPhoto,
  getCurrentEnrollment,
  getEnrollment,
  listMyModels,
  uploadEnrollmentPhoto,
} from '@/lib/api/facemarket.js';
import { ModelFaceUpload } from './ModelFaceUpload.jsx';
import {
  ENROLLMENT_ANGLES,
  enrollmentReasonMessage,
  nextEnrollmentStep,
} from './biometricEnrollment.js';
import s from './ModelRegister.module.css';

const CX_ORIGIN = 'https://cx.raonsecure.co.kr:17543';
const CX_CONFIG_URL = import.meta.env.VITE_CX_CONFIG_URL
  || `${CX_ORIGIN}/ent/esign/config/config.mid.json`;
const DEVICE_KEY = 'wearless.fmDeviceId';
const CONSENT_VERSION = '2026-08-v1';
const FaceLivenessStep = lazy(() => import('./FaceLivenessStep.jsx'));

let cxLoader;
function loadCxWidget() {
  if (window.OACX) return Promise.resolve();
  if (cxLoader) return cxLoader;
  const addScript = (id, src) => new Promise((resolve, reject) => {
    if (document.getElementById(id)) return resolve();
    const element = document.createElement('script');
    element.id = id;
    element.src = src;
    element.onload = resolve;
    element.onerror = () => {
      element.remove();
      reject(new Error('인증 모듈을 불러오지 못했어요.'));
    };
    document.head.appendChild(element);
  });
  const pending = new Promise((resolve, reject) => {
    if (!document.getElementById('oacx-ux-css')) {
      const link = document.createElement('link');
      link.id = 'oacx-ux-css';
      link.rel = 'stylesheet';
      link.href = `${CX_ORIGIN}/ent/esign/oacx-ux.css`;
      document.head.appendChild(link);
    }
    addScript('oacx-vendor', `${CX_ORIGIN}/ent/esign/oacx-vendor.js`)
      .then(() => addScript('oacx-ux', `${CX_ORIGIN}/ent/esign/oacx-ux.js`))
      .then(() => {
        let tries = 0;
        const timer = setInterval(() => {
          if (window.OACX) {
            clearInterval(timer);
            resolve();
          } else if (++tries > 50) {
            clearInterval(timer);
            reject(new Error('인증 모듈이 준비되지 않았어요.'));
          }
        }, 100);
      })
      .catch(reject);
  });
  cxLoader = pending.catch((error) => {
    cxLoader = undefined;
    throw error;
  });
  return cxLoader;
}

function getDeviceId() {
  let deviceId;
  try { deviceId = localStorage.getItem(DEVICE_KEY); } catch { /* 저장소 차단 */ }
  if (deviceId) return deviceId;
  deviceId = crypto.randomUUID();
  try { localStorage.setItem(DEVICE_KEY, deviceId); } catch { /* 메모리 값으로 계속 */ }
  return deviceId;
}

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export function ModelRegister() {
  const [step, setStep] = useState('loading');
  const [enrollment, setEnrollment] = useState(null);
  const [session, setSession] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [consentAccepted, setConsentAccepted] = useState(false);
  const mounted = useRef(true);
  const issuedLivenessEnrollmentRef = useRef(null);

  const restore = useCallback(async () => {
    setStep('loading');
    setError('');
    try {
      const current = await getCurrentEnrollment();
      if (!mounted.current) return;
      setEnrollment(current);
      setStep(nextEnrollmentStep(current));
    } catch (requestError) {
      if (!mounted.current) return;
      if (requestError?.status !== 404) {
        setError(requestError?.message || '등록 상태를 불러오지 못했어요.');
        setStep('error');
        return;
      }
      try {
        const models = await listMyModels();
        const verified = models.find((model) => model.status === 'verified');
        if (!mounted.current) return;
        if (verified) {
          setEnrollment({ modelId: verified.id, status: 'passed' });
          setStep('done');
        } else {
          setStep('consent');
        }
      } catch (modelError) {
        if (!mounted.current) return;
        setError(modelError?.message || '등록 상태를 불러오지 못했어요.');
        setStep('error');
      }
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    restore();
    return () => {
      mounted.current = false;
      const enrollmentId = issuedLivenessEnrollmentRef.current;
      issuedLivenessEnrollmentRef.current = null;
      if (enrollmentId) cancelEnrollment(enrollmentId).catch(() => {});
    };
  }, [restore]);

  const photoApi = useMemo(() => enrollment ? ({
    load: () => getEnrollment(enrollment.id),
    upload: (photo) => uploadEnrollmentPhoto({ enrollmentId: enrollment.id, ...photo }),
    remove: (angle) => deleteEnrollmentPhoto(enrollment.id, angle),
  }) : null, [enrollment]);

  const startEnrollment = async () => {
    setBusy(true);
    setError('');
    try {
      const created = await createEnrollment({
        documentVersion: CONSENT_VERSION,
        deviceId: getDeviceId(),
      });
      setEnrollment(created);
      setStep(nextEnrollmentStep(created));
    } catch (requestError) {
      setError(requestError?.message || '등록을 시작하지 못했어요.');
    } finally {
      setBusy(false);
    }
  };

  const finishPhotos = async () => {
    try {
      const current = await getEnrollment(enrollment.id);
      setEnrollment(current);
      setStep(nextEnrollmentStep(current));
    } catch (requestError) {
      setError(requestError?.message || '사진 상태를 확인하지 못했어요.');
    }
  };

  const abandonLiveness = useCallback(async () => {
    const enrollmentId = enrollment?.id;
    if (mounted.current) {
      setStep('cancelling');
      setError('');
      setSession(null);
    }
    if (enrollmentId) {
      try {
        await cancelEnrollment(enrollmentId);
      } catch {
        if (!mounted.current) return;
        issuedLivenessEnrollmentRef.current = enrollmentId;
        setError('현재 등록을 안전하게 취소하지 못했어요. 다시 시도해 주세요.');
        setStep('cancel_failed');
        return;
      }
    }
    issuedLivenessEnrollmentRef.current = null;
    if (!mounted.current) return;
    setEnrollment(null);
    setError(enrollmentReasonMessage('liveness_retry'));
    setStep('failed');
  }, [enrollment?.id]);

  useEffect(() => {
    if (step !== 'liveness' || !enrollment?.id || session) return undefined;
    let active = true;
    createLivenessSession(enrollment.id, crypto.randomUUID())
      .then((created) => {
        if (!active) {
          cancelEnrollment(enrollment.id).catch(() => {});
          return;
        }
        issuedLivenessEnrollmentRef.current = enrollment.id;
        setSession(created);
      })
      .catch(() => {
        if (active) abandonLiveness();
        else cancelEnrollment(enrollment.id).catch(() => {});
      });
    return () => { active = false; };
  }, [abandonLiveness, enrollment?.id, session, step]);

  useEffect(() => {
    if (step !== 'processing' || !enrollment?.id) return undefined;
    let active = true;
    (async () => {
      let consecutiveFailures = 0;
      for (let elapsed = 0; active && elapsed <= 120000; elapsed += 2500) {
        let current;
        try {
          current = await getEnrollment(enrollment.id);
          consecutiveFailures = 0;
        } catch (requestError) {
          if (!active) return;
          consecutiveFailures += 1;
          if (consecutiveFailures > 3) {
            setError(requestError?.message || '등록 상태를 확인하지 못했어요.');
            setStep('poll_error');
            return;
          }
          await wait(2500);
          continue;
        }
        if (!active) return;
        setEnrollment(current);
        const restoredStep = nextEnrollmentStep(current);
        if (restoredStep !== 'processing') {
          setStep(restoredStep);
          if (restoredStep === 'failed') setError(enrollmentReasonMessage(current.reason));
          return;
        }
        await wait(2500);
      }
      if (active) {
        setError('처리가 지연되고 있어요. 다시 확인해 주세요.');
        setStep('poll_timeout');
      }
    })();
    return () => { active = false; };
  }, [enrollment?.id, step]);

  const finishIdentity = useCallback(async () => {
    const sessionId = session?.sessionId;
    if (!sessionId || !enrollment?.id) return;
    setStep('identity');
    setError('');
    try {
      await loadCxWidget();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const decision = await new Promise((resolve, reject) => {
        const options = {
          contentInfo: { signType: 'ENT_MID' },
          compareCI: false,
          isBirth: true,
        };
        window.OACX.LOAD_MODULE(CX_CONFIG_URL, options, async (response) => {
          try {
            const parsed = typeof response === 'string' ? JSON.parse(response) : response;
            const token = parsed?.token;
            if (!token) throw new Error('인증 토큰을 받지 못했어요. 다시 시도해 주세요.');
            if (!mounted.current) throw new Error('등록 화면이 닫혔어요.');
            resolve(await completeEnrollment(enrollment.id, { sessionId, token }));
          } catch (requestError) {
            reject(requestError);
          }
        });
      });
      issuedLivenessEnrollmentRef.current = null;
      if (!mounted.current) return;
      setEnrollment((current) => ({ ...current, ...decision }));
      setStep(nextEnrollmentStep(decision));
      if (!decision.passed) setError(enrollmentReasonMessage(decision.reason));
    } catch {
      await abandonLiveness();
    } finally {
      if (mounted.current) setSession(null);
    }
  }, [abandonLiveness, enrollment?.id, session]);

  if (step === 'loading') return <div className="wizard narrow"><div className="surface">등록 상태를 확인하고 있어요…</div></div>;

  if (step === 'error') {
    return (
      <div className="wizard narrow"><div className="surface">
        <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
        <Button variant="secondary" block onClick={restore}>다시 불러오기</Button>
      </div></div>
    );
  }

  if (step === 'cancelling' || step === 'cancel_failed') {
    return (
      <div className="wizard narrow"><div className="surface">
        <h1 className={s.stateTitle}>현재 등록을 종료하고 있어요</h1>
        <p className={error ? s.error : 'hint'} role={error ? 'alert' : undefined}>
          {error || '사용한 라이브 인증 세션과 사진을 안전하게 정리하고 있어요.'}
        </p>
        {step === 'cancel_failed' && (
          <Button variant="secondary" block onClick={abandonLiveness}>등록 취소 다시 시도</Button>
        )}
      </div></div>
    );
  }

  if (step === 'poll_timeout' || step === 'poll_error') {
    return (
      <div className="wizard narrow"><div className="surface">
        <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
        <Button variant="secondary" block onClick={restore}>다시 확인하기</Button>
      </div></div>
    );
  }

  if (step === 'done') {
    return (
      <div className="wizard narrow"><div className={s.successWrap}>
        <div className={s.successIcon}><Icon name="check" size={30} stroke={2.4} /></div>
        <h1 className={s.successTitle}>검증 완료</h1>
        <p className={s.successLead}>현재 라이선스와 생체 확인이 모두 활성 상태예요.</p>
        <Button
          variant="secondary"
          block
          onClick={() => {
            setEnrollment(null);
            setConsentAccepted(false);
            setStep('consent');
          }}
        >
          새 생체 등록 시작
        </Button>
        <Link to="/model" className={s.nextCard}>내 모델 관리로 이동 <Icon name="chevRight" size={18} /></Link>
      </div></div>
    );
  }

  if (step === 'terms') {
    return (
      <div className="wizard narrow"><div className={s.successWrap}>
        <div className={s.successIcon}><Icon name="check" size={30} stroke={2.4} /></div>
        <h1 className={s.successTitle}>생체 확인 완료 · 라이선스 발급 대기</h1>
        <p className={s.successLead}>마지막으로 얼굴 사용 조건을 정해 주세요.</p>
        <Link to={`/model/license?step=terms&enrollment=${encodeURIComponent(enrollment.id)}`} className={s.nextCard}>
          라이선스 조건 설정 <Icon name="chevRight" size={18} />
        </Link>
      </div></div>
    );
  }

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>FaceMarket 모델 등록</h1>
        <p>동의와 얼굴 사진, 라이브 촬영, 모바일 신분증 확인을 순서대로 진행해요.</p>
      </div>

      {step === 'consent' && (
        <div className="surface">
          <div className={s.purposeNotice}>
            <div className={s.purposeNoticeHead}><Icon name="info" size={15} /> 생체정보 처리 동의</div>
            <ul className={s.purposeList}>
              <li>정면·45도·측면 사진과 라이브 얼굴을 동일인 확인에 사용해요.</li>
              <li>AWS Face Liveness는 미국 동부(us-east-1)에서 처리돼요.</li>
              <li>정부 신분증 사진과 라이브 촬영 원본은 대조 후 저장하지 않아요.</li>
              <li>이번 해커톤 버전에는 라이브 촬영을 대신할 수동 심사가 없어요.</li>
            </ul>
          </div>
          <label className={s.consentCheck}>
            <input type="checkbox" checked={consentAccepted} onChange={(event) => setConsentAccepted(event.target.checked)} />
            생체정보 수집·이용과 국외 처리 내용을 확인하고 동의합니다.
          </label>
          <Button variant="primary" block disabled={!consentAccepted || busy} onClick={startEnrollment}>
            {busy ? '등록 시작 중…' : '동의하고 사진 등록 시작'}
          </Button>
        </div>
      )}

      {step === 'photos' && photoApi && (
        <ModelFaceUpload
          embedded
          photoApi={photoApi}
          angles={ENROLLMENT_ANGLES}
          nextLabel="다음 · 라이브 얼굴 확인"
          onDone={finishPhotos}
        />
      )}

      {step === 'liveness' && (
        <div className={`surface ${s.livenessWrap}`}>
          {session ? (
            <Suspense fallback="라이브 인증 화면을 불러오고 있어요…">
              <FaceLivenessStep
                session={session}
                onAnalysisComplete={finishIdentity}
                onCancel={abandonLiveness}
                onError={abandonLiveness}
              />
            </Suspense>
          ) : '라이브 인증 세션을 준비하고 있어요…'}
        </div>
      )}

      {step === 'identity' && (
        <div className="surface">
          <h2 className={s.stateTitle}>모바일 신분증 확인</h2>
          <p className="hint">라이브 얼굴과 신분증 사진을 안전하게 대조하고 있어요.</p>
          <div id="oacxDiv" className={s.widget} />
        </div>
      )}

      {step === 'processing' && (
        <div className="surface">
          <h2 className={s.stateTitle}>모델 자산을 준비하고 있어요</h2>
          <p className="hint">현재 등록에 결속된 private 자산만 만들어요. 이 화면은 닫았다가 다시 열어도 이어집니다.</p>
        </div>
      )}

      {step === 'failed' && (
        <div className="surface">
          <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error || enrollmentReasonMessage(enrollment?.reason)}</p>
          <Button variant="primary" block onClick={() => { setError(''); setConsentAccepted(false); setStep('consent'); }}>
            새 등록으로 다시 시작
          </Button>
        </div>
      )}

      {error && !['failed', 'error'].includes(step) && (
        <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
      )}
    </div>
  );
}

export default ModelRegister;
