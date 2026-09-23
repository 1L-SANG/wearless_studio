import { useEffect, useId, useRef, useState } from 'react';
import { updateModelSponsorship } from '@/lib/api/facemarket.js';
import { BOTTOM_WAIST_SIZES, SPONSORSHIP_NOTICE, TOP_SIZES, sponsorshipDraft, sponsorshipPayload } from './sponsorshipOptions.js';
import s from './SponsorshipSettings.module.css';

export function SponsorshipFields({ value, onChange, disabled = false }) {
  const id = useId();
  const field = (name, next) => onChange({ ...value, [name]: next });
  const toggle = <button type="button" role="switch" aria-checked={value.sponsorshipEnabled} aria-label="의류 협찬 받기" className={s.toggle} disabled={disabled}
        onClick={() => field('sponsorshipEnabled', !value.sponsorshipEnabled)}><span /></button>;
  return <section className={s.card} aria-labelledby={`${id}-title`}>
    <div className={s.heading}>
      <div><h2 id={`${id}-title`}>의류 협찬 받기</h2><span className={s.optional}>선택 · 기본 꺼짐</span></div>
      {!value.sponsorshipEnabled && toggle}
    </div>
    <p className={s.description}>켜 두면 셀러가 바로 협찬을 요청할 수 있어요. 옷을 받으면 입고 찍어서 피드에 올려요. 옷은 내 것, 현금은 없어요. <strong>언제든 끌 수 있어요.</strong></p>
    <p className={s.description}>협찬 옷도 위에서 정한 허용 품목 안에서만 와요.</p>
    <ul className={s.rules}>
      <li>옷을 받은 뒤 <strong>3일 이내</strong>, 본인 인스타그램 피드에 <strong>1회</strong> 게시해요.</li>
      <li>게시물은 <strong>30일</strong> 유지해요.</li>
      <li>옷은 반납하지 않아요.</li>
    </ul>
    {value.sponsorshipEnabled && <div className={s.fields}>
      <div className={s.field}><div className={s.fieldHeading}><label htmlFor={`${id}-instagram`}>인스타 계정</label>{toggle}</div><input id={`${id}-instagram`} autoCapitalize="none" autoCorrect="off" spellCheck={false} maxLength={31}
        value={value.instagramHandle} onChange={event => field('instagramHandle', event.target.value)} disabled={disabled} placeholder="아이디를 적어주세요" aria-describedby={`${id}-instagram-help`} />
        <small id={`${id}-instagram-help`}>공개 계정을 적어주세요. @는 빼고 저장돼요.</small></div>
      <div className={s.field}><label htmlFor={`${id}-followers`}>팔로워 수</label><input id={`${id}-followers`} inputMode="numeric" value={value.instagramFollowers}
        onChange={event => field('instagramFollowers', event.target.value)} disabled={disabled} placeholder="0" aria-describedby={`${id}-followers-help`} />
        <small id={`${id}-followers-help`}>본인 입력이에요. 저장한 날짜가 기준일로 표시돼요.</small></div>
      <div className={s.field}><label htmlFor={`${id}-top`}>상의 사이즈</label><select id={`${id}-top`} value={value.sizeTop} onChange={event => field('sizeTop', event.target.value)} disabled={disabled}>
        <option value="">골라 주세요</option>{TOP_SIZES.map(size => <option key={size} value={size}>{size}</option>)}</select></div>
      <div className={s.field}><label htmlFor={`${id}-bottom`}>하의 사이즈</label><select id={`${id}-bottom`} value={value.sizeBottomWaist} onChange={event => field('sizeBottomWaist', event.target.value)} disabled={disabled}>
        <option value="">허리 인치를 골라 주세요</option>{BOTTOM_WAIST_SIZES.map(size => <option key={size} value={size}>{size}인치</option>)}</select></div>
      <label className={s.consent} htmlFor={`${id}-consent`}>
        <input id={`${id}-consent`} type="checkbox" checked={value.profileConsent === true} disabled={disabled} onChange={event => field('profileConsent', event.target.checked)} />
        <span><strong>프로필 정보 수집에 동의합니다</strong> <em className={s.required}>필수</em>
          {/* 이 두 문장은 서버 facemarket_sponsorship.SPONSORSHIP_NOTICES 와 글자 그대로 같아야 해요(동의 이력 해시). */}
          <small>협찬 모델을 찾는 로그인 셀러에게 인스타 계정, 팔로워 수, 사이즈가 보여요. 배송지는 요청이 온 뒤에 받아요. 협찬을 끄거나 탈퇴하면 이 정보는 바로 지워요.</small><small>동의하지 않아도 모델 등록과 얼굴 사용료 정산은 이용할 수 있어요.</small></span>
      </label>
    </div>}
    <p className={s.notice}>{SPONSORSHIP_NOTICE}</p>
  </section>;
}

export function ModelSponsorshipSettings({ model, onModelChange, disabled = false }) {
  const [draft, setDraft] = useState(() => sponsorshipDraft(model));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [failed, setFailed] = useState(false);
  const [retryDraft, setRetryDraft] = useState(null);
  const inFlight = useRef(false);
  const latestModel = useRef(model);
  const saved = useRef({ id: model?.id, values: sponsorshipDraft(model) });
  useEffect(() => { latestModel.current = model; }, [model]);
  useEffect(() => {
    const next = sponsorshipDraft(model);
    const previous = saved.current;
    saved.current = { id: model?.id, values: next };
    if (previous.id !== model?.id) {
      setDraft(next); setRetryDraft(null); setMessage(''); setFailed(false);
      return;
    }
    setDraft(current => Object.fromEntries(Object.keys(next).map(key => [
      key, current[key] === previous.values[key] ? next[key] : current[key],
    ])));
  }, [model?.id, model?.sponsorshipEnabled, model?.instagramHandle, model?.instagramFollowers, model?.sizeTop, model?.sizeBottomWaist, model?.sponsorshipProfileConsentAt]);
  const save = async next => {
    if (disabled || inFlight.current || !model?.id) return;
    let payload;
    try { payload = sponsorshipPayload(next); }
    catch (error) { setFailed(true); setMessage(error.message); return; }
    inFlight.current = true; setBusy(true); setMessage(''); setFailed(false);
    const modelId = model.id;
    try {
      const updated = await updateModelSponsorship(modelId, payload, 'model_mypage');
      if (latestModel.current?.id !== modelId) return;
      const updatedModel = { ...latestModel.current, ...updated };
      saved.current = { id: modelId, values: sponsorshipDraft(updatedModel) };
      onModelChange?.(updatedModel);
      // 끄면 서버가 계정·팔로워·사이즈와 동의 기록을 지워요. 화면도 서버 값으로 맞춰요.
      setDraft(sponsorshipDraft(updatedModel));
      setRetryDraft(null);
      setMessage(payload.sponsorshipEnabled ? '협찬 참여 설정을 저장했어요.' : '협찬을 껐어요. 계정과 사이즈 정보는 지웠어요.');
    } catch (error) {
      if (latestModel.current?.id !== modelId) return;
      const enabled = saved.current.values.sponsorshipEnabled;
      setDraft(current => ({ ...current, sponsorshipEnabled: enabled }));
      setRetryDraft(next); setFailed(true);
      setMessage(`${error.message || '설정을 저장하지 못했어요.'} 마지막으로 확인한 설정은 ${enabled ? '켜짐' : '꺼짐'}이에요. 다시 저장해 주세요.`);
    }
    finally { inFlight.current = false; setBusy(false); }
  };
  const change = next => {
    if (disabled || busy) return;
    setDraft(next); setMessage(''); setFailed(false); setRetryDraft(null);
    if (!next.sponsorshipEnabled && saved.current.values.sponsorshipEnabled) void save(next);
  };
  return <div className={s.settings}>
    <SponsorshipFields value={draft} onChange={change} disabled={disabled || busy} />
    {!disabled && <div className={s.actions}><button type="button" className={s.save} disabled={busy} onClick={() => save(retryDraft || draft)}>{busy ? '저장 중이에요…' : retryDraft ? '다시 저장하기' : '협찬 설정 저장'}</button></div>}
    {message && <p className={s.message} role={failed ? 'alert' : 'status'}>{message}</p>}
  </div>;
}
