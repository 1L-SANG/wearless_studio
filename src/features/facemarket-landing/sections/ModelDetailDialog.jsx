import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { getSponsorshipInterest, requestSponsorshipInterest } from '@/lib/api/facemarket.js';
import { SPONSORSHIP_UNAVAILABLE_MESSAGE, sponsorshipInterestErrorMessage } from '../../model/sponsorshipCredential.js';
/* =============================================================
   모델 상세 서랍 — 카드를 누르면 **오른쪽에서** 나온다(사용자 지시: "팝업처럼이 아니라 우측에서
   기존 화면 흐려지면서"). 뒤 화면은 어둡게 + 흐리게 깔린다. 서랍 자체는 검정 바탕.

   구성(2026-09-08 오너 확정):
     위   확대샷 | 전신샷 두 장이 가장자리까지 붙고, 아래쪽 그라데이션 위에 이름·성별·나이대가 얹힌다.
     아래 가격(맨 위) → "상세페이지 만들러 가기" 버튼(모델 이름은 넣지 않는다, 9/8 오너) → 키 | 몸무게 → 허용 품목 | 제외 품목
          (한 줄에 양쪽) → 착용 사이즈(칸 나눠서, 맨 밑).
     끝   검증 한 줄(신분증 본인확인 완료 · 라이선스 증서 발급 완료).
   지역·스타일 태그·사용 기한·'이 모델의 컷' 갤러리는 일부러 없다(같은 날 오너 결정).

   모델 객체는 publicModels.js 가 만든 한 가지 모양이다(실모델·가상 예시 공통). 값이 없는 줄은 그리지
   않는다 — 예시는 착용 사이즈·제외 품목이 없고, 실모델은 몸무게·사이즈를 아직 받지 않는다.
   예시는 이름 옆에 '예시' 배지를 둔다 — 목록에만 고지가 있으면 이 창을 캡처했을 때 같이 안 나간다.

   접근성: 열릴 때 포커스를 창 안으로 들이고, Esc·바깥 클릭으로 닫고, 닫을 때 원래 있던 곳으로
   포커스를 되돌린다. Tab 은 창 안에서만 돈다.
   ============================================================= */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { sellerStudioUrl } from '../data/publicModels.js';
import { pricingParts } from '@/lib/facemarketPricing.js';
import s from '../BrowseModels.module.css';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/* 한 줄에 두 칸(왼쪽·오른쪽). 값이 없는 칸은 빼고, 남은 칸이 하나면 그 칸이 줄을 다 쓴다. */
function Pair({ cells }) {
  const live = cells.filter((cell) => cell.dd);
  if (live.length === 0) return null;
  return (
    <div className={s.dialogPair}>
      {live.map((cell) => (
        <div className={s.dialogCell} key={cell.dt}>
          <dt>{cell.dt}</dt>
          <dd>{cell.dd}</dd>
        </div>
      ))}
    </div>
  );
}

export function ModelDetailDialog({ model, onClose }) {
  const { session, loading, openLogin } = useAuth();
  const [interested, setInterested] = useState(false);
  const [interestBusy, setInterestBusy] = useState(false);
  const [interestError, setInterestError] = useState('');
  const submittingInterest = useRef(false);
  const interestVersion = useRef(0);
  const userId = session?.user?.id;
  const sponsorship = model.kind === 'real' ? model.sponsorship : null;
  useEffect(() => {
    const version = ++interestVersion.current;
    submittingInterest.current = false;
    setInterested(false); setInterestBusy(false); setInterestError('');
    if (!userId || !sponsorship?.enabled) return undefined;
    let alive = true;
    getSponsorshipInterest(model.id).then(result => {
      if (alive && interestVersion.current === version) setInterested(result.interested === true);
    }).catch(error => {
      // 유효한 협찬 동의 증명서가 없는 모델은 서버가 404 로 답해요.
      if (alive && interestVersion.current === version) setInterestError(error?.status === 404 ? SPONSORSHIP_UNAVAILABLE_MESSAGE : '알림 신청 상태를 확인하지 못했어요. 다시 신청해도 중복되지 않아요.');
    });
    return () => { alive = false; interestVersion.current += 1; };
  }, [userId, model.id, sponsorship?.enabled]);
  const requestInterest = async () => {
    if (loading || submittingInterest.current || interested) return;
    if (!session) { onClose(); openLogin(`/models?model=${encodeURIComponent(model.id)}`); return; }
    // 신청을 시작하면 그 전에 보낸 조회 응답은 더 이상 화면을 바꾸지 않아요.
    const version = ++interestVersion.current;
    submittingInterest.current = true; setInterestBusy(true); setInterestError('');
    try {
      await requestSponsorshipInterest(model.id);
      if (interestVersion.current === version) setInterested(true);
    } catch (error) {
      if (interestVersion.current === version) setInterestError(sponsorshipInterestErrorMessage(error, '알림 신청을 저장하지 못했어요. 다시 시도해 주세요.'));
    } finally {
      if (interestVersion.current === version) { submittingInterest.current = false; setInterestBusy(false); }
    }
  };
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  // 창을 열기 직전에 포커스를 쥐고 있던 요소. 닫을 때 여기로 돌려준다.
  const restoreRef = useRef(null);

  const handleKeyDown = useCallback(
    (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;

      const nodes = panelRef.current?.querySelectorAll(FOCUSABLE);
      if (!nodes || nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      // 양 끝에서 감아 돈다. shift+Tab 으로 첫 요소에서 뒤로 나가는 경우도 같이 막는다.
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    },
    [onClose],
  );

  useEffect(() => {
    restoreRef.current = document.activeElement;
    closeRef.current?.focus();

    /* 뒤 배경이 스크롤되지 않게. 스크롤바가 사라지며 페이지가 옆으로 튀지 않도록 사라진
       폭만큼 패딩으로 메운다(맥의 오버레이 스크롤바에서는 0 이라 아무 일도 안 일어난다). */
    const { body } = document;
    const gap = window.innerWidth - document.documentElement.clientWidth;
    const prevOverflow = body.style.overflow;
    const prevPad = body.style.paddingRight;
    body.style.overflow = 'hidden';
    if (gap > 0) body.style.paddingRight = `${gap}px`;

    return () => {
      body.style.overflow = prevOverflow;
      body.style.paddingRight = prevPad;
      restoreRef.current?.focus?.();
    };
  }, []);

  const isExample = model.kind === 'example';
  const meta = [model.gender, model.ageBand].filter(Boolean).join(' · ');
  const { license, sizeParts } = model;

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div className={s.backdrop} onKeyDown={handleKeyDown} role="presentation">
      {/* 바깥을 눌러 닫는 건 배경 레이어가 받는다. 창 안의 클릭은 여기까지 올라오지 않게
          막는다 — 안 막으면 창 안 여백을 누를 때마다 닫힌다. */}
      <button aria-label="닫기" className={s.backdropHit} onClick={onClose} tabIndex={-1} type="button" />
      <div
        aria-labelledby="fm-model-dialog-title"
        aria-modal="true"
        className={s.dialog}
        ref={panelRef}
        role="dialog"
      >
        <button aria-label="닫기" className={s.dialogClose} onClick={onClose} ref={closeRef} type="button">
          <Icon name="x" size={18} stroke={2} />
        </button>

        {/* 사진 두 장 + 그라데이션 + 이름. 사진 밑 '확대샷·전신샷' 글자는 붙이지 않는다(alt 에만). */}
        <div className={s.dialogHero}>
          <img alt={`${model.name} 확대샷`} className={s.dialogHeroShot} src={model.closeup} />
          {model.fullbody
            ? <img alt={`${model.name} 전신샷`} className={s.dialogHeroShot} src={model.fullbody} />
            : <div className={s.dialogHeroEmpty}>전신샷 준비 중</div>}
          <div className={s.dialogHeroText}>
            <h2 className={s.dialogName} id="fm-model-dialog-title">{model.name}</h2>
            {(meta || isExample) && (
              <p className={s.dialogMeta}>
                {meta && <span>{meta}</span>}
                {isExample && <span className={s.exampleBadge}>예시</span>}
              </p>
            )}
          </div>
        </div>

        <div className={s.dialogBody}>
          {/* 가격이 맨 위, 그 아래 셀러가 바로 가는 버튼(2026-09-08 오너 지시). 예시 모델은 갈 곳이
              없으므로 버튼을 두지 않는다 — 눌러도 아무 일 없는 버튼은 안 둔다. */}
          {/* 가격은 플랫폼 공통 고정값(lib/facemarketPricing.js). 모델별 단가는 초기에 쓰지 않는다. */}
          <div className={s.dialogPriceRow}>
            <span className={s.dialogPrice}>{pricingParts().perCut}</span>
            <span className={s.dialogPriceNote}>/ {pricingParts().monthly} ({pricingParts().cap})</span>
          </div>
          {!isExample && (
            <a className={s.dialogCta} href={sellerStudioUrl(model.id)}>
              상세페이지 만들러 가기
              <Icon name="arrowRight" size={16} stroke={2} />
            </a>
          )}

          {/* 키 | 몸무게, 허용 품목 | 제외 품목 — 한 줄에 양쪽. 사용 기한은 쓰지 않는다(오너 지시). */}
          <dl className={s.dialogRows}>
            <Pair cells={[{ dt: '키', dd: model.height }, { dt: '몸무게', dd: model.weight }]} />
            {license && (
              <Pair cells={[
                { dt: '허용 품목', dd: license.uses.length ? license.uses.join(' · ') : '미정' },
              ]} />
            )}
          </dl>

          {/* 착용 사이즈는 맨 밑에, 칸을 나눠 읽기 쉽게. 값이 있을 때만. */}
          {sizeParts && (
            <div className={s.dialogSizes}>
              <span className={s.dialogSizesTitle}>착용 사이즈</span>
              <dl className={s.dialogSizeCells}>
                {sizeParts.top && <div><dt>상의</dt><dd>{sizeParts.top}</dd></div>}
                {sizeParts.bottom && <div><dt>하의</dt><dd>{sizeParts.bottom}</dd></div>}
                {sizeParts.shoe && <div><dt>신발</dt><dd>{sizeParts.shoe}</dd></div>}
              </dl>
            </div>
          )}

          {sponsorship?.enabled && <section className={s.dialogSponsorship} aria-label="의류 협찬">
            <h3>의류 협찬</h3>
            <dl className={s.dialogRows}>
              {/* 비로그인에는 계정·팔로워·사이즈를 보이지 않아요(프로필 정보 수집 동의 범위). */}
              {!sponsorship.masked && <Pair cells={[{ dt: '인스타 계정', dd: <a href={sponsorship.instagramUrl} target="_blank" rel="noopener noreferrer">@{sponsorship.instagramHandle}</a> },
                { dt: '팔로워 수', dd: `${sponsorship.instagramFollowers.toLocaleString('ko-KR')}명` }]} />}
              {!sponsorship.masked && <Pair cells={[{ dt: '상의', dd: sponsorship.sizeTop }, { dt: '하의 허리', dd: `${sponsorship.sizeBottomWaist}인치` }]} />}
              <Pair cells={[{ dt: '게시 조건', dd: '옷 받은 뒤 7일 이내 1회 · 90일 유지' }]} />
            </dl>
            {sponsorship.masked && <p className={s.dialogSponsorshipNote}>셀러 계정으로 로그인하면 인스타 계정, 팔로워 수, 사이즈를 볼 수 있어요.</p>}
            {!sponsorship.masked && <p className={s.dialogSponsorshipNote}>팔로워 수는 본인 입력이에요.{sponsorship.reportedAt && !Number.isNaN(Date.parse(sponsorship.reportedAt)) ? ` ${new Date(sponsorship.reportedAt).toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul' })} 기준이에요.` : ''}</p>}
            <p className={s.dialogSponsorshipNote}>옷 증정에 대한 본인 인스타 피드 게시예요. 현금 보상은 없어요. 게시물 재사용은 별도 합의가 필요해요.</p>
            <button type="button" className={s.interestButton} onClick={requestInterest} disabled={loading || interestBusy || interested}>
              {interested ? '알림 신청됨' : interestBusy ? '알림 신청 중이에요…' : '협찬 요청하기 · 준비 중, 알림 받기'}
            </button>
            <p className={s.dialogSponsorshipNote} role="status">{interested ? '이 모델의 협찬 요청 기능이 준비되면 알려드릴게요.' : '지금은 알림 신청만 받아요. 실제 협찬 요청이나 배송은 시작되지 않아요.'}</p>
            {interestError && <p className={s.dialogSponsorshipNote} role="alert">{interestError}</p>}
          </section>}

          {model.verified && (
            <p className={s.dialogVerify}>
              <span>신분증 본인확인 완료</span>
              <span>라이선스 증서 발급 완료</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
