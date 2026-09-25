import { sponsorshipDraft, sponsorshipPayload } from './sponsorshipOptions.js';
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { cancelEnrollment, completeEnrollment, createEnrollment, createIdentity, createLicense, createLivenessSession, deleteEnrollmentPhoto, fetchEnrollmentPhotoUrl, getFacemarketConfig, getCurrentEnrollment, getEnrollment, listLicenses, listMyModels, reopenEnrollmentPhotos, updateModelSponsorship, uploadEnrollmentPhoto } from '@/lib/api/facemarket.js';
import { CX_AUTH_CONFIG_URL, runIdentityWidget } from '@/lib/api/facemarketIdentityWidget.js';
import { toPreviewImage } from '../../lib/imageTranscode.js';
import { enrollmentReasonMessage } from './biometricEnrollment.js';
import IdDocumentStep from './IdDocumentStep.jsx';
import IdentityMethodStep from './IdentityMethodStep.jsx';
import { deriveSimpleAuthUnavailableReason, isMobileLike, parseIdentityMethods, SIMPLE_AUTH_DEVICE_REASON } from './identityMethodConfig.js';
import { CONSENT_VERSION, PHOTO_GROUPS, PHOTO_REVIEW_SUB, SLOTS, defaultRegisterTerms, photoProgress, photoSlotKey, readRegisterDraft, restoreRegisterScreen, saveRegisterDraft } from './registerSlots.js';
import { heading, renderConditions, renderConsent, renderPhotos, renderReshoot } from './RegisterScreens.jsx';
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
  useState(null); // 기존 테스트의 상태 순서를 유지해요.
  const [config, setConfig] = useState(null);
  const [, setLicense] = useState(null);
  const [session, setSession] = useState(null);
  const [previews, setPreviews] = useState({});
  const [priceAgreed, setPriceAgreed] = useState(false);
  const [withdrawalOpen, setWithdrawalOpen] = useState(false);
  const [editingPhotos, setEditingPhotos] = useState(false);
  const [identityReturn, setIdentityReturn] = useState(0);
  const [sponsorship, setSponsorship] = useState(sponsorshipDraft);
  const [sponsorshipReady, setSponsorshipReady] = useState(null);
  const [sponsorshipRetry, setSponsorshipRetry] = useState(0);
  const [sponsorshipError, setSponsorshipError] = useState('');
  const [photoSlot, setPhotoSlot] = useState(null);
  const [assetPollRetry, setAssetPollRetry] = useState(0);
  const [assetError, setAssetError] = useState('');
  const mounted = useRef(true);
  const operation = useRef(null);
  const previewUrls = useRef({});
  const inFlight = useRef(false);
  const errorRef = useRef(null);
  // "이전"으로 취소한 등록 id — 그 등록의 늦은 응답을 무시하는 데 써요(backFromIdCapture·finishIdDocument).
  const abandonedEnrollmentId = useRef(null);
  const savedSponsorshipEnabled = useRef(false);

  const showRecord = useCallback((record) => {
    setEnrollment(record);
    const screen = restoreRegisterScreen(record);
    const consentCurrent = [record?.consentDocumentVersion, record?.termsConsentVersion].every((version) => version === CONSENT_VERSION);
    const needsConsent = record?.id && !['passed', 'vc_pending'].includes(record.status) && !consentCurrent;
    setStep(needsConsent ? '1' : screen.step); setSub(screen.sub);
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
        if (['passed', 'vc_pending'].includes(record.status) && currentLicense) {
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
  useEffect(() => {
    let position = null;
    if (step !== 'loading' && enrollment?.id) {
      try {
        const key = `fm.registration.photoPos.${enrollment.id}`;
        const saved = JSON.parse(sessionStorage.getItem(key));
        sessionStorage.removeItem(key);
        if (step === '2' && saved?.sub === sub && Date.now() - saved.at >= 0 && Date.now() - saved.at < 300000
          && Number.isFinite(saved.scrollY) && saved.scrollY >= 0 && SLOTS.some(slot => slot.key === saved.slot && slot.group === PHOTO_GROUPS[sub - 1]?.id)) position = saved;
      } catch { /* 저장소가 막혀도 현재 화면을 계속 써요. */ }
    }
    globalThis.window?.scrollTo?.({ top: position?.scrollY || 0, left: 0, behavior: 'instant' });
    if (position) { setPhotoSlot(position.slot); setError('사진을 받지 못했어요. 다시 골라 주세요.'); }
    else if (step !== 'id_capture' && typeof document !== 'undefined') document.querySelector?.('[data-registration] h1')?.focus?.({ preventScroll: true });
  }, [step, sub, identityReturn]);

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

  const assetsPending = ['processing', 'asset_building'].includes(enrollment?.status);
  useEffect(() => {
    if (!enrollment?.id || !(step === 'processing' || (['2', '3'].includes(step) && assetsPending))) {
      setAssetError(''); return;
    }
    setAssetError('');
    const preparingAssets = step !== 'processing';
    const controller = new AbortController();
    const deadline = setTimeout(() => controller.abort(), preparingAssets ? 300000 : 120000);
    let active = true;
    (async () => {
      let failures = 0;
      try {
        while (!controller.signal.aborted) {
          try {
            const current = await getEnrollment(enrollment.id, { signal: controller.signal });
            if (!active) return;
            failures = 0; setEnrollment(current);
            if (!['processing', 'asset_building', 'review_pending'].includes(current.status)) {
              if (preparingAssets && current.status === 'license_pending') return;
              const screen = restoreRegisterScreen(current); setStep(screen.step); setSub(screen.sub); return;
            }
            if (step === 'processing' && current.status !== 'review_pending') { setStep('3'); return; }
          } catch (requestError) { if (++failures > 3 || controller.signal.aborted) throw requestError; }
          await pause(2500, controller.signal);
        }
      } catch (requestError) {
        if (active) {
          if (preparingAssets) setAssetError(controller.signal.aborted ? '사진 정리가 늦어지고 있어요. 잠시 뒤 다시 확인해 주세요.' : requestError.message || '사진 준비 상태를 불러오지 못했어요. 다시 시도해 주세요.');
          else {
            setError(controller.signal.aborted ? '처리가 예상보다 오래 걸리고 있어요. 다시 확인해 주세요.' : requestError.message);
            setStep('poll_error');
          }
        }
      } finally { clearTimeout(deadline); }
    })();
    return () => { active = false; clearTimeout(deadline); controller.abort(); };
  }, [step, enrollment?.id, assetsPending, assetPollRetry]);

  // 사진 작업 오류는 해당 카드에서 보여요. 다른 오류만 배너 위치로 안내해요.
  useEffect(() => {
    if (!error || !errorRef.current || (photoSlot && ['2', 'reshoot'].includes(step))) return;
    errorRef.current.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    errorRef.current.focus?.({ preventScroll: true });
  }, [error]);

  useEffect(() => {
    const modelId = enrollment?.modelId;
    setSponsorshipReady(null); setSponsorshipError('');
    if (!modelId) { setSponsorship(sponsorshipDraft()); return undefined; }
    let alive = true;
    listMyModels().then(models => {
      if (!alive) return;
      const model = models.find(item => item.id === modelId);
      if (!model) throw new Error('모델 정보를 불러오지 못했어요.');
      savedSponsorshipEnabled.current = model.sponsorshipEnabled === true;
      setSponsorship(sponsorshipDraft(model)); setSponsorshipReady(modelId);
    }).catch(error => { if (alive) setSponsorshipError(error.message || '협찬 설정을 불러오지 못했어요.'); });
    return () => { alive = false; };
  }, [enrollment?.modelId, sponsorshipRetry]);

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
    } finally {
      inFlight.current = false;
      if (mounted.current) { setBusy(false); setIdentityReturn(value => value + 1); }
    }
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

  // 업로드 응답으로 다음 화면을 열고, 응답이 없는 재조회 경로의 오류는 촬영 시트에 돌려줘요.
  const finishIdDocument = async (uploaded) => {
    if (!enrollment?.id) return;
    const current = uploaded || await getEnrollment(enrollment.id);
    if (!mounted.current || abandonedEnrollmentId.current === enrollment.id) return;
    setEnrollment(current);
    setError('');
    const screen = restoreRegisterScreen(current);
    setStep(screen.step); setSub(screen.sub);
  };

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
    // 재촬영 요청 — 등록 상태는 그대로(passed) 두고 요청된 칸만 갈아 끼워요. 서버도 같은
    // 규칙이에요(_validate_photo_mutation_enrollment: 요청된 칸이 아니면 409).
    if (enrollment?.photoReviewStatus === 'reshoot_requested') return enrollment;
    if (!['photos_pending', 'liveness_pending'].includes(enrollment?.status)) {
      throw new Error('현재 등록 단계에서는 사진을 고칠 수 없어요.');
    }
    return enrollment;
  };

  const clearPhotoPick = () => {
    try { sessionStorage.removeItem(`fm.registration.photoPos.${enrollment?.id}`); } catch { /* 저장소 없이도 업로드할 수 있어요. */ }
  };
  const rememberPhotoPick = (slot) => {
    if (!enrollment?.id) return;
    try { sessionStorage.setItem(`fm.registration.photoPos.${enrollment.id}`, JSON.stringify({ sub, scrollY: globalThis.window?.scrollY || 0, slot, at: Date.now() })); } catch { /* 저장소 없이도 사진을 고를 수 있어요. */ }
  };
  const clearPhotoError = () => { setPhotoSlot(null); setError(''); };

  const changePhoto = async (slot, file) => {
    clearPhotoPick();
    if (inFlight.current || !enrollment?.id || (assetsPending && step !== 'reshoot')) return;
    inFlight.current = true; setBusy(true); setError(''); setPhotoSlot(slot);
    try {
      // 등록 사진은 **원본 바이트 그대로** 올려요 — 이 사진이 곧 학습셋이라, 상품 사진용
      // 축소 규칙(4000px·JPEG 0.85)을 먹이면 48MP 원본이 12MP 손실본이 돼요.
      // EXIF 회전·HEIC 해독은 서버가 정규화본을 만들며 한 번에 해요.
      const editable = await editableEnrollment();
      if (!mounted.current) return;
      const result = await uploadEnrollmentPhoto({ enrollmentId: editable.id, slot, fileBlob: file, filename: file.name });
      if (!mounted.current) return;
      // 미리보기만 브라우저에서 작게 만들어요(업로드하지 않아요). 실패하면 미리보기만 없어요.
      const preview = await toPreviewImage(file);
      if (!mounted.current) return;
      if (previewUrls.current[slot]) URL.revokeObjectURL(previewUrls.current[slot]);
      if (preview) {
        previewUrls.current[slot] = URL.createObjectURL(preview);
      } else {
        // 브라우저 미리보기가 실패해도 예시 그림으로 되돌아가지 않게 서버의 정규화본을 읽어요.
        // HEIC처럼 브라우저가 직접 못 그리는 형식도 업로드가 끝나면 내 사진으로 바뀌어야 해요.
        try {
          previewUrls.current[slot] = await fetchEnrollmentPhotoUrl(editable.id, result.slot || slot);
        } catch { delete previewUrls.current[slot]; }
      }
      setPreviews({ ...previewUrls.current });
      if (Array.isArray(result.photos)) setEnrollment(result);
      else setEnrollment((current) => ({ ...current, photos: [...(current.photos || []).filter((photo) => photoSlotKey(photo) !== slot), { ...result, slot }] }));
      // 업로드 응답은 사진 한 장(EnrollmentPhotoView)이라 재촬영 목록이 안 들어 있어요 —
      // 남은 칸을 세려면 등록을 다시 읽어야 해요. 안 읽으면 "0장 남았어요" 가 영원히 안 떠요.
      if (editable.photoReviewStatus === 'reshoot_requested') {
        const refreshed = await getEnrollment(editable.id);
        if (mounted.current) setEnrollment(refreshed);
      }
    } catch (requestError) { if (mounted.current) setError(requestError instanceof TypeError && !requestError.status ? '인터넷 연결이 불안정해 사진을 올리지 못했어요. 다시 시도해 주세요.' : requestError.message || '사진을 올리지 못했어요. 다시 시도해 주세요.'); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };
  const removePhoto = async (slot) => {
    if (inFlight.current || !enrollment?.id || assetsPending) return;
    inFlight.current = true; setBusy(true); setError(''); setPhotoSlot(slot);
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
    clearPhotoError();
    if (!photoProgress(enrollment?.photos).complete || inFlight.current) return;
    if (['license_pending', 'processing', 'asset_building'].includes(enrollment.status)) { setStep('3'); return; }
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
    clearPhotoError();
    if (sub < PHOTO_REVIEW_SUB) {
      if (photoProgress(enrollment?.photos, PHOTO_GROUPS[sub - 1].id).complete) {
        setSub(editingPhotos ? PHOTO_REVIEW_SUB : sub + 1); setEditingPhotos(false);
      }
    } else await finishPhotos();
  };

  const submitConditions = async () => {
    if (!priceAgreed || !terms.allowedUse.length || !enrollment?.id || enrollment.status !== 'license_pending' || sponsorshipReady !== enrollment.modelId || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    const controller = new AbortController(); operation.current = controller;
    let deadline;
    let issuing = false;
    try {
      const payload = sponsorshipPayload(sponsorship);
      if (payload.sponsorshipEnabled || savedSponsorshipEnabled.current) {
        await updateModelSponsorship(enrollment.modelId, payload, 'model_register');
        savedSponsorshipEnabled.current = payload.sponsorshipEnabled;
      }
      if (!mounted.current) return;
      saveRegisterDraft(enrollment.id, terms);
      issuing = true;
      deadline = setTimeout(() => controller.abort(), 20000);
      const issued = await createLicense({ enrollmentId: enrollment.id, allowedUse: terms.allowedUse }, { signal: controller.signal });
      if (!mounted.current) return;
      setLicense(issued); setStep('done');
    } catch (requestError) {
      clearTimeout(deadline);
      if (!mounted.current) return;
      if (issuing && (requestError.code === 'vc_issue_delayed' || requestError.status >= 500 || controller.signal.aborted || requestError.name === 'AbortError')) {
        try {
          const current = await getEnrollment(enrollment.id);
          if (!mounted.current) return;
          setEnrollment(current);
          if (['vc_pending', 'passed'].includes(current.status)) { setStep('done'); return; }
          const screen = restoreRegisterScreen(current);
          if (screen.step === 'failed') { setStep('failed'); setError(enrollmentReasonMessage(current.reason)); return; }
        } catch { /* 저장된 조건을 유지하고 같은 화면에서 다시 시도해요. */ }
      }
      if (mounted.current) setError(controller.signal.aborted ? '발급 서버 응답이 늦어요. 잠시 뒤에 다시 시도해 주세요.' : requestError.message || '설정을 저장하지 못했어요.');
    } finally { clearTimeout(deadline); inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  const restart = async () => {
    setBusy(true); setError('');
    try {
      const models = await listMyModels();
      if (!mounted.current) return;
      if (models.some((model) => model.status === 'awaiting_confirm')) { navigate('/model/confirm', { replace: true }); return; }
      if (!mounted.current) return;
      Object.values(previewUrls.current).forEach((url) => URL.revokeObjectURL(url)); previewUrls.current = {}; setPreviews({});
      setEnrollment(null); setLicense(null); setConsents([false, false]); setTerms(defaultRegisterTerms()); setPriceAgreed(false); setEditingPhotos(false); setWithdrawalOpen(false); setPhotoSlot(null); setSub(1); setStep('1');
    } catch (requestError) { if (mounted.current) setError(requestError.message); }
    finally { if (mounted.current) setBusy(false); }
  };

  const current = step === 'done' ? 4 : ['processing', 'poll_error', 'liveness', 'reshoot'].includes(step) ? 2 : Number(step[0]) || 1;
  const checkingPhotos = step === '2' && sub === PHOTO_REVIEW_SUB && busy;
  const visibleAssetError = assetsPending && ['2', '3'].includes(step) ? assetError : '';
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
      hint: consents.every(Boolean) ? '필수 동의를 마쳤어요' : '필수 항목 2개에 동의해 주세요',
    };
  } else if (step === 'method') {
    content = <>{heading('본인 확인 방법을 골라 주세요')}<IdentityMethodStep methods={IDENTITY_METHODS} onPick={handleMethodPick} simpleAuthUnavailableReason={SIMPLE_AUTH_UNAVAILABLE_REASON} /></>;
    previous = { label: '이전', action: () => setStep('1') };
  } else if (step === 'id_capture') {
    content = <>{heading('주민등록증을 찍어 올려요', `신분증 이미지는 심사 이후 바로 삭제되며, 다른 어떠한 용도로도 활용되지 않습니다. 촬영 후 주민등록번호 뒤 7자리를 가리고 확인해야 올릴 수 있어요.`)}
      {enrollment?.id && <IdDocumentStep
        key={enrollment.id}
        enrollmentId={enrollment.id}
        onUploaded={finishIdDocument}
        // 409(이 단계가 아님/경로 꺼짐)면 성공 때와 같은 재조회 경로로 되돌려요 — 이 화면은
        // 성공으로만 빠져나가서, 안 그러면 사용자가 갇혀요.
        onStale={() => finishIdDocument()}
      />}</>;
    previous = { label: '이전', action: backFromIdCapture };
  } else if (step === '2') {
    content = renderPhotos({ sub, enrollment, previews, busy, editingDisabled: assetsPending, onFile: changePhoto, onRemove: removePhoto, photoSlot, error, onPick: rememberPhotoPick, onCancelPick: clearPhotoPick, editGroup: (groupSub) => { clearPhotoError(); setSub(groupSub); setEditingPhotos(true); } });
    const progress = photoProgress(enrollment?.photos, sub < PHOTO_REVIEW_SUB ? PHOTO_GROUPS[sub - 1].id : undefined);
    next = { label: checkingPhotos ? '사진을 확인하는 중…' : sub === PHOTO_REVIEW_SUB ? '확인 완료' : '다음', action: nextPhoto, disabled: !progress.complete, hint: checkingPhotos ? '등록 사진을 확인하고 있어요. 10초쯤 걸려요.' : assetsPending ? '사진을 정리하고 있어요. 준비가 끝나면 사진을 바꾸거나 지울 수 있어요.' : progress.complete ? '모두 저장했어요' : `${progress.count}/${progress.total}장 저장. ${progress.total - progress.count}장을 더 올려 주세요.` };
    previous = { label: '이전', action: () => { clearPhotoError(); if (sub > 1) setSub(sub - 1); else setStep('1'); } };
  } else if (step === 'reshoot') {
    content = renderReshoot({ enrollment, previews, busy, onFile: changePhoto, photoSlot, error });
    const remaining = (enrollment?.reshootSlots || []).length;
    next = {
      label: busy ? '올리는 중이에요' : remaining ? `${remaining}장 남았어요` : '확인 요청 보내기',
      action: restore,
      disabled: busy || remaining > 0,
      hint: remaining ? '요청받은 사진을 모두 올리면 담당자가 다시 확인해요.' : '다 올렸어요. 담당자가 다시 확인해요.',
    };
    previous = { label: '나중에 하기', action: () => navigate('/status') };
  } else if (step === '3') {
    content = <>{renderConditions({ terms, setTerms, busy, priceAgreed, setPriceAgreed, sponsorship, setSponsorship, sponsorshipLoading: sponsorshipReady !== enrollment?.modelId })}{sponsorshipReady !== enrollment?.modelId && !sponsorshipError && <p role="status" className={s.description}>저장된 협찬 설정을 불러오고 있어요.</p>}{sponsorshipError && <div role="alert"><p>{sponsorshipError}</p><button type="button" className={s.textLink} onClick={() => setSponsorshipRetry(value => value + 1)}>설정 다시 불러오기</button></div>}</>;
    next = { label: busy ? '저장하고 있어요' : '라이선스 증서 발급하기', action: submitConditions, disabled: enrollment?.status !== 'license_pending' || !terms.allowedUse.length || !priceAgreed || sponsorshipReady !== enrollment?.modelId, hint: assetsPending ? '사진을 정리하고 있어요. 잠시 뒤 발급할 수 있어요.' : priceAgreed ? '필수 동의를 마쳤어요' : '필수 동의에 체크해 주세요' };
    previous = { label: '이전', action: () => { setStep('2'); setSub(PHOTO_REVIEW_SUB); setEditingPhotos(false); } };
  } else if (step === 'done') {
    content = <section className={s.doneContent}><svg className={s.doneMark} viewBox="0 0 60 60" aria-hidden="true"><circle cx="30" cy="30" r="28.75" /><path d="m19 30 8 8 15-17" /></svg><h1 tabIndex={-1}>축하해요, 등록이 끝났어요</h1><div className={s.doneDescription}><p>발급한 라이선스 증서는 모델님이 철회하기 전까지 유효해요.</p><p>모델님의 얼굴을 활용한 테스트컷은 2일 이내 보내드릴게요.</p><p>모델님이 테스트컷을 보고 최종 확정해주시면 라이선스 증서 발급과 함께 등록이 최종 완료됩니다.</p></div><Link to="/status" className={s.doneButton}>마이페이지로</Link><button type="button" className={s.textLink} onClick={restart} disabled={busy}>새로 등록하기</button></section>;
  } else if (step === 'liveness') {
    content = <>{heading('라이브 인증을 진행해요', '화면의 안내에 따라 얼굴을 보여 주세요.')}<Suspense fallback={<p role="status">인증 화면을 준비하고 있어요.</p>}><FaceLivenessStep session={session} onAnalysisComplete={() => finishMatch()} onError={(requestError) => { setSession(null); setError(requestError?.message || '라이브 인증이 중단됐어요.'); setStep('2'); setSub(PHOTO_REVIEW_SUB); }} onCancel={() => { setSession(null); setStep('2'); setSub(PHOTO_REVIEW_SUB); }} /></Suspense></>;
  } else if (step === 'loading' || step === 'processing') {
    content = <>{heading(step === 'loading' ? '등록 상태를 불러오고 있어요' : '등록을 마무리하고 있어요')}<p className={s.description} role="status"><span className={s.spinner} /> 잠시만 기다려 주세요.</p></>;
  } else {
    content = heading(step === 'failed' ? '등록을 이어갈 수 없어요' : '등록 상태를 확인하지 못했어요', step === 'failed' ? `${enrollmentReasonMessage(enrollment?.reason)} 다시 시작하면 사진과 조건을 새로 받아요.` : undefined);
    next = { label: step === 'failed' ? '다시 시작하기' : '다시 확인하기', action: step === 'failed' ? restart : restore };
  }

  return <div className={s.page} data-registration data-step={step}>
    <div className={s.main}>
      {step !== 'done' && <nav className={s.progress} aria-label="등록 진행 상황"><div className={s.progressMeta}><span>{current} / 4</span><span>{busy ? '저장 중이에요' : enrollment?.id ? '진행 상황이 저장돼요' : '모델 등록'}</span></div><ol className={s.steps}>{['본인확인', '사진', '조건', '증서'].map((label, index) => <li key={label} className={index < current ? s.reached : ''} aria-current={index === current - 1 ? 'step' : undefined}><i className={s.stepBar} /><span>{index < current - 1 ? '✓ ' : ''}{label}</span></li>)}</ol></nav>}
      {error && !(photoSlot && ['2', 'reshoot'].includes(step)) && <p className={s.error} role="alert" ref={errorRef} tabIndex={-1}>{error}</p>}
      {content}
      {/* 위젯이 붙을 빈 호스트. React 는 이 안을 절대 안 본다 — #oacxDiv 는
          facemarketIdentityWidget 이 직접 만들어 넣는다(oacxHost.js 참고).
          React 가 #oacxDiv 를 그리면 Vue 가 그걸 교체할 때 형제 앵커가 깨져 앱이 죽는다. */}
      <div id="oacxHost" />
    </div>
    {/* 이전만 있는 화면(수단 선택·신분증 촬영)도 푸터를 그려요 — 다음 동작은 화면 안에 있어요. */}
    {(next || previous) && <footer className={s.bottomBar}>
      {visibleAssetError ? <div id="register-asset-error" className={`${s.footerHint} ${s.assetError}`} role="alert"><p>{visibleAssetError}</p><button type="button" className={s.textLink} onClick={() => { setAssetError(''); setAssetPollRetry(value => value + 1); }}>사진 준비 상태 다시 확인하기</button></div>
        : next?.hint && <p id="register-hint" className={s.footerHint} aria-live="polite">{next.hint}</p>}
      <div className={s.footerActions}><div className={s.footerInner}>{previous && <button type="button" className={s.secondary} disabled={busy} onClick={previous.action}>{previous.label}</button>}{next && <button type="button" className={s.primary} disabled={busy || !!next.disabled} aria-busy={checkingPhotos || undefined} aria-describedby={visibleAssetError ? 'register-asset-error' : next.hint ? 'register-hint' : undefined} onClick={next.action}>{checkingPhotos ? <><span className={s.spinner} aria-hidden={true} />{next.label}</> : next.label}</button>}</div></div>
    </footer>}
  </div>;
}
