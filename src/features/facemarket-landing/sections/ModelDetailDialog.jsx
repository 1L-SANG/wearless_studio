/* =============================================================
   모델 상세 서랍 — 카드를 누르면 **오른쪽에서** 나온다(사용자 지시: "팝업처럼이 아니라 우측에서
   기존 화면 흐려지면서"). 뒤 화면은 어둡게 + 흐리게 깔린다.

   구성(2026-09-07 오너 스케치): 위에 **확대샷 | 전신샷** 두 장 나란히 · 아래 이름 · 그 아래
   두 칸 "신체 사이즈 | 라이선스 조건" · 우상단 X. 확대샷은 얼굴이 주로 보이는 스튜디오샷,
   전신샷은 전신이 드러나고 포즈가 있는 스튜디오샷이다 — 모델이 /model/confirm 에서 고른 그 두 장이다.

   모델 객체는 publicModels.js 가 만든 한 가지 모양이다(실모델·가상 예시 공통). 예시는 이름 옆에
   '예시' 배지를 둔다 — 목록에만 고지가 있으면 이 창을 열어 놓고 캡처했을 때 그 사실이 같이 안 나간다.
   예시 중 본인 전신 사진이 없는 모델은 전신 칸을 "준비 중"으로 비워 둔다 — 남의 전신을 돌려 쓰면
   이름 밑에 다른 사람이 선다.

   접근성: 열릴 때 포커스를 창 안으로 들이고, Esc·바깥 클릭으로 닫고, 닫을 때 원래 있던
   곳으로 포커스를 되돌린다. Tab 은 창 안에서만 돈다 — 안 가두면 뒤에 깔린 카드들로 포커스가
   새어 나가고(그쪽은 aria-hidden 도 아니다) 화면 낭독 순서가 뒤죽박죽이 된다.
   ============================================================= */
import { useCallback, useEffect, useRef } from 'react';
import { Icon } from '@/components/ui.jsx';
import s from '../BrowseModels.module.css';

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

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
  const isExample = model.kind === 'example';

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

        {/* 사진 두 장에 '확대샷·전신샷' 글자는 붙이지 않는다(2026-09-08 오너 지시). 보면 아는
            것이고, 검정 바탕에서는 라벨이 사진보다 먼저 눈에 들어온다. alt 에만 남긴다. */}
        <div className={s.dialogShots}>
          <img alt={`${model.name} 확대샷`} className={s.dialogShot} src={model.closeup} />
          {model.fullbody
            ? <img alt={`${model.name} 전신샷`} className={s.dialogShot} src={model.fullbody} />
            : <div className={s.dialogShotEmpty}>전신샷 준비 중</div>}
        </div>

        <div className={s.dialogHead}>
          <h2 className={s.dialogName} id="fm-model-dialog-title">{model.name}</h2>
          {isExample && <span className={s.exampleBadge}>예시</span>}
        </div>

        <div className={s.dialogCols}>
          <section className={s.dialogCol}>
            <h3 className={s.dialogColTitle}>신체 사이즈</h3>
            <dl className={s.specList}>
              {model.body.length > 0
                ? model.body.map((row) => (
                  <div className={s.specRow} key={row.dt}><dt>{row.dt}</dt><dd>{row.dd}</dd></div>
                ))
                : <div className={s.specRow}><dt>키·체형</dt><dd>아직 등록되지 않았어요</dd></div>}
            </dl>
          </section>

          {/* 라이선스 조건의 세 항목은 실제 발급 폼(ModelLicense.jsx TermsStep)이 받는 값과
              같은 것들이다 — 허용 품목·건당 단가·유효기간. 실모델은 그 폼에서 정한 값 그대로다. */}
          <section className={s.dialogCol}>
            <h3 className={s.dialogColTitle}>라이선스 조건</h3>
            <dl className={s.specList}>
              <div className={s.specRow}>
                <dt>허용 품목</dt>
                <dd>{license?.uses?.length ? license.uses.join(' · ') : '미정'}</dd>
              </div>
              <div className={s.specRow}>
                <dt>건당 단가</dt>
                <dd>{license?.unitPrice != null ? `${license.unitPrice.toLocaleString('ko-KR')}원` : '미정'}</dd>
              </div>
              <div className={s.specRow}>
                <dt>유효기간</dt>
                <dd>{license?.validity || '미정'}</dd>
              </div>
            </dl>
          </section>
        </div>
      </div>
    </div>
  );
}
