import { Link } from 'react-router-dom';
import { ArrowUpRight, CircleCheck, FileText } from 'lucide-react';
import { seoulDateKey, seoulYearMonth } from '@/lib/datetime.js';
import { EmptyPanel } from './MyPageParts.jsx';
import s from './MyPage.module.css';

export const isIssuedLicense = license => Boolean(license?.vcId && ['active', 'revoked'].includes(license.status));
const certificateDate = value => seoulDateKey(value, '').replaceAll('-', '. ');

/** 증서를 보고 있는 자리에서 /model/license(증서 목록·해지)로 가는 길.
 *
 * 왜 증서 카드인가: 마이페이지가 보여 주는 증서는 **최신 한 장의 요약**이고(vcId·발급일·/verify),
 * 발급받은 증서 전체와 해지는 /model/license 에만 있다. 지금은 등록 완료 화면에서만 그리로 갈 수
 * 있어서, 등록을 마친 뒤에는 사실상 길이 끊긴다. 증서를 확인하는 그 자리에 이어 붙이는 게 맞다.
 * ProfileHero 의 "내 증서 보기"는 이 카드를 담은 대화상자를 열므로 거기서도 같은 길이 생긴다.
 *
 * 발급 전에는 아예 렌더되지 않는다 — 위 EmptyPanel 분기로 빠지고, 발급 흐름 안내는 마이페이지의
 * 등록 진행 카드(mypageState.registrationCard)가 이미 맡고 있어 진입점을 둘로 만들 이유가 없다.
 * 해지한 뒤에도 막지 않는다: 기록과 증서는 계속 볼 수 있어야 한다.
 *
 * 용어는 **해지**다. '폐기'로 쓰지 않는다 — 근거는
 * features/facemarket-landing/sections/ModelInfoSection.jsx 의 어휘 주석.
 */
export function LicenseManageLink({ license, className }) {
  if (!isIssuedLicense(license)) return null;
  return <Link className={className} to="/model/license">
    <span>{license.status === 'revoked' ? '해지한 라이선스 보기' : '라이선스 관리'}</span>
    <ArrowUpRight className={s.icon} aria-hidden="true" />
  </Link>;
}

export function ProfileHero({ model, license, onCertificate, registering = false }) {
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
      <span className={s.certificateName}>{issued ? '초상 라이선스 증서' : registering ? '테스트컷 준비 중' : '라이선스 발급 준비'}</span>
      <span className={s.certificateMeta}>{!issued ? (registering ? '테스트컷을 확정하면 증서를 볼 수 있어요' : '발급이 완료되면 여기에 표시돼요') : license.status === 'revoked' ? '철회됨' : `${name} · ${certificateDate(license.createdAt)} 발급`}</span>
      <span className={s.certificateLink}><span>{issued ? '내 증서 보기' : registering ? '준비 상황 확인' : '발급 진행 확인'}</span><ArrowUpRight className={s.icon} aria-hidden="true" /></span>
    </button>
  </section>;
}

export function MyPageCertificate({ license, model, revoked = false, registering = false }) {
  if (!isIssuedLicense(license)) return <div className={s.certificateLarge}><EmptyPanel title={registering ? '테스트컷 준비 중' : '등록을 마치면 증서를 발급해요.'} description={registering ? '테스트컷을 확정하면 라이선스 증서를 볼 수 있어요.' : '발급 전에는 증서 번호가 표시되지 않아요.'} /></div>;
  return <div className={s.certificateLarge}>
    <div className={s.certificateTop}>FACEMARKET<CircleCheck className={s.icon} aria-hidden="true" /></div>
    <div className={s.certificateName}>초상 라이선스 증서</div><div className={s.certificateMeta}>Likeness License</div>
    <div className={s.certHolder}>
      {model?.coverImageUrl ? <img className={s.holderPortrait} src={model.coverImageUrl} alt="" /> : <span className={s.holderPortrait} aria-hidden="true" />}
      <div><strong>{model?.displayName || '내 프로필'}</strong><small>{revoked || license.status === 'revoked' ? '철회된 증서' : '모델 본인에게 발급된 라이선스'}</small></div>
    </div>
    <p className={s.certificateMeta}>발급일 {certificateDate(license.createdAt)}</p>
    <div className={s.certCode}><div>{license.vcId}</div>
      <Link to={`/verify/${encodeURIComponent(license.id)}`}>확인 주소 /verify/{license.id}</Link>
    </div>
    <LicenseManageLink license={license} className={s.certificateManage} />
  </div>;
}
