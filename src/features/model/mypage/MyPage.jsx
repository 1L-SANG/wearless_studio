import { Link } from 'react-router-dom';
import { MyPageIllustration } from './MyPageIllustration.jsx';
import { MyPageTimeline, timelineTime } from './MyPageTimeline.jsx';
import { ActiveDashboard } from './MyPageDashboard.jsx';
import { isRegistrationJourney } from './mypageState.js';
import s from './MyPage.module.css';

export { ActiveDashboard } from './MyPageDashboard.jsx';

export function MyPage({ journey, application, enrollment, model, license, onAction, onLogin, onModelChange, onLicenseChange }) {
  const received = timelineTime(application?.createdAt);
  const reviewed = timelineTime(application?.reviewedAt);
  if (journey.mode === 'guest') return <div className={`${s.page} ${s.guest}`}>
    <MyPageIllustration name="guest" hero /><p className={s.pageLabel}>마이페이지</p>
    <h1>로그인하면 내 상태와 수익을 볼 수 있어요</h1>
    <div className={s.actions}><button className={s.primary} type="button" onClick={onLogin}>로그인</button></div>
    <Link className={s.textLink} to="/model/apply">아직 지원 전이라면 모델 지원 보기</Link>
  </div>;
  if (journey.mode === 'active' || isRegistrationJourney(journey)) return <div className={`${s.page} ${s.dashboardPage}`}><ActiveDashboard key={model?.id || 'registration'} journey={journey} model={model} license={license} enrollment={enrollment}
    onModelChange={onModelChange} onLicenseChange={onLicenseChange} /></div>;

  const resuming = journey.step === 2;
  const savedPhotos = resuming && enrollment?.status === 'photos_pending';
  const rejected = journey.step === 3;
  let title, description, progressDescription;
  if (journey.needsApplication) {
    title = '모델 지원을 시작해요';
    description = '지원서를 보내면 검토 결과를 알려드려요.';
    progressDescription = '모델 지원서를 작성해 주세요.';
  } else if (rejected) {
    title = '이번에는 승인되지 않았어요';
    description = `${reviewed ? `${reviewed}에 검토가 끝났어요. ` : ''}아래 사유를 보고 고쳐서 다시 지원할 수 있어요.`;
  } else if (journey.step === 0) {
    title = '지원서를 검토하고 있어요';
    description = `${received ? `${received}에 받았어요. ` : ''}24시간 이내에 ${application?.contactEmail ? `${application.contactEmail}으로 ` : '메일로 '}결과를 알려드려요.`;
    progressDescription = '지원서를 검토중이에요. 24시간 이내 결과를 전달해드릴게요.';
  } else {
    title = resuming ? '등록을 이어서 해요' : '승인됐어요. 이제 등록을 시작해요';
    description = resuming ? (savedPhotos ? '지난번에 사진 단계까지 저장돼 있어요. 그 자리부터 이어져요.' : '지난번에 저장한 단계부터 이어져요.') : '본인확인, 사진, 조건, 증서 4단계예요. 10분이면 끝나요. 승인 메일의 링크를 눌러도 이 화면으로 와요.';
    progressDescription = resuming ? (savedPhotos ? '사진 단계까지 저장돼 있어요. 그 자리부터 이어져요.' : '지난번에 저장한 단계부터 이어져요.') : '얼굴 구현을 위한 이미지들과, 라이선스 증서에 대한 설정이 필요해요.';
  }
  return <div className={s.page}>
    <div className={s.introWithIllus}><div><p className={s.pageLabel}>마이페이지</p><h1>{title}</h1><p className={s.description}>{description}</p></div>
      <MyPageIllustration name={`onb${journey.step}`} />
    </div>
    {rejected ? <>
      <div className={s.reasonCard}><p className={s.muted}>검토한 사람이 남긴 사유</p><p>{application?.rejectReason || '사유가 아직 표시되지 않았어요. 지원 결과 메일을 확인해 주세요.'}</p></div>
      <dl className={s.recordTable}><div><dt>적은 내용</dt><dd>30일 안에 지워요. 그전에 다시 지원하면 불러와서 고칠 수 있어요.</dd></div><div><dt>사진</dt><dd>같이 지워요. 다시 지원할 때 새로 올려 주세요.</dd></div></dl>
      <Link to="/models" className={s.textLink}>모델 리스트 둘러보기</Link>
      <div className={s.bottomBar}><button className={s.primary} type="button" onClick={onAction}>다시 지원하기</button></div>
    </> : <MyPageTimeline journey={journey} description={progressDescription} onAction={onAction} />}
    {journey.action?.kind === 'cancel' && <div className={s.endLink}><button className={s.textLink} type="button" onClick={onAction}>지원 취소</button></div>}

  </div>;
}
