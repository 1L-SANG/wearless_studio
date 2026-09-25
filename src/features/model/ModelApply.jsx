import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Icon, useToast } from '@/components/ui.jsx';
import { FormInput } from '@/components/ui/FormInput.jsx';
import { ImageUpload } from '@/components/ui/ImageUpload.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { deleteStagedApplicationPhoto, getCurrentApplication, stageApplicationPhoto, submitApplication } from '@/lib/api/facemarket.js';
import { readApplyDraft, writeApplyDraft, clearApplyDraft } from '@/lib/applyDraft.js';
import { toUploadableImage } from '@/lib/imageTranscode.js';
import { REVIEW_SLA_LABEL } from '../facemarket-landing/facemarketTerms.js';
import { nextBirthdateSegments, backspaceBirthdateSegments, birthdateFromSegments, birthdateProblem, isAdultBirthdate } from './birthdateInput.js';
import { INSTAGRAM_HANDLE_PATTERN, normalizeInstagramHandle } from './sponsorshipOptions.js';
import s from './ModelApply.module.css';

const PRIVACY_CONSENT_VERSION = '2026-09-v1';
const STEPS = ['기본 정보', '프로필', '확인'];
const EXPERIENCE_OPTIONS = [
  { value: 'none', label: '경력 없음' },
  { value: 'beginner', label: '1년 미만' },
  { value: 'intermediate', label: '1년에서 3년' },
  { value: 'professional', label: '3년 이상' },
];
const ATTESTATIONS = [
  { key: 'adultAndTruthful', text: '만 19세 이상이며, 본인이 직접 적은 내용임을 확인합니다.' },
  { key: 'photosAreMine', text: '올린 사진은 본인의 사진이고, 보정이나 필터, AI 생성이 아니며, 제출할 권리가 있음을 확인합니다.' },
  { key: 'noAgencyContract', text: '현재 어떤 모델 에이전시와도 계약하거나 소속되어 있지 않음을 확인합니다.', detail: '기존 계약이 있으면 FaceMarket 모델 리스트에서 제외될 수 있습니다.' },
  { key: 'reviewOnlyUse', text: '제출한 내용이 FaceMarket 등록 심사에만 쓰이고, 다른 목적으로는 쓰이지 않는다는 점을 확인합니다.' },
  { key: 'privacyPolicy' },
];
const EMPTY = {
  contactEmail: '', applicantName: '', gender: '', birthdate: '', phone: '',
  heightCm: '', weightKg: '', experienceLevel: '', agencyContracted: null,
  portfolioUrl: '', snsUrl: '',
};
const PHOTO_GUIDANCE = [
  '얼굴이 잘 보이게 정면에서 가까이 찍은 사진을 올려 주세요.',
  '보정이나 필터 없이 찍은 원본 사진이어야 해요. 안경과 모자는 벗어 주세요.',
  '지원서 확인용으로만 쓰고, 공개 프로필이나 AI 학습에는 쓰지 않아요.',
];

const inputText = value => String(value ?? '');

function optionalLink(value) {
  const link = inputText(value).trim();
  if (!link) return null;
  return /^[a-z][a-z\d+.-]*:/i.test(link) ? link : `https://${link}`;
}

// '@아이디'로 적은 인스타 계정만 프로필 주소로 바꾼다. '@' 없는 낱말은 그대로 둔다.
function snsLink(value) {
  const raw = inputText(value).trim();
  if (raw.startsWith('@')) {
    const handle = normalizeInstagramHandle(raw);
    if (INSTAGRAM_HANDLE_PATTERN.test(handle) && !handle.includes('..')) return `https://www.instagram.com/${handle}/`;
  }
  return optionalLink(value);
}

function linkError(link, value, field) {
  if (!link) return '';
  if (link.length > 500) return '링크는 https://를 포함해 500자까지 입력할 수 있어요. 더 짧은 공유 주소를 입력해 주세요.';
  if (!/^https?:\/\//.test(link)) return 'http:// 또는 https://로 시작하는 웹 주소를 입력해 주세요.';
  let hostname = '';
  try { hostname = new URL(link).hostname; } catch { hostname = ''; }
  if (/\s/.test(inputText(value).trim()) || !hostname.includes('.')) {
    return field === 'snsUrl'
      ? '주소를 다시 확인해 주세요. 인스타 아이디만 적을 때는 @아이디로 적어 주세요.'
      : '주소를 다시 확인해 주세요. 예: https://portfolio.com';
  }
  return '';
}

function mergeDraft(base, draft) {
  const merged = { ...base };
  for (const key of Object.keys(EMPTY)) {
    if (!Object.prototype.hasOwnProperty.call(draft || {}, key)) continue;
    const value = draft[key];
    if (value === null || typeof value === 'string' || typeof value === 'boolean'
      || (typeof value === 'number' && Number.isFinite(value))) merged[key] = value;
  }
  return merged;
}

function formatPhone(input) {
  const raw = String(input).trim();
  let digits = raw.replace(/\D/g, '');
  if (/^(\+|00)82/.test(raw)) {
    digits = digits.replace(/^(00)?82/, '');
    if (!digits.startsWith('0')) digits = `0${digits}`;
  }
  digits = digits.slice(0, 13);
  const prefixLength = digits.startsWith('02') ? 2 : 3;
  if (digits.length <= prefixLength) return digits;
  if (digits.length <= prefixLength + 4) return `${digits.slice(0, prefixLength)}-${digits.slice(prefixLength)}`;
  const middleEnd = digits.length <= prefixLength + 7 ? prefixLength + 3 : prefixLength + 4;
  return `${digits.slice(0, prefixLength)}-${digits.slice(prefixLength, middleEnd)}-${digits.slice(middleEnd)}`;
}
const validInteger = (value, min, max) => /^\d+$/.test(String(value)) && Number(value) >= min && Number(value) <= max;

export function BirthdateInput({ value, onChange }) {
  const [segments, setSegments] = useState(() => value ? inputText(value).split('-') : ['', '', '']);
  const inputs = useRef([]);
  // apply()가 포커스를 옮기면 떠나는 칸의 blur가 같은 렌더의 옛 segments로 바로 불린다.
  // 그때는 한 자리 채우기를 건너뛰어 방금 넣은 값을 덮지 않게 한다.
  let moving = false;
  const apply = (result) => {
    setSegments(result.segments);
    onChange(birthdateFromSegments(result.segments));
    moving = true;
    inputs.current[result.focusIndex]?.focus();
    moving = false;
  };
  const padLoneDigit = (index) => {
    if (moving || !/^[1-9]$/.test(segments[index])) return;
    const padded = [...segments];
    padded[index] = `0${segments[index]}`;
    setSegments(padded);
    onChange(birthdateFromSegments(padded));
  };
  const problem = birthdateProblem(segments);
  return (
    <>
    <div className={s.birthdate}>
      {['년', '월', '일'].map((label, index) => (
        <div className={s.birthSegment} key={label}>
          <label className={s.birthControl}>
            <input ref={(el) => { inputs.current[index] = el; }} className={s.input}
              aria-label={`생년월일 ${label}`} required inputMode="numeric" autoComplete="off"
              aria-describedby="birthdate-hint" aria-invalid={problem ? true : undefined}
              maxLength={index === 0 ? 4 : 2} placeholder=" " value={segments[index]}
              onChange={(event) => apply(nextBirthdateSegments(segments, index, event.target.value))}
              onBlur={index > 0 ? () => padLoneDigit(index) : undefined}
              onPaste={(event) => {
                const text = event.clipboardData.getData('text');
                if (text.replace(/\D/g, '').length === 8) {
                  event.preventDefault();
                  apply(nextBirthdateSegments(segments, index, text));
                }
              }}
              onKeyDown={(event) => {
                if (event.key === 'Backspace' && !segments[index] && index > 0) {
                  event.preventDefault();
                  apply(backspaceBirthdateSegments(segments, index));
                }
              }} />
            <span className={s.birthLabel}>{label}</span>
          </label>
        </div>
      ))}
    </div>
    <p id="birthdate-hint" className={problem ? `${s.hint} ${s.hintError}` : s.hint} aria-live="polite">{problem === 'calendar' ? '달력에 있는 날짜인지 확인해 주세요.' : problem === 'year' ? '태어난 해는 네 자리로 적어 주세요.' : '만 19세 이상만 지원할 수 있어요.'}</p>
    </>
  );
}

export function ModelApply() {
  const navigate = useNavigate();
  const location = useLocation();
  const { push } = useToast();
  const { session } = useAuth();
  const accountEmail = session?.user?.email || '';
  const userId = session?.user?.id || null;
  const [phase, setPhase] = useState('loading');
  const [form, setForm] = useState(EMPTY);
  const [photo, setPhoto] = useState(null);
  const [attest, setAttest] = useState(() => Object.fromEntries(ATTESTATIONS.map(({ key }) => [key, false])));
  const [step, setStep] = useState(1);
  const [editing, setEditing] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [touched, setTouched] = useState({});
  const photoUrl = useRef(null);
  const allAttestationsInput = useRef(null);
  const heading = useRef(null);
  const mounted = useRef(true);
  const submitting = useRef(false);
  const shownView = useRef(null);
  const syncedKey = useRef(null);
  const draftOwner = useRef(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    getCurrentApplication().then((app) => {
      if (!alive) return;
      if (app && ['under_review', 'approved'].includes(app.status)) {
        clearApplyDraft(userId);
        navigate('/status', { replace: true });
        return;
      }
      // 재지원은 보존 중인 입력값만 복원한다. 사진과 체크사항은 새로 받는다.
      const previous = app && !app.piiPurgedAt ? app : {};
      draftOwner.current = userId;
      setForm(mergeDraft(Object.fromEntries(Object.entries(EMPTY).map(([key, fallback]) => [
        key, key === 'contactEmail' ? (previous.contactEmail || accountEmail) : (previous[key] ?? fallback),
      ])), readApplyDraft(userId)));
      setPhase('ready');
    }).catch((err) => {
      if (!alive) return;
      if (err?.status !== 404) push?.(err?.code === 'browser_offline' ? '인터넷 연결이 끊겼어요. 연결을 확인해 주세요.' : err.message, { icon: 'alertCircle' });
      draftOwner.current = userId;
      setForm(mergeDraft({ ...EMPTY, contactEmail: accountEmail }, readApplyDraft(userId)));
      setPhase('ready');
    });
    return () => { alive = false; };
  }, [accountEmail, userId, navigate, push]);

  useEffect(() => () => {
    if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
  }, []);

  useEffect(() => {
    if (phase === 'loading' || phase === 'submitting') return;
    const view = phase === 'complete' ? 'complete' : step;
    if (shownView.current === view) return;
    shownView.current = view;
    globalThis.window?.scrollTo?.(0, 0);
    heading.current?.focus({ preventScroll: true });
  }, [step, phase]);

  const set = useCallback((key, value) => setForm((current) => ({ ...current, [key]: value })), []);
  const markTouched = (key) => setTouched((current) => (current[key] ? current : { ...current, [key]: true }));
  const onPickPhoto = async (file) => {
    if (!file || uploading) return;
    setUploading(true);
    setError('');
    const previous = photo;
    let previewUrl = null;
    try {
      // 심사용 사진이라 원본 대신 긴 변 2048px JPEG로 줄여 올린다. 줄이지 못하면 원본을 보낸다.
      let upload = file;
      try { upload = await toUploadableImage(file, { maxEdge: 2048, forceJpeg: true }); } catch { upload = file; }
      previewUrl = URL.createObjectURL(upload);
      if (mounted.current) setPhoto({ staged: false, stageId: previous?.stageId, previewUrl, fileName: file.name });
      const stagedPhoto = await stageApplicationPhoto({ kind: 'profile', fileBlob: upload, filename: upload.name || file.name });
      if (!stagedPhoto?.stageId) throw new Error('사진 저장 결과를 확인하지 못했어요. 다시 올려 주세요.');
      if (!mounted.current) {
        URL.revokeObjectURL(previewUrl);
        return;
      }
      if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
      photoUrl.current = previewUrl;
      setPhoto({ staged: true, stageId: stagedPhoto.stageId, previewUrl, fileName: file.name });
    } catch (err) {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      if (mounted.current) {
        setPhoto(previous);
        setError(err instanceof TypeError
          ? '인터넷 연결이 불안정해 사진을 올리지 못했어요. 연결을 확인하고 사진을 다시 골라 주세요.'
          : err?.code === 'file_too_large'
            ? '사진 용량이 너무 커요. 25MB 이하 사진으로 골라 주세요.'
            : err?.message || '사진을 올리지 못했어요. 다시 시도해 주세요.');
      }
    } finally {
      if (mounted.current) setUploading(false);
    }
  };

  const contactEmail = inputText(form.contactEmail).trim();
  const emailValid = contactEmail.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contactEmail);
  const links = { portfolioUrl: optionalLink(form.portfolioUrl), snsUrl: snsLink(form.snsUrl) };
  const linkErrors = {
    portfolioUrl: linkError(links.portfolioUrl, form.portfolioUrl, 'portfolioUrl'),
    snsUrl: linkError(links.snsUrl, form.snsUrl, 'snsUrl'),
  };
  const basicChecks = [
    ['이름', Boolean(inputText(form.applicantName).trim())],
    ['성별', ['female', 'male'].includes(form.gender)],
    ['생년월일', isAdultBirthdate(form.birthdate)],
    ['전화번호', /^[\d-]+$/.test(form.phone) && /^\d{9,13}$/.test(inputText(form.phone).replace(/-/g, ''))],
    ['이메일', emailValid],
  ];
  const basicValid = basicChecks.every(([, ok]) => ok);
  const photoReady = Boolean(photo?.staged && photo?.stageId);
  const heightValid = validInteger(form.heightCm, 100, 250);
  const weightValid = form.weightKg === '' || form.weightKg == null || validInteger(form.weightKg, 30, 200);
  const agencyAnswered = typeof form.agencyContracted === 'boolean';
  const profileValid = photoReady && !uploading && heightValid && weightValid
    && agencyAnswered && !linkErrors.portfolioUrl && !linkErrors.snsUrl;
  const missingRequired = step === 1 ? basicChecks.filter(([, ok]) => !ok).map(([label]) => label)
    : step === 2 ? [!photoReady && '내 이미지', !heightValid && '키', !agencyAnswered && '에이전시 경험'].filter(Boolean) : [];
  const needsFix = step === 2
    ? [!weightValid && '몸무게', linkErrors.portfolioUrl && '포트폴리오 링크', linkErrors.snsUrl && 'SNS 링크'].filter(Boolean) : [];

  useEffect(() => {
    // 기록 항목 하나는 한 번만 맞춘다. 라우터는 기록 변경을 transition으로 늦게 반영해서,
    // phase만 먼저 바뀐 렌더(제출 성공, 제출 오류 뒤 고치기)는 아직 떠난 항목을 들고 있다.
    if (phase === 'loading' || phase === 'submitting' || syncedKey.current === location.key) return;
    syncedKey.current = location.key;
    if (phase === 'complete') {
      // 완료 뒤 뒤로가기로 지원서 단계 기록에 오면 지원 상태 화면으로 보낸다.
      if (!location.state?.applyComplete) navigate('/status', { replace: true });
      return;
    }
    const wanted = Number(location.state?.applyStep) || 1;
    const reachable = basicValid ? (profileValid ? 3 : 2) : 1;
    const shown = Math.min(wanted, reachable);
    setStep(shown);
    setEditing(location.state?.applyEditing && shown === wanted && shown < 3 ? shown : null);
    // 낮춰 보여 준 단계는 기록 항목에도 적는다. applyLowered가 있으면 '이전'이 기록을 거슬러 가지 않고 앞 단계로 바꿔 적어 제자리로 튕기지 않는다.
    if (shown !== wanted) {
      navigate(`${location.pathname}${location.search || ''}`, {
        replace: true, state: { applyStep: shown, applyEditing: false, applyLowered: true },
      });
    }
    // 입력 검증 값은 일부러 deps에서 뺀다. 기록 이동과 첫 로드 때만 단계를 맞추고 타자 중에는 건드리지 않는다.
  }, [location.key, phase]);
  useEffect(() => {
    if (phase === 'ready' && draftOwner.current === userId) writeApplyDraft(userId, form);
  }, [phase, form, userId]);

  const uncheckedCount = ATTESTATIONS.filter(({ key }) => !attest[key]).length;
  const allAttestationsChecked = uncheckedCount === 0;
  const someAttestationsChecked = uncheckedCount > 0 && uncheckedCount < ATTESTATIONS.length;
  const busy = phase === 'submitting';
  const canSubmit = basicValid && profileValid && uncheckedCount === 0;

  useEffect(() => {
    if (allAttestationsInput.current) allAttestationsInput.current.indeterminate = someAttestationsChecked;
  }, [someAttestationsChecked, step]);

  const setAllAttestations = (checked) => {
    setAttest(Object.fromEntries(ATTESTATIONS.map(({ key }) => [key, checked])));
  };

  const removePhoto = async () => {
    if (!photo?.stageId || uploading) return;
    const stageId = photo.stageId;
    setUploading(true);
    setError('');
    setPhoto((current) => current ? { ...current, staged: false } : current);
    try {
      await deleteStagedApplicationPhoto('profile', stageId);
      if (mounted.current) {
        if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
        photoUrl.current = null;
        setPhoto(null);
      }
    } catch (err) {
      if (mounted.current) setError(err.code === 'staging_changed'
        ? err.message
        : '사진 삭제 결과를 확인하지 못했어요. 사진을 다시 올려 주세요.');
    } finally {
      if (mounted.current) setUploading(false);
    }
  };

  const submit = async () => {
    if (!canSubmit || submitting.current) return;
    submitting.current = true;
    setPhase('submitting');
    setError('');
    try {
      await submitApplication({
        contactEmail,
        applicantName: inputText(form.applicantName).trim(),
        gender: form.gender,
        birthdate: form.birthdate,
        phone: form.phone,
        heightCm: Number(form.heightCm),
        weightKg: form.weightKg === '' || form.weightKg == null ? null : Number(form.weightKg),
        experienceLevel: form.experienceLevel || null,
        agencyContracted: form.agencyContracted,
        portfolioUrl: links.portfolioUrl,
        snsUrl: links.snsUrl,
        profileStageId: photo.stageId,
        attestations: attest,
        privacyConsent: { accepted: true, documentVersion: PRIVACY_CONSENT_VERSION },
      });
      clearApplyDraft(userId);
      if (mounted.current) {
        // 완료 화면 기록 항목에 표시를 남겨 두고, 그 뒤 뒤로가기로 옛 단계 항목에 오면 동기화 effect가 상태 화면으로 보낸다.
        navigate(`${location.pathname}${location.search || ''}`, { replace: true, state: { applyComplete: true } });
        setPhase('complete');
      }
    } catch (err) {
      if (mounted.current) {
        // edit()가 오류를 지우므로 단계를 먼저 옮기고 오류를 나중에 적는다.
        if (err.code === 'profile_photo_changed') {
          if (photoUrl.current) URL.revokeObjectURL(photoUrl.current);
          photoUrl.current = null;
          setPhoto(null);
          edit(2);
          setError('사진을 다시 올려 주세요. 올린 지 오래됐거나 다른 화면에서 바뀌었어요. 적어 둔 내용은 그대로예요.');
        } else if (err.status === 409) {
          setError('이미 검토 중인 지원서가 있어요. 상태 화면에서 확인해 주세요.');
        } else if (err.status >= 400 && err.status < 500) {
          const correctionStep = {
            invalid_email: 1, invalid_gender: 1, invalid_phone: 1,
            invalid_url: 2, invalid_height: 2, invalid_weight: 2, invalid_experience: 2,
          }[err.code];
          if (correctionStep) edit(correctionStep);
          setError(err.message || '입력한 내용을 확인하고 다시 보내 주세요.');
        } else {
          setError(err.status
            ? '지금은 지원서를 처리하지 못했어요. 잠시 후 다시 눌러 주세요.'
            : '서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.');
        }
        setPhase('ready');
      }
    } finally { submitting.current = false; }
  };
  // 단계마다 브라우저 기록을 하나씩 남겨 휴대폰 뒤로가기가 이전 단계로 가게 한다.
  const goTo = (destination, options) => {
    setError('');
    setStep(destination);
    navigate(`${location.pathname}${location.search || ''}`, {
      replace: Boolean(options?.replace),
      state: { applyStep: destination, applyEditing: Boolean(options?.editing) },
    });
  };
  const edit = (destination) => { setEditing(destination); goTo(destination, { editing: true }); };
  const returnToReview = () => {
    setEditing(null);
    if (location.state?.applyEditing === true && location.state?.applyStep === step) {
      setError('');
      // 확인 단계로 갈 수 없는 입력이면 3단계를 잠깐 그리지 않고 동기화 effect가 낮춰 주게 둔다.
      if (basicValid && profileValid) setStep(3);
      navigate(-1);
    } else {
      goTo(3, { replace: true });
    }
  };
  const next = () => {
    if ((step === 1 && !basicValid) || (step === 2 && !profileValid)) return;
    if (editing) returnToReview();
    else goTo(step + 1);
  };
  const goBack = () => {
    if (editing) returnToReview();
    else if (step === 1) navigate('/apply');
    else if (location.state?.applyStep === step && !location.state?.applyEditing && !location.state?.applyLowered) {
      setError('');
      setStep(step - 1);
      navigate(-1);
    } else {
      goTo(step - 1, { replace: true });
    }
  };
  const lastFieldKeyDown = (e) => {
    if (e.key !== 'Enter' || e.nativeEvent.isComposing) return;
    e.preventDefault();
    e.currentTarget.blur();
    next();
  };
  const showAttestations = () => {
    const el = document.getElementById('attest-all');
    el?.scrollIntoView({ block: 'center' });
    el?.focus({ preventScroll: true });
  };

  if (phase === 'loading') return <div className={s.page}><p className={s.loading}>불러오는 중…</p></div>;
  if (phase === 'complete') return (
    <section className={s.completePage}>
      <div className={s.completeMark}><Icon name="check" size={32} stroke={1.5} /></div>
      <h1 ref={heading} tabIndex={-1}>지원서가 접수 완료됐어요</h1>
      <p>
        <span>{`${REVIEW_SLA_LABEL}에 결과를 ${contactEmail} 주소로 보내 드려요. 메일이 안 보이면 스팸함도 확인해 주세요.`}</span>
        <span>승인되면 본인확인과 18장의 사진 촬영을 올려야 해요. 밝은 낮에 도와줄 사람과 찍으면 평균 10분이면 등록과정이 끝나요.</span>
      </p>
      <Link className={s.completeCta} to="/status">지원 상태 보기</Link>
      <Link className={s.completeGuide} to="/photo-guide">촬영 가이드 미리 보기</Link>
    </section>
  );

  const basicRows = [
    ['이름', form.applicantName], ['성별', form.gender === 'female' ? '여성' : '남성'],
    ['생년월일', inputText(form.birthdate).replace(/^(\d{4})-(\d{2})-(\d{2})$/, (_, year, month, day) => `${year}년 ${Number(month)}월 ${Number(day)}일`)], ['전화번호', form.phone], ['이메일', contactEmail],
  ];
  const profileRows = [
    ['키', form.heightCm ? `${form.heightCm}cm` : ''], ['몸무게', form.weightKg ? `${form.weightKg}kg` : ''],
    ['에이전시 경험', form.agencyContracted == null ? '' : form.agencyContracted ? '있음' : '없음'],
    ['모델 경력', EXPERIENCE_OPTIONS.find(({ value }) => value === form.experienceLevel)?.label, '고르지 않음'],
    ['포트폴리오', links.portfolioUrl || ''], ['SNS', links.snsUrl || ''],
  ];

  return (
    <div className={s.page}>
      <div className={s.content}>
        <nav className={s.progress} aria-label="지원서 작성 단계">
          <p className={s.stepCount}>{step} / 3</p>
          <ol>{STEPS.map((label, index) => (
            <li key={label} className={index + 1 <= step ? s.progressReached : ''} aria-current={index + 1 === step ? 'step' : undefined}>
              <span className={s.track} />
              <span className={s.stepLabel}>{index + 1 < step && <Icon name="check" size={12} />}{label}{index + 1 < step && <span className="sr-only"> 완료</span>}</span>
            </li>
          ))}</ol>
        </nav>
        <h1 ref={heading} tabIndex={-1} className={s.heading}>{['기본 정보를 입력해요', '프로필을 알려 주세요', '지원서를 확인해요'][step - 1]}</h1>
        <p className={s.description}>{step === 3 ? '이름과 생년월일이 신분증과 같은지 한 번 더 봐주세요.' : <><span className={s.required}>*</span> 표시는 꼭 적어야 해요.</>}</p>

        {step === 1 && <div className={s.form}>
          <FormInput label="이름" required autoComplete="name" value={inputText(form.applicantName)} onChange={(e) => set('applicantName', e.target.value)}
            hint="신분증에 적힌 이름 그대로 적어 주세요. 승인 뒤 본인확인 때 신분증과 맞춰 봐요." messageId="applicant-name-hint" />
          <fieldset className={s.field}><legend className={s.label}>성별<span className={s.required}>*</span></legend>
            <div className={s.pills}>{[{ value: 'female', label: '여성' }, { value: 'male', label: '남성' }].map(({ value, label }) => (
              <button key={value} type="button" className={`${s.pill} ${form.gender === value ? s.pillOn : ''}`} aria-pressed={form.gender === value} onClick={() => set('gender', value)}>{label}</button>
            ))}</div>
          </fieldset>
          <fieldset className={s.field}><legend className={s.label}>생년월일<span className={s.required}>*</span></legend>
            <BirthdateInput value={inputText(form.birthdate)} onChange={(value) => set('birthdate', value)} />
          </fieldset>
          <FormInput label="전화번호" required type="tel" autoComplete="tel" placeholder="010-1234-5678" value={inputText(form.phone)} onChange={(e) => set('phone', formatPhone(e.target.value))} />
          <FormInput label="이메일" required type="email" autoComplete="email" spellCheck={false} value={inputText(form.contactEmail)} onChange={(e) => set('contactEmail', e.target.value)}
            onBlur={() => markTouched('contactEmail')} enterKeyHint="done" onKeyDown={lastFieldKeyDown}
            error={touched.contactEmail && contactEmail && !emailValid ? '이메일 주소를 name@example.com 형식으로 적어 주세요.' : ''}
            hint="지원 결과를 이 주소로 보내 드려요." messageId="contact-email-message" />
        </div>}

        {step === 2 && <div className={s.form}>
          <section aria-labelledby="application-photo-title" className={s.photoSection}>
            <h2 id="application-photo-title" className={s.label}>내 이미지<span className={s.required}>*</span></h2>
            <div className={s.photoGuidance}>
              {PHOTO_GUIDANCE.map((text) => <p key={text}><Icon name="check" size={16} /><span>{text}</span></p>)}
            </div>
          <div className={s.upload}>
            <ImageUpload
              className={s.applicationUploader}
              previewUrl={photo?.previewUrl}
              fileName={photo?.fileName}
              accept="image/png,image/jpeg,image/webp"
              uploading={uploading}
              title="사진 선택하기"
              description="휴대폰에서는 바로 찍어서 올릴 수도 있어요"
              busyLabel="사진을 올리고 있어요"
              onSelect={onPickPhoto}
              onRemove={removePhoto}
              onReject={() => setError('이 형식은 올릴 수 없어요. JPG, PNG, WEBP 사진으로 다시 골라 주세요.')}
              alt="내 이미지"
            />
          </div>
          </section>
          <div className={s.measurements}>{[
            { key: 'heightCm', label: '키', unit: 'cm', min: 100, max: 250, help: '100~250cm 사이로 적어 주세요.' },
            { key: 'weightKg', label: '몸무게', unit: 'kg', min: 30, max: 200, help: '30~200kg 사이로 적어 주세요.' },
          ].map(({ key, label, unit, min, max, help }) => {
            const invalid = Boolean(form[key] && !validInteger(form[key], min, max));
            return <FormInput key={key} label={label} required={key === 'heightCm'} optional={key === 'weightKg'} unit={unit} className={s.measurementField}
              inputMode="numeric" maxLength={3} value={form[key] ?? ''} onChange={(e) => set(key, e.target.value.replace(/\D/g, '').slice(0, 3))}
              onBlur={() => markTouched(key)} error={touched[key] && invalid ? help : ''} hint={`${min}~${max}${unit}`} messageId={`${key}-help`} />;
          })}</div>
          <fieldset className={s.field}><legend className={s.label}>모델 에이전시에 속해본 경험이 있나요?<span className={s.required}>*</span></legend><div className={s.pills}>{[{ value: true, label: '예' }, { value: false, label: '아니오' }].map(({ value, label }) => (
            <button key={label} type="button" className={`${s.pill} ${form.agencyContracted === value ? s.pillOn : ''}`} aria-pressed={form.agencyContracted === value} onClick={() => set('agencyContracted', value)}>{label}</button>
          ))}</div></fieldset>
          <fieldset className={s.field}><legend className={s.label}>모델 경력<span className={s.optional}> 선택</span></legend><div className={s.pills}>{EXPERIENCE_OPTIONS.map(({ value, label }) => (
            <button key={value} type="button" className={`${s.pill} ${form.experienceLevel === value ? s.pillOn : ''}`} aria-pressed={form.experienceLevel === value} onClick={() => set('experienceLevel', form.experienceLevel === value ? '' : value)}>{label}</button>
          ))}</div></fieldset>
          {[{ key: 'portfolioUrl', label: '포트폴리오 링크', example: 'https://portfolio.com' }, { key: 'snsUrl', label: 'SNS 링크', example: 'https://instagram.com/계정' }].map(({ key, label, example }) => (
            <FormInput key={key} label={label} optional placeholder={example} inputMode="url" spellCheck={false} value={form[key]} onChange={(e) => set(key, e.target.value)}
              onBlur={() => markTouched(key)} enterKeyHint={key === 'snsUrl' ? 'done' : undefined} onKeyDown={key === 'snsUrl' ? lastFieldKeyDown : undefined}
              error={touched[key] ? linkErrors[key] : ''} hint={key === 'snsUrl' ? '협찬 참여를 원하는 경우, 인스타 계정을 적어주세요.' : undefined} messageId={`${key}-message`} />
          ))}
        </div>}

        {step === 3 && <>
          {[{ title: '기본 정보', destination: 1, rows: basicRows }, { title: '프로필', destination: 2, rows: profileRows }].map(({ title, destination, rows }) => (
            <section className={s.summarySection} key={title}><div className={s.sectionHead}><h2>{title}</h2><button type="button" aria-label={`${title} 고치기`} disabled={busy} onClick={() => edit(destination)}>고치기</button></div>
              <dl className={s.summary}>
                {destination === 2 && <div><dt>내 이미지</dt><dd><img className={s.thumb} src={photo?.previewUrl} alt="내 이미지" /></dd></div>}
                {rows.map(([label, value, emptyText]) => <div key={label}><dt>{label}</dt><dd className={!value ? s.empty : ''}>{value || emptyText || '적지 않음'}</dd></div>)}
              </dl>
            </section>
          ))}
          <section className={s.summarySection}><div className={s.sectionHead}><h2>확인 사항</h2></div>
            <div className={s.attestations}>
              <label className={s.allAttestations}>
                <input ref={allAttestationsInput} id="attest-all" type="checkbox" checked={allAttestationsChecked} aria-checked={someAttestationsChecked ? 'mixed' : allAttestationsChecked} disabled={busy} onChange={(e) => setAllAttestations(e.target.checked)} />
                <span>전체 동의</span>
              </label>
              {ATTESTATIONS.map(({ key, text, detail }) => {
                const row = (
                  <label key={key} className={s.attestation}><input type="checkbox" checked={attest[key]} disabled={busy} onChange={(e) => setAttest((current) => ({ ...current, [key]: e.target.checked }))} />
                    <span><span className={s.requiredWord}>(필수)</span>{' '}{key === 'privacyPolicy' ? '개인정보 처리 약관에 대해 동의합니다.' : text}{detail && <span className={s.attestationDetail}>{detail}</span>}</span>
                  </label>
                );
                // 약관 링크를 체크 줄 밖에 두어 체크하려는 탭이 새 탭을 열지 않게 한다.
                return key === 'privacyPolicy'
                  ? <div key={key}>{row}<a className={s.attestationLink} href="/privacy" target="_blank" rel="noopener noreferrer">개인정보 처리 약관 전문 보기</a></div>
                  : row;
              })}
            </div>
          </section>
        </>}
      </div>
      <div className={s.bottomBar}>
        {error && <p className={s.error} role="alert">{error}{error.startsWith('이미') && <> <Link to="/status">지원 상태 보기</Link></>}</p>}
        {step < 3 && !uploading && (missingRequired.length > 0 || needsFix.length > 0) && <p className={s.remaining} aria-live="polite">{missingRequired.length ? `남은 필수 항목: ${missingRequired.join(', ')}` : `다시 확인할 칸: ${needsFix.join(', ')}`}</p>}
        {step === 3 && uncheckedCount > 0 && <p className={s.remaining} aria-live="polite"><button type="button" onClick={showAttestations}>{`아직 확인하지 않은 항목이 ${uncheckedCount}개 있어요.`}</button></p>}
        <div className={s.barInner}>
          <button className={s.previous} type="button" disabled={busy || uploading} onClick={goBack}>이전</button>
          <button className={s.primary} type="button" disabled={busy || (step === 1 ? !basicValid : step === 2 ? !profileValid : !canSubmit)} onClick={step === 3 ? submit : next}>{busy ? '보내는 중' : step === 3 ? '지원 완료' : '다음'}</button>
        </div>
      </div>
    </div>
  );
}

export default ModelApply;
