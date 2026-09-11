import { Link } from 'react-router-dom';
import { BRAND_USE_CATEGORIES } from '../../lib/brandUseCategories.js';
import { STANDARD_UNIT_PRICE_KRW, MONTHLY_PASS_PRICE_KRW, MONTHLY_PASS_CUTS, MONTHLY_OVERAGE_KRW, MODEL_SHARE, formatKrw } from '../facemarket-landing/facemarketTerms.js';
import { SLOTS, PHOTO_GROUPS, REGISTER_BODIES, photoProgress, photoSlotKey, toggleRegisterCategory, CONSENT_VERSION } from './registerSlots.js';
import { RegisterIllustration } from './RegisterIllustration.jsx';
import s from './ModelRegister.module.css';

export const heading = (title, description) => <div className={s.intro}><h1 tabIndex={-1}>{title}</h1>{description && <p className={s.description}>{description}</p>}</div>;
const legalLink = (to, text = '전문 보기') => <Link className={s.textLink} to={to} target="_blank" rel="noreferrer">{text}</Link>;

export function renderConsent(consents, setConsents) {
  const check = (index, label) => <label className={s.consentLabel} htmlFor={`consent-${index}`}><input id={`consent-${index}`} type="checkbox" checked={consents[index]} onChange={(event) => setConsents((current) => current.map((value, i) => i === index ? event.target.checked : value))} /><span>{label}<span className={s.required}> *</span></span></label>;
  return <>
    {heading('먼저 본인인지 확인해요', '얼굴은 민감정보라 실명 확인이 먼저예요. 정부 모바일 신분증으로 확인이 끝나야 사진을 올릴 수 있어요.')}
    <ul className={s.reassurance}>
      <li><span>✓</span><p>원본 사진은 비공개 저장소에만 두고, 쇼핑몰은 완성된 착용컷만 받아요.</p></li>
      <li><span>✓</span><p>신분증에서 받는 건 이름(가려서), 생년월일, 확인 결과뿐이에요. 신분증 사진은 받지 않아요.</p></li>
      <li><span>✓</span><p>그만두면 새 사용이 바로 멈추고 30일 안에 얼굴 정보를 파기해요.</p></li>
    </ul>
    <details className={s.withdrawal}><summary>그만두면 이렇게 돼요</summary><ol className={s.numbered}>
      <li><strong>새로운 사용이 그 자리에서 멈춰요</strong><p>사유는 필요 없고, 위약금도 없어요.</p></li>
      <li><strong>얼굴 정보는 30일 안에 파기해요</strong><p>백업은 90일 안에 지워요. 끝나면 알려 드려요.</p></li>
      <li><strong>이미 발행된 착용컷은 그 건의 기간이 끝날 때까지만 남아요</strong></li>
    </ol></details>
    <div className={s.consentList}>
      <div className={s.consentCard}>{check(0, 'FaceMarket 모델 이용약관에 동의하고 개인정보 처리방침을 확인했어요.')}<div className={s.legalLinks}>{legalLink('/terms', '이용약관 전문 보기')}{legalLink('/privacy', '처리방침 전문 보기')}</div></div>
      <div className={s.consentCard}><span className={s.tag}>필수 · 법정</span>{check(1, '나의 얼굴 정보를 아래와 같이 수집·생성·이용하는 것에 동의해요.')}<div className={s.legalSummary}>
        <p><b>수집:</b> 얼굴·전신 사진 18장, 그걸로 만든 얼굴 참조 자산과 얼굴 특징정보</p>
        <p><b>목적:</b> 얼굴 참조 자산 제작, 내가 정한 조건 안에서 착용컷 생성, 결과 품질 검사</p>
        <p><b>보유:</b> 라이선스가 유지되는 동안, 철회하면 30일 안에 파기</p>
        <p><b>거부:</b> 동의하지 않을 수 있지만 등록은 진행할 수 없어요.</p>{legalLink('/biometric-consent')}
      </div></div>
      <div className={s.consentCard}><span className={s.tag}>필수 · 법정</span>{check(2, '나의 얼굴 정보가 아래와 같이 국외로 이전되는 것에 동의해요.')}<div className={s.legalSummary}>
        <p><b>받는 곳:</b> Amazon Web Services, Cloudflare, Google, OpenAI(미국)</p><p><b>언제:</b> 사진을 저장할 때, 착용컷을 만들 때</p>
        <p><b>무엇:</b> 사진, 얼굴 특징정보, 얼굴 참조 자산</p><p><b>기간:</b> 저장은 라이선스 유지 동안, 생성은 최대 55일 뒤 삭제</p>
        <p><b>거부:</b> 가능하지만 서버가 국외에 있어 등록은 진행할 수 없어요.</p>{legalLink('/overseas-consent')}
      </div></div>
    </div>
  </>;
}

export function renderIdentity(step, enrollment, busy, retry, editConsent) {
  const done = step === '1c';
  return <>
    {heading('먼저 본인인지 확인해요', '정부 모바일 신분증으로 만 19세 이상인지, 본인 명의인지 확인해요.')}
    <div className={s.consentDone}><span>✓ 동의 완료 · 3항목</span><button className={s.textLink} type="button" onClick={editConsent} disabled={busy}>수정</button></div>
    <div className={s.verifyCard}>
      <div className={s.verifySymbol} aria-hidden="true">{done ? '✓' : <span className={s.spinner} />}</div>
      <div><h2><span className={s.desktopOnly}>모바일 신분증 앱으로 QR을 스캔하세요</span><span className={s.mobileOnly}>모바일 신분증 앱에서 확인해 주세요</span></h2>
        <p className={s.description}>휴대폰의 정부 모바일 신분증 앱(운전면허증·주민등록증)에서 인증해 주세요. 인증창에 표시된 QR 또는 앱 열기를 이용해요.</p>
        <p className={s.statusPill} role="status">{done ? '✓ 확인됐어요' : busy ? '인증 대기 중 · 앱에서 확인해 주세요' : '인증을 시작해 주세요'}</p>
        {!done && <button className={s.textLink} type="button" onClick={retry} disabled={busy}>{busy ? '인증창에서 확인해 주세요' : '인증창 열기'}</button>}
      </div>
    </div>
    {done && <div className={s.identityResult}><strong>{enrollment?.nameMasked || '본인 명의'} · 성인 확인됨</strong><span>이름은 가려서 저장했어요</span></div>}
  </>;
}

export function renderPhotos({ sub, enrollment, previews, busy, onFile, onRemove, body, setBody, setSub }) {
  const titles = ['얼굴 8장을 채워 주세요', '상반신 5장이에요', '전신 5장이에요', '몸의 두께도 알려 주면 좋아요', '18장을 확인해 주세요'];
  const description = sub === 4 ? '셀러가 옷을 고를 때 참고해요. 고르지 않아도 다음으로 갈 수 있어요.' : sub === 5 ? '내 사진으로 모두 채웠는지 한 번 더 봐 주세요. 바꾸고 싶은 칸을 누르면 다시 채울 수 있어요.' : '예시 칸을 누르고 내 사진으로 바꿔 넣어요. 같은 옷, 같은 장소에서 한 번에 찍는 게 좋아요. 사진은 컷을 만들 때만 쓰고 공개되지 않아요.';
  const progress = photoProgress(enrollment?.photos);
  const group = PHOTO_GROUPS[sub - 1];
  const uploaded = new Set((enrollment?.photos || []).map(photoSlotKey));
  const slots = sub === 5 ? SLOTS : SLOTS.filter((slot) => slot.group === group?.id);
  return <>
    {heading(titles[sub - 1], description)}
    {sub === 1 && <div className={s.tips}><strong>친구나 셀카봉, 카메라 타이머를 준비하면 편해요.</strong><p>혼자 찍는다면 폰 카메라의 타이머를 쓰고, 밝은 곳에서 필터 없이 찍어요. 모자·마스크·선글라스는 벗어요.</p></div>}
    {sub !== 4 && <div className={s.photoCounter} aria-live="polite"><div><span>사진 18장</span><strong>{progress.count} / 18장</strong></div><progress aria-label="사진 채운 장수" value={progress.count} max={18} /></div>}
    {group && <div className={s.groupHeading}><h2>{group.title} · {photoProgress(enrollment?.photos, group.id).total}장</h2><span className={s.tag}>{group.badge}</span><strong>{photoProgress(enrollment?.photos, group.id).count} / {photoProgress(enrollment?.photos, group.id).total}</strong></div>}
    {sub === 4 ? <>
      <div className={s.bodyGrid} aria-label="몸의 두께, 선택 항목">{REGISTER_BODIES.map((item) => <button key={item.value} type="button" className={`${s.bodyCard} ${body === item.value ? s.selected : ''}`} aria-pressed={body === item.value} disabled={busy} onClick={() => setBody(item.value)}><span className={s.bodyArt}><RegisterIllustration className={s.person} width={item.width} /></span><span className={s.bodyName}>{item.label}</span>{body === item.value && <span className={s.bodyCheck}>✓</span>}</button>)}</div>
      <p className={s.bodyOptional}>{body ? <>고른 두께는 {REGISTER_BODIES.find((item) => item.value === body)?.label}예요. <button type="button" className={s.textLink} onClick={() => setBody(null)} disabled={busy}>선택 지우기</button></> : '지금 고르지 않아도 괜찮아요.'}</p>
    </> : <div className={sub === 5 ? s.contactSheet : s.photoGrid}>{slots.map((slot) => {
      const filled = uploaded.has(slot.key);
      return <div className={`${s.slotCard} ${sub === 5 ? s.contactCard : ''}`} key={slot.key}>
        <label className={`${s.slot} ${filled ? s.filled : ''}`}>
          <input className={s.fileInput} type="file" accept="image/*,.heic,.heif,.hif" capture="user" disabled={busy} aria-label={`${slot.n}번 ${slot.title} ${filled ? '사진 바꾸기' : '사진 올리기'}`} onChange={(event) => { const file = event.target.files?.[0]; event.target.value = ''; if (file) onFile(slot.key, file); }} />
          {previews[slot.key] ? <img src={previews[slot.key]} alt={`${slot.n}번 내 사진`} width="180" height="240" /> : <RegisterIllustration className={s.person} framing={slot.framing} angle={slot.angle} />}
          <span className={s.slotBadge}>{filled ? '✓ 내 사진' : '예시'}</span>
          {sub === 5 ? <span className={s.contactEdit}>{filled ? '바꾸기' : '올리기'}</span> : <><span className={s.slotAction}>{filled ? '바꾸기' : '내 사진으로'}</span><span className={s.slotBottom}><span className={s.caption}>{slot.n} · {slot.title}<span className={s.tapLabel}>탭해서 {filled ? '바꾸기' : '올리기'}</span></span><span className={s.slotHint}>{filled && !previews[slot.key] ? '사진을 저장했어요' : slot.hint}</span></span></>}
        </label>
        {filled && sub !== 5 && <button type="button" className={s.removePhoto} aria-label={`${slot.n}번 사진 지우기`} disabled={busy} onClick={() => onRemove(slot.key)}>×</button>}
        {sub === 5 && <span className={s.contactNumber}>{slot.n}</span>}
      </div>;
    })}</div>}
    {sub === 5 && <div className={s.summaryBody}><span>몸의 두께 · <strong>{REGISTER_BODIES.find((item) => item.value === body)?.label || '고르지 않았어요'}</strong></span><button type="button" className={s.textLink} onClick={() => setSub(4)} disabled={busy}>고치기</button></div>}
  </>;
}

export function renderConditions(terms, setTerms) {
  const descriptions = ['상의, 하의, 아우터, 원피스 같은 평상복이에요.', '운동복, 레깅스, 요가복이에요.', '라운지웨어, 파자마예요.'];
  return <>
    {heading('내 얼굴을 쓸 수 있는 옷을 정해요', '세 가지 모두 켜진 채 시작해요. 원하지 않는 것만 끄면 돼요. 여기서 정한 조건이 셀러 화면에 그대로 보이고, 조건 밖 요청은 셀러 쪽에서 아예 막혀요.')}
    <h2 className={s.sectionTitle}>내 얼굴을 쓸 수 있는 옷</h2>
    <div className={s.categories}>{BRAND_USE_CATEGORIES.map((category, index) => { const on = terms.allowedUse.includes(category); const last = on && terms.allowedUse.length === 1; return <div className={s.categoryRow} key={category}><div><h3>{category}</h3><p>{descriptions[index]}</p></div><button type="button" className={s.switch} role="switch" aria-label={`${category} 허용`} aria-checked={on} disabled={last} onClick={() => setTerms((current) => ({ ...current, allowedUse: toggleRegisterCategory(current.allowedUse, category) }))}><span /></button>{last && <p className={s.categoryNote}>최소 한 가지는 켜 두어야 해요</p>}{!on && <p className={s.categoryNote}>끈 옷은 셀러가 고를 수 없어요. 언제든 다시 켤 수 있어요.</p>}</div>; })}</div>
    <section className={s.validitySection}><h2>유효기간</h2><div className={s.chips} aria-label="유효기간">{[{ value: 365, label: '1년' }, { value: 730, label: '2년' }, { value: null, label: '영구' }].map((item) => <button type="button" key={item.label} className={`${s.chip} ${terms.validDays === item.value ? s.selected : ''}`} aria-pressed={terms.validDays === item.value} onClick={() => setTerms((current) => ({ ...current, validDays: item.value }))}>{item.label}</button>)}</div><p className={s.description}>{terms.validDays == null ? '철회하기 전까지 유효해요. 언제든 철회할 수 있어요.' : `발급일부터 ${terms.validDays === 365 ? '1년' : '2년'} 동안 유효해요. 끝나기 한 달 전에 메일로 알려 드려요.`}</p></section>
    <div className={s.priceCard}><h2>가격은 플랫폼이 정해요</h2><p>셀러는 한 건에 <strong>{formatKrw(STANDARD_UNIT_PRICE_KRW)}</strong>, 월 이용권은 <strong>{formatKrw(MONTHLY_PASS_PRICE_KRW)}({MONTHLY_PASS_CUTS}건)</strong>이에요. 초과하면 건당 {formatKrw(MONTHLY_OVERAGE_KRW)}이에요.</p><p>셀러가 낸 금액의 <strong>{MODEL_SHARE * 100}%</strong>가 내 몫이에요. 한 건이면 <strong>{formatKrw(STANDARD_UNIT_PRICE_KRW * MODEL_SHARE)}</strong>이에요.</p><p>지급은 아직 시작 전이에요. 표시 금액은 지급 확정액이 아니에요.</p><Link className={s.textLink} to="/status#earnings">마이페이지에서 내역 보기</Link></div>
  </>;
}

export function renderCertificate(enrollment) {
  return <>
    {heading('마지막으로 증서를 발급해요', '증서는 내가 언제 무엇에 동의했는지 증명하는 서명된 기록이에요. 나중에 다툼이 생기면 이게 근거가 돼요.')}
    <h2 className={s.sectionTitle}>증서에 담기는 것</h2><dl className={s.recordTable}>
      {[
        ['모델 식별자', enrollment?.modelId || '발급할 때 정해져요'], ['라이선스 번호', '발급할 때 정해져요'], ['발급 일시', '발급할 때 정해져요'],
        ['얼굴 파일 해시', enrollment?.faceHash || '저장된 사진으로 발급할 때 기록해요'], ['계약 문서 버전', 'v1.0'], ['동의 문서 버전', enrollment?.consentDocumentVersion || CONSENT_VERSION],
      ].map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
    </dl><p className={s.certificateNote}>조건은 등록이 끝난 뒤에도 바꿀 수 있어요. 바꿔도 증서를 다시 발급하지 않고, 바뀐 기록만 남아요.</p>{legalLink('/license-agreement', '계약 요약과 전문 보기')}
  </>;
}
