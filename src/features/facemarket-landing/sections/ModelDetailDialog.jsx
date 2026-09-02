/* =============================================================
   모델 상세 창 — '모델 정보 보기'를 누르면 열린다.
   구성(사용자 스케치): 얼굴 이미지 · 이름 · 신체 사이즈 | 라이선스 조건 · 모델 예시 이미지.

   내용은 전부 가상 모델의 지어낸 값이다(browseModels.js). 창 안에도 '예시' 배지를 둔다 —
   목록에만 고지가 있으면 이 창을 열어 놓고 캡처했을 때 그 사실이 같이 안 나간다.

   접근성: 열릴 때 포커스를 창 안으로 들이고, Esc·바깥 클릭으로 닫고, 닫을 때 원래 있던
   곳으로 포커스를 되돌린다. Tab 은 창 안에서만 돈다 — 안 가두면 뒤에 깔린 카드들로 포커스가
   새어 나가고(그쪽은 aria-hidden 도 아니다) 화면 낭독 순서가 뒤죽박죽이 된다.
   ============================================================= */
import { useCallback, useEffect, useRef } from 'react';
import { Icon } from '@/components/ui.jsx';
import s from '../BrowseModels.module.css';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

function formatValidity(days) {
  if (days % 365 === 0) return `${days / 365}년`;
  return `${days}일`;
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

  const { license } = model;

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

        <div className={s.dialogFaces}>
          {model.faces.map((src) => (
            <img alt="" className={s.dialogFace} key={src} src={src} />
          ))}
        </div>

        <div className={s.dialogHead}>
          <h2 className={s.dialogName} id="fm-model-dialog-title">{model.name}</h2>
          <span className={s.exampleBadge}>예시</span>
        </div>

        <div className={s.dialogCols}>
          <section className={s.dialogCol}>
            <h3 className={s.dialogColTitle}>신체 사이즈</h3>
            <dl className={s.specList}>
              <div className={s.specRow}><dt>키</dt><dd>{model.height}cm</dd></div>
              <div className={s.specRow}><dt>몸무게</dt><dd>{model.weight}kg</dd></div>
            </dl>
          </section>

          {/* 라이선스 조건의 세 항목은 실제 발급 폼(ModelLicense.jsx TermsStep)이 받는 값과
              같은 것들이다 — 허용 품목·건당 단가·유효기간. 값만 예시다. */}
          <section className={s.dialogCol}>
            <h3 className={s.dialogColTitle}>라이선스 조건</h3>
            <dl className={s.specList}>
              <div className={s.specRow}>
                <dt>허용 품목</dt>
                <dd>{license.uses.join(' · ')}</dd>
              </div>
              <div className={s.specRow}>
                <dt>건당 단가</dt>
                <dd>{license.unitPrice.toLocaleString('ko-KR')}원</dd>
              </div>
              <div className={s.specRow}>
                <dt>유효기간</dt>
                <dd>{formatValidity(license.validDays)}</dd>
              </div>
            </dl>
          </section>
        </div>

        {/* 전신 예시는 그 모델 본인의 사진이 있을 때만 온다(browseModels.js). 없는 모델에게
            남의 전신 사진을 돌려 쓰면 이름 밑에 다른 얼굴이 서므로, 칸째로 접는다. */}
        {model.examples.length > 0 && (
          <section className={s.dialogExamples}>
            <h3 className={s.dialogColTitle}>모델 예시 이미지</h3>
            <div className={s.exampleGrid}>
              {model.examples.map((src) => (
                <img alt="" className={s.exampleImage} key={src} src={src} />
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
