import { Link } from 'react-router-dom';
import { ArrowUpRight, CircleCheck, FileText } from 'lucide-react';
import { seoulDateKey, seoulYearMonth } from '@/lib/datetime.js';
import { EmptyPanel } from './MyPageParts.jsx';
import s from './MyPage.module.css';

export const isIssuedLicense = license => Boolean(license?.vcId && ['active', 'revoked'].includes(license.status));
const certificateDate = value => seoulDateKey(value, '').replaceAll('-', '. ');

export function ProfileHero({ model, license, onCertificate }) {
  const issued = isIssuedLicense(license);
  const since = seoulYearMonth(license?.createdAt || model?.createdAt);
  const [year, month] = since.split('.');
  const name = model?.displayName || '내 프로필';
  return <section className={s.profileHero} aria-label="내 프로필">
    <div className={s.identity}>
      {model?.coverImageUrl ? <img className={s.portrait} src={model.coverImageUrl} alt="내 프로필 사진" /> : <span className={s.portrait} aria-hidden="true" />}
      <div className={s.identityCopy}><p className={s.eyebrow}>MY FACEMARKET</p><h1 className={s.profileName}>{name}</h1>
        <p className={s.profileMeta}>모델{since && ` · ${year}년 ${Number(month)}월부터 함께`}</p></div>
    </div>
    <button type="button" className={s.licenseMini} onClick={onCertificate} aria-haspopup="dialog">
      <span className={s.certificateTop}>FACEMARKET LICENSE<FileText className={s.icon} aria-hidden="true" /></span>
      <span className={s.certificateName}>{issued ? '초상 라이선스 증서' : '라이선스 발급 준비'}</span>
      <span className={s.certificateMeta}>{!issued ? '발급이 완료되면 여기에 표시돼요' : license.status === 'revoked' ? '철회됨' : `${name} · ${certificateDate(license.createdAt)} 발급`}</span>
      <span className={s.certificateLink}><span>{issued ? '내 증서 보기' : '발급 진행 확인'}</span><ArrowUpRight className={s.icon} aria-hidden="true" /></span>
    </button>
  </section>;
}

export function MyPageCertificate({ license, model, revoked = false }) {
  if (!isIssuedLicense(license)) return <div className={s.certificateLarge}><EmptyPanel title="등록을 마치면 증서를 발급해요." description="발급 전에는 증서 번호가 표시되지 않아요." /></div>;
  return <div className={s.certificateLarge}>
    <div className={s.certificateTop}>FACEMARKET<CircleCheck className={s.icon} aria-hidden="true" /></div>
    <div className={s.certificateName}>초상 라이선스 증서</div><div className={s.certificateMeta}>Likeness License</div>
    <div className={s.certHolder}>
      {model?.coverImageUrl ? <img className={s.holderPortrait} src={model.coverImageUrl} alt="" /> : <span className={s.holderPortrait} aria-hidden="true" />}
      <div><strong>{model?.displayName || '내 프로필'}</strong><small>{revoked || license.status === 'revoked' ? '철회된 증서' : '모델 본인에게 발급된 라이선스'}</small></div>
    </div>
    <p className={s.certificateMeta}>발급일 {certificateDate(license.createdAt)}</p>
    <div className={s.certCode}><div>vc:{license.vcId}</div>
      <Link to={`/verify/${encodeURIComponent(license.id)}`}>확인 주소 /verify/{license.id}</Link>
    </div>
  </div>;
}
