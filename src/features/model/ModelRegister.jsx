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
  getFacemarketConfig,
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

// 진행 레일 — 등록은 실제 순차 KYC 흐름이라 번호 마커가 의미를 갖는다(장식 아님).
// 인라인 렌더(중첩 컴포넌트 아님)로 두어 테스트 하네스 트리에 그대로 펼쳐지게 한다.
const FLOW_STEPS = [
  { key: 'consent', label: '동의' },
  { key: 'identity', label: '신분증' },
  { key: 'photos', label: '사진' },
  { key: 'profile', label: '대표' },
  { key: 'liveness', label: '라이브' },
  { key: 'done', label: '완료' },
];
const RAIL_INDEX = {
  consent: 0, identity: 1, identity_failed: 1, photos: 2,
  profile: 3, liveness: 4, liveness_failed: 4, reidentify: 4, processing: 5, terms: 5,
};
function renderStepRail(step) {
  const current = RAIL_INDEX[step];
  if (current == null) return null;
  return (
    <ol className={s.rail} aria-label="등록 진행 단계">
      {FLOW_STEPS.map((f, i) => {
        const state = i < current ? 'done' : i === current ? 'active' : 'todo';
        return (
          <li
            key={f.key}
            className={`${s.railStep} ${s[`rail_${state}`]}`}
            aria-current={state === 'active' ? 'step' : undefined}
          >
            <span className={s.railNode}>
              {state === 'done' ? <Icon name="check" size={13} stroke={2.6} /> : i + 1}
            </span>
            <span className={s.railLabel}>{f.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function ModelRegister() {
  const [step, setStep] = useState('loading');
  const [enrollment, setEnrollment] = useState(null);
  const [session, setSession] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [consentAccepted, setConsentAccepted] = useState(false);
  // 라이브니스 필요 여부 — 서버 /config 가 authoritative. false 면 라이브 단계를 건너뛰고
  // 사진 → 완료로 직행(매칭 앵커는 신분증 초상). 조회 전 기본 true(보수적).
  // 주의: 위 상태들 뒤에 둔다 — 테스트가 useState 를 위치(index)로 프리셋하므로 순서 보존.
  const [livenessRequired, setLivenessRequired] = useState(true);
  const mounted = useRef(true);
  const issuedLivenessEnrollmentRef = useRef(null);
  // 신분증 초상(dlphotoimage HEX) — 앞단 identity 스텝에서 위젯 콜백으로 받아 라이브니스 후
  // SFace 매치 때까지 메모리에서만 보관한다. local/session storage 금지·로그 금지.
  const portraitRef = useRef(null);
  // finishMatch 는 아래에서 정의되지만 그 위의 이펙트가 참조해야 한다(라이브니스 off 자동완료).
  // dep-array forward-reference 를 피하려고 ref 로 최신 함수를 넘긴다.
  const finishMatchRef = useRef(null);

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
      // 언마운트(라우트 이동·HMR·StrictMode 이중 마운트)로는 등록을 취소하지 않는다.
      // 취소는 사용자가 명시적으로 할 때(abandonLiveness)만 한다 — 진행상황(신분증·사진) 보존.
      // (dev HMR 이 리마운트할 때마다 라이브니스 세션 발급 등록이 조용히 취소되던 버그를 막는다.)
      issuedLivenessEnrollmentRef.current = null;
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
  // OACX(모바일 신분증) 위젯을 #oacxDiv 에 띄워 신분증 초상(dlphotoimage HEX)을 portraitRef 에
  // 담고 인증 토큰을 돌려준다. runIdentity(앞단 CI 게이트)와 reCaptureIdentity(초상 재확보) 공용.
  const runCxWidget = useCallback(async () => {
    // 이전 시도(취소 포함)의 위젯 DOM 을 비워 재시도 시 깨끗한 창이 뜨게 한다.
    const oacxHost = typeof document !== 'undefined' ? document.getElementById('oacxDiv') : null;
    if (oacxHost) oacxHost.replaceChildren();
    await loadCxWidget();
    await new Promise((resolve) => requestAnimationFrame(resolve));
    return new Promise((resolve, reject) => {
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
  }, []);

  const runIdentity = useCallback(async () => {
    const enrollmentId = enrollment?.id;
    if (!enrollmentId) return;
    setStep('identity');
    setError('');
    setBusy(true);
    try {
      const token = await runCxWidget();
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
  }, [enrollment?.id, runCxWidget]);

  // 새로고침·복귀로 초상 ref(메모리)를 잃었을 때: 사진은 서버에 저장돼 있으므로 신분증만 다시
  // 확인해 초상을 되찾고 라이브니스로 넘어간다(사진 재촬영 없음). 서버 identity 게이트를 다시
  // 부르지 않는다 — CI 는 앞단에서 이미 확정됐고, 최종 매치(completeEnrollment)의 SFace 대조가
  // 초상↔라이브 동일인 여부를 강제하므로 재확보한 초상이 남이면 거기서 걸러진다.
  const reCaptureIdentity = useCallback(async () => {
    const enrollmentId = enrollment?.id;
    if (!enrollmentId) return;
    setStep('reidentify');
    setError('');
    setBusy(true);
    try {
      await runCxWidget();
      if (!mounted.current) return;
      setStep('liveness');
    } catch (requestError) {
      if (!mounted.current) return;
      setError(requestError?.message || '본인 확인에 실패했어요. 다시 시도해 주세요.');
      setStep('reidentify');
    } finally {
      if (mounted.current) setBusy(false);
    }
  }, [enrollment?.id, runCxWidget]);

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

  // 라이브니스가 에러/취소돼도 등록(신분증·사진)은 버리지 않고 라이브 인증만 다시 시도한다.
  // AWS 위젯 에러는 콘솔에 안 남으므로 메시지를 화면에 띄워 원인을 보이게 한다(진행상황 보존).
  const onLivenessError = useCallback((err) => {
    if (!mounted.current) return;
    const detail = err?.error?.message || err?.message || err?.state?.message
      || (err && typeof err === 'object' ? JSON.stringify(err) : String(err ?? ''));
    setSession(null); // 이 라이브니스 세션은 재사용 불가 — 재시도 시 새 세션을 발급한다.
    setError(detail ? ('라이브 인증 오류: ' + String(detail).slice(0, 300)) : '라이브 인증이 중단됐어요.');
    setStep('liveness_failed');
  }, []);

  useEffect(() => {
    if (step !== 'liveness' || !enrollment?.id || session) return undefined;
    // 새로고침·복귀로 초상 ref 를 잃었으면(사진은 서버에 저장됨) 라이브니스 세션을 만들기 전에
    // 신분증만 다시 확인해 초상을 되찾는다 — 사진 재촬영 없이 이어서 진행한다.
    if (!portraitRef.current) { setStep('reidentify'); return undefined; }
    // 라이브니스 off — 세션/위젯 없이 신분증 초상 앵커로 바로 완료한다.
    if (!livenessRequired) { finishMatchRef.current?.(); return undefined; }
    let active = true;
    createLivenessSession(enrollment.id, crypto.randomUUID())
      .then((created) => {
        // 이펙트가 정리됨(리렌더·스텝 이동) — 만든 세션만 버리고 등록은 그대로 둔다.
        if (!active) return;
        issuedLivenessEnrollmentRef.current = enrollment.id;
        setSession(created);
      })
      .catch((sessionError) => {
        // 세션 생성 실패(일시적)로 등록을 취소하지 않는다 — 라이브 인증만 다시 시도.
        if (!active) return;
        setError(sessionError?.message || '라이브 인증 세션을 시작하지 못했어요. 다시 시도해 주세요.');
        setStep('liveness_failed');
      });
    return () => { active = false; };
  }, [enrollment?.id, session, step, livenessRequired]);

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

  // OACX 위젯은 #oacxDiv 에 Vue 앱을 마운트하며 그 노드를 갈아치우고, 자체 '취소'(class="popup-close",
  // click→closeApp) 는 우리 콜백을 주지 않는다. 그래서 document 캡처 리스너로 popup-close 클릭을 잡아
  // 취소로 처리하고 페이지를 새로고침한다(새로고침하면 identity_pending 이라 신분증 카드로 복귀).
  useEffect(() => {
    if (step !== 'identity' || !busy) return undefined;
    if (typeof document === 'undefined') return undefined;
    const onClick = (event) => {
      const target = event.target;
      if (target && target.closest && target.closest('.popup-close')) {
        if (typeof window !== 'undefined') window.location.reload();
      }
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, [step, busy]);

  // 라이브니스 통과 후 최종 매치: 저장된 세션 + 앞단에서 담아 둔 신분증 초상(ref)으로 완료한다.
  // token 은 전달하지 않는다 — CI 게이트는 앞단 identity 에서 이미 끝났다.
  const finishMatch = useCallback(async () => {
    const sessionId = session?.sessionId;
    const enrollmentId = enrollment?.id;
    // 라이브니스 off 면 세션이 없다 — enrollmentId·초상만 있으면 완료(신분증 초상 앵커).
    if (!enrollmentId) return;
    if (livenessRequired && !sessionId) return;
    const idPhotoHex = portraitRef.current;
    if (!idPhotoHex) {
      // 초상 ref 유실(라이브니스 도중 새로고침 등) — 조용한 실패 금지. 등록·사진은 서버에 보존돼
      // 있으니 취소하지 않고, 신분증만 다시 확인(reCaptureIdentity)해 초상을 되찾아 이어서 진행한다.
      if (mounted.current) {
        setError('본인 확인 정보가 만료됐어요. 신분증만 다시 확인하면 사진 그대로 이어서 진행돼요.');
        setStep('reidentify');
      }
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
  }, [abandonLiveness, enrollment?.id, session, livenessRequired]);
  // 위 이펙트(라이브니스 off 자동완료)가 forward-reference 없이 최신 finishMatch 를 부르게 한다.
  finishMatchRef.current = finishMatch;

  // 마운트 시 라이브니스 필요 여부 조회. 실패해도 기본 true 유지(라이브 단계를 보수적으로 노출).
  // 기존 이펙트 순서를 흩뜨리지 않도록 맨 마지막 useEffect 로 둔다.
  useEffect(() => {
    let active = true;
    getFacemarketConfig()
      .then((cfg) => { if (active) setLivenessRequired(cfg?.livenessRequired !== false); })
      .catch(() => { /* 기본 true 유지 */ });
    return () => { active = false; };
  }, []);

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

      {renderStepRail(step)}

      {step === 'consent' && (
        <div className="surface">
          <div className={s.stepHead}>
            <div className={s.medallion}><Icon name="checkSquare" size={22} /></div>
            <div>
              <div className={s.stepEyebrow}>STEP 1 / 6</div>
              <h2 className={s.stateTitle}>생체정보 처리 동의</h2>
            </div>
          </div>
          <div className={s.purposeNotice}>
            <div className={s.purposeNoticeHead}><Icon name="info" size={15} /> 이렇게 처리돼요</div>
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
          <div className={s.stepHead}>
            <div className={s.medallion}><Icon name="lock" size={22} /></div>
            <div>
              <div className={s.stepEyebrow}>STEP 2 / 6</div>
              <h2 className={s.stateTitle}>모바일 신분증 확인</h2>
            </div>
          </div>
          <p className="hint">본인 명의 모바일 신분증으로 신원을 먼저 확인해요. 확인 후 얼굴 사진을 등록해요.</p>
          <div className={s.identityAction}>
            <Button variant="primary" block onClick={runIdentity}>
              {busy ? '인증 창 다시 열기' : '모바일 신분증으로 인증'}
            </Button>
          </div>
          <p className={s.retryNote}>인증 창을 닫았거나 취소했다면 위 버튼으로 다시 열 수 있어요.</p>
        </div>
      )}

      {(step === 'identity' || step === 'reidentify') && busy && (
        <div className={s.authOverlay}>
          <div className={s.authModal}>
            <div id="oacxDiv" className={s.widget} />
          </div>
        </div>
      )}

      {step === 'reidentify' && (
        <div className="surface">
          <div className={s.stepHead}>
            <div className={s.medallion}><Icon name="lock" size={22} /></div>
            <div>
              <div className={s.stepEyebrow}>본인 확인만 다시</div>
              <h2 className={s.stateTitle}>신분증만 다시 확인해요</h2>
            </div>
          </div>
          <p className="hint">등록한 얼굴 사진은 그대로 저장돼 있어요. 보안을 위해 본인 확인만 다시 하면 라이브 얼굴 확인으로 바로 넘어가요.</p>
          {error && <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>}
          <div className={s.identityAction}>
            <Button variant="primary" block onClick={reCaptureIdentity}>
              {busy ? '인증 창 다시 열기' : '모바일 신분증으로 다시 확인'}
            </Button>
          </div>
          <p className={s.retryNote}>사진은 다시 올리지 않아도 돼요. 본인 확인 후 바로 라이브 촬영이에요.</p>
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
          <div className={s.stepHead}>
            <div className={s.medallion}><Icon name="image" size={22} /></div>
            <div>
              <div className={s.stepEyebrow}>STEP 4 / 6 · 선택</div>
              <h2 className={s.stateTitle}>대표 이미지</h2>
            </div>
          </div>
          <p className="hint">셀러 카탈로그 카드에 노출할 대표 이미지를 올려요. 원하지 않으면 건너뛸 수 있어요.</p>
          <label className={s.uploadZone}>
            <input
              type="file"
              accept="image/*"
              disabled={busy}
              className={s.uploadInput}
              onChange={(event) => submitProfileImage(event.target.files?.[0])}
            />
            <div className={s.uploadIcon}><Icon name="imagePlus" size={20} /></div>
            <div className={s.uploadText}>대표 이미지 선택</div>
            <div className={s.uploadHint}>JPG · PNG · WebP</div>
          </label>
          <Button variant="secondary" block disabled={busy} onClick={() => setStep('liveness')}>
            {busy ? '업로드 중…' : (livenessRequired ? '건너뛰고 라이브 얼굴 확인' : '건너뛰고 본인 확인 완료')}
          </Button>
        </div>
      )}

      {step === 'liveness' && (
        <div className={`surface ${s.livenessWrap}`}>
          {!livenessRequired
            ? '본인 확인을 마치고 있어요…'
            : session ? (
              <Suspense fallback="라이브 인증 화면을 불러오고 있어요…">
                <FaceLivenessStep
                  session={session}
                  onAnalysisComplete={finishMatch}
                  onCancel={onLivenessError}
                  onError={onLivenessError}
                />
              </Suspense>
            ) : '라이브 인증 세션을 준비하고 있어요…'}
        </div>
      )}

      {step === 'liveness_failed' && (
        <div className="surface">
          <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
          <Button variant="primary" block onClick={() => { setError(''); setStep('liveness'); }}>
            라이브 인증 다시 시도
          </Button>
          <p className={s.retryNote}>신분증 확인·얼굴 사진은 그대로 유지돼요. 라이브 인증만 다시 진행해요.</p>
        </div>
      )}

      {step === 'processing' && (
        <div className="surface">
          <div className={s.stepHead}>
            <div className={`${s.medallion} ${s.medallionSpin}`}><Icon name="loader" size={22} /></div>
            <div>
              <div className={s.stepEyebrow}>STEP 6 / 6</div>
              <h2 className={s.stateTitle}>모델 자산을 준비하고 있어요</h2>
            </div>
          </div>
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

      {error && !['failed', 'error', 'identity_failed', 'liveness_failed', 'reidentify'].includes(step) && (
        <p className={s.error} role="alert"><Icon name="alertCircle" size={15} /> {error}</p>
      )}
    </div>
  );
}

export default ModelRegister;
