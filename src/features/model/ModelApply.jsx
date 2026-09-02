/* =============================================================
   features/model — 모델 지원서 (/model/apply)

   구조(MirrorMirror 지원 페이지에서 가져옴), 외형은 facemarket 랜딩·ModelHub 언어:
     이용 원칙(5카드) → 지원 자격(필수/우대) → 준비물(3카드) → 지원서 폼 → 프로필 사진 1장 →
     확인 2개 → 개인정보 고지(끝까지 스크롤해야 동의 가능) → 제출 → FAQ 아코디언.
   에이전시 관련 문항·확인·FAQ 는 두지 않는다(2026-09-02 사용자 결정). 키는 승인 뒤 프로필
   단계(컴카드)에서 받으므로 지원서에는 없다. 입력 안 힌트(placeholder)도 두지 않는다.
   지원 → 관리자 검토 → 승인 → 신분증 인증 → 등록. 제출 전 사진은 종류별로 임시 저장하고
   제출 시 서버가 지원서에 연결한다. 재지원이면 이전 값·사진(30일 내)으로 프리필.
   설계: docs/designs/facemarket-application-renewal.md
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useToast } from '@/components/ui.jsx';
import {
  getCurrentApplication, stageApplicationPhoto, submitApplication,
} from '@/lib/api/facemarket.js';
import s from './ModelApply.module.css';

const PRIVACY_CONSENT_VERSION = '2026-09-v1';

/* ── 정적 카피 ─────────────────────────────────────────────────────────────── */

const HOUSE_RULES = [
  { title: '성인물 생성 금지', desc: '얼굴이 성적이거나 성적으로 암시적인 콘텐츠 생성에 쓰이지 않아요. 누구도, 언제라도.' },
  { title: '정치적 설득 금지', desc: '선거 캠페인 콘텐츠, 후보 지지, 조작된 정치적 발언에 쓰이지 않아요.' },
  { title: '허위 보증 금지', desc: '하지 않은 말·사용하지 않은 제품·지지하지 않은 것을 했다고 주장하는 데 쓰이지 않아요.' },
  { title: '종교·혐오 콘텐츠 금지', desc: '종교적 지지, 혐오 콘텐츠, 괴롭힘, 오해를 부르는 추천에 쓰이지 않아요.' },
  { title: '대가 없는 사용 없음', desc: '모든 상업적 사용은 건마다 정산돼요. 항상, 매번, 당신에게.', dark: true },
];

const QUAL_REQUIRED = [
  '만 18세 이상',
  '제출하는 사진의 권리를 본인이 보유할 것 (동의 없는 스튜디오 저작물 불가)',
  'AI 생성 방식의 초상 활용에 동의할 것',
  '정확한 신체 치수 제공 (키·가슴·허리·엉덩이)',
  '본인확인을 위한 유효한 신분증(모바일 신분증)',
  '보정·AI 생성이 아닌 실제 사진 제출',
];
const QUAL_PREFERRED = [
  '모델 경력 (에디토리얼·커머셜·런웨이 등)',
  '포트폴리오 또는 컴카드',
  '활발한 SNS 활동',
  '브랜드 파트너 콘텐츠 참여 의향',
];

const NEED_ITEMS = [
  { n: '01', title: '프로필 사진', desc: '자연광, 최소한의 메이크업, 머리를 넘긴 정면 사진 1장. 필터·보정 없이. 폰 화질이면 충분해요 — 있는 그대로를 보고 싶어요.' },
  { n: '02', title: '기본 정보', desc: '이름·생년월일·지역, 경력 수준, 관심 있는 모델 카테고리. 그리고 당신이 누구인지 알려주는 짧은 소개.' },
  { n: '03', title: '포트폴리오·SNS (선택)', desc: '활동을 보여줄 수 있는 포트폴리오나 SNS 링크가 있으면 검토에 도움이 돼요. 없어도 지원할 수 있어요.' },
];

const CATEGORY_OPTIONS = [
  { value: 'fashion', label: '패션' },
  { value: 'commercial', label: '커머셜' },
  { value: 'fitness', label: '피트니스' },
  { value: 'lifestyle', label: '라이프스타일' },
];
const EXPERIENCE_OPTIONS = [
  { value: 'none', label: '경력 없음' },
  { value: 'beginner', label: '입문 (1년 미만)' },
  { value: 'intermediate', label: '중급 (1~3년)' },
  { value: 'professional', label: '전문 (3년 이상)' },
];

// 지원 사진은 프로필 1장(2026-09-02 사용자 결정). 백엔드는 종류별 슬롯을 지원하지만 요구는 프로필만.
const PHOTO_SLOTS = [
  { kind: 'profile', label: '프로필 사진' },
];

const ATTESTATIONS = [
  { key: 'adultAndTruthful', text: '만 18세 이상이며, 제공한 모든 정보가 사실이고 정확함을 확인합니다.' },
  { key: 'photosAreMine', text: '이 사진은 본인의 것이며, 최신 상태이고 변형되지 않았으며, 제출할 권리가 있음을 확인합니다.' },
];

const PRIVACY_NOTICE = [
  { h: null, p: '지원하기 전에, 제출한 내용이 어떻게 처리되는지 알려드립니다.' },
  { h: '우리가 그것을 사용하는 이유', p: '저희는 귀하의 정보와 사진을 검토하여 FaceMarket 모델 스튜디오에 초대할지 여부를 결정합니다. 이 자료들은 상업적으로 사용되거나, 어떤 브랜드나 고객과 공유되거나, 현재 단계에서 스튜디오에 등록되지 않습니다.' },
  { h: '당신의 사진들', p: '업로드하는 사진은 본인의 것이어야 하며, 최신 상태이고 원본이 변형되지 않아야 합니다. 우리는 오직 귀하의 신청서를 평가하기 위해서만 이들을 사용합니다.' },
  { h: '승인되지 않았다면', p: '신청이 승인되지 않으면 30일 이내에 정보와 사진을 삭제합니다. 우리는 검토를 완료하는 데 필요한 것 외에는 아무것도 보관하지 않습니다.' },
  { h: '승인되었다면', p: '모델 등록을 시작할 수 있는 개인 링크가 포함된 이메일을 받게 됩니다. 그 단계는 이 지원서와 별개입니다. 그때 본인이 직접 생체 정보 처리에 동의해야 하며, 이는 본인만 할 수 있고 기관이나 대리인이 대신할 수 없습니다.' },
];

const FAQ = [
  { q: 'AI 생성 방식의 초상 활용이란 실제로 무엇을 뜻하나요?', a: '브랜드가 촬영 없이 당신의 얼굴로 상품 착용컷·캠페인 이미지를 생성해요. 어떤 품목에, 건당 얼마로, 얼마 동안 쓸 수 있는지는 당신이 라이선스 조건으로 직접 정하고, 그 조건 밖의 사용은 막혀요.' },
  { q: '초상 라이선싱이란 무엇인가요?', a: '당신의 얼굴을 쓰는 조건(용도·단가·기간)을 정해 두고, 브랜드가 그 조건 안에서만 쓰게 하는 계약이에요. 조건은 누구나 확인할 수 있는 라이선스로 남고, 언제든 철회할 수 있어요.' },
  { q: '승인 절차는 어떻게 진행되나요?', a: '지원서 제출 → 관리자 검토(사진·정보) → 승인/거절 안내(이메일 + 이 화면) → 승인되면 모바일 신분증으로 본인확인 → 얼굴 등록 → 라이선스 조건 설정 순서예요. 지원서의 이름·생년월일은 신분증과 대조돼요.' },
  { q: '보상은 어떻게 이루어지나요?', a: '상업적 사용이 발생할 때마다 당신이 정한 단가로 정산돼요. 대가 없는 사용은 없어요.' },
  { q: '해외 거주자도 지원할 수 있나요?', a: '본인확인에 모바일 신분증(한국)이 필요해 현재는 국내 신분증 보유자만 등록을 마칠 수 있어요.' },
  { q: '지원서에 어떤 사진이 필요한가요?', a: '정면 헤드샷 프로필 사진 1장이에요. 필터·보정·AI 생성 사진은 안 돼요. 폰 화질이면 충분해요. 얼굴 등록용 각도별 사진은 승인 후 등록 단계에서 따로 받아요.' },
  { q: '모델 경력이 없어도 지원할 수 있나요?', a: '네. 경력은 우대 사항이지 필수가 아니에요. 사진과 기본 정보로 검토해요.' },
];

const EMPTY = {
  contactEmail: '', lastName: '', firstName: '', phone: '', birthdate: '', region: '',
  gender: null, experienceLevel: '', categories: [],
  portfolioUrl: '', snsUrl: '', bio: '',
};

/* 이름은 성·이름 두 칸으로 받고 서버에는 한 문자열(applicant_name)로 보낸다 — 신분증 대조
   (cx_identity.compare_identity_claim)가 공백을 전부 지우고 비교하므로 한글은 붙여 쓰고
   라틴은 공백으로 잇는다("KIM MINSU" == "KimMinsu"). 프리필은 역으로 나눈다: 공백이 있으면
   첫 공백, 한글만이면 첫 글자를 성으로(두 글자 성은 사용자가 고친다). */
const HANGUL_ONLY = /^[\u3131-\u318E\uAC00-\uD7A3]+$/;
function joinName(last, first) {
  const l = last.trim(); const f = first.trim();
  return HANGUL_ONLY.test(l) && HANGUL_ONLY.test(f) ? `${l}${f}` : `${l} ${f}`.trim();
}
function splitName(name) {
  const n = (name || '').trim();
  if (!n) return ['', ''];
  const i = n.indexOf(' ');
  if (i > 0) return [n.slice(0, i), n.slice(i + 1).trim()];
  if (HANGUL_ONLY.test(n) && n.length >= 2) return [n[0], n.slice(1)];
  return [n, ''];
}

function prefillFrom(app) {
  if (!app) return EMPTY;
  const [lastName, firstName] = splitName(app.applicantName);
  return {
    contactEmail: app.contactEmail || '',
    lastName,
    firstName,
    phone: app.phone || '',
    birthdate: app.birthdate || '',
    region: app.region || '',
    gender: app.gender || null,
    experienceLevel: app.experienceLevel || '',
    categories: Array.isArray(app.categories) ? app.categories : [],
    portfolioUrl: app.portfolioUrl || '',
    snsUrl: app.snsUrl || '',
    bio: app.bio || '',
  };
}

/* ── 실루엣(사진 슬롯 플레이스홀더) ────────────────────────────────────────── */
function Silhouette({ kind }) {
  const c = '#cfd2d6';
  if (kind === 'profile') {
    return (
      <svg viewBox="0 0 120 140" className={s.silhouette} aria-hidden="true">
        <ellipse cx="60" cy="52" rx="30" ry="36" fill={c} />
        <path d="M14 140c4-34 24-50 46-50s42 16 46 50z" fill={c} />
      </svg>
    );
  }
  if (kind === 'closeup') {
    return (
      <svg viewBox="0 0 120 140" className={s.silhouette} aria-hidden="true">
        <path d="M40 22c22-12 46 2 48 30 1 14-4 24-10 32l6 10c-14 6-32 6-46 0l4-10C30 74 24 60 26 44c1-10 6-18 14-22z" fill={c} />
        <path d="M18 140c6-30 24-44 42-44s36 14 42 44z" fill={c} />
      </svg>
    );
  }
  if (kind === 'waist_up') {
    return (
      <svg viewBox="0 0 120 140" className={s.silhouette} aria-hidden="true">
        <ellipse cx="60" cy="26" rx="16" ry="20" fill={c} />
        <path d="M30 140V80c0-20 12-32 30-32s30 12 30 32v60z" fill={c} />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 120 140" className={s.silhouette} aria-hidden="true">
      <ellipse cx="60" cy="16" rx="10" ry="12" fill={c} />
      <path d="M44 34h32l8 44-10 2v60h-10V88h-8v52H46V80l-10-2z" fill={c} />
    </svg>
  );
}

/* ── 페이지 ────────────────────────────────────────────────────────────────── */
export function ModelApply() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading | ready | submitting
  const [form, setForm] = useState(EMPTY);
  // kind → { staged, previewUrl, name, fromPrevious }
  const [photos, setPhotos] = useState({});
  const [attest, setAttest] = useState({ adultAndTruthful: false, photosAreMine: false });
  const [noticeRead, setNoticeRead] = useState(false);
  const [privacyConsent, setPrivacyConsent] = useState(false);
  const [openFaq, setOpenFaq] = useState(null);
  const fileInputs = useRef({});
  const noticeRef = useRef(null);

  useEffect(() => {
    let alive = true;
    getCurrentApplication()
      .then((app) => {
        if (!alive) return;
        if (app && ['under_review', 'approved'].includes(app.status)) {
          navigate('/status', { replace: true });
          return;
        }
        if (app) {
          setForm(prefillFrom(app));
          const kinds = Array.isArray(app.photoKinds) ? app.photoKinds : (app.hasProfileImage ? ['profile'] : []);
          setPhotos(Object.fromEntries(kinds.map((k) => [k, { staged: true, fromPrevious: true }])));
        }
        setPhase('ready');
      })
      .catch((e) => {
        if (!alive) return;
        if (e?.status !== 404) push?.(e.message, { icon: 'alertCircle' });
        setPhase('ready');
      });
    return () => { alive = false; };
  }, [navigate, push]);

  // 프리뷰 objectURL 해제
  useEffect(() => () => {
    Object.values(photos).forEach((p) => { if (p?.previewUrl) URL.revokeObjectURL(p.previewUrl); });
  }, [photos]);

  const set = useCallback((k, v) => setForm((f) => ({ ...f, [k]: v })), []);
  const toggleCategory = useCallback((v) => setForm((f) => {
    const next = new Set(f.categories);
    next.has(v) ? next.delete(v) : next.add(v);
    return { ...f, categories: [...next] };
  }), []);

  const onPickPhoto = useCallback(async (kind, e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    try {
      await stageApplicationPhoto({ kind, fileBlob: file, filename: file.name });
      setPhotos((p) => {
        if (p[kind]?.previewUrl) URL.revokeObjectURL(p[kind].previewUrl);
        return { ...p, [kind]: { staged: true, previewUrl: URL.createObjectURL(file), name: file.name } };
      });
    } catch (err) {
      push?.(err.message, { icon: 'alertCircle' });
    }
  }, [push]);

  const onNoticeScroll = useCallback((e) => {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 4) setNoticeRead(true);
  }, []);
  // 고지가 짧아 스크롤이 없으면 바로 읽은 것으로.
  useEffect(() => {
    const el = noticeRef.current;
    if (phase === 'ready' && el && el.scrollHeight <= el.clientHeight + 4) setNoticeRead(true);
  }, [phase]);

  const allPhotos = PHOTO_SLOTS.every((p) => photos[p.kind]?.staged);
  const allAttest = ATTESTATIONS.every((a) => attest[a.key]);
  const canSubmit = form.lastName.trim() && form.firstName.trim() && form.contactEmail && form.birthdate
    && form.region && form.experienceLevel && form.categories.length > 0
    && allPhotos && allAttest && privacyConsent;

  const submit = useCallback(async () => {
    if (!canSubmit) { push?.('필수 항목을 모두 채워 주세요.', { icon: 'alertCircle' }); return; }
    setPhase('submitting');
    try {
      await submitApplication({
        contactEmail: form.contactEmail.trim(),
        applicantName: joinName(form.lastName, form.firstName),
        phone: form.phone.trim() || null,
        birthdate: form.birthdate,
        region: form.region.trim(),
        gender: form.gender || null,
        experienceLevel: form.experienceLevel || null,
        categories: form.categories,
        portfolioUrl: form.portfolioUrl.trim() || null,
        snsUrl: form.snsUrl.trim() || null,
        bio: form.bio.trim() || null,
        attestations: attest,
        privacyConsent: { accepted: true, documentVersion: PRIVACY_CONSENT_VERSION },
      });
      push?.('지원서를 제출했어요. 관리자 검토를 기다려 주세요.', { icon: 'check' });
      navigate('/status', { replace: true });
    } catch (err) {
      push?.(err.message, { icon: 'alertCircle' });
      setPhase('ready');
    }
  }, [canSubmit, form, attest, navigate, push]);

  if (phase === 'loading') {
    return <div className={s.page}><p className={s.loading}>불러오는 중…</p></div>;
  }

  return (
    <div className={s.page}>
      {/* ── 이용 원칙 ─────────────────────────────────────────── */}
      <section className={s.section}>
        <p className={s.eyebrow}>이용 원칙</p>
        <h1 className={s.h1}>모든 모델을 현장의 배우처럼 대합니다. 예외는 없습니다.</h1>
        <p className={s.lead}>
          이 제한은 FaceMarket 의 모든 라이선스에 적용돼요 — 누가 사든, 얼마를 내든, 무엇을 만들든.
          라이선스를 철회하면 이후 사용은 정해진 기준에 따라 중단되고 전부 기록돼요. 당신이 멈추라고 하면, 멈춥니다.
        </p>
        <ul className={s.rules}>
          {HOUSE_RULES.map((r) => (
            <li key={r.title} className={`${s.rule}${r.dark ? ` ${s.ruleDark}` : ''}`}>
              <span className={`${s.ruleIcon}${r.dark ? ` ${s.ruleIconPay}` : ''}`}>{r.dark ? '₩' : '✕'}</span>
              <div>
                <strong>{r.title}</strong>
                <p>{r.desc}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      {/* ── 지원 자격 ─────────────────────────────────────────── */}
      <section className={s.section}>
        <h2 className={s.h2}>지원 자격</h2>
        <p className={s.lead}>FaceMarket 모델로 등록하려면 아래 조건을 충족해야 해요.</p>
        <div className={s.qualGrid}>
          <div className={s.qualCard}>
            <h3 className={s.qualHead}>필수</h3>
            <ul className={s.qualList}>{QUAL_REQUIRED.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
          <div className={`${s.qualCard} ${s.qualCardMuted}`}>
            <h3 className={s.qualHead}>우대 (필수 아님)</h3>
            <ul className={s.qualList}>{QUAL_PREFERRED.map((t) => <li key={t}>{t}</li>)}</ul>
          </div>
        </div>
      </section>

      {/* ── 준비물 (다크) ──────────────────────────────────────── */}
      <section className={s.section}>
        {/* 랜딩 "등록 절차" 블록과 같은 옅은 파랑 판. 컨테이너는 다른 섹션과 같아 좌측선이 맞는다. */}
        <div className={s.needBlock}>
          <h2 className={s.h2}>준비물</h2>
          <p className={s.lead}>지원을 시작하기 전에 아래를 준비해 주세요. 약 5분 걸려요.</p>
          <ul className={s.needGrid}>
            {NEED_ITEMS.map((n) => (
              <li key={n.n} className={s.needCard}>
                <span className={s.needNum}>{n.n}</span>
                <h3>{n.title}</h3>
                <p>{n.desc}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ── 지원서 ───────────────────────────────────────────── */}
      <section className={s.section} id="apply-form">
        <h2 className={s.h2}>지원서 작성</h2>
        <p className={s.lead}>
          이름과 생년월일은 승인 뒤 신분증과 대조돼요 — 신분증과 같게 적어 주세요.
          지원서는 접수 순서대로 검토돼요.
        </p>

        <div className={s.form}>
        <div className={s.grid2}>
          <label className={s.field}>
            <span className={s.label}>성<i>*</i></span>
            <input className={s.input} value={form.lastName} autoComplete="family-name"
              onChange={(e) => set('lastName', e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.label}>이름<i>*</i></span>
            <input className={s.input} value={form.firstName} autoComplete="given-name"
              onChange={(e) => set('firstName', e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.label}>이메일<i>*</i></span>
            <input className={s.input} type="email" value={form.contactEmail} autoComplete="email"
              onChange={(e) => set('contactEmail', e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.label}>전화번호</span>
            <input className={s.input} type="tel" value={form.phone} autoComplete="tel"
              onChange={(e) => set('phone', e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.label}>생년월일<i>*</i></span>
            <input className={s.input} type="date" value={form.birthdate}
              onChange={(e) => set('birthdate', e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.label}>지역 (시, 국가)<i>*</i></span>
            <input className={s.input} value={form.region}
              onChange={(e) => set('region', e.target.value)} />
          </label>
        </div>

        <div className={s.field}>
          <span className={s.label}>성별</span>
          <div className={s.pills}>
            {[{ v: 'female', l: '여성' }, { v: 'male', l: '남성' }].map((g) => (
              <button key={g.v} type="button" className={`${s.pill}${form.gender === g.v ? ` ${s.pillOn}` : ''}`}
                onClick={() => set('gender', form.gender === g.v ? null : g.v)}>{g.l}</button>
            ))}
          </div>
        </div>

        <p className={s.groupTitle}>경력</p>

        <label className={s.field}>
          <span className={s.label}>경력 수준<i>*</i></span>
          <select className={s.input} value={form.experienceLevel} onChange={(e) => set('experienceLevel', e.target.value)}>
            <option value="">경력 수준을 선택하세요</option>
            {EXPERIENCE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>

        <div className={s.field}>
          <span className={s.label}>관심 있는 모델 카테고리<i>*</i></span>
          <div className={s.checks}>
            {CATEGORY_OPTIONS.map((c) => (
              <label key={c.value} className={`${s.check}${form.categories.includes(c.value) ? ` ${s.checkOn}` : ''}`}>
                <input type="checkbox" checked={form.categories.includes(c.value)} onChange={() => toggleCategory(c.value)} />
                {c.label}
              </label>
            ))}
          </div>
        </div>

        <div className={s.grid2}>
          <label className={s.field}>
            <span className={s.label}>포트폴리오 링크</span>
            <input className={s.input} type="url" value={form.portfolioUrl}
              onChange={(e) => set('portfolioUrl', e.target.value)} />
          </label>
          <label className={s.field}>
            <span className={s.label}>SNS 링크</span>
            <input className={s.input} type="url" value={form.snsUrl}
              onChange={(e) => set('snsUrl', e.target.value)} />
          </label>
        </div>

        <label className={s.field}>
          <span className={s.label}>자기소개</span>
          <textarea className={`${s.input} ${s.textarea}`} rows={5} value={form.bio}
            onChange={(e) => set('bio', e.target.value)} />
        </label>

        {/* ── 사진 ─────────────────────────────────────────── */}
        <p className={s.groupTitle}>사진</p>
        <div className={s.photoGrid}>
          {PHOTO_SLOTS.map((slot) => {
            const p = photos[slot.kind];
            return (
              <div key={slot.kind} className={s.photoSlot}>
                <span className={s.label}>{slot.label}<i>*</i></span>
                <button type="button" className={`${s.photoBox}${p?.staged ? ` ${s.photoBoxOn}` : ''}`}
                  onClick={() => fileInputs.current[slot.kind]?.click()}>
                  {p?.previewUrl
                    ? <img src={p.previewUrl} alt={`${slot.label} 미리보기`} className={s.photoPreview} />
                    : <Silhouette kind={slot.kind} />}
                  <span className={s.photoPlus} aria-hidden="true">{p?.staged ? '✓' : '+'}</span>
                  {p?.fromPrevious && !p?.previewUrl && <span className={s.photoKeep}>이전 지원서 사진 유지</span>}
                </button>
                <input ref={(el) => { fileInputs.current[slot.kind] = el; }} type="file"
                  accept="image/png,image/jpeg,image/webp" hidden onChange={(e) => onPickPhoto(slot.kind, e)} />
              </div>
            );
          })}
        </div>

        {/* ── 확인 ─────────────────────────────────────────── */}
        <p className={s.groupTitle}>확인</p>
        <div className={s.attests}>
          {ATTESTATIONS.map((a) => (
            <label key={a.key} className={s.attest}>
              <input type="checkbox" checked={!!attest[a.key]} onChange={(e) => setAttest((x) => ({ ...x, [a.key]: e.target.checked }))} />
              <span>{a.text}</span>
            </label>
          ))}
        </div>

        {/* ── 개인정보 고지 (끝까지 스크롤) ───────────────────── */}
        <p className={s.noticeTitle}>지원서 개인정보 고지</p>
        <div ref={noticeRef} className={s.notice} onScroll={onNoticeScroll}>
          {PRIVACY_NOTICE.map((n, i) => (
            <div key={i} className={s.noticeBlock}>
              {n.h && <strong>{n.h}</strong>}
              <p>{n.p}</p>
            </div>
          ))}
        </div>
        {!noticeRead && <p className={s.noticeGate}>고지를 끝까지 스크롤하면 동의할 수 있어요</p>}
        <label className={`${s.attest}${noticeRead ? '' : ` ${s.attestDisabled}`}`}>
          <input type="checkbox" disabled={!noticeRead} checked={privacyConsent}
            onChange={(e) => setPrivacyConsent(e.target.checked)} />
          <span>
            지원서 개인정보 고지를 읽었으며, 제출한 정보와 사진이 지원 검토에만 사용됨을 이해합니다.
            현재 단계에서는 상업적으로 사용되거나, 브랜드·제3자에게 공개되거나, 모델로 등록되지 않습니다.
          </span>
        </label>

        <button type="button" className={s.submit} disabled={!canSubmit || phase === 'submitting'} onClick={submit}>
          {phase === 'submitting' ? '제출 중…' : '지원서 제출하기'}
        </button>
        </div>
      </section>

      {/* ── FAQ ──────────────────────────────────────────────── */}
      <section className={s.section}>
        <h2 className={s.h2}>FAQ</h2>
        <ul className={s.faq}>
          {FAQ.map((f, i) => (
            <li key={f.q} className={s.faqItem}>
              <button type="button" className={s.faqQ} aria-expanded={openFaq === i}
                onClick={() => setOpenFaq(openFaq === i ? null : i)}>
                <span>{f.q}</span>
                <span className={s.faqToggle} aria-hidden="true">{openFaq === i ? '−' : '+'}</span>
              </button>
              {openFaq === i && <p className={s.faqA}>{f.a}</p>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default ModelApply;
