import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { updateLicenseTerms } from '@/lib/api/facemarket.js';
import { BRAND_USE_CATEGORIES } from '@/lib/brandUseCategories.js';
import { seoulDate } from '@/lib/datetime.js';
import { pricingLine } from '../../../lib/facemarketPricing.js';
import { MyPageDialog } from './MyPageDialog.jsx';
import { toggleAllowedCategory } from './conditionState.js';
import s from './MyPage.module.css';

export function licenseValidity(license) {
  if (!license) return '확인 중이에요';
  return license.licenseValidUntil ? `${seoulDate(license.licenseValidUntil)}까지` : '영구(철회하기 전까지)';
}

export function MyPageConditions({ license, model, revoked = false, compact = false, onLicenseChange }) {
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [validDays, setValidDays] = useState(license?.licenseValidUntil ? '365' : 'forever');
  const saving = useRef(false);
  const allowed = license?.allowedUse || [];
  const disabled = revoked || !license || license.status !== 'active' || busy;
  const save = async patch => {
    if (saving.current || disabled) return;
    saving.current = true;
    setBusy(true);
    setMessage('');
    try {
      const updated = await updateLicenseTerms(license.id, patch);
      onLicenseChange(updated);
      setMessage('사용 조건을 저장했어요.');
      if ('validDays' in patch) setDialog(null);
    } catch (error) { setMessage(error.message || '저장하지 못했어요. 다시 시도해 주세요.'); }
    finally { saving.current = false; setBusy(false); }
  };
  const toggle = category => {
    const next = toggleAllowedCategory(allowed, category);
    if (next.length === allowed.length && next.every(value => allowed.includes(value))) {
      setMessage('옷 종류는 최소 1개를 켜 두어야 해요.');
      return;
    }
    void save({ allowedUse: next });
  };
  const switches = <div className={s.conditionList}>{BRAND_USE_CATEGORIES.map(category => <div className={s.conditionRow} key={category}>
    <h3>{category}</h3><div className={s.switchState}><span>{allowed.includes(category) ? '켬' : '끔'}</span>
      <button type="button" role="switch" aria-label={`${category} 허용`} aria-checked={allowed.includes(category)}
        className={s.switchControl} onClick={() => toggle(category)} disabled={disabled}><span className={s.switch} aria-hidden="true"><span /></span></button>
    </div>
  </div>)}</div>;
  const links = <div className={s.summaryLinks}>
    {!revoked && <><button type="button" className={s.textLink} onClick={() => setDialog('conditions')}>조건 바꾸기</button><span aria-hidden="true">·</span></>}
    <button type="button" className={s.textLink} onClick={() => setDialog('certificate')}>증서 보기</button>
    {!compact && !revoked && <><span aria-hidden="true">·</span><button type="button" className={s.textLink} onClick={() => setDialog('seller')}>셀러 화면에서 보기</button></>}
  </div>;
  return <>
    {compact ? links : <section id="conditions" className={s.dashboardSection} aria-labelledby="conditions-title">
      <h2 id="conditions-title">사용 조건</h2>{switches}
      <p className={s.conditionsExpiry}>유효기간 {licenseValidity(license)} · 증서 {license?.vcId || '발급 준비 중이에요'}</p>
      {links}
    </section>}
    {message && !dialog && <p className={s.muted} role="status">{message}</p>}
    {dialog && <MyPageDialog title={dialog === 'conditions' ? '조건 바꾸기' : dialog === 'certificate' ? '증서 보기' : '셀러 화면에서 보기'} busy={busy} onClose={() => setDialog(null)}>
      {message && <p className={s.muted} role="status">{message}</p>}
      {dialog === 'conditions' && <>
        {(!license || license.status !== 'active') && <p>증서 발급을 마치면 여기서 조건을 바꿀 수 있어요.</p>}
        {switches}
        <form onSubmit={event => { event.preventDefault(); void save({ validDays: validDays === 'forever' ? null : Number(validDays) }); }}>
          <label className={s.validityField} htmlFor="license-validity">유효기간
            <select id="license-validity" value={validDays} onChange={event => setValidDays(event.target.value)} disabled={disabled}>
              <option value="365">오늘부터 1년</option><option value="730">오늘부터 2년</option><option value="forever">영구</option>
            </select>
          </label>
          <p className={s.muted}>이용 가격은 플랫폼 표준가예요. {pricingLine()}이에요. 이 금액의 70%가 내 몫이에요.</p>
          <p className={s.muted}>조건 변경은 별도 기록으로 남고 증서는 다시 발급하지 않아요.</p>
          <button className={s.primary} type="submit" disabled={disabled}>유효기간 저장하기</button>
        </form>
      </>}
      {dialog === 'certificate' && <>
        <dl className={s.recordTable}><div><dt>증서 번호</dt><dd>{license?.vcId || '발급 준비 중이에요'}</dd></div>
          {license?.createdAt && <div><dt>발급일</dt><dd>{seoulDate(license.createdAt)}</dd></div>}
          <div><dt>유효기간</dt><dd>{licenseValidity(license)}</dd></div>
        </dl>
        {license?.id && <Link className={s.textLink} to={`/verify/${encodeURIComponent(license.id)}`}>증서 확인 주소 열기</Link>}
      </>}
      {dialog === 'seller' && <>
        <div className={s.identity}>{model?.coverImageUrl && <img className={s.avatar} src={model.coverImageUrl} alt="대표 이미지" />}<strong>{model?.displayName || '내 모델'}</strong></div>
        <p>{model?.status === 'verified' && license?.status === 'active' ? '셀러에게 보이는 모델 정보예요.' : '현재는 비공개 상태예요.'}</p>
        <p>{allowed.join(' · ')}</p><p>{pricingLine()}</p>
      </>}
    </MyPageDialog>}
  </>;
}
