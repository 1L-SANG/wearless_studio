import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/admin-ui/button.jsx';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/admin-ui/card.jsx';
import { adminApplicationProfileImage, adminFetchApplicationPhotoUrl } from '@/lib/api/facemarket.js';
import { seoulClock, seoulDateKey } from '@/lib/datetime.js';
import { bodyTypeLabel, heightBucketLabel } from '@/lib/facemarketPhysique.js';
import s from './AdminSubmissionDetails.module.css';

const EMPTY = '입력 안 함';
const LABELS = {
  female: '여성', male: '남성', other: '기타', none: '경력 없음', beginner: '입문',
  intermediate: '중급', professional: '전문', under_review: '검토 중', approved: '승인됨',
  rejected: '거절됨', cancelled: '취소됨', pending: '대기', awaiting_confirm: '확인 대기',
  verified: '검증됨', reverification_required: '재검증 필요', suspended: '정지', passed: '완료',
  identity_pending: '신분증 인증 대기', photos_pending: '사진 등록 대기', liveness_pending: '본인 확인 대기',
  processing: '처리 중', asset_building: '이미지 준비 중', license_pending: '사용 조건 입력 대기',
  vc_pending: '라이선스 발급 중', review_pending: '검토 대기', failed: '실패',
  active: '유효', revoked: '철회됨', expired: '만료됨',
  delicate: '여리여리', slim: '마름', regular: '보통', normal: '보통', plump: '통통',
  chubby: '통통', muscular: '근육형', custom: '직접 입력',
  black: '검정', dark_brown: '짙은 갈색', brown: '갈색', light_brown: '밝은 갈색', blonde: '금발', gray: '회색',
  blue: '파랑', green: '초록', hazel: '헤이즐', red: '빨강',
  short: '짧은 머리', medium: '중간 길이', long: '긴 머리', bald: '민머리',
  '20s': '20대', '30s': '30대', '40s': '40대', '50s_plus': '50대 이상',
};
const PHOTO_KINDS = [
  ['profile', '프로필'], ['closeup', '클로즈업'], ['waist_up', '상반신'], ['full_length', '전신'],
];
const ATTESTATIONS = [
  ['adultAndTruthful', '성인·사실 기재 확인'], ['photosAreMine', '본인 사진 확인'],
  ['noAgencyContract', '소속사 미계약 확인'], ['reviewOnlyUse', '심사 목적 사용 확인'],
  ['privacyPolicy', '개인정보 처리방침 확인'],
];
const valueOf = value => value == null || value === '' || (Array.isArray(value) && !value.length) ? EMPTY
  : Array.isArray(value) ? value.join(', ') : typeof value === 'boolean' ? (value ? '있음' : '없음')
    : typeof value === 'object' ? EMPTY : String(value);
const labelOf = value => LABELS[value] || value;
const unit = (value, suffix) => value == null || value === '' ? null : `${value}${suffix}`;
const timestamp = value => value ? `${seoulDateKey(value, EMPTY)} ${seoulClock(value, '')}`.trim() : null;
const age = birthdate => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(birthdate || '')) return null;
  const today = seoulDateKey(new Date());
  return Number(today.slice(0, 4)) - Number(birthdate.slice(0, 4)) - (today.slice(5) < birthdate.slice(5) ? 1 : 0);
};

function Field({ label, value, wide = false, children }) {
  return <div className={wide ? s.adminFullWidth : undefined}><dt>{label}</dt><dd>{children ?? valueOf(value)}</dd></div>;
}

export function SubmissionPhoto({ application, kind, label }) {
  const [url, setUrl] = useState(null);
  const [error, setError] = useState(false);
  const uri = application.photoUris?.[kind];
  useEffect(() => {
    let alive = true;
    let objectUrl;
    setUrl(null);
    setError(false);
    const request = uri ? adminApplicationProfileImage(uri) : adminFetchApplicationPhotoUrl(application.id, kind);
    request.then(next => {
      if (alive) { objectUrl = next; setUrl(next); }
      else URL.revokeObjectURL(next);
    }).catch(() => { if (alive) setError(true); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [application.id, uri, kind]);
  return <figure className={s.adminPhoto}>
    {url ? <img src={url} alt={`지원자 ${label} 사진`} /> : <p>{error ? '사진을 불러오지 못했어요.' : '사진을 불러오고 있어요.'}</p>}
    <figcaption>{label}</figcaption>
  </figure>;
}

export function AdminSubmissionDetails({ application, detail, onClose }) {
  const dialogRef = useRef(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    dialog.showModal();
    return () => { dialog.close(); };
  }, []);
  const app = application || detail?.application || {};
  const model = detail?.model;
  const enrollment = detail?.enrollment;
  const profile = detail?.profile || {};
  const licenses = detail?.licenses || [];
  const consents = detail?.consentEvents || [];
  const present = Array.isArray(app.photoKinds) ? app.photoKinds : app.hasProfileImage ? ['profile'] : [];
  const photos = PHOTO_KINDS.filter(([kind]) => present.includes(kind));
  const years = age(app.birthdate);

  return <dialog ref={dialogRef} className={s.adminDialog} aria-labelledby="admin-submission-title"
    onCancel={event => { event.preventDefault(); onClose(); }}
    onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); onClose(); } }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <Card className={s.adminCard}>
      <CardHeader className={s.adminHeader}>
        <div><CardTitle id="admin-submission-title">상세히 보기</CardTitle><CardDescription>입력한 내용을 모두 확인해요.</CardDescription></div>
        <Button variant="ghost" size="sm" onClick={onClose}>닫기</Button>
      </CardHeader>
      <CardContent className={s.adminBody}>
        <section><h4>지원서</h4><dl className={s.adminFields}>
          <Field label="이름" value={app.applicantName} />
          <Field label="생년월일" value={years == null ? app.birthdate : `${app.birthdate} (만 ${years}세)`} />
          <Field label="성별" value={labelOf(app.gender)} /><Field label="지역" value={app.region} />
          <Field label="키" value={unit(app.heightCm, 'cm')} /><Field label="몸무게" value={unit(app.weightKg, 'kg')} />
          <Field label="이메일" value={app.contactEmail} /><Field label="전화" value={app.phone} />
          <Field label="경력 수준" value={labelOf(app.experienceLevel)} /><Field label="소속사 계약 여부" value={app.agencyContracted} />
          <Field label="카테고리" value={app.categories} /><Field label="지원 상태" value={labelOf(app.status)} />
          <Field label="포트폴리오 URL" value={app.portfolioUrl} wide /><Field label="SNS URL" value={app.snsUrl} wide />
          <Field label="자기소개" value={app.bio} wide />
          <Field label="지원 사진" wide>{photos.length ? <div className={s.adminPhotos}>
            {photos.map(([kind, label]) => <SubmissionPhoto key={kind} application={app} kind={kind} label={label} />)}
          </div> : EMPTY}</Field>
          <Field label="접수일" value={timestamp(app.createdAt)} /><Field label="검토일" value={timestamp(app.reviewedAt)} />
          <Field label="거절 사유" value={app.rejectReason} wide /><Field label="신원 불일치 횟수" value={unit(app.identityMismatchCount, '회')} />
        </dl></section>
        {model && <section><h4>등록·프로필</h4><dl className={s.adminFields}>
          <Field label="등록 상태" value={labelOf(enrollment?.status)} /><Field label="사진 슬롯 수" value={unit(enrollment?.photoCount, '장')} />
          <Field label="등록한 체형" value={bodyTypeLabel(enrollment?.bodyType ?? model.bodyType)} />
          <Field label="표시명" value={model.displayName} /><Field label="모델 상태" value={labelOf(model.status)} />
          <Field label="등록 성별" value={labelOf(model.gender)} />
          <Field label="나이대" value={years == null ? labelOf(profile.ageRange) : `${Math.floor(years / 10) * 10}대`} />
          <Field label="키 구간" value={heightBucketLabel(enrollment?.heightBucket ?? model.heightBucket)} />
          <Field label="프로필 키" value={unit(profile.heightCm, 'cm')} /><Field label="프로필 몸무게" value={unit(profile.weightKg, 'kg')} />
          <Field label="프로필 성별" value={labelOf(profile.gender)} /><Field label="프로필 나이대" value={labelOf(profile.ageRange)} />
          <Field label="프로필 체형" value={labelOf(profile.bodyType)} /><Field label="직접 적은 체형" value={profile.bodyTypeCustom} wide />
          <Field label="가슴 둘레" value={unit(profile.bustCm, 'cm')} /><Field label="허리 둘레" value={unit(profile.waistCm, 'cm')} />
          <Field label="엉덩이 둘레" value={unit(profile.hipCm, 'cm')} /><Field label="의류 사이즈" value={profile.clothingSize} />
          <Field label="피부 톤" value={profile.skinTone} /><Field label="헤어 컬러" value={labelOf(profile.hairColor)} />
          <Field label="헤어 길이" value={labelOf(profile.hairLength)} /><Field label="눈 색" value={labelOf(profile.eyeColor)} />
          <Field label="직접 적은 머리 정보" value={profile.hair} wide />
        </dl></section>}
        {model && <section><h4>라이선스 조건</h4>{licenses.length ? licenses.map((license, index) => <dl className={s.adminFields} key={index}>
          <Field label="허용 품목" value={license.allowedUse} wide />
          {(license.optLocationCuts || license.optLookbookPersonReplace) && <>
            <Field label="선택 동의 항목" value={[license.optLocationCuts && '로케이션 촬영', license.optLookbookPersonReplace && '룩북 인물 교체'].filter(Boolean)} wide />
            <Field label="선택 동의 버전" value={license.optConsentVersion} /><Field label="선택 동의 시각" value={timestamp(license.optConsentedAt)} />
          </>}
          <Field label="발급일" value={timestamp(license.createdAt)} />
          <Field label="유효기간" value={license.validUntil === null ? '철회 시까지' : timestamp(license.validUntil)} />
          <Field label="라이선스 상태" value={labelOf(license.status)} /><Field label="VC id" value={license.vcId} wide />
        </dl>) : <p>{EMPTY}</p>}</section>}
        <section><h4>동의 기록</h4><dl className={s.adminFields}>
          <Field label="개인정보 동의 버전" value={app.privacyConsentVersion} /><Field label="개인정보 동의 시각" value={timestamp(app.privacyConsentedAt)} />
          {ATTESTATIONS.map(([key, label]) => <Field key={key} label={label} value={app.attestations?.[key] == null ? null : app.attestations[key] ? '확인했어요' : '확인하지 않았어요'} />)}
        </dl>{model && (consents.length ? consents : [{}]).map((consent, index) => <dl className={s.adminFields} key={index}>
          <Field label="생체 동의 버전" value={consent.biometricVersion} /><Field label="약관 동의 버전" value={consent.termsVersion} />
          <Field label="국외 고지 버전" value={consent.overseasVersion} /><Field label="동의 시각" value={timestamp(consent.acceptedAt)} />
        </dl>)}</section>
      </CardContent>
    </Card>
  </dialog>;
}
