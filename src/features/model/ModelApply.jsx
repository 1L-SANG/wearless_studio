/* =============================================================
   features/model — 모델 지원서 (/model/apply)

   지원 → 관리자 검토 → 승인 → 신분증 인증 → 등록. 이 화면은 여정의 첫 관문이다.
   제출 전 프로필 사진은 임시 저장(스테이징)하고, 제출 시 서버가 지원서에 연결한다.
   재지원(거절 후)이면 이전 지원서 값으로 프리필한다(사진은 30일 내면 서버가 보존).
   설계: docs/designs/facemarket-application-renewal.md
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Chips, ErrorState, Field, Icon, useToast } from '@/components/ui.jsx';
import {
  getCurrentApplication, stageApplicationPhoto, submitApplication,
} from '@/lib/api/facemarket.js';
import s from './ModelPersonalization.module.css';

const PRIVACY_CONSENT_VERSION = '2026-09-v1';

const CATEGORY_OPTIONS = [
  { value: 'fashion', label: '패션' },
  { value: 'commercial', label: '커머셜' },
  { value: 'fitness', label: '피트니스' },
  { value: 'lifestyle', label: '라이프스타일' },
];
const GENDER_OPTIONS = [
  { value: 'female', label: '여성' },
  { value: 'male', label: '남성' },
];

const EMPTY = {
  contactEmail: '', applicantName: '', birthdate: '', region: '',
  gender: null, heightCm: '', agencyContracted: false, categories: [],
  portfolioUrl: '', snsUrl: '', bio: '',
};

// 거절/취소된 이전 지원서에서 프리필 가능한 필드(사진 제외 — 서버가 보존).
function prefillFrom(app) {
  if (!app) return EMPTY;
  return {
    contactEmail: app.contactEmail || '',
    applicantName: app.applicantName || '',
    birthdate: app.birthdate || '',
    region: app.region || '',
    gender: app.gender || null,
    heightCm: app.heightCm != null ? String(app.heightCm) : '',
    agencyContracted: !!app.agencyContracted,
    categories: Array.isArray(app.categories) ? app.categories : [],
    portfolioUrl: app.portfolioUrl || '',
    snsUrl: app.snsUrl || '',
    bio: app.bio || '',
  };
}

export function ModelApply() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading | ready | submitting
  const [form, setForm] = useState(EMPTY);
  const [photoName, setPhotoName] = useState('');
  const [photoStaged, setPhotoStaged] = useState(false);
  const [reapplyFromPhoto, setReapplyFromPhoto] = useState(false);
  const [privacyConsent, setPrivacyConsent] = useState(false);

  // 활성 지원서가 있으면 상태 허브로, 터미널(거절/취소)이면 프리필해 재지원.
  useEffect(() => {
    let alive = true;
    getCurrentApplication()
      .then((app) => {
        if (!alive) return;
        if (app && ['under_review', 'approved'].includes(app.status)) {
          navigate('/model', { replace: true });
          return;
        }
        if (app) {
          setForm(prefillFrom(app));
          if (app.hasProfileImage) { setReapplyFromPhoto(true); setPhotoStaged(true); }
        }
        setPhase('ready');
      })
      .catch((e) => {
        if (!alive) return;
        if (e?.status === 404) { setPhase('ready'); return; }
        push?.(e.message, { icon: 'alertCircle' });
        setPhase('ready');
      });
    return () => { alive = false; };
  }, [navigate, push]);

  const set = useCallback((k, v) => setForm((f) => ({ ...f, [k]: v })), []);

  const onPickPhoto = useCallback(async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      await stageApplicationPhoto({ fileBlob: file, filename: file.name });
      setPhotoStaged(true);
      setReapplyFromPhoto(false);
      setPhotoName(file.name);
    } catch (err) {
      push?.(err.message, { icon: 'alertCircle' });
    }
  }, [push]);

  const submit = useCallback(async () => {
    if (!privacyConsent) { push?.('개인정보 수집·이용 동의가 필요해요.', { icon: 'alertCircle' }); return; }
    if (!photoStaged) { push?.('프로필 사진을 업로드해 주세요.', { icon: 'alertCircle' }); return; }
    setPhase('submitting');
    try {
      await submitApplication({
        contactEmail: form.contactEmail.trim(),
        applicantName: form.applicantName.trim(),
        birthdate: form.birthdate,
        region: form.region.trim(),
        gender: form.gender || null,
        heightCm: form.heightCm ? Number(form.heightCm) : null,
        agencyContracted: form.agencyContracted,
        categories: form.categories,
        portfolioUrl: form.portfolioUrl.trim() || null,
        snsUrl: form.snsUrl.trim() || null,
        bio: form.bio.trim() || null,
        privacyConsent: { accepted: true, documentVersion: PRIVACY_CONSENT_VERSION },
      });
      push?.('지원서를 제출했어요. 관리자 검토를 기다려 주세요.', { icon: 'check' });
      navigate('/model', { replace: true });
    } catch (err) {
      push?.(err.message, { icon: 'alertCircle' });
      setPhase('ready');
    }
  }, [form, photoStaged, privacyConsent, navigate, push]);

  if (phase === 'loading') {
    return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  }

  const canSubmit = form.contactEmail && form.applicantName && form.birthdate
    && form.region && form.categories.length > 0 && photoStaged && privacyConsent;

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>모델 지원서</h1>
        <p>지원서를 제출하면 관리자 검토 후 승인된 분만 모델 등록을 진행할 수 있어요.</p>
      </div>

      <div className="surface">
        <div className={s.sectionLabel}>프로필 사진</div>
        <label className="field-row" style={{ cursor: 'pointer' }}>
          <span className="lbl">사진 1장 {photoStaged && <span className="opt">업로드됨</span>}</span>
          <input type="file" accept="image/png,image/jpeg,image/webp" onChange={onPickPhoto} />
          {reapplyFromPhoto && <span className="hint">이전 지원서 사진이 유지돼요. 바꾸려면 새로 올려주세요.</span>}
          {photoName && <span className="hint">{photoName}</span>}
        </label>
      </div>

      <div className="surface">
        <div className={s.sectionLabel}>기본 정보</div>
        <Field label="이메일" type="email" value={form.contactEmail}
          onChange={(e) => set('contactEmail', e.target.value)} placeholder="승인·거절 안내를 받을 이메일" />
        <Field label="이름" value={form.applicantName}
          onChange={(e) => set('applicantName', e.target.value)} placeholder="신분증과 동일하게" />
        <Field label="생년월일" type="date" value={form.birthdate}
          onChange={(e) => set('birthdate', e.target.value)} />
        <Field label="지역" value={form.region}
          onChange={(e) => set('region', e.target.value)} placeholder="예: 서울" />
        <label className="lbl">성별 <span className="opt">선택</span></label>
        <Chips options={GENDER_OPTIONS} value={form.gender} onChange={(v) => set('gender', v)} />
        <Field label="키(cm)" opt="선택" type="number" value={form.heightCm}
          onChange={(e) => set('heightCm', e.target.value)} placeholder="예: 175" />
      </div>

      <div className="surface">
        <div className={s.sectionLabel}>모델 활동</div>
        <label className="lbl">활동하고 싶은 카테고리</label>
        <Chips multi options={CATEGORY_OPTIONS} value={form.categories}
          onChange={(v) => set('categories', v)} />
        <label className="field-row" style={{ marginTop: 12 }}>
          <span className="lbl">에이전시 계약 여부</span>
          <Chips options={[{ value: 'yes', label: '계약함' }, { value: 'no', label: '계약 안 함' }]}
            value={form.agencyContracted ? 'yes' : 'no'}
            onChange={(v) => set('agencyContracted', v === 'yes')} allowDeselect={false} />
        </label>
        <Field label="포트폴리오 링크" opt="선택" value={form.portfolioUrl}
          onChange={(e) => set('portfolioUrl', e.target.value)} placeholder="https://" />
        <Field label="SNS 링크" opt="선택" value={form.snsUrl}
          onChange={(e) => set('snsUrl', e.target.value)} placeholder="https://" />
        <label className="lbl">자기소개 <span className="opt">선택</span></label>
        <textarea className="field" rows={4} value={form.bio}
          onChange={(e) => set('bio', e.target.value)} placeholder="간단한 소개를 남겨주세요." />
      </div>

      <div className="surface">
        <label className={s.consentRow} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', cursor: 'pointer' }}>
          <input type="checkbox" checked={privacyConsent} onChange={(e) => setPrivacyConsent(e.target.checked)} />
          <span className="hint">
            개인정보(이름·생년월일·연락처·사진) 수집·이용에 동의해요. 거절·취소 시 30일 후 익명화되고,
            승인되면 모델 운영 정보로 보관돼요.
          </span>
        </label>
      </div>

      <div style={{ marginTop: 16 }}>
        <Button variant="primary" block iconRight="arrowRight"
          disabled={!canSubmit || phase === 'submitting'} onClick={submit}>
          {phase === 'submitting' ? '제출 중…' : '지원서 제출하기'}
        </Button>
      </div>
    </div>
  );
}

export default ModelApply;
