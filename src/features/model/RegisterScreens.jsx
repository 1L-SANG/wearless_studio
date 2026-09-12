import { Info } from 'lucide-react';
import { Link } from 'react-router-dom';
import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';
import { STANDARD_UNIT_PRICE_KRW, MONTHLY_PASS_PRICE_KRW, MONTHLY_PASS_CUTS, MODEL_SHARE, formatKrw } from '../facemarket-landing/facemarketTerms.js';
import { SLOTS, PHOTO_GROUPS, REGISTER_BODIES, photoSlotKey, toggleRegisterCategory } from './registerSlots.js';
import { RegisterIllustration } from './RegisterIllustration.jsx';
import s from './ModelRegister.module.css';

export const heading = (title, description) => <div className={s.intro}><h1 tabIndex={-1}>{title}</h1>{description && <p className={s.description}>{description}</p>}</div>;
const legalLink = (to, text = '전문 보기') => <Link className={s.textLink} to={to} target="_blank" rel="noreferrer">{text}</Link>;

export function renderConsent(consents, setConsents, withdrawalOpen, setWithdrawalOpen, busy = false) {
  const allChecked = consents.every(Boolean);
  const someChecked = consents.some(Boolean) && !allChecked;
  const check = (index, label) => <label className={s.consentLabel} htmlFor={`consent-${index}`}><input id={`consent-${index}`} type="checkbox" checked={consents[index]} disabled={busy} onChange={(event) => setConsents((current) => current.map((value, i) => i === index ? event.target.checked : value))} /><span>{label}<span className={s.required}> (필수)</span></span></label>;
  return <>
    {heading('먼저 본인인지 확인해요', '필수 항목을 확인한 뒤 신분증 인증을 진행해요.')}
    <ul className={s.reassurance}>
      <li><span>✓</span><p>원본 얼굴 이미지는 비공개 저장소에 보관되며 노출되지 않습니다.</p></li>
      <li><span>✓</span><p>철저한 본인인증을 위해 신분증 검사를 진행합니다. (이외 목적 사용X)</p></li>
      <li className={s.withdrawalRow}><span>✓</span><div className={s.withdrawalInfo} onMouseEnter={() => setWithdrawalOpen(true)} onMouseLeave={(event) => { if (!event.currentTarget.contains(document.activeElement)) setWithdrawalOpen(false); }}>
        <p>언제든지 모델 등록을 잠시 중지하거나 철회할 수 <span className={s.keepTogether}>있습니다 <button type="button" className={s.infoButton} aria-label="그만두면 이렇게 돼요" aria-describedby="withdrawal-tooltip" onMouseEnter={() => setWithdrawalOpen(true)} onFocus={() => setWithdrawalOpen(true)} onBlur={() => setWithdrawalOpen(false)} onClick={() => setWithdrawalOpen(true)} onKeyDown={(event) => { if (event.key === 'Escape') setWithdrawalOpen(false); }}><Info size={18} aria-hidden="true" /></button></span></p>
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
        <p><b>수집:</b> 얼굴·전신 사진 18장, 그걸로 만든 얼굴 참조 자산과 얼굴 특징정보</p>
        <p><b>목적:</b> 얼굴 참조 자산 제작, 내가 정한 조건 안에서 착용컷 생성, 결과 품질 검사</p>
        <p><b>보유:</b> 라이선스가 유지되는 동안, 철회하면 30일 안에 파기</p>
        <p><b>거부:</b> 동의하지 않을 수 있지만 등록은 진행할 수 없어요.</p>{legalLink('/biometric-consent')}
      </div></div>
      <div className={`${s.consentCard} ${s.noticeCard}`}><span className={s.tag}>안내 · 동의 아님</span><p className={s.consentLabel}><span>얼굴 정보는 착용컷을 만들기 위해 미국의 서버와 저장소, 이미지 생성 서비스로 전송돼요. 계약 이행을 위한 위탁이라 동의 대신 알려 드려요(개인정보 보호법 제28조의8 제1항 제3호).</span></p><div className={s.legalSummary}>
        <p><b>받는 곳:</b> Amazon Web Services, Cloudflare, Google, OpenAI(미국)</p><p><b>언제:</b> 사진을 저장할 때, 착용컷을 만들 때</p>
        <p><b>무엇:</b> 사진, 얼굴 특징정보, 얼굴 참조 자산</p><p><b>기간:</b> 저장은 라이선스 유지 동안, 생성은 최대 55일 뒤 삭제</p>
        <p><b>거부:</b> 처리방침 제6조의 연락처로 알릴 수 있지만, 서버와 생성 엔진이 국외에 있어 등록은 진행할 수 없어요.</p>{legalLink('/overseas-transfer', '국외 이전 안내 전문 보기')}
      </div></div>
    </div>
  </>;
}

export function renderPhotos({ sub, enrollment, previews, busy, onFile, onRemove, editGroup }) {
  if (sub === 4) return <>
    {heading('18장을 확인해 주세요', '내 사진으로 모두 채웠는지 한 번 더 봐 주세요.')}
    <div className={s.reviewRows}>{PHOTO_GROUPS.map((group, index) => {
      const slots = SLOTS.filter((slot) => slot.group === group.id);
      return <div className={s.reviewRow} key={group.id}>
        <h2>{group.title} {slots.length}장</h2>
        <div className={s.reviewThumbnails} tabIndex={0} role="region" aria-label={`${group.title} 사진`}>
          {slots.map((slot) => <div className={s.reviewThumbnail} key={slot.key}>{previews[slot.key] ? <img src={previews[slot.key]} alt={`${slot.n}번 내 사진`} width="60" height="80" /> : <span>{slot.n}번 사진을 저장했어요</span>}</div>)}
        </div>
        <button type="button" className={s.textLink} aria-label={`${group.title} 사진 고치기`} onClick={() => editGroup(index + 1)} disabled={busy}>고치기</button>
      </div>;
    })}</div>
  </>;
  const group = PHOTO_GROUPS[sub - 1];
  const uploaded = new Set((enrollment?.photos || []).map(photoSlotKey));
  return <>
    {heading('사진을 등록해요', '모델 이미지를 만들려면 여러 방향의 사진이 필요해요. 각 카드의 예시와 같은 구도로 올려 주세요.')}
    {sub === 1 && <div className={s.tips}><strong>친구나 셀카봉, 카메라 타이머를 준비하면 편해요.</strong><p>혼자 찍는다면 폰 카메라의 타이머를 쓰고, 밝은 곳에서 필터 없이 찍어요. 모자·마스크·선글라스는 벗어요.</p></div>}
    {sub > 1 && <p className={s.encouragement}>{sub === 2 ? '거의 다 왔어요. 방금 하신 대로 아래 이미지들을 찍어 주세요.' : '이제 마지막이에요. 아래 이미지들만 찍으면 끝나요.'}</p>}
    <div className={s.groupHeading}><span className={s.tag}>{group.badge}</span></div>
    <div className={s.photoGrid}>{SLOTS.filter((slot) => slot.group === group.id).map((slot) => {
      const filled = uploaded.has(slot.key);
      return <div className={s.slotCard} key={slot.key}>
        <label className={`${s.slot} ${filled ? s.filled : ''}`}>
          <input className={s.fileInput} type="file" accept="image/*,.heic,.heif,.hif" capture="user" disabled={busy} aria-label={`${slot.n}번 ${slot.title} ${filled ? '사진 바꾸기' : '사진 올리기'}`} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) onFile(slot.key, file); }} />
          {previews[slot.key] ? <img src={previews[slot.key]} alt={`${slot.n}번 내 사진`} width="180" height="240" /> : <RegisterIllustration className={s.person} framing={slot.framing} angle={slot.angle} />}
          <span className={s.slotBadge}>{filled ? '✓ 내 사진' : '예시'}</span>
          <span className={s.slotAction}>{filled ? '바꾸기' : '내 사진으로'}</span><span className={s.slotBottom}><span className={s.caption}>{slot.n} · {slot.title}<span className={s.tapLabel}>탭해서 {filled ? '바꾸기' : '올리기'}</span></span><span className={s.slotHint}>{filled && !previews[slot.key] ? '사진을 저장했어요' : slot.hint}</span></span>
        </label>
        {filled && <button type="button" className={s.removePhoto} aria-label={`${slot.n}번 사진 지우기`} disabled={busy} onClick={() => onRemove(slot.key)}>×</button>}
      </div>;
    })}</div>
  </>;
}

export function renderConditions({ terms, setTerms, body, setBody, busy, priceAgreed, setPriceAgreed }) {
  const descriptions = ['상의, 하의, 아우터, 원피스 같은 평상복이에요.', '운동복, 레깅스, 요가복이에요.', '라운지웨어, 파자마예요.'];
  return <>
    {heading('사용 조건을 정해요', '내 얼굴을 쓸 수 있는 옷과 몸의 두께를 알려 주세요. 여기서 정한 조건이 셀러 화면에 그대로 보여요.')}
    <h2 className={s.sectionTitle}>내 얼굴을 쓸 수 있는 옷<span className={s.required}>*</span></h2>
    <div className={s.categories}>{BRAND_USE_CATEGORIES.map((category, index) => { const on = terms.allowedUse.includes(category); const last = on && terms.allowedUse.length === 1; return <div className={s.categoryRow} key={category}><div><h3>{category}</h3><p>{descriptions[index]}</p></div><button type="button" className={s.switch} role="switch" aria-label={`${category} 허용`} aria-checked={on} disabled={busy || last} onClick={() => setTerms((current) => ({ ...current, allowedUse: toggleRegisterCategory(current.allowedUse, category) }))}><span /></button>{last && <p className={s.categoryNote}>최소 한 가지는 켜 두어야 해요</p>}{!on && <p className={s.categoryNote}>끈 옷은 셀러가 고를 수 없어요. 언제든 다시 켤 수 있어요.</p>}</div>; })}</div>
    <section className={s.bodySection}>
      <h2>몸의 두께<span className={s.optional}>선택</span></h2><p className={s.bodyDescription}>셀러가 옷을 고를 때 참고해요. 고르지 않아도 괜찮아요.</p>
      <div className={s.bodyGrid} aria-label="몸의 두께, 선택 항목">{REGISTER_BODIES.map((item) => <button key={item.value} type="button" className={`${s.bodyCard} ${body === item.value ? s.selected : ''}`} aria-pressed={body === item.value} disabled={busy} onClick={() => setBody(item.value)}><span className={s.bodyArt}><RegisterIllustration className={s.person} width={item.width} /></span><span className={s.bodyName}>{item.label}</span>{body === item.value && <span className={s.bodyCheck}>✓</span>}</button>)}</div>
      <p className={s.bodyOptional}>{body ? <>고른 두께는 {REGISTER_BODIES.find((item) => item.value === body)?.label}예요. <button type="button" className={s.textLink} onClick={() => setBody(null)} disabled={busy}>선택 지우기</button></> : '지금 고르지 않아도 괜찮아요.'}</p>
    </section>
    <div className={s.priceCard}><h2>셀러 사용료 규칙</h2><ul>
      <li>셀러가 한 번 모델을 이용한다면 {formatKrw(STANDARD_UNIT_PRICE_KRW)} / 월정액으로 이용한다면 {formatKrw(MONTHLY_PASS_PRICE_KRW)}을 결제해요. (1개월 당 {MONTHLY_PASS_CUTS}회 제한)</li>
      <li>이 중 {MODEL_SHARE * 100}% 금액을 모델님께 자동 정산해드려요.</li>
    </ul></div>
    <div className={s.contractLink}>{legalLink('/license-agreement', '계약 요약과 전문 보기')}</div>
    <label className={`${s.consentLabel} ${s.priceConsent}`} htmlFor="price-agreed"><input id="price-agreed" type="checkbox" checked={priceAgreed} disabled={busy} onChange={(event) => setPriceAgreed(event.target.checked)} /><span>셀러 사용료 규칙에 동의해요<span className={s.required}> (필수)</span></span></label>
  </>;
}
