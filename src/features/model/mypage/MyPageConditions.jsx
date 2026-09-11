import { useRef, useState } from 'react';
import { ChevronRight, SlidersHorizontal } from 'lucide-react';
import { updateLicenseTerms } from '@/lib/api/facemarket.js';
import { BRAND_USE_CATEGORIES } from '@/lib/brandUseCategories.js';
import { seoulDateKey } from '@/lib/datetime.js';
import { MyPageDialog } from './MyPageDialog.jsx';
import { MyPageCertificate } from './MyPageCertificate.jsx';
import { toggleAllowedCategory } from './conditionState.js';
import s from './MyPage.module.css';

export function MyPageConditions({ license, model, revoked = false, registering = false, onLicenseChange, onCertificate, onManage }) {
  const [editing, setEditing] = useState(false);
  const [allowed, setAllowed] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const saving = useRef(false);
  const canEdit = !revoked && license?.status === 'active';
  const openEditor = () => { setAllowed(license?.allowedUse || []); setMessage(''); setEditing(true); };
  const toggle = category => {
    const next = toggleAllowedCategory(allowed, category);
    if (next.length === allowed.length && next.every(value => allowed.includes(value))) {
      setMessage('옷 종류는 최소 1개를 켜 두어야 해요.');
      return;
    }
    setMessage('');
    setAllowed(next);
  };
  const save = async event => {
    event.preventDefault();
    if (saving.current || !canEdit) return;
    if (!allowed.length) { setMessage('옷 종류는 최소 1개를 켜 두어야 해요.'); return; }
    saving.current = true;
    setBusy(true);
    setMessage('');
    try {
      const updated = await updateLicenseTerms(license.id, { allowedUse: allowed });
      onLicenseChange({ ...license, ...updated });
      setEditing(false);
      setMessage('사용 조건을 저장했어요.');
    } catch (error) { setMessage(error.message || '저장하지 못했어요. 다시 시도해 주세요.'); }
    finally { saving.current = false; setBusy(false); }
  };
  return <>
    <div className={s.panelHeading}><div><h2>내 라이선스</h2><p className={s.panelCaption}>증서와 내가 정한 사용 범위를 한곳에서 확인해요.</p></div></div>
    <div className={s.licenseLayout}>
      <div><MyPageCertificate license={license} model={model} revoked={revoked} />
        <button type="button" className={s.certificateOpen} onClick={onCertificate}>내 증서 보기</button></div>
      <div className={s.conditionsDetail}><h3>사용 조건</h3><dl>
        <div className={s.detailPair}><dt>허용한 의류</dt><dd>{license?.allowedUse?.join(' · ') || '아직 선택하지 않았어요'}</dd></div>
        <div className={s.detailPair}><dt>사용 범위</dt><dd>패션 상품의 상세페이지</dd></div>
        <div className={s.detailPair}><dt>라이선스 기간</dt><dd>{revoked ? `${seoulDateKey(license?.updatedAt, '').replaceAll('-', '. ')} 철회` : '철회하기 전까지'}</dd></div>
      </dl>
        {!revoked && <button type="button" className={s.quietButton} disabled={!canEdit} onClick={openEditor}>사용 조건 편집<ChevronRight className={s.icon} aria-hidden="true" /></button>}
      </div>
    </div>
    {message && !editing && <p className={s.muted} role="status">{message}</p>}
    <section className={s.licenseManagement}><div><h3>활동 관리</h3>
      <p>{revoked ? '종료된 라이선스와 데이터를 확인해요.' : registering ? '등록 이후의 활동 설정을 확인해요.' : '잠시 쉬어가기와 라이선스 종료를 관리해요.'}</p></div>
      <button type="button" className={s.quietButton} onClick={onManage}><SlidersHorizontal className={s.icon} aria-hidden="true" />활동 설정<ChevronRight className={s.icon} aria-hidden="true" /></button>
    </section>
    {editing && <MyPageDialog title="사용 조건 편집" busy={busy} onClose={() => setEditing(false)}>
      <form onSubmit={save}><p>내 이미지를 사용할 수 있는 의류 범위를 정해요.</p>
        <fieldset className={s.checkOptions} disabled={busy}><legend className={s.visuallyHidden}>허용할 의류</legend>
          {BRAND_USE_CATEGORIES.map(category => <label className={s.checkOption} key={category}>
            <input type="checkbox" checked={allowed.includes(category)} onChange={() => toggle(category)} />{category}</label>)}
        </fieldset>
        {message && <p role="alert" className={s.error}>{message}</p>}
        <div className={s.actionRow}><button type="button" className={s.quietButton} onClick={() => setEditing(false)} disabled={busy}>취소</button>
          <button type="submit" className={s.primaryButton} disabled={busy || !canEdit}>적용하기</button></div>
      </form>
    </MyPageDialog>}
  </>;
}
