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
import { useCallback, useEffect, useRef } from 'react';
import { Icon } from '@/components/ui.jsx';
import { sellerStudioUrl } from '../data/publicModels.js';
import s from '../BrowseModels.module.css';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';
const won = (n) => `${Number(n).toLocaleString('ko-KR')}원`;

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
          {license?.unitPrice != null && (
            <div className={s.dialogPriceRow}>
              <span className={s.dialogPrice}>{won(license.unitPrice)}</span>
              <span className={s.dialogPriceNote}>건당</span>
              {license.monthlyPrice != null && (
                <span className={s.dialogPriceNote}>· 월 {won(license.monthlyPrice)}, 한 쇼핑몰 30일 무제한</span>
              )}
            </div>
          )}
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
                { dt: '제외 품목', dd: license.excluded.length ? license.excluded.join(' · ') : '없음' },
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
