import { SponsorshipFields } from './SponsorshipSettings.jsx';
import { Camera, ImagePlus, Info, RefreshCw, Trash2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';
import { STANDARD_UNIT_PRICE_KRW, MONTHLY_PASS_PRICE_KRW, MONTHLY_PASS_CUTS, MODEL_SHARE, formatKrw } from '../facemarket-landing/facemarketTerms.js';
import { SLOTS, PHOTO_GROUPS, PHOTO_REVIEW_SUB, REGISTER_BODIES, photoSlotKey, photoProgress, toggleRegisterCategory } from './registerSlots.js';
import { RegisterIllustration } from './RegisterIllustration.jsx';
import { PhotoPoseIllustration } from './PhotoPoseIllustration.jsx';
import { PhotoPreparation, ReferencePhotoNotice } from './PhotoGuide.jsx';
import s from './ModelRegister.module.css';

export const heading = (title, description) => <div className={s.intro}><h1 tabIndex={-1}>{title}</h1>{description && <p className={s.description}>{description}</p>}</div>;
const legalLink = (to, text = '전문 보기') => <Link className={s.textLink} to={to} target="_blank" rel="noreferrer">{text}</Link>;

export function renderConsent(consents, setConsents, withdrawalOpen, setWithdrawalOpen, busy = false) {
  const allChecked = consents.every(Boolean);
  const someChecked = consents.some(Boolean) && !allChecked;
  const check = (index, label) => <label className={s.consentLabel} htmlFor={`consent-${index}`}><input id={`consent-${index}`} type="checkbox" checked={consents[index]} disabled={busy} onChange={(event) => setConsents((current) => current.map((value, i) => i === index ? event.target.checked : value))} /><span>{label}<span className={s.required}> (필수)</span></span></label>;
  return <>
    {heading('먼저 본인인지 확인해요', '약관에 동의한 뒤 본인인증을 진행해요.')}
    <ul className={s.reassurance}>
      <li><span>✓</span><p>원본 사진은 공개되지 않아요.</p></li>
      <li><span>✓</span><p>신분증은 본인확인에만 사용해요.</p></li>
      <li className={s.withdrawalRow}><span>✓</span><div className={s.withdrawalInfo} onMouseEnter={() => setWithdrawalOpen(true)} onMouseLeave={(event) => { if (!event.currentTarget.contains(document.activeElement)) setWithdrawalOpen(false); }}>
        <p>언제든 등록을 중지하거나 철회할 수 <span className={s.keepTogether}>있어요. <button type="button" className={s.infoButton} aria-label="그만두면 이렇게 돼요" aria-describedby="withdrawal-tooltip" onMouseEnter={() => setWithdrawalOpen(true)} onFocus={() => setWithdrawalOpen(true)} onBlur={() => setWithdrawalOpen(false)} onClick={() => setWithdrawalOpen(true)} onKeyDown={(event) => { if (event.key === 'Escape') setWithdrawalOpen(false); }}><Info size={18} aria-hidden="true" /></button></span></p>
        <div id="withdrawal-tooltip" className={s.withdrawalTooltip} role="tooltip" hidden={!withdrawalOpen} onMouseEnter={() => setWithdrawalOpen(true)}><ul>
          <li>새로운 사용이 그 자리에서 멈춰요. 사유는 필요 없고, 위약금도 없어요.</li>
          <li>얼굴 정보는 30일 안에 파기해요. 백업은 90일 안에 지워요. 끝나면 알려 드려요.</li>
          <li>이미 발행된 착용컷은 철회 후에도 기존 이용 조건에 따라 남아요.</li>
        </ul></div>
      </div></li>
    </ul>
    <div className={s.consentList}>
      <label className={s.consentAll} htmlFor="register-consent-all">
        <input ref={(node) => { if (node) node.indeterminate = someChecked; }} id="register-consent-all" type="checkbox" checked={allChecked} aria-checked={someChecked ? 'mixed' : allChecked} disabled={busy} onChange={(event) => setConsents([event.target.checked, event.target.checked])} />
        <span>필수 항목 전체 동의</span>
      </label>
      <div className={s.consentCard}>{check(0, 'FaceMarket 모델 이용약관에 동의하고 개인정보 처리방침을 확인했어요.')}<div className={s.legalLinks}>{legalLink('/terms', '이용약관 전문 보기')}{legalLink('/privacy', '처리방침 전문 보기')}</div></div>
      <div className={s.consentCard}><span className={s.tag}>필수 · 법정</span>{check(1, '나의 얼굴 정보를 아래와 같이 수집·생성·이용하는 것에 동의해요.')}<div className={s.legalSummary}>
        <p><b>수집:</b> 얼굴·옆모습·뒷모습 사진 18장, 그걸로 만든 얼굴 참조 자산과 얼굴 특징정보</p>
        <p><b>목적:</b> 같은 사람인지 확인, 얼굴 참조 자산 제작, 내가 정한 조건 안에서 착용컷 생성, 결과 품질 검사</p>
        <p><b>학습:</b> 18장 중 12장으로 FaceMarket이 직접 학습해요. 학습을 외부 AI 회사에 맡기지 않아요.</p>
        <p><b>확인:</b> 학습 전 담당자가 사진 품질을 확인해요. 열람 기록은 남아요.</p>
        <p><b>사용 시점:</b> 테스트컷을 확인·승인한 뒤부터 착용컷 생성에 써요.</p>
        <p><b>보유:</b> 라이선스가 유지되는 동안, 철회하면 30일 안에 파기</p>
        <p><b>거부:</b> 동의하지 않을 수 있지만 등록은 진행할 수 없어요.</p>{legalLink('/biometric-consent')}
      </div></div>
    </div>
  </>;
}

function renderPhotoCard({ slot, filled, preview, busy, onFile, onRemove, reason }) {
  const hintId = `photo-hint-${slot.key}`;
  const inputId = `photo-input-${slot.key}`;
  return <div className={s.slotCard} key={slot.key}>
    <input id={inputId} className={s.fileInput} type="file" accept="image/*,.heic,.heif,.hif" disabled={busy} aria-label={`${slot.n}번 ${slot.title} ${filled ? '다시 촬영하거나 사진 교체' : '사진 찍기 또는 선택'}`} aria-describedby={hintId} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) onFile(slot.key, file); }} />
    <label className={`${s.slot} ${filled ? s.filled : ''}`} htmlFor={inputId}>
      <span className={s.slotImage}>
        {preview ? <img src={preview} alt={`${slot.n}번 내 사진`} width="180" height="240" /> : filled ? <span className={s.savedPhoto}>✓<span>사진이 저장됐어요</span></span> : <PhotoPoseIllustration className={s.photoPose} slot={slot} />}
        <span className={s.slotBadge}>{String(slot.n).padStart(2, '0')}{filled ? ' · 저장 완료' : reason ? ' · 다시 찍기' : ' · 촬영 예시'}</span>
        {!filled && !reason && <span className={s.slotImageCue}><Camera size={16} aria-hidden="true" />예시를 눌러 사진 추가</span>}
      </span>
      <span className={s.slotBottom}>
        <span className={s.caption}>{slot.title}</span>
        <span className={s.slotHint} id={hintId}>{reason || slot.hint}</span>
      </span>
    </label>
    <div className={s.slotActions}>
      <label className={s.slotAction} htmlFor={inputId} aria-disabled={busy}>
        {filled || reason ? <RefreshCw size={15} aria-hidden="true" /> : <ImagePlus size={15} aria-hidden="true" />}
        {filled ? '재촬영·교체' : reason ? '재촬영·올리기' : '사진 찍기·선택'}
      </label>
      {filled && onRemove && <button type="button" className={s.removePhoto} aria-label={`${slot.n}번 사진 삭제`} disabled={busy} onClick={() => onRemove(slot.key)}><Trash2 size={15} aria-hidden="true" />삭제</button>}
    </div>
  </div>;
}

export function renderPhotos({ sub, enrollment, previews, busy, onFile, onRemove, editGroup }) {
  const photos = enrollment?.photos || [];
  const uploaded = new Set(photos.map(photoSlotKey));
  if (sub === PHOTO_REVIEW_SUB) return <>
    {heading(`${SLOTS.length}장을 확인해 주세요`, '사진이 번호별 자세와 맞는지 확인해 주세요.')}
    <div className={s.reviewRows}>{PHOTO_GROUPS.map((group, index) => {
      const slots = SLOTS.filter((slot) => slot.group === group.id);
      return <div className={s.reviewRow} key={group.id}>
        <h2>{group.title} {slots.length}장</h2>
        <div className={s.reviewThumbnails} tabIndex={0} role="region" aria-label={`${group.title} 사진`}>
          {slots.map((slot) => <div className={s.reviewThumbnail} key={slot.key}>{previews[slot.key] ? <img src={previews[slot.key]} alt={`${slot.n}번 ${slot.title}`} width="60" height="80" /> : <span>{slot.n}번 {uploaded.has(slot.key) ? '저장 완료' : '사진이 없어요'}</span>}</div>)}
        </div>
        <button type="button" className={s.textLink} aria-label={`${group.title} 사진 고치기`} onClick={() => editGroup(index + 1)} disabled={busy}>고치기</button>
      </div>;
    })}</div>
  </>;
  const group = PHOTO_GROUPS[sub - 1];
  const slots = SLOTS.filter((slot) => slot.group === group.id);
  const progress = photoProgress(photos);
  const groupProgress = photoProgress(photos, group.id);
  return <>
    {heading('등록 사진 18장을 올려요', '번호에 맞춰 올려 주세요. 나중에 이어서 등록해도 돼요.')}
    <div className={s.photoProgress}>
      <div><span>전체 사진</span><strong aria-live="polite">{progress.count} / {progress.total}장 저장</strong></div>
      <progress aria-label="전체 사진 업로드 진행률" value={progress.count} max={progress.total} />
      <ol className={s.photoStages}>{PHOTO_GROUPS.map((item, index) => {
        const saved = photoProgress(photos, item.id);
        return <li key={item.id} aria-current={sub === index + 1 ? 'step' : undefined} data-complete={saved.complete}><span>{index + 1}. {item.title}</span><b>{saved.complete ? '✓ ' : ''}{saved.count}/{saved.total}</b></li>;
      })}</ol>
    </div>
    {sub === 1 && <details className={s.captureHelp}><summary>촬영 준비 보기</summary><PhotoPreparation /></details>}
    <div className={s.photoGuideLink}><Link className={s.textLink} to="/photo-guide" target="_blank" rel="noreferrer">18장 촬영 가이드 열기 (새 탭)</Link><span>JPG · PNG · WEBP · HEIC</span></div>
    <div className={s.groupHeading}>
      <span className={s.tag}>{sub} / {PHOTO_GROUPS.length} · {slots[0].n}~{slots.at(-1).n}번</span>
      <h2>{group.action} {slots.length}장</h2><p>{group.note}</p>
    </div>
    <p className={s.uploadInstruction}><Camera size={17} aria-hidden="true" /><span>예시 이미지를 눌러 카메라로 찍거나 앨범에서 선택하세요. 올리면 예시가 내 사진으로 바뀌어요.</span></p>
    <div className={s.photoGrid} aria-busy={busy}>{slots.map((slot) => renderPhotoCard({ slot, filled: uploaded.has(slot.key), preview: previews[slot.key], busy, onFile, onRemove }))}</div>
    <p className={s.groupStatus} role="status">{groupProgress.complete ? '이 단계 사진을 모두 저장했어요.' : `${groupProgress.total - groupProgress.count}장을 더 올려 주세요.`}</p>
  </>;
}

/* 관리자가 "이 칸은 다시 찍어 주세요" 한 뒤의 화면.

   등록은 이미 끝났고 모델도 있다 — 여기서 받는 건 **요청된 칸뿐**이다. 다 올리면 서버가
   저절로 '확인 대기'로 되돌려 관리자 큐에 다시 띄운다(_consume_reshoot_slot). 사유는
   관리자가 적은 문장을 그대로 보여 준다 — 무엇이 문제였는지가 다시 찍는 데 필요한 전부다. */
export function renderReshoot({ enrollment, previews, busy, onFile }) {
  const requested = enrollment?.reshootSlots || [];
  const reasons = new Map(requested.map((item) => [item.slot, item.reason]));
  const slots = SLOTS.filter((slot) => reasons.has(slot.key));
  return <>
    {heading('요청받은 사진을 다시 올려요', '아래 사유를 보고 해당 사진만 다시 찍어 주세요.')}
    {slots.length === 0 && <p className={s.description} role="status">다시 찍을 칸이 없어요. 잠시 후 다시 확인해 주세요.</p>}
    {slots.some((slot) => ['sh_front2', 'sh_gaze_left', 'sh_gaze_right'].includes(slot.key)) && <ReferencePhotoNotice />}
    <p className={s.description}><Link className={s.textLink} to="/photo-guide" target="_blank" rel="noreferrer">촬영 가이드 보기 (새 탭)</Link></p>
    <div className={s.photoGrid}>{slots.map((slot) => renderPhotoCard({ slot, preview: previews[slot.key], busy, onFile, reason: reasons.get(slot.key) || slot.hint }))}</div>
  </>;
}

export function renderConditions({ terms, setTerms, body, setBody, busy, priceAgreed, setPriceAgreed, sponsorship, setSponsorship, sponsorshipLoading }) {
  const descriptions = ['상의, 하의, 아우터, 원피스 같은 평상복이에요.', '운동복, 레깅스, 요가복이에요.', '라운지웨어, 파자마예요.'];
  return <>
    {heading('사용 조건을 정해요', '내 얼굴을 사용할 옷 종류를 골라 주세요.')}
    <h2 className={s.sectionTitle}>내 얼굴을 쓸 수 있는 옷<span className={s.required}>*</span></h2>
    <div className={s.categories}>{BRAND_USE_CATEGORIES.map((category, index) => { const on = terms.allowedUse.includes(category); const last = on && terms.allowedUse.length === 1; return <div className={s.categoryRow} key={category}><div><h3>{category}</h3><p>{descriptions[index]}</p></div><button type="button" className={s.switch} role="switch" aria-label={`${category} 허용`} aria-checked={on} disabled={busy || last} onClick={() => setTerms((current) => ({ ...current, allowedUse: toggleRegisterCategory(current.allowedUse, category) }))}><span /></button>{last && <p className={s.categoryNote}>한 가지 이상 선택해 주세요.</p>}{!on && <p className={s.categoryNote}>이 종류의 옷에는 내 얼굴을 쓸 수 없어요.</p>}</div>; })}</div>
    <section className={s.bodySection}>
      <h2>몸의 두께<span className={s.optional}>선택</span></h2><p className={s.bodyDescription}>셀러가 옷을 고를 때 참고해요.</p>
      <div className={s.bodyGrid} aria-label="몸의 두께, 선택 항목">{REGISTER_BODIES.map((item) => <button key={item.value} type="button" className={`${s.bodyCard} ${body === item.value ? s.selected : ''}`} aria-pressed={body === item.value} disabled={busy} onClick={() => setBody(item.value)}><span className={s.bodyArt}><RegisterIllustration className={s.person} width={item.width} /></span><span className={s.bodyName}>{item.label}</span>{body === item.value && <span className={s.bodyCheck}>✓</span>}</button>)}</div>
      <p className={s.bodyOptional}>{body ? <>고른 두께는 {REGISTER_BODIES.find((item) => item.value === body)?.label}예요. <button type="button" className={s.textLink} onClick={() => setBody(null)} disabled={busy}>선택 지우기</button></> : '선택하지 않아도 돼요.'}</p>
    </section>
    <div className={s.priceCard}><h2>셀러 사용료 규칙</h2><ul>
      <li>셀러 이용료: 1회 {formatKrw(STANDARD_UNIT_PRICE_KRW)}, 월 {formatKrw(MONTHLY_PASS_PRICE_KRW)} (월 {MONTHLY_PASS_CUTS}회)</li>
      <li>결제 금액의 {MODEL_SHARE * 100}%를 정산받아요.</li>
    </ul></div>
    {sponsorship && <SponsorshipFields value={sponsorship} onChange={setSponsorship} disabled={busy || sponsorshipLoading} />}
    <div className={s.contractLink}>{legalLink('/license-agreement', '계약 요약과 전문 보기')}</div>
    <label className={`${s.consentLabel} ${s.priceConsent}`} htmlFor="price-agreed"><input id="price-agreed" type="checkbox" checked={priceAgreed} disabled={busy} onChange={(event) => setPriceAgreed(event.target.checked)} /><span>셀러 사용료 규칙에 동의해요<span className={s.required}> (필수)</span></span></label>
  </>;
}
