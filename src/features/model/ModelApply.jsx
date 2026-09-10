import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Icon, useToast } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { getCurrentApplication, stageApplicationPhoto, submitApplication } from '@/lib/api/facemarket.js';
import { REVIEW_SLA_LABEL } from '../facemarket-landing/facemarketTerms.js';
import { nextBirthdateSegments, backspaceBirthdateSegments, birthdateFromSegments, isAdultBirthdate } from './birthdateInput.js';
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
  { key: 'noAgencyContract', text: '현재 어떤 모델 에이전시와도 계약, 소속되어 있지 않은 상태임을 확인합니다.', detail: '기존 계약이 존재할 경우 facemarket 모델 리스트에서 제외될 수 있습니다.' },
  { key: 'reviewOnlyUse', text: '지원자를 facemarket에 등록할 지 판단하는 여부로만 제출한 내용들이 사용됩니다.', detail: '이외의 목적으로는 일체 사용하지 않습니다.' },
  { key: 'privacyPolicy' },
];
const EMPTY = {
  contactEmail: '', applicantName: '', gender: '', birthdate: '', phone: '',
  heightCm: '', weightKg: '', experienceLevel: '', agencyContracted: null,
  portfolioUrl: '', snsUrl: '',
};
const PHOTO_GUIDANCE = [
  '얼굴이 잘 보이는 사진으로 업로드해주세요.',
  '정면 가까이서 찍고, 필터없는 이미지를 업로드해주세요.',
  '지원서 확인용으로만 사용되며, 공개 프로필에 올라가지 않습니다.',
];

function optionalLink(value) {
  const link = value.trim();
  if (!link) return null;
  return /^[a-z][a-z\d+.-]*:/i.test(link) ? link : `https://${link}`;
}

function linkError(value) {
  const link = optionalLink(value);
  if (!link) return '';
  if (link.length > 500) return '링크는 https://를 포함해 500자까지 입력할 수 있어요. 더 짧은 공유 주소를 입력해 주세요.';
  if (!/^https?:\/\//.test(link)) return 'http:// 또는 https://로 시작하는 웹 주소를 입력해 주세요.';
  return '';
}

function formatPhone(input) {
  const digits = input.replace(/\D/g, '').slice(0, 13);
  const prefixLength = digits.startsWith('02') ? 2 : 3;
  if (digits.length <= prefixLength) return digits;
  if (digits.length <= prefixLength + 4) return `${digits.slice(0, prefixLength)}-${digits.slice(prefixLength)}`;
  const middleEnd = digits.length <= prefixLength + 7 ? prefixLength + 3 : prefixLength + 4;
  return `${digits.slice(0, prefixLength)}-${digits.slice(prefixLength, middleEnd)}-${digits.slice(middleEnd)}`;
}
const validInteger = (value, min, max) => /^\d+$/.test(String(value)) && Number(value) >= min && Number(value) <= max;

function BirthdateInput({ value, onChange }) {
  const [segments, setSegments] = useState(() => value ? value.split('-') : ['', '', '']);
  const inputs = useRef([]);
  const apply = (result) => {
    setSegments(result.segments);
    onChange(birthdateFromSegments(result.segments));
    inputs.current[result.focusIndex]?.focus();
  };
  return (
    <div className={s.birthdate}>
      {['년', '월', '일'].map((label, index) => (
        <div className={s.birthSegment} key={label}>
          {index > 0 && <span className={s.birthSeparator} aria-hidden="true">.</span>}
          <input ref={(el) => { inputs.current[index] = el; }} className={`${s.input} ${index === 0 ? s.birthYear : s.birthPart}`}
            aria-label={`생년월일 ${label}`} required inputMode="numeric" autoComplete={['bday-year', 'bday-month', 'bday-day'][index]}
            placeholder={index === 0 ? '2000' : '01'} value={segments[index]}
            onChange={(event) => apply(nextBirthdateSegments(segments, index, event.target.value))}
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
        </div>
      ))}
    </div>
  );
}

export function ModelApply() {
  const navigate = useNavigate();
  const { push } = useToast();
  const { session } = useAuth();
  const accountEmail = session?.user?.email || '';
  const [phase, setPhase] = useState('loading');
  const [form, setForm] = useState(EMPTY);
  const [photo, setPhoto] = useState(null);
  const [attest, setAttest] = useState(() => Object.fromEntries(ATTESTATIONS.map(({ key }) => [key, false])));
  const [step, setStep] = useState(1);
  const [editing, setEditing] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const fileInput = useRef(null);
  const heading = useRef(null);
  const mounted = useRef(true);
  const submitting = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    getCurrentApplication().then((app) => {
      if (!alive) return;
      if (app && ['under_review', 'approved'].includes(app.status)) {
        navigate('/status', { replace: true });
        return;
      }
      // 재지원은 보존 중인 입력값만 복원한다. 사진과 체크사항은 새로 받는다.
      const previous = app && !app.piiPurgedAt ? app : {};
      setForm(Object.fromEntries(Object.entries(EMPTY).map(([key, fallback]) => [
        key, key === 'contactEmail' ? (previous.contactEmail || accountEmail) : (previous[key] ?? fallback),
      ])));
      setPhase('ready');
    }).catch((err) => {
      if (!alive) return;
      if (err?.status !== 404) push?.(err.message, { icon: 'alertCircle' });
      setForm({ ...EMPTY, contactEmail: accountEmail });
      setPhase('ready');
    });
    return () => { alive = false; };
  }, [accountEmail, navigate, push]);

  useEffect(() => () => {
    if (photo?.previewUrl) URL.revokeObjectURL(photo.previewUrl);
  }, [photo]);

  useEffect(() => {
    if (phase === 'loading' || phase === 'submitting') return;
    globalThis.window?.scrollTo?.(0, 0);
    heading.current?.focus({ preventScroll: true });
  }, [step, phase]);

  const set = useCallback((key, value) => setForm((current) => ({ ...current, [key]: value })), []);
  const onPickPhoto = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file || uploading) return;
    setUploading(true);
    setError('');
    try {
      await stageApplicationPhoto({ kind: 'profile', fileBlob: file, filename: file.name });
      if (mounted.current) setPhoto({ staged: true, previewUrl: URL.createObjectURL(file) });
    } catch (err) {
      if (mounted.current) setError(err.message || '사진을 올리지 못했어요. 다시 시도해주세요.');
    } finally {
      if (mounted.current) setUploading(false);
    }
  };

  const contactEmail = form.contactEmail.trim();
  const emailValid = contactEmail.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contactEmail);
  const linkErrors = { portfolioUrl: linkError(form.portfolioUrl), snsUrl: linkError(form.snsUrl) };
  const basicValid = Boolean(form.applicantName.trim() && ['female', 'male'].includes(form.gender)
    && isAdultBirthdate(form.birthdate) && /^[\d-]+$/.test(form.phone)
    && /^\d{9,13}$/.test(form.phone.replace(/-/g, '')) && emailValid);
  const profileValid = Boolean(photo?.staged && !uploading && validInteger(form.heightCm, 100, 250)
    && (form.weightKg === '' || form.weightKg == null || validInteger(form.weightKg, 30, 200))
    && typeof form.agencyContracted === 'boolean' && !linkErrors.portfolioUrl && !linkErrors.snsUrl);
  const uncheckedCount = ATTESTATIONS.filter(({ key }) => !attest[key]).length;
  const busy = phase === 'submitting';
  const canSubmit = basicValid && profileValid && uncheckedCount === 0;

  const submit = async () => {
    if (!canSubmit || submitting.current) return;
    submitting.current = true;
    setPhase('submitting');
    setError('');
    try {
      await submitApplication({
        contactEmail,
        applicantName: form.applicantName.trim(),
        gender: form.gender,
        birthdate: form.birthdate,
        phone: form.phone,
        heightCm: Number(form.heightCm),
        weightKg: form.weightKg === '' || form.weightKg == null ? null : Number(form.weightKg),
        experienceLevel: form.experienceLevel || null,
        agencyContracted: form.agencyContracted,
        portfolioUrl: optionalLink(form.portfolioUrl),
        snsUrl: optionalLink(form.snsUrl),
        attestations: attest,
        privacyConsent: { accepted: true, documentVersion: PRIVACY_CONSENT_VERSION },
      });
      if (mounted.current) setPhase('complete');
    } catch (err) {
      if (mounted.current) {
        if (err.status === 409) {
          setError('이미 검토 중인 지원서가 있어요. 상태 화면에서 확인해 주세요.');
        } else if (err.status >= 400 && err.status < 500) {
          setError(err.message || '입력한 내용을 확인하고 다시 보내 주세요.');
          const correctionStep = {
            invalid_email: 1, invalid_gender: 1, invalid_phone: 1,
            invalid_url: 2, invalid_height: 2, invalid_weight: 2, invalid_experience: 2,
          }[err.code];
          if (correctionStep) {
            setStep(correctionStep);
            setEditing(correctionStep);
          }
        } else {
          setError(err.status
            ? '지금은 지원서를 처리하지 못했어요. 잠시 후 다시 눌러 주세요.'
            : '서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.');
        }
        setPhase('ready');
      }
    } finally { submitting.current = false; }
  };
  const goTo = (destination) => { setError(''); setStep(destination); };
  const edit = (destination) => { setEditing(destination); goTo(destination); };
  const next = () => {
    if ((step === 1 && !basicValid) || (step === 2 && !profileValid)) return;
    goTo(editing ? 3 : step + 1);
    setEditing(null);
  };

  if (phase === 'loading') return <div className={s.page}><p className={s.loading}>불러오는 중…</p></div>;
  if (phase === 'complete') return (
    <section className={s.completePage}>
      <div className={s.completeMark}><Icon name="check" size={32} stroke={1.5} /></div>
      <h1 ref={heading} tabIndex={-1}>지원서가 접수 완료됐어요</h1>
      <p>{REVIEW_SLA_LABEL} 결과를 이메일로 알려드려요.<br />상단의 'Digital DNA 관리' 탭에서도 실시간 상태를 확인 가능해요</p>
      <Link className={s.completeCta} to="/status">지원 상태 보기</Link>
    </section>
  );

  const basicRows = [
    ['이름', form.applicantName], ['성별', form.gender === 'female' ? '여성' : '남성'],
    ['생년월일', form.birthdate.replace(/^(\d{4})-(\d{2})-(\d{2})$/, (_, year, month, day) => `${year}년 ${Number(month)}월 ${Number(day)}일`)], ['전화번호', form.phone], ['이메일', contactEmail],
  ];
  const profileRows = [
    ['키', form.heightCm ? `${form.heightCm}cm` : ''], ['몸무게', form.weightKg ? `${form.weightKg}kg` : ''],
    ['경력', EXPERIENCE_OPTIONS.find(({ value }) => value === form.experienceLevel)?.label],
    ['에이전시 경험', form.agencyContracted == null ? '' : form.agencyContracted ? '있음' : '없음'],
    ['포트폴리오', form.portfolioUrl], ['SNS', form.snsUrl],
  ];

  return (
    <div className={s.page}>
      <div className={s.content}>
        <nav className={s.progress} aria-label="지원서 작성 단계">
          <p className={s.stepCount}>{step} / 3</p>
          <ol>{STEPS.map((label, index) => (
            <li key={label} className={index + 1 <= step ? s.progressReached : ''} aria-current={index + 1 === step ? 'step' : undefined}>
              <span className={s.track} />
              <span className={s.stepLabel}>{index + 1 < step && <Icon name="check" size={12} />}{label}</span>
            </li>
          ))}</ol>
        </nav>
        <h1 ref={heading} tabIndex={-1} className={s.heading}>{['기본적인 정보들을 알려주세요.', '추가 정보들까지 알려주세요.', '보내기 전에 확인해주세요.'][step - 1]}</h1>
        <p className={s.description}>{step === 3 ? '이름과 생년월일이 신분증과 같은지 한 번 더 봐주세요.' : <><span className={s.required}>*</span>표시는 필수 입력 항목입니다.</>}</p>

        {step === 1 && <div className={s.form}>
          <label className={`${s.field} ${s.nameField}`}><span className={s.label}>이름<span className={s.required}>*</span></span>
            <input className={s.input} autoComplete="name" required value={form.applicantName} onChange={(e) => set('applicantName', e.target.value)} />
          </label>
          <fieldset className={s.field}><legend className={s.label}>성별<span className={s.required}>*</span></legend>
            <div className={s.pills}>{[{ value: 'female', label: '여성' }, { value: 'male', label: '남성' }].map(({ value, label }) => (
              <button key={value} type="button" className={`${s.pill} ${form.gender === value ? s.pillOn : ''}`} aria-pressed={form.gender === value} onClick={() => set('gender', value)}>{label}</button>
            ))}</div>
          </fieldset>
          <fieldset className={s.field}><legend className={s.label}>생년월일<span className={s.required}>*</span></legend>
            <BirthdateInput value={form.birthdate} onChange={(value) => set('birthdate', value)} />
            <p className={s.hint}>만 19세 이상만 지원할 수 있어요.</p>
          </fieldset>
          <label className={`${s.field} ${s.phoneField}`}><span className={s.label}>전화번호<span className={s.required}>*</span></span>
            <input className={s.input} type="tel" required autoComplete="tel" placeholder="010-0000-0000" value={form.phone} onChange={(e) => set('phone', formatPhone(e.target.value))} />
          </label>
          <label className={`${s.field} ${s.emailField}`}><span className={s.label}>이메일<span className={s.required}>*</span></span>
            <input className={s.input} type="email" required autoComplete="email" spellCheck={false} value={form.contactEmail} onChange={(e) => set('contactEmail', e.target.value)} aria-invalid={Boolean(contactEmail && !emailValid)} aria-describedby={contactEmail && !emailValid ? 'contact-email-error' : undefined} />
            {contactEmail && !emailValid && <span id="contact-email-error" className={`${s.hint} ${s.error}`} role="alert">이메일을 254자 이내의 name@example.com 형식으로 입력해 주세요.</span>}
          </label>
        </div>}

        {step === 2 && <div className={s.form}>
          <div className={s.upload}>
            <div className={s.photoControl}>
              <button type="button" className={s.photoSlot} disabled={uploading} onClick={() => fileInput.current?.click()} aria-label={photo ? '다른 사진으로 바꾸기' : '사진 올리기'}>
                {photo?.previewUrl ? <img src={photo.previewUrl} alt="지원서 프로필 사진" /> : <>
                  <svg className={s.silhouette} viewBox="0 0 120 140" aria-hidden="true"><ellipse cx="60" cy="48" rx="26" ry="32" /><path d="M16 134c4-28 22-42 44-42s40 14 44 42" /></svg>
                  <span>{uploading ? '올리는 중' : '사진 올리기'}</span>
                </>}
              </button>
              {photo && <button type="button" className={s.changePhoto} disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? '올리는 중' : '다른 사진으로 바꾸기'}</button>}
              <input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={onPickPhoto} />
            </div>
            <div className={s.photoGuidance}><h2 className={s.label}>프로필 이미지 업로드<span className={s.required}>*</span></h2>
              {PHOTO_GUIDANCE.map((text) => <p key={text}><Icon name="check" size={16} /><span>{text}</span></p>)}
            </div>
          </div>
          <div className={s.measurements}>{[{ key: 'heightCm', label: '키', unit: 'cm', min: 100, max: 250 }, { key: 'weightKg', label: '몸무게', unit: 'kg', min: 30, max: 200 }].map(({ key, label, unit, min, max }) => (
            <label key={key} className={s.field}><span className={s.label}>{label}{key === 'heightCm' && <span className={s.required}>*</span>}</span>
              <span className={s.suffix}><input className={s.input} inputMode="numeric" maxLength={3} required={key === 'heightCm'} aria-invalid={Boolean(form[key] && !validInteger(form[key], min, max))} value={form[key] ?? ''} onChange={(e) => set(key, e.target.value.replace(/\D/g, '').slice(0, 3))} /><span>{unit}</span></span>
            </label>
          ))}</div>
          <fieldset className={s.field}><legend className={s.label}>경력</legend><div className={s.pills}>{EXPERIENCE_OPTIONS.map(({ value, label }) => (
            <button key={value} type="button" className={`${s.pill} ${form.experienceLevel === value ? s.pillOn : ''}`} aria-pressed={form.experienceLevel === value} onClick={() => set('experienceLevel', form.experienceLevel === value ? '' : value)}>{label}</button>
          ))}</div></fieldset>
          <fieldset className={s.field}><legend className={s.label}>모델 에이전시에 속해본 경험이 있나요?<span className={s.required}>*</span></legend><div className={s.pills}>{[{ value: true, label: '예' }, { value: false, label: '아니오' }].map(({ value, label }) => (
            <button key={label} type="button" className={`${s.pill} ${form.agencyContracted === value ? s.pillOn : ''}`} aria-pressed={form.agencyContracted === value} onClick={() => set('agencyContracted', value)}>{label}</button>
          ))}</div></fieldset>
          {[{ key: 'portfolioUrl', label: '포트폴리오 링크', placeholder: 'www.portfolio.com' }, { key: 'snsUrl', label: 'SNS 링크', placeholder: 'www.instagram.com/example' }].map(({ key, label, placeholder }) => (
            <label key={key} className={`${s.field} ${s.linkField}`}><span className={s.label}>{label}</span>
              <input className={s.input} inputMode="url" spellCheck={false} placeholder={placeholder} value={form[key]} onChange={(e) => set(key, e.target.value)} aria-invalid={Boolean(linkErrors[key])} aria-describedby={linkErrors[key] ? `${key}-error` : undefined} />
              {linkErrors[key] && <span id={`${key}-error`} className={`${s.hint} ${s.error}`} role="alert">{linkErrors[key]}</span>}
            </label>
          ))}
        </div>}

        {step === 3 && <>
          {[{ title: '기본 정보', destination: 1, rows: basicRows }, { title: '프로필', destination: 2, rows: profileRows }].map(({ title, destination, rows }) => (
            <section className={s.summarySection} key={title}><div className={s.sectionHead}><h2>{title}</h2><button type="button" aria-label={`${title} 고치기`} disabled={busy} onClick={() => edit(destination)}>고치기</button></div>
              <dl className={s.summary}>
                {destination === 2 && <div><dt>사진</dt><dd><img className={s.thumb} src={photo?.previewUrl} alt="지원서 프로필 사진" /></dd></div>}
                {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className={!value ? s.empty : ''}>{value || '적지 않음'}</dd></div>)}
              </dl>
            </section>
          ))}
          <section className={s.summarySection}><div className={s.sectionHead}><h2>체크사항</h2></div>
            <div className={s.attestations}>{ATTESTATIONS.map(({ key, text, detail }) => (
              <label key={key} className={s.attestation}><input type="checkbox" checked={attest[key]} disabled={busy} onChange={(e) => setAttest((current) => ({ ...current, [key]: e.target.checked }))} />
                <span><span className={s.requiredWord}>(필수)</span>{key === 'privacyPolicy' ? <><a href="/privacy" target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>개인정보 처리 약관</a>에 대해 동의합니다.</> : text}{detail && <><br />{detail}</>}</span>
              </label>
            ))}</div>
          </section>
        </>}
      </div>
      <div className={s.bottomBar}>
        {error && <p className={s.error} role="alert">{error}{error.startsWith('이미') && <> <Link to="/status">지원 상태 보기</Link></>}</p>}
        {step === 3 && uncheckedCount > 0 && <p className={s.remaining} aria-live="polite">{`아직 표시하지 않은 체크사항이 ${uncheckedCount}개 있어요.`}</p>}
        <div className={s.barInner}>
          <button className={s.previous} type="button" disabled={busy || uploading} onClick={() => { setEditing(null); if (step === 1) navigate('/apply'); else goTo(step - 1); }}>이전</button>
          <button className={s.primary} type="button" disabled={busy || (step === 1 ? !basicValid : step === 2 ? !profileValid : !canSubmit)} onClick={step === 3 ? submit : next}>{busy ? '보내는 중' : step === 3 ? '지원 완료' : '다음'}</button>
        </div>
      </div>
    </div>
  );
}

export default ModelApply;
