import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Icon } from '@/components/ui.jsx';
import {
  cancelEnrollment,
  completeEnrollment,
  createEnrollment,
  createIdentity,
  createLivenessSession,
  deleteEnrollmentPhoto,
  getCurrentEnrollment,
  getEnrollment,
  listMyModels,
  uploadEnrollmentPhoto,
  uploadProfileImage,
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
  const loaderScripts = [];
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
    loaderScripts.push(element);
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
    loaderScripts.forEach((element) => element.remove());
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

// runIdentity(앞단 CI 게이트)·finishMatch(라이브니스 후 SFace 매치) 도중 던져지는 에러 중
// 서버가 실제로 응답해 거절한 경우(err.status 존재 — httpAdapter/facemarket.js 가 실제 HTTP
// 응답에만 status 를 싣는다)만 터미널로 다룬다. CX 위젯 로딩 실패·토큰 누락·네트워크 왕복
// 실패(응답 자체를 못 받음)는 서버 쪽 등록 상태가 그대로이므로, 등록을 버리지 않고 같은
// 단계를 안전하게 재시도할 수 있다.
function isTransientIdentityError(error) {
  return !(error && typeof error.status === 'number');
}

export function ModelRegister() {
  const [step, setStep] = useState('loading');
  const [enrollment, setEnrollment] = useState(null);
  const [session, setSession] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [consentAccepted, setConsentAccepted] = useState(false);
  const mounted = useRef(true);
  const issuedLivenessEnrollmentRef = useRef(null);
  // 신분증 초상(dlphotoimage HEX) — 앞단 identity 스텝에서 위젯 콜백으로 받아 라이브니스 후
  // SFace 매치 때까지 메모리에서만 보관한다. local/session storage 금지·로그 금지.
  const portraitRef = useRef(null);

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

  // 앞단 identity 스텝: CX 표준인증창(ENT_MID)으로 본인 명의 신원을 먼저 확인한다.
  // 성공 token → createIdentity(등록 스코프 CI 게이트), 신분증 초상(dlphotoimage)은 ref 로만 보관.
  const runIdentity = useCallback(async () => {
    const enrollmentId = enrollment?.id;
    if (!enrollmentId) return;
    setStep('identity');
    setError('');
    setBusy(true);
    try {
      await loadCxWidget();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      const token = await new Promise((resolve, reject) => {
        const options = {
          contentInfo: { signType: 'ENT_MID' },
          compareCI: false,
          isBirth: true,
          // useConvertor:true 없이는 위젯이 RESULT 스텝에서 신분증 사진(dlphotoimage)을
          // 만들지 않는다 — D1: 생체 등록 SFace 매치의 유일한 초상 출처.
          useConvertor: true,
        };
        window.OACX.LOAD_MODULE(CX_CONFIG_URL, options, (response) => {
          try {
            const parsed = typeof response === 'string' ? JSON.parse(response) : response;
            const authToken = parsed?.token;
            if (!authToken) throw new Error('인증 토큰을 받지 못했어요. 다시 시도해 주세요.');
            if (!mounted.current) throw new Error('등록 화면이 닫혔어요.');
            // 신분증 사진(HEX JPEG) — 위젯 콜백에서 받은 그대로 ref(메모리)에만 담는다.
            // 저장(local/session storage)·로그 금지 — 라이브니스 후 매치에만 서버로 전달한다.
            portraitRef.current = parsed?.data?.dlphotoimage;
            resolve(authToken);
          } catch (requestError) {
            reject(requestError);
          }
        });
      });
      await createIdentity(enrollmentId, { token });
      if (!mounted.current) return;
      const current = await getEnrollment(enrollmentId);
      if (!mounted.current) return;
      setEnrollment(current);
      setStep(nextEnrollmentStep(current));
    } catch (requestError) {
      if (!mounted.current) return;
      if (isTransientIdentityError(requestError)) {
        // 등록을 그대로 두고 신원 확인만 재시도한다 — 동의 재입력 없이.
        setError(requestError?.message || '일시적인 오류가 발생했어요. 다시 시도해 주세요.');
        setStep('identity_failed');
        return;
      }
      // 서버가 실제로 신원을 거절(터미널) — 등록을 정리하고 처음부터 다시 시작.
      portraitRef.current = null;
      setEnrollment(null);
      setError(requestError?.message || '신원 확인에 실패했어요. 다시 시도해 주세요.');
      setStep('failed');
      cancelEnrollment(enrollmentId).catch(() => {});
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [enrollment?.id]);

  const finishPhotos = async () => {
    try {
      const current = await getEnrollment(enrollment.id);
      setEnrollment(current);
      // profile 은 서버 상태가 없는 UI 전용 스텝 — 사진 완료 후 명시적으로 진입한다.
      // (서버 상태는 이미 liveness_pending 이라 nextEnrollmentStep 은 liveness 를 반환한다.)
      setStep('profile');
    } catch (requestError) {
      setError(requestError?.message || '사진 상태를 확인하지 못했어요.');
    }
  };

  // profile 스텝(선택): 셀러 카탈로그 대표 이미지. 비게이팅 — 업로드/건너뛰기 모두 liveness 로.
  const submitProfileImage = async (file) => {
    if (!file || !enrollment?.id) return;
    setBusy(true);
    setError('');
    try {
      await uploadProfileImage({ enrollmentId: enrollment.id, fileBlob: file, filename: file.name });
      if (!mounted.current) return;
      setStep('liveness');
    } catch (requestError) {
      if (!mounted.current) return;
      setError(requestError?.message || '대표 이미지 업로드에 실패했어요.');
    } finally {
      if (mounted.current) setBusy(false);
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
    let requestController;
    const deadline = Date.now() + 120000;
    const stopForTimeout = () => {
      if (!active) return;
      active = false;
      requestController?.abort();
      setError('처리가 지연되고 있어요. 다시 확인해 주세요.');
      setStep('poll_timeout');
    };
    const deadlineTimer = setTimeout(stopForTimeout, 120000);
    (async () => {
      let consecutiveFailures = 0;
      while (active) {
        if (Date.now() >= deadline) {
          stopForTimeout();
          return;
        }
        let current;
        const controller = new AbortController();
        requestController = controller;
        try {
          current = await getEnrollment(enrollment.id, { signal: controller.signal });
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
        } finally {
          if (requestController === controller) requestController = undefined;
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
    })();
    return () => {
      active = false;
      clearTimeout(deadlineTimer);
      requestController?.abort();
    };
  }, [enrollment?.id, step]);

  // 라이브니스 통과 후 최종 매치: 저장된 세션 + 앞단에서 담아 둔 신분증 초상(ref)으로 완료한다.
  // token 은 전달하지 않는다 — CI 게이트는 앞단 identity 에서 이미 끝났다.
  const finishMatch = useCallback(async () => {
    const sessionId = session?.sessionId;
    const enrollmentId = enrollment?.id;
    if (!sessionId || !enrollmentId) return;
    const idPhotoHex = portraitRef.current;
    if (!idPhotoHex) {
      // 새로고침 등으로 초상 ref 유실 — 조용한 실패 금지. 단회용 토큰이라 앞단 재인증이 필요하다.
      setError('신원 확인 정보가 만료됐어요. 처음 화면에서 모바일 신분증 인증을 다시 진행해 주세요.');
      setStep('identity');
      return;
    }
    setError('');
    try {
      const decision = await completeEnrollment(enrollmentId, { sessionId, idPhotoHex });
      // 매치에 쓴 초상은 즉시 폐기한다.
      portraitRef.current = null;
      issuedLivenessEnrollmentRef.current = null;
      if (mounted.current) setSession(null);
      if (!mounted.current) return;
      setEnrollment((current) => ({ ...current, ...decision }));
      setStep(nextEnrollmentStep(decision));
      if (!decision.passed) setError(enrollmentReasonMessage(decision.reason));
    } catch (requestError) {
      if (!mounted.current) return;
      if (isTransientIdentityError(requestError)) {
        // 등록·세션·초상 ref 를 그대로 두고 매치만 재시도한다 — 라이브니스 재촬영 없이.
        setError(requestError?.message || '일시적인 오류가 발생했어요. 다시 시도해 주세요.');
        setStep('identity_failed');
        return;
      }
      await abandonLiveness();
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
        <p>동의, 모바일 신분증 확인, 얼굴 사진, 라이브 촬영을 순서대로 진행해요.</p>
      </div>

      {step === 'consent' && (
        <div className="surface">
          <div className={s.purposeNotice}>
            <div className={s.purposeNoticeHead}><Icon name="info" size={15} /> 생체정보 처리 동의</div>
            <ul className={s.purposeList}>
              <li>먼저 본인 명의 모바일 신분증으로 신원을 확인해요.</li>
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
            {busy ? '등록 시작 중…' : '동의하고 신원 확인 시작'}
          </Button>
        </div>
      )}

      {step === 'identity' && (
        <div className="surface">
          <h2 className={s.stateTitle}>모바일 신분증 확인</h2>
          <p className="hint">본인 명의 모바일 신분증으로 신원을 먼저 확인해요. 확인 후 얼굴 사진을 등록해요.</p>
          <div id="oacxDiv" className={s.widget} />
          <Button variant="primary" block disabled={busy} onClick={runIdentity}>
            {busy ? '신원 확인 중…' : '모바일 신분증으로 인증'}
          </Button>
        </div>
      )}

      {step === 'identity_failed' && (
        <div className="surface">
          <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
          <Button variant="primary" block disabled={busy} onClick={session ? finishMatch : runIdentity}>다시 시도</Button>
        </div>
      )}

      {step === 'photos' && photoApi && (
        <ModelFaceUpload
          embedded
          photoApi={photoApi}
          angles={ENROLLMENT_ANGLES}
          nextLabel="다음 · 대표 이미지"
          onDone={finishPhotos}
        />
      )}

      {step === 'profile' && (
        <div className="surface">
          <h2 className={s.stateTitle}>대표 이미지 선택 (선택)</h2>
          <p className="hint">셀러 카탈로그 카드에 노출할 대표 이미지를 올려요. 원하지 않으면 건너뛸 수 있어요.</p>
          <label className={s.consentCheck}>
            <input
              type="file"
              accept="image/*"
              disabled={busy}
              onChange={(event) => submitProfileImage(event.target.files?.[0])}
            />
          </label>
          <Button variant="secondary" block disabled={busy} onClick={() => setStep('liveness')}>
            {busy ? '업로드 중…' : '건너뛰고 라이브 얼굴 확인'}
          </Button>
        </div>
      )}

      {step === 'liveness' && (
        <div className={`surface ${s.livenessWrap}`}>
          {session ? (
            <Suspense fallback="라이브 인증 화면을 불러오고 있어요…">
              <FaceLivenessStep
                session={session}
                onAnalysisComplete={finishMatch}
                onCancel={abandonLiveness}
                onError={abandonLiveness}
              />
            </Suspense>
          ) : '라이브 인증 세션을 준비하고 있어요…'}
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

      {error && !['failed', 'error', 'identity_failed'].includes(step) && (
        <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
      )}
    </div>
  );
}

export default ModelRegister;
