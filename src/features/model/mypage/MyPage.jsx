import { useState } from 'react';
import { Link } from 'react-router-dom';
import { pauseMyModel, resumeMyModel, revokeLicense } from '@/lib/api/facemarket.js';
import { MyPageDialog } from './MyPageDialog.jsx';
import { MyPageIllustration } from './MyPageIllustration.jsx';
import { MyPageTimeline, timelineTime } from './MyPageTimeline.jsx';
import { MyPageEarnings } from './MyPageEarnings.jsx';
import { MyPageConditions } from './MyPageConditions.jsx';
import s from './MyPage.module.css';

export function ActiveDashboard({ journey, model, license, licenses, onModelChange, onLicenseChange, onSignOut }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [withdrawing, setWithdrawing] = useState(false);
  const { flag } = journey;
  const revoked = flag === 'revoked';
  const paused = flag === 'paused';
  const canResume = model.suspensionSource === 'owner';
  const changeActivity = async () => {
    if (busy || (paused && !canResume)) return;
    setBusy(true);
    setMessage('');
    try {
      const updated = paused ? await resumeMyModel(model.id) : await pauseMyModel(model.id);
      onModelChange({ ...model, ...updated });
      setMessage(paused ? '활동을 다시 켰어요.' : '활동을 잠시 멈췄어요.');
    } catch (error) { setMessage(error.message || '바꾸지 못했어요. 다시 시도해 주세요.'); }
    finally { setBusy(false); }
  };
  const withdraw = async () => {
    if (busy || !license) return;
    setBusy(true);
    setMessage('');
    try {
      const updated = await revokeLicense(license.id);
      onLicenseChange({ ...license, ...updated });
      setWithdrawing(false);
      setMessage('라이선스를 해지했어요.');
    } catch (error) { setMessage(error.message || '해지하지 못했어요. 다시 시도해 주세요.'); }
    finally { setBusy(false); }
  };
  return <>
    <div className={s.identity}>
      {model.coverImageUrl ? <img className={s.avatar} src={model.coverImageUrl} alt="내 대표 이미지" /> : <span className={s.avatar} aria-hidden="true" />}
      <h1 className={s.profileName}>{model.displayName || '내 모델'}</h1>
      <span className={`${s.badge} ${flag === 'none' ? s.activeBadge : ''}`}>{revoked ? '철회됨' : paused ? '잠시 멈춤' : '활동 중'}</span>
    </div>
    {revoked && <div className={s.exception}><p>라이선스를 해지했어요. 새로 사용하는 요청은 받지 않아요. 이전 사용 기록과 증서는 계속 볼 수 있어요.</p></div>}
    {paused && <div className={s.exception}><p>{canResume ? '새 요청이 들어오지 않아요. 언제든 다시 켤 수 있어요.' : '운영팀이 활동을 멈췄어요. 다시 켜려면 운영팀의 확인이 필요해요.'}</p>
      {canResume && <button className={s.textLink} type="button" disabled={busy} onClick={changeActivity}>다시 켜기</button>}
    </div>}
    <MyPageEarnings modelId={model.id} licenses={licenses} revoked={revoked} />
    <MyPageConditions license={license} model={model} revoked={revoked} onLicenseChange={onLicenseChange} />
    <section className={s.dashboardSection} aria-label="계정"><div className={s.accountList}>
      {!revoked && <>
        <Link to="/model/register" className={s.accountRow}>사진 다시 올리기</Link>
        <button type="button" className={s.accountRow} disabled={busy || (paused && !canResume)} onClick={changeActivity}>{paused ? '다시 켜기' : '활동 잠시 멈춤'}</button>
        <button type="button" className={s.accountRow} onClick={() => setWithdrawing(true)}>그만두기</button>
      </>}
      <button type="button" className={s.accountRow} onClick={onSignOut}>로그아웃</button>
    </div></section>
    {message && !withdrawing && <p role="status" className={s.muted}>{message}</p>}
    {withdrawing && <MyPageDialog title="그만두면 이렇게 돼요" busy={busy} onClose={() => setWithdrawing(false)}>
      <p>이 라이선스를 해지하면 새로 사용하는 요청을 받지 않아요. 다시 활동하려면 새로 등록하고 증서를 발급받아야 해요.</p>
      {message && <p role="alert" className={s.error}>{message}</p>}
      <button type="button" className={s.primary} onClick={withdraw} disabled={busy}>라이선스 해지하기</button>
    </MyPageDialog>}
  </>;
}

export function MyPage({ journey, application, enrollment, model, license, licenses = [], onAction, onLogin, onSignOut, onModelChange, onLicenseChange }) {
  const received = timelineTime(application?.createdAt);
  const reviewed = timelineTime(application?.reviewedAt);
  const registered = timelineTime(enrollment?.completedAt || model?.enrollmentCompletedAt);
  if (journey.mode === 'guest') return <div className={`${s.page} ${s.guest}`}>
    <MyPageIllustration name="guest" hero /><p className={s.pageLabel}>마이페이지</p>
    <h1>로그인하면 내 상태와 수익을 볼 수 있어요</h1>
    <div className={s.actions}><button className={s.primary} type="button" onClick={onLogin}>로그인</button></div>
    <Link className={s.textLink} to="/model/apply">아직 지원 전이라면 모델 지원 보기</Link>
  </div>;
  if (journey.mode === 'active') return <div className={s.page}><ActiveDashboard journey={journey} model={model} license={license} licenses={licenses}
    onModelChange={onModelChange} onLicenseChange={onLicenseChange} onSignOut={onSignOut} /></div>;

  const resuming = journey.step === 2;
  const savedPhotos = resuming && enrollment?.status === 'photos_pending';
  const rejected = journey.step === 3;
  let title, description, progressDescription;
  if (journey.mode === 'review') {
    title = { review: '사진을 검수하고 있어요', assets: '테스트컷을 만들고 있어요', confirm: '테스트컷 4장을 골라 주세요' }[journey.sub];
    description = {
      review: `${registered ? `${registered}에 등록을 마쳤어요. ` : ''}보통 1~3일 걸려요. 끝나면 메일로 알려요.`,
      assets: '검수가 끝났어요. 셀러에게 보일 테스트컷을 만드는 중이에요.',
      confirm: '마음에 드는 4장을 고르면 공개할 모습이 확정돼요.',
    }[journey.sub];
    progressDescription = journey.sub === 'review' ? '사진 18장과 증서를 확인하고 있어요. 끝나면 테스트컷을 만들어요.'
      : journey.sub === 'assets' ? description : '테스트컷 중 마음에 드는 4장을 고르면 공개할 모습이 확정돼요.';
  } else if (journey.needsApplication) {
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
      <MyPageIllustration name={journey.mode === 'review' ? journey.sub : `onb${journey.step}`} />
    </div>
    {rejected ? <>
      <div className={s.reasonCard}><p className={s.muted}>검토한 사람이 남긴 사유</p><p>{application?.rejectReason || '사유가 아직 표시되지 않았어요. 지원 결과 메일을 확인해 주세요.'}</p></div>
      <dl className={s.recordTable}><div><dt>적은 내용</dt><dd>30일 안에 지워요. 그전에 다시 지원하면 불러와서 고칠 수 있어요.</dd></div><div><dt>사진</dt><dd>같이 지워요. 다시 지원할 때 새로 올려 주세요.</dd></div></dl>
      <Link to="/models" className={s.textLink}>모델 리스트 둘러보기</Link>
      <div className={s.bottomBar}><button className={s.primary} type="button" onClick={onAction}>다시 지원하기</button></div>
    </> : <MyPageTimeline journey={journey} description={progressDescription} onAction={onAction} />}
    {journey.action?.kind === 'cancel' && <div className={s.endLink}><button className={s.textLink} type="button" onClick={onAction}>지원 취소</button></div>}
    {journey.mode === 'review' && <>
      <MyPageConditions compact license={license} model={model} onLicenseChange={onLicenseChange} />
      {journey.action?.kind === 'reload' && <button className={s.textLink} type="button" onClick={onAction}>상태 새로고침</button>}
    </>}
  </div>;
}
