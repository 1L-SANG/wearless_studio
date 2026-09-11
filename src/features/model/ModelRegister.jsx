import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { cancelEnrollment, completeEnrollment, createEnrollment, createIdentity, createLicense, createLivenessSession, deleteEnrollmentPhoto, fetchEnrollmentPhotoUrl, getFacemarketConfig, getCurrentEnrollment, getEnrollment, listLicenses, listMyModels, submitPhysique, uploadEnrollmentPhoto } from '@/lib/api/facemarket.js';
import { runIdentityWidget } from '@/lib/api/facemarketIdentityWidget.js';
import { toUploadableImage } from '../../lib/imageTranscode.js';
import { enrollmentReasonMessage } from './biometricEnrollment.js';
import { CONSENT_VERSION, PHOTO_GROUPS, SLOTS, defaultRegisterTerms, photoProgress, photoSlotKey, readRegisterDraft, restoreRegisterScreen, saveRegisterDraft } from './registerSlots.js';
import { heading, renderConditions, renderConsent, renderPhotos } from './RegisterScreens.jsx';
import s from './ModelRegister.module.css';

const FaceLivenessStep = lazy(() => import('./FaceLivenessStep.jsx'));
const DEVICE_KEY = 'wearless.fmDeviceId';
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
      const token = await runIdentityWidget({ signal: controller.signal });
      if (!mounted.current || controller.signal.aborted) return;
      const verified = await createIdentity(record.id, { token });
      if (!mounted.current || controller.signal.aborted) return;
      setEnrollment(verified); setSub(1); setStep('2');
    } catch (requestError) {
      if (mounted.current && !controller.signal.aborted) { setError(requestError.message || '본인 확인에 실패했어요.'); }
    } finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  };

  const startEnrollment = async () => {
    if (!consents.every(Boolean) || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    let created;
    try {
      // 동의를 편집한 등록은 서버의 같은 활성 등록을 반환해요.
      created = await createEnrollment({ documentVersion: CONSENT_VERSION, deviceId: getDeviceId() });
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

  const changePhoto = async (slot, file) => {
    if (inFlight.current || !enrollment?.id) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      const blob = await toUploadableImage(file);
      const result = await uploadEnrollmentPhoto({ enrollmentId: enrollment.id, slot, fileBlob: blob, filename: blob.name || file.name });
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
      const stored = enrollment.photos.find((photo) => photoSlotKey(photo) === slot);
      await deleteEnrollmentPhoto(enrollment.id, stored?.slot || stored?.angle || slot);
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
      if (enrollment?.id && ['rejected', 'failed', 'expired'].includes(enrollment.status)) await cancelEnrollment(enrollment.id);
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

  const current = step === 'done' ? 4 : ['processing', 'poll_error', 'liveness'].includes(step) ? 2 : Number(step[0]) || 1;
  let content, next, previous;
  if (step === '1') {
    content = renderConsent(consents, setConsents, withdrawalOpen, setWithdrawalOpen);
    const identityPending = enrollment?.status === 'identity_pending' && [enrollment.consentDocumentVersion, enrollment.termsConsentVersion].every((version) => version === CONSENT_VERSION);
    next = { label: busy ? '인증창에서 확인해 주세요' : error ? '다시 인증하기' : identityPending ? '신분증 인증하기' : '동의하고 신분증 인증하기', action: identityPending ? () => runIdentity() : startEnrollment, disabled: !consents.every(Boolean), hint: consents.every(Boolean) ? '세 가지 동의를 모두 확인했어요' : '세 가지를 모두 켜야 다음으로 갈 수 있어요' };
  } else if (step === '2') {
    content = renderPhotos({ sub, enrollment, previews, busy, onFile: changePhoto, onRemove: removePhoto, editGroup: (groupSub) => { setSub(groupSub); setEditingPhotos(true); } });
    const complete = sub <= 3 ? photoProgress(enrollment?.photos, PHOTO_GROUPS[sub - 1].id).complete : photoProgress(enrollment?.photos).complete;
    next = { label: sub === 4 ? '확인 완료' : '이동', action: nextPhoto, disabled: !complete, hint: complete ? '다 채웠어요' : '사진을 다 채워야 다음으로 갈 수 있어요' };
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
      <div id="oacxDiv" />
    </div>
    {next && <footer className={s.bottomBar}>{next.hint && <p id="register-hint" className={s.footerHint} aria-live="polite">{next.hint}</p>}<div className={s.footerActions}><div className={s.footerInner}>{previous && <button type="button" className={s.secondary} disabled={busy} onClick={previous.action}>{previous.label}</button>}<button type="button" className={s.primary} disabled={busy || !!next.disabled} aria-describedby={next.hint ? 'register-hint' : undefined} onClick={next.action}>{next.label}</button></div></div></footer>}
  </div>;
}
