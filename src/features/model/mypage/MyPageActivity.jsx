import { useRef, useState } from 'react';
import { ChevronRight, FileText, Info, Pause, Play, Square } from 'lucide-react';
import { pauseMyModel, resumeMyModel, revokeLicense } from '@/lib/api/facemarket.js';
import company from '@/lib/companyInfo.json';
import { MyPageDialog } from './MyPageDialog.jsx';
import { activityOptions, isRegistrationJourney } from './mypageState.js';
import s from './MyPage.module.css';

const contact = `mailto:${company.email}`;
const optionContent = {
  pause: { label: '잠시 쉬어가기', description: '이전 기록을 남겨두고 새 사용을 잠시 멈춰요.', icon: Pause },
  resume: { label: '활동 재개', description: '새로운 사용 요청을 다시 받아요.', icon: Play },
  contact: { label: '활동 재개 문의', description: '운영팀의 확인 후 활동을 재개할 수 있어요.', icon: Info },
  revoke: { label: '라이선스 종료', description: '새로운 이미지 사용을 허용하지 않아요.', icon: Square },
};

export function MyPageActivity({ journey, model, license, dialog, onDialogChange, onModelChange, onLicenseChange }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const saving = useRef(false);
  const options = activityOptions(journey, model);
  const revoked = journey.flag === 'revoked';
  const paused = journey.flag === 'paused';
  const registering = isRegistrationJourney(journey);
  const canResume = options.includes('resume');
  const changeActivity = async action => {
    if (saving.current || !options.includes(action) || !model?.id) return;
    saving.current = true;
    setBusy(true);
    setMessage('');
    try {
      if (action === 'revoke') {
        if (!license?.id) return;
        const updated = await revokeLicense(license.id);
        onLicenseChange({ ...license, ...updated });
      } else {
        const updated = action === 'resume' ? await resumeMyModel(model.id) : await pauseMyModel(model.id);
        onModelChange({ ...model, ...updated });
      }
      onDialogChange(null);
      setMessage(action === 'resume' ? '활동을 다시 시작했어요.' : action === 'pause' ? '활동을 잠시 멈췄어요.' : '라이선스를 종료했어요.');
    } catch (error) { setMessage(error.message || '바꾸지 못했어요. 다시 시도해 주세요.'); }
    finally { saving.current = false; setBusy(false); }
  };
  const open = value => { setMessage(''); onDialogChange(value); };
  return <>
    {(revoked || paused) && <div className={s.stateMessage}><Info className={s.icon} aria-hidden="true" /><div>
      <p>{revoked ? '라이선스가 철회되었어요.' : canResume ? '활동을 잠시 쉬고 있어요.' : '운영팀이 활동을 잠시 멈췄어요.'}</p>
      <small>{revoked ? '새로운 사용은 중단되고 이전 기록은 남아 있어요.' : canResume ? '새로운 사용 요청을 받지 않아요.' : '다시 시작하려면 운영팀의 확인이 필요해요.'}</small></div>
      {paused && (canResume ? <button type="button" className={s.quietButton} disabled={busy} onClick={() => changeActivity('resume')}>활동 재개</button> : <a className={s.quietButton} href={contact}>문의하기</a>)}
    </div>}
    {message && !dialog && <p role="status" className={s.muted}>{message}</p>}
    {dialog && <MyPageDialog title={dialog === 'pause' ? '잠시 쉬어갈까요?' : dialog === 'revoke' ? '라이선스 종료' : dialog === 'data' ? '개인정보 및 데이터 관리' : '활동 관리'} busy={busy} onClose={() => onDialogChange(null)}>
      {dialog === 'manage' && (revoked || registering ? <>
        <p>{revoked ? '라이선스가 종료되어 새로운 사용이 중단되었어요.' : '등록과 검토를 마치면 활동을 관리할 수 있어요.'}</p>
        <div className={s.actionRow}><a className={s.quietButton} href={contact}>운영팀 문의</a><button type="button" className={s.quietButton} onClick={() => open('data')}>데이터 관리</button></div>
      </> : <><p>내 이미지의 새로운 사용을 관리해요.</p><div className={s.manageOptions}>
        {options.filter(id => id !== 'data').map(id => {
          const content = optionContent[id];
          const Icon = content.icon;
          const body = <><span className={s.optionIcon}><Icon className={s.icon} aria-hidden="true" /></span><span><strong>{content.label}</strong><small>{content.description}</small></span><ChevronRight className={s.icon} aria-hidden="true" /></>;
          return id === 'contact' ? <a key={id} className={s.manageOption} href={contact}>{body}</a>
            : <button key={id} type="button" className={s.manageOption} disabled={busy} onClick={() => id === 'resume' ? changeActivity(id) : open(id)}>{body}</button>;
        })}
      </div><div className={s.manageData}><button type="button" onClick={() => open('data')}><FileText className={s.icon} aria-hidden="true" />개인정보 및 데이터 관리<ChevronRight className={s.icon} aria-hidden="true" /></button></div></>)}
      {(dialog === 'pause' || dialog === 'revoke') && <>
        <p>{dialog === 'pause' ? '새로운 이미지 사용을 잠시 멈춰요. 지금까지의 사용 기록과 정산 내역은 이곳에서 확인할 수 있어요.' : '종료하면 내 이미지의 새로운 사용이 중단돼요. 이전에 사용된 페이지와 정산 기록은 남아요.'}</p>
        <div className={s.actionRow}><button type="button" className={s.quietButton} disabled={busy} onClick={() => open('manage')}>돌아가기</button>
          <button type="button" className={`${s.primaryButton} ${dialog === 'revoke' ? s.dangerButton : ''}`} disabled={busy || !options.includes(dialog)} onClick={() => changeActivity(dialog)}>{dialog === 'revoke' ? '라이선스 종료하기' : '잠시 쉬어가기'}</button></div>
      </>}
      {dialog === 'data' && <><p>라이선스 종료와 개인정보 삭제는 별도로 진행해요. 삭제 요청은 운영팀 메일로 받고 있어요.</p><a className={s.textLink} href={contact}>{company.email}</a></>}
      {message && <p role="alert" className={s.error}>{message}</p>}
    </MyPageDialog>}
  </>;
}
