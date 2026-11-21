/* 상세페이지 생성이 연달아 두 번 실패해 서버가 1시간 동안 다시 만들기를 막았을 때 크게 띄우는 안내.
   실패는 우리 쪽 문제라 사과하고, 언제 다시 되는지만 알린다(2026-11-21 오너 지시).
   그림은 404 화면과 같은 옷 태그 가족이다. */
import { useEffect, useRef } from 'react';
import { HangTag } from '@/components/NotFound.jsx';
import styles from './GenerationLocked.module.css';

export function lockWaitText(until, now = Date.now()) {
  const minutes = Math.max(1, Math.ceil((Date.parse(until) - now) / 60000));
  return minutes >= 55 ? '1시간' : `${minutes}분`;
}

export function GenerationLocked({ until, now, onLeave }) {
  const card = useRef(null);
  useEffect(() => { card.current?.focus({ preventScroll: true }); }, []);
  return <div className="ed-genoverlay">
    <section ref={card} tabIndex={-1} className={`surface ${styles.card}`} role="alertdialog" aria-modal="true"
      aria-labelledby="gen-locked-title" aria-describedby="gen-locked-desc">
      <HangTag brand="WEARLESS" code="SORRY" caption="BE RIGHT BACK" codeSize={30} className={styles.art} />
      <h2 id="gen-locked-title" className={styles.title}>지금 서버에 문제가 생겼어요</h2>
      <p id="gen-locked-desc" className={styles.desc}>{lockWaitText(until, now)} 뒤에 다시 시도해 주세요. 불편을 드려 죄송해요.</p>
      <button type="button" className="btn btn-primary btn-block" onClick={onLeave}>보관함으로 가기</button>
    </section>
  </div>;
}

export default GenerationLocked;
