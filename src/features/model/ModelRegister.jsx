import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { cancelEnrollment, completeEnrollment, createEnrollment, createIdentity, createLicense, createLivenessSession, deleteEnrollmentPhoto, fetchEnrollmentPhotoUrl, getFacemarketConfig, getCurrentEnrollment, getEnrollment, listLicenses, listMyModels, reopenEnrollmentPhotos, submitPhysique, uploadEnrollmentPhoto } from '@/lib/api/facemarket.js';
import { CX_AUTH_CONFIG_URL, runIdentityWidget } from '@/lib/api/facemarketIdentityWidget.js';
import { toUploadableImage } from '../../lib/imageTranscode.js';
import { enrollmentReasonMessage } from './biometricEnrollment.js';
import IdDocumentStep from './IdDocumentStep.jsx';
import IdentityMethodStep from './IdentityMethodStep.jsx';
import { deriveSimpleAuthUnavailableReason, isMobileLike, parseIdentityMethods, SIMPLE_AUTH_DEVICE_REASON } from './identityMethodConfig.js';
import { CONSENT_VERSION, PHOTO_GROUPS, SLOTS, defaultRegisterTerms, photoProgress, photoSlotKey, readRegisterDraft, restoreRegisterScreen, saveRegisterDraft } from './registerSlots.js';
import { heading, renderConditions, renderConsent, renderPhotos } from './RegisterScreens.jsx';
import s from './ModelRegister.module.css';

const FaceLivenessStep = lazy(() => import('./FaceLivenessStep.jsx'));
const DEVICE_KEY = 'wearless.fmDeviceId';
// 파생 로직(삼항 방향 포함)은 identityMethodConfig.js 에 순수 함수로 뽑아 뒀다 — 이 파일은
// JSX 를 포함해 plain node 테스트가 직접 import 할 수 없어서, 그 로직만 값 단위로 검증하려면
// 이렇게 갈라야 한다.
const SIMPLE_AUTH_UNAVAILABLE_REASON = deriveSimpleAuthUnavailableReason(CX_AUTH_CONFIG_URL);
// 발표/롤백 모드(FM_IDENTITY_METHODS=mid)에서 이 배열 길이가 1 이면 선택 화면을 아예 거치지
// 않는다 — 한 방법뿐인데 고르라고 하면 클릭 한 번이 늘 뿐이다.
const IDENTITY_METHODS = parseIdentityMethods(import.meta.env.VITE_FM_IDENTITY_METHODS);
// 서버 facemarket_enrollment.REVIEW_DEADLINE_DAYS 와 같은 값 — 심사 대기 화면이 사용자에게
// 언제까지 기다리면 되는지 말해 준다(그 기한이 지나면 서버가 자동으로 닫고 메일을 보낸다).
const REVIEW_DEADLINE_DAYS = 5;
function getDeviceId() {
  try {
    const current = localStorage.getItem(DEVICE_KEY);
    if (current) return current;
    const value = crypto.randomUUID(); localStorage.setItem(DEVICE_KEY, value); return value;
  } catch { return crypto.randomUUID(); }
}
const pause = (ms, signal) => new Promise((resolve, reject) => {
  const abort = () => { clearTimeout(timer); reject(new Error('요청이 중단됐어요.')); };
  const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
  if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, { once: true });
});

export function ModelRegister() {
  const navigate = useNavigate();
  const { state: routeState } = useLocation();
  const handoff = routeState?.completionSummary;
  const [step, setStep] = useState(handoff?.modelId ? 'done' : 'loading');
  const [enrollment, setEnrollment] = useState(handoff || null);
  const [sub, setSub] = useState(1);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [consents, setConsents] = useState([false, false]);
  const [terms, setTerms] = useState(defaultRegisterTerms);
  const [body, setBody] = useState(null);
  const [config, setConfig] = useState(null);
  const [license, setLicense] = useState(null);
  const [session, setSession] = useState(null);
  const [previews, setPreviews] = useState({});
  const [priceAgreed, setPriceAgreed] = useState(false);
  const [withdrawalOpen, setWithdrawalOpen] = useState(false);
  const [editingPhotos, setEditingPhotos] = useState(false);
  const mounted = useRef(true);
  const operation = useRef(null);
  const previewUrls = useRef({});
  const inFlight = useRef(false);
  // "이전"으로 취소한 등록 id — 그 등록의 늦은 응답을 무시하는 데 써요(backFromIdCapture·finishIdDocument).
  const abandonedEnrollmentId = useRef(null);

  const showRecord = useCallback((record) => {
    setEnrollment(record);
    const screen = restoreRegisterScreen(record);
    const consentCurrent = [record?.consentDocumentVersion, record?.termsConsentVersion].every((version) => version === CONSENT_VERSION);
    const needsConsent = record?.id && !['passed', 'review_pending'].includes(record.status) && !consentCurrent;
    setStep(needsConsent ? '1' : screen.step); setSub(screen.sub);
    setBody(record?.bodyType || null);
    setTerms(record?.licenseTerms ? { allowedUse: record.licenseTerms.allowedUse } : readRegisterDraft(record?.id));
    setConsents([consentCurrent, consentCurrent]);
  }, []);

  const restore = useCallback(async () => {
    setError(''); setStep('loading');
    try {
      const [models, runtimeConfig] = await Promise.all([listMyModels(), getFacemarketConfig()]);
      if (!mounted.current) return;
      setConfig(runtimeConfig);
      if (models.some((model) => model.status === 'awaiting_confirm')) { navigate('/model/confirm', { replace: true }); return; }
      const licenses = await listLicenses();
      if (!mounted.current) return;
      try {
        const record = await getCurrentEnrollment();
        if (!mounted.current) return;
        showRecord(record);
        const currentLicense = licenses.find((item) => item.id === record.licenseId || (item.modelId === record.modelId && item.status === 'active'));
        if (['passed', 'review_pending'].includes(record.status) && currentLicense) {
          setLicense(currentLicense); setTerms({ allowedUse: currentLicense.allowedUse });
        }
      } catch (requestError) {
        if (requestError?.status !== 404) throw requestError;
        const currentLicense = licenses.find((item) => item.status === 'active' && models.some((model) => model.id === item.modelId));
        if (currentLicense) {
          setEnrollment({ modelId: currentLicense.modelId }); setLicense(currentLicense);
          setTerms({ allowedUse: currentLicense.allowedUse }); setStep('done');
        } else { setEnrollment(null); setConsents([false, false]); setStep('1'); }
      }
    } catch (requestError) {
      if (mounted.current) { setError(requestError.message || '등록 상태를 불러오지 못했어요.'); setStep('error'); }
    }
  }, [navigate, showRecord]);

  useEffect(() => {
    mounted.current = true;
    restore();
    return () => {
      mounted.current = false; operation.current?.abort();
      Object.values(previewUrls.current).forEach((url) => URL.revokeObjectURL(url)); previewUrls.current = {};
    };
  }, [restore]);

  useEffect(() => { if (enrollment?.id) saveRegisterDraft(enrollment.id, terms); }, [enrollment?.id, terms]);
  useEffect(() => { if (typeof document !== 'undefined') document.querySelector?.('[data-registration] h1')?.focus?.({ preventScroll: true }); }, [step, sub]);

  // 새로고침 뒤에도 본인 사진은 인증된 비공개 경로로만 읽어요.
  useEffect(() => {
    if (step !== '2' || !enrollment?.id) return;
    let active = true;
    const controller = new AbortController();
    (async () => {
      for (const photo of enrollment.photos || []) {
        const key = photoSlotKey(photo);
        if (!SLOTS.some((slot) => slot.key === key) || previewUrls.current[key]) continue;
        try {
          const url = await fetchEnrollmentPhotoUrl(enrollment.id, photo.slot || photo.angle, { signal: controller.signal });
          if (!active) { URL.revokeObjectURL(url); return; }
          if (previewUrls.current[key]) { URL.revokeObjectURL(url); continue; }
          previewUrls.current[key] = url; setPreviews({ ...previewUrls.current });
        } catch { /* 사진을 저장했다는 표시와 바꾸기 동작은 유지해요. */ }
      }
    })();
    return () => { active = false; controller.abort(); };
  }, [enrollment?.id, enrollment?.photos, step]);

  useEffect(() => {
    if (step !== 'processing' || !enrollment?.id) return;
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), 120000);
    let active = true;
    (async () => {
      let failures = 0;
      try {
        while (!controller.signal.aborted) {
          try {
            const current = await getEnrollment(enrollment.id, { signal: controller.signal });
            if (!active) return;
            failures = 0; setEnrollment(current);
            if (!['processing', 'asset_building'].includes(current.status)) {
              const screen = restoreRegisterScreen(current); setStep(screen.step); setSub(screen.sub); return;
            }
          } catch (requestError) { if (++failures > 3 || controller.signal.aborted) throw requestError; }
          await pause(2500, controller.signal);
        }
      } catch (requestError) {
        if (active) { setError(controller.signal.aborted ? '처리가 예상보다 오래 걸리고 있어요. 다시 확인해 주세요.' : requestError.message); setStep('poll_error'); }
      } finally { clearTimeout(deadline); }
    })();
    return () => { active = false; clearTimeout(deadline); controller.abort(); };
  }, [step, enrollment?.id]);

  const runIdentity = async (record = enrollment) => {
    if (!record?.id || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    const controller = new AbortController(); operation.current = controller;
    try {
      // 위젯 종류는 이 등록이 어느 경로로 시작했는지로만 갈린다(서버가 준 값) — 화면 상태가
      // 아니라 등록 자체가 진실이라, 새로고침 뒤 이어서 인증해도 같은 위젯이 열려요.
      const token = await runIdentityWidget({ identityMethod: record?.identityMethod || 'mid', signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return;
      const verified = await createIdentity(record.id, { token });
      if (!mounted.current || controller.signal.aborted) return;
      setEnrollment(verified);
      // 다음 화면은 항상 서버 상태로 고른다(최종리뷰 C1) — mid 는 photos_pending 이라
      // 오늘까지처럼 사진 화면('2')으로 가지만, simple_auth 는 신분증을 아직 안 찍었으므로
      // id_capture_pending 이고 그러면 촬영 화면으로 가야 한다. '2' 를 여기 박아 두면
      // simple_auth applicant 는 신분증 촬영 화면을 아예 못 보고 사진 단계에서
      // "현재 등록 단계에서는 사진을 고칠 수 없어요" 만 본다.
      const screen = restoreRegisterScreen(verified);
      setStep(screen.step); setSub(screen.sub);
    } catch (requestError) {
      if (mounted.current && !controller.signal.aborted) { setError(requestError.message || '본인 확인에 실패했어요.'); }
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  // identityMethod: 인증 수단 선택 화면(step 'method')이 넘겨주는 값. 'mid'(또는 미지정)면
  // 옛 요청 그대로(identityMethod 키를 아예 안 보낸다) — FM_IDENTITY_METHODS=mid 인 배포에서
  // 서버로 가는 요청이 오늘과 바이트 단위로 같아야 한다는 제약을 이렇게 지켜요.
  const startEnrollment = async (identityMethod) => {
    if (!consents.every(Boolean) || inFlight.current) return;
    // 간편인증 설정이 없으면, 그리고 이 기기가 폰처럼 보이지 않으면(기기 힌트) **시작
    // 전에** 막아요. 선택 화면(IdentityMethodStep)이 두 이유 다 버튼을 비활성화해 안내해
    // 주지만, 수단이 하나뿐이면(VITE_FM_IDENTITY_METHODS=simple_auth) 그 화면 자체가 안
    // 뜨고(useEffect 가 onPick 을 곧장 불러요) 여기로 곧장 와요 — 그러면 설정 부재는
    // 신분증을 다 찍어 올린 **뒤**에야, 기기 힌트는 승인·촬영 때문에 PC↔폰을 오가는
    // **도중**에야 만나게 돼요. 두 가드를 같은 모양(if + setError + return)으로 나란히
    // 두었다 — 조건이 늘었다고 새 패턴을 만들지 않는다.
    if (identityMethod === 'simple_auth' && SIMPLE_AUTH_UNAVAILABLE_REASON) {
      setError(SIMPLE_AUTH_UNAVAILABLE_REASON);
      return;
    }
    // isMobileLike() 는 판별이 불확실하면(window·matchMedia 부재) 허용으로 접는다(fail
    // open) — 이 가드도 같은 방향을 따른다. 선택 화면이 통과시켰을 사용자를 여기서 더
    // 엄하게 막으면(fail closed) 안 된다.
    if (identityMethod === 'simple_auth' && !isMobileLike()) {
      setError(SIMPLE_AUTH_DEVICE_REASON);
      return;
    }
    inFlight.current = true; setBusy(true); setError('');
    let created;
    try {
      // 동의를 편집한 등록은 서버의 같은 활성 등록을 반환해요.
      created = await createEnrollment({
        documentVersion: CONSENT_VERSION,
        deviceId: getDeviceId(),
        ...(identityMethod && identityMethod !== 'mid' ? { identityMethod } : {}),
      });
      if (!mounted.current) return;
      setEnrollment(created);
    } catch (requestError) {
      if (!mounted.current) return;
      if (requestError.code === 'model_confirmation_required') { navigate('/model/confirm', { replace: true }); return; }
      setError(requestError.message || '등록을 시작하지 못했어요.');
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
    if (created && mounted.current) {
      if (created.status === 'identity_pending') await runIdentity(created);
      else showRecord(created);
    }
  };
  // IdentityMethodStep 은 methods.length===1 이면 onPick 을 effect 로 자동 호출해요 — onPick 이
  // 매 렌더 새 함수 참조면 그 effect 가 deps 변경으로 다시 돌아 등록을 한 번 더 만들 수 있어요.
  // 참조는 고정하되(useCallback deps 없음) 최신 startEnrollment 를 ref 로 부릅니다 —
  // startEnrollment 는 consents 를 읽으므로 클로저를 굳히면 첫 렌더의 false 를 영원히 보게 돼요.
  const startEnrollmentRef = useRef(null);
  startEnrollmentRef.current = startEnrollment;
  const handleMethodPick = useCallback((method) => startEnrollmentRef.current?.(method), []);

  // 간편인증 경로 전용: 신분증 업로드(마스킹 확인 완료)가 끝나면 서버 상태를 다시 읽어
  // 다음 화면으로 넘어가요. 성공하면 photos_pending 으로 바뀌고(신분증은 이미 본인확인
  // 뒤에 찍은 거라 더 볼 게 없어요 — Task6 순서 뒤집기), 사진 3장 화면으로 넘어갑니다.
  const finishIdDocument = async () => {
    if (!enrollment?.id) return;
    try {
      const current = await getEnrollment(enrollment.id);
      if (!mounted.current) return;
      // "이전"으로 이미 취소한 등록의 늦은 응답(업로드 중 되돌리기 → 409 → 재조회)이면 무시해요
      // — 취소된 등록을 복원하면 방금 한 되돌리기가 '실패' 화면으로 뒤집혀요.
      if (abandonedEnrollmentId.current === enrollment.id) return;
      setEnrollment(current);
      // 이전 시도에서 남은 부모 배너를 지워요 — 안 지우면 재시도가 성공해 다음 화면으로
      // 넘어가도 지난 실패 메시지가 그대로 떠 있어요.
      setError('');
      const screen = restoreRegisterScreen(current);
      setStep(screen.step); setSub(screen.sub);
    } catch (requestError) {
      if (mounted.current) setError(requestError?.message || '등록 상태를 확인하지 못했어요.');
    }
  };

  // 심사 대기 화면(review_pending)은 폴링하지 않아요 — 사람 심사는 즉시 끝나지 않아요.
  // 대신 (a) 직접 확인할 수 있는 새로고침과 (b) 취소 탈출구를 줍니다. 취소가 없으면 심사가
  // 밀렸을 때 단일 활성 등록 슬롯이 묶인 채 아무것도 할 수 없어요(서버는 review_pending
  // 취소를 이미 허용해요 — 화면에만 길이 없었어요).
  const refreshReview = useCallback(async () => {
    if (!enrollment?.id) return;
    setBusy(true); setError('');
    try {
      const current = await getEnrollment(enrollment.id);
      if (!mounted.current) return;
      setEnrollment(current);
      const screen = restoreRegisterScreen(current);
      setStep(screen.step); setSub(screen.sub);
    } catch (requestError) {
      if (mounted.current) setError(requestError?.message || '등록 상태를 확인하지 못했어요.');
    } finally { if (mounted.current) setBusy(false); }
  }, [enrollment?.id]);

  const cancelReview = useCallback(async () => {
    if (!enrollment?.id) return;
    setBusy(true); setError('');
    try {
      await cancelEnrollment(enrollment.id);
      if (!mounted.current) return;
      setEnrollment(null); setConsents([false, false]); setStep('1'); setSub(1);
    } catch (requestError) {
      if (mounted.current) setError(requestError?.message || '등록을 취소하지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally { if (mounted.current) setBusy(false); }
  }, [enrollment?.id]);

  // 신분증 촬영 화면의 "이전" — 수단을 잘못 골랐을 때의 되돌리기. 서버는 identity_method 를
  // 등록 생성 때 박고 바꿔 주지 않아요(createEnrollment 는 활성 등록이 있으면 그걸 그대로
  // 돌려줘요). 그래서 되돌리기 = 이 등록을 취소하고 수단 선택으로. 이 화면엔 아직 서버에 남은
  // 게 없어(신분증은 제출 순간 올라가고 곧장 다음 상태로 가요) 확인 없이 바로 취소해요.
  // 동의는 이미 한 것이라 유지하고, 수단이 하나뿐이면(선택 화면이 자동으로 다시 시작해 버려요)
  // 동의 화면으로 가요.
  const backFromIdCapture = useCallback(async () => {
    if (!enrollment?.id || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      await cancelEnrollment(enrollment.id);
      if (!mounted.current) return;
      abandonedEnrollmentId.current = enrollment.id;
      setEnrollment(null); setSub(1); setStep(IDENTITY_METHODS.length > 1 ? 'method' : '1');
    } catch (requestError) {
      if (mounted.current) setError(requestError?.message || '이전 단계로 돌아가지 못했어요. 잠시 후 다시 시도해 주세요.');
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }, [enrollment?.id]);

  const editableEnrollment = async () => {
    if (enrollment?.status === 'license_pending') {
      const reopened = await reopenEnrollmentPhotos(enrollment.id);
      if (mounted.current) setEnrollment(reopened);
      return reopened;
    }
    if (!['photos_pending', 'liveness_pending'].includes(enrollment?.status)) {
      throw new Error('현재 등록 단계에서는 사진을 고칠 수 없어요.');
    }
    return enrollment;
  };

  const changePhoto = async (slot, file) => {
    if (inFlight.current || !enrollment?.id) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const blob = await toUploadableImage(file);
      const editable = await editableEnrollment();
      if (!mounted.current) return;
      const result = await uploadEnrollmentPhoto({ enrollmentId: editable.id, slot, fileBlob: blob, filename: blob.name || file.name });
      if (!mounted.current) return;
      const url = URL.createObjectURL(blob);
      if (previewUrls.current[slot]) URL.revokeObjectURL(previewUrls.current[slot]);
      previewUrls.current[slot] = url; setPreviews({ ...previewUrls.current });
      if (Array.isArray(result.photos)) setEnrollment(result);
      else setEnrollment((current) => ({ ...current, photos: [...(current.photos || []).filter((photo) => photoSlotKey(photo) !== slot), { ...result, slot }] }));
    } catch (requestError) { if (mounted.current) setError(requestError.message || '사진을 올리지 못했어요. 다시 시도해 주세요.'); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  const removePhoto = async (slot) => {
    if (inFlight.current || !enrollment?.id) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const editable = await editableEnrollment();
      if (!mounted.current) return;
      const stored = editable.photos.find((photo) => photoSlotKey(photo) === slot);
      await deleteEnrollmentPhoto(editable.id, stored?.slot || stored?.angle || slot);
      if (!mounted.current) return;
      if (previewUrls.current[slot]) URL.revokeObjectURL(previewUrls.current[slot]);
      delete previewUrls.current[slot]; setPreviews({ ...previewUrls.current });
      setEnrollment((current) => ({ ...current, status: 'photos_pending', photos: current.photos.filter((photo) => photoSlotKey(photo) !== slot) }));
    } catch (requestError) { if (mounted.current) setError(requestError.message || '사진을 지우지 못했어요.'); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  const finishMatch = async (livenessSession = session) => {
    if (!enrollment?.id || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    const controller = new AbortController(); operation.current = controller;
    const deadline = setTimeout(() => controller.abort(), 120000);
    try {
      const result = await completeEnrollment(enrollment.id, { sessionId: livenessSession?.sessionId }, { signal: controller.signal });
      if (!mounted.current) return;
      setEnrollment((current) => ({ ...current, ...result })); setSession(null);
      const screen = restoreRegisterScreen(result); setStep(screen.step); setSub(screen.sub);
      if (screen.step === 'failed') setError(enrollmentReasonMessage(result.reason));
    } catch (requestError) {
      if (mounted.current) { setError(controller.signal.aborted ? '처리가 늦어지고 있어요. 다시 확인해 주세요.' : requestError.message); setStep('poll_error'); }
    } finally { clearTimeout(deadline); inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  const finishPhotos = async () => {
    if (!photoProgress(enrollment?.photos).complete || inFlight.current) return;
    if (enrollment.status === 'license_pending') { setStep('3'); return; }
    inFlight.current = true; setError(''); setBusy(true);
    try {
      const settings = config || await getFacemarketConfig();
      if (!mounted.current) return;
      setConfig(settings);
      if (settings.livenessRequired) {
        const created = await createLivenessSession(enrollment.id, crypto.randomUUID());
        if (!mounted.current) return;
        setSession(created); setStep('liveness');
      } else { inFlight.current = false; await finishMatch(); }
    } catch (requestError) { if (mounted.current) setError(requestError.message || '등록을 마치지 못했어요.'); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  const nextPhoto = async () => {
    if (busy) return;
    if (sub <= 3) {
      if (photoProgress(enrollment?.photos, PHOTO_GROUPS[sub - 1].id).complete) {
        setSub(editingPhotos ? 4 : sub + 1); setEditingPhotos(false);
      }
    } else await finishPhotos();
  };

  const submitConditions = async () => {
    if (!priceAgreed || !terms.allowedUse.length || !enrollment?.id || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      if (body !== (enrollment.bodyType || null)) {
        const record = await submitPhysique({ enrollmentId: enrollment.id, bodyType: body });
        if (!mounted.current) return;
        setEnrollment(record);
      }
    } catch (requestError) {
      if (mounted.current) setError(requestError.message || '체형 정보를 저장하지 못했어요.');
      return;
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
    if (mounted.current) await issueCertificate();
  };

  const issueCertificate = async () => {
    if (!enrollment?.id || !terms.allowedUse.length || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setStep('4b');
    saveRegisterDraft(enrollment.id, terms);
    const controller = new AbortController(); operation.current = controller;
    const deadline = setTimeout(() => controller.abort(), 240000);
    try {
      while (!controller.signal.aborted) {
        try {
          const issued = await createLicense({ enrollmentId: enrollment.id, allowedUse: terms.allowedUse }, { signal: controller.signal });
          if (!mounted.current || controller.signal.aborted) return;
          setLicense(issued); setStep('done'); return;
        } catch (requestError) {
          if (requestError.code !== 'vc_issue_delayed' || controller.signal.aborted) throw requestError;
          await pause(8000, controller.signal);
          const current = await getEnrollment(enrollment.id, { signal: controller.signal });
          if (!mounted.current) return;
          setEnrollment(current);
          if (['passed', 'review_pending'].includes(current.status)) {
            const licenses = await listLicenses();
            const issued = licenses.find((item) => item.id === current.licenseId);
            if (issued) { if (mounted.current) { setLicense(issued); setStep('done'); } return; }
          }
        }
      }
    } catch (requestError) { if (mounted.current) { setError(controller.signal.aborted ? '발급 서버 응답이 늦어요. 잠시 뒤에 다시 시도해 주세요.' : requestError.message); setStep('4c'); } }
    finally { clearTimeout(deadline); inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  const restart = async () => {
    setBusy(true); setError('');
    try {
      const models = await listMyModels();
      if (!mounted.current) return;
      if (models.some((model) => model.status === 'awaiting_confirm')) { navigate('/model/confirm', { replace: true }); return; }
      if (!mounted.current) return;
      Object.values(previewUrls.current).forEach((url) => URL.revokeObjectURL(url)); previewUrls.current = {}; setPreviews({});
      setEnrollment(null); setLicense(null); setConsents([false, false]); setTerms(defaultRegisterTerms()); setPriceAgreed(false); setEditingPhotos(false); setWithdrawalOpen(false); setBody(null); setSub(1); setStep('1');
    } catch (requestError) { if (mounted.current) setError(requestError.message); }
    finally { if (mounted.current) setBusy(false); }
  };

  // 새로고침으로 돌아온 발급 요청도 같은 등록 ID로 이어가요.
  useEffect(() => {
    if (step === '4b' && enrollment?.id && !inFlight.current) issueCertificate();
  }, [step, enrollment?.id]);

  const current = step === 'done' ? 4 : ['processing', 'poll_error', 'liveness', 'review'].includes(step) ? 2 : Number(step[0]) || 1;
  // 이 등록이 어느 경로인가 — 화면 문구를 실제로 열리는 위젯에 맞추는 데 써요(표시층 전용,
  // 컷 파이프라인·상태머신은 이 값으로 갈리지 않아요).
  const isSimpleAuthEnrollment = enrollment?.identityMethod === 'simple_auth';
  let content, next, previous;
  if (step === '1') {
    content = renderConsent(consents, setConsents, withdrawalOpen, setWithdrawalOpen, busy);
    const identityPending = enrollment?.status === 'identity_pending' && [enrollment.consentDocumentVersion, enrollment.termsConsentVersion].every((version) => version === CONSENT_VERSION);
    // 간편인증 사용자에게 "신분증 인증하기" 라고 적어 두면, 눌러서 열리는 PASS·카카오·
    // 네이버 선택창과 문구가 정면으로 어긋나요.
    const verb = isSimpleAuthEnrollment ? '간편인증' : '신분증 인증';
    const chooseMethod = IDENTITY_METHODS.length > 1;
    next = {
      label: busy ? '인증창에서 확인해 주세요' : error ? '다시 인증하기' : identityPending ? `${verb}하기` : chooseMethod ? '동의하고 본인 확인 시작' : `동의하고 ${verb}하기`,
      action: identityPending ? () => runIdentity() : chooseMethod ? () => setStep('method') : () => startEnrollment(IDENTITY_METHODS[0]),
      disabled: !consents.every(Boolean),
      hint: consents.every(Boolean) ? '두 가지 필수 항목을 모두 확인했어요' : '두 가지 필수 항목에 동의해야 다음으로 갈 수 있어요',
    };
  } else if (step === 'method') {
    content = <>{heading('본인 확인 방법을 골라 주세요', '방법에 따라 다음 단계가 조금 달라요.')}<IdentityMethodStep methods={IDENTITY_METHODS} onPick={handleMethodPick} simpleAuthUnavailableReason={SIMPLE_AUTH_UNAVAILABLE_REASON} /></>;
    previous = { label: '이전', action: () => setStep('1') };
  } else if (step === 'id_capture') {
    content = <>{heading('신분증을 찍어 올려요', `주민등록번호 뒷자리는 직접 가린 뒤 올려 주세요. 이 사진은 담당자가 본인 확인을 마칠 때까지만 보관하고, 심사가 끝나면 바로 지워요(최대 7일).`)}
      {enrollment?.id && <IdDocumentStep
        enrollmentId={enrollment.id}
        onUploaded={finishIdDocument}
        // 409(이 단계가 아님/경로 꺼짐)면 성공 때와 같은 재조회 경로로 되돌려요 — 이 화면은
        // 성공으로만 빠져나가서, 안 그러면 사용자가 갇혀요.
        onStale={finishIdDocument}
        onError={(requestError) => setError(requestError?.message || '신분증 업로드에 실패했어요. 잠시 후 다시 시도해 주세요.')}
      />}</>;
    previous = { label: '이전', action: backFromIdCapture };
  } else if (step === 'review') {
    // 간편인증 경로에서 관리자가 신분증 사진을 육안으로 재확인하는 동안 머무는 화면.
    // 결과는 메일로 나가요(승인·거절·기한초과 3종).
    content = <>{heading('검수 중이에요', `담당자가 신분증과 얼굴 사진을 직접 확인하고 있어요. 결과는 메일로 알려 드려요 — 보통 하루 안에 끝나고, ${REVIEW_DEADLINE_DAYS}일이 지나면 자동으로 종료돼요.`)}<p className={s.description} role="status">이 화면을 닫아도 검수는 계속돼요. 마이페이지에서도 진행 상태를 볼 수 있어요.</p></>;
    next = { label: busy ? '확인 중이에요' : '지금 결과 확인하기', action: refreshReview, disabled: busy };
    previous = { label: '기다리지 않고 취소하기', action: cancelReview };
  } else if (step === '2') {
    content = renderPhotos({ sub, enrollment, previews, busy, onFile: changePhoto, onRemove: removePhoto, editGroup: (groupSub) => { setSub(groupSub); setEditingPhotos(true); } });
    const complete = sub <= 3 ? photoProgress(enrollment?.photos, PHOTO_GROUPS[sub - 1].id).complete : photoProgress(enrollment?.photos).complete;
    next = { label: sub === 4 ? '확인 완료' : '다음', action: nextPhoto, disabled: !complete, hint: complete ? '다 채웠어요' : '사진을 다 채워야 다음으로 갈 수 있어요' };
    previous = { label: '이전', action: () => { if (sub > 1) setSub(sub - 1); else setStep('1'); } };
  } else if (step === '3') {
    content = renderConditions({ terms, setTerms, body, setBody, busy, priceAgreed, setPriceAgreed });
    next = { label: '라이선스 증서 발급하기', action: submitConditions, disabled: !terms.allowedUse.length || !priceAgreed, hint: '발급하기를 누르면 초상 라이선스 계약에 서명한 것으로 기록돼요.' };
    previous = { label: '이전', action: () => { setStep('2'); setSub(4); setEditingPhotos(false); } };
  } else if (step === '4b') {
    content = <>{heading('라이선스 증서를 발급하고 있어요', '발급에 3분 정도 걸려요. 발급되면 이메일로 알려드려요. 로그인 후 마이페이지에서도 확인할 수 있어요.')}<ol className={s.issueList}><li><span>✓</span>서명할 내용을 준비했어요</li><li><span className={s.spinner} />발급 서버에 기록하고 있어요</li><li><span className={s.dot} />증서 번호 받기</li></ol><p className={s.description}>이 화면을 닫아도 발급은 계속돼요.</p></>;
    next = { label: '발급 중이에요', disabled: true };
  } else if (step === '4c') {
    content = <>{heading('증서를 발급하지 못했어요', '사진과 조건은 그대로 저장돼 있어요. 다시 누르면 이 단계부터 이어서 해요.')}<div className={s.reasonCard}><span>발급 서버가 남긴 사유</span><p>{error || '발급을 마치지 못했어요. 다시 시도해 주세요.'}</p></div><dl className={s.recordTable}><div><dt>사진</dt><dd>사진 {enrollment?.photoCount ?? photoProgress(enrollment?.photos).count}장 저장됨</dd></div><div><dt>조건</dt><dd>{terms.allowedUse.join(', ')} 허용</dd></div><div><dt>남은 일</dt><dd>증서 발급만 남았어요</dd></div></dl><p className={s.certificateNote}>지금 닫아도 괜찮아요. 나중에 등록 화면으로 돌아오면 이 단계부터 다시 시작해요.</p></>;
    next = { label: '다시 발급하기', action: issueCertificate }; previous = { label: '나중에 하기', action: () => navigate('/status') };
  } else if (step === 'done') {
    content = <section className={s.doneContent}><svg className={s.doneMark} viewBox="0 0 60 60" aria-hidden="true"><circle cx="30" cy="30" r="28.75" /><path d="m19 30 8 8 15-17" /></svg><h1 tabIndex={-1}>축하해요, 등록이 끝났어요</h1><div className={s.doneDescription}>{license ? <p>{(license.allowedUse || []).join(', ')}에 쓸 수 있고 철회하기 전까지 유효해요.</p> : <p>발급한 조건은 증서에서 확인할 수 있어요.</p>}<p>다음은 우리가 사진을 검수하고 테스트컷을 보내요. 도착하면 메일로 알려요.</p>{license?.vcId && <p className={s.doneCertificate}>증서 번호 {license.vcId}</p>}</div><Link to="/status" className={s.doneButton}>마이페이지로</Link><Link to="/model/license" className={s.textLink}>증서 보기</Link><button type="button" className={s.textLink} onClick={restart} disabled={busy}>새 생체 등록 시작</button></section>;
  } else if (step === 'liveness') {
    content = <>{heading('라이브 인증을 진행해요', '화면의 안내에 따라 얼굴을 보여 주세요.')}<Suspense fallback={<p role="status">인증 화면을 준비하고 있어요.</p>}><FaceLivenessStep session={session} onAnalysisComplete={() => finishMatch()} onError={(requestError) => { setSession(null); setError(requestError?.message || '라이브 인증이 중단됐어요.'); setStep('2'); setSub(4); }} onCancel={() => { setSession(null); setStep('2'); setSub(4); }} /></Suspense></>;
  } else if (step === 'loading' || step === 'processing') {
    content = <>{heading(step === 'loading' ? '등록 상태를 불러오고 있어요' : '등록을 마무리하고 있어요')}<p className={s.description} role="status"><span className={s.spinner} /> 잠시만 기다려 주세요.</p></>;
  } else {
    content = heading(step === 'failed' ? '등록을 이어갈 수 없어요' : '등록 상태를 확인하지 못했어요', step === 'failed' ? `${enrollmentReasonMessage(enrollment?.reason)} 다시 시작하면 사진과 조건을 새로 받아요.` : undefined);
    next = { label: step === 'failed' ? '다시 시작하기' : '다시 확인하기', action: step === 'failed' ? restart : restore };
  }

  return <div className={s.page} data-registration data-step={step}>
    <div className={s.main}>
      {step !== 'done' && <nav className={s.progress} aria-label="등록 진행 상황"><div className={s.progressMeta}><span>{current} / 4</span><span>{busy ? '저장 중이에요' : enrollment?.id ? '진행 상황이 저장돼요' : '모델 등록'}</span></div><ol className={s.steps}>{['본인확인', '사진', '조건', '증서'].map((label, index) => <li key={label} className={index < current ? s.reached : ''} aria-current={index === current - 1 ? 'step' : undefined}><i className={s.stepBar} /><span>{index < current - 1 ? '✓ ' : ''}{label}</span></li>)}</ol></nav>}
      {error && step !== '4c' && <p className={s.error} role="alert">{error}</p>}
      {content}
      {/* 위젯이 붙을 빈 호스트. React 는 이 안을 절대 안 본다 — #oacxDiv 는
          facemarketIdentityWidget 이 직접 만들어 넣는다(oacxHost.js 참고).
          React 가 #oacxDiv 를 그리면 Vue 가 그걸 교체할 때 형제 앵커가 깨져 앱이 죽는다. */}
      <div id="oacxHost" />
    </div>
    {/* 이전만 있는 화면(수단 선택·신분증 촬영)도 푸터를 그려요 — 다음 동작은 화면 안에 있어요. */}
    {(next || previous) && <footer className={s.bottomBar}>{next?.hint && <p id="register-hint" className={s.footerHint} aria-live="polite">{next.hint}</p>}<div className={s.footerActions}><div className={s.footerInner}>{previous && <button type="button" className={s.secondary} disabled={busy} onClick={previous.action}>{previous.label}</button>}{next && <button type="button" className={s.primary} disabled={busy || !!next.disabled} aria-describedby={next.hint ? 'register-hint' : undefined} onClick={next.action}>{next.label}</button>}</div></div></footer>}
  </div>;
}
