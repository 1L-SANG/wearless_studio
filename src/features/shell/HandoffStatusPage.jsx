import { useEffect, useRef } from 'react';
import { Button } from '@/components/ui.jsx';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { draftSlot } from '@/lib/draftSlot.js';
import { DraftProductRow } from './DraftProductRow.jsx';
import { HangTag } from './HangTag.jsx';
import s from '@/components/NotFound.module.css';
import p from './HandoffStatusPage.module.css';

const COPY = {
  pc: { tag: 'ON PC', eyebrow: 'IN PROGRESS', title: 'PC에서 이 상품을 이어서 만들고 있어요' },
  gone: { tag: 'CLOSED', eyebrow: 'DONE', title: '이 상품은 PC에서 마무리됐거나 정리됐어요', desc: '새 상품을 올릴 수 있어요.' },
  error: { tag: 'RETRY', eyebrow: 'OFFLINE', title: '저장된 상품을 확인하지 못했어요', desc: '인터넷 연결을 확인하고 다시 시도해 주세요.' },
};

export function HandoffStatusPage({ variant, meta, busy, onNext, onRetry, onUpload, onSlotChange }) {
  const { session } = useAuth();
  const latest = useRef(null);
  latest.current = { onSlotChange, userId: session?.user?.id || null };
  const lastCheck = useRef(-Infinity);
  useEffect(() => {
    if (variant === 'error' || !latest.current.onSlotChange) return;
    let alive = true;
    let checking = false;
    const check = async () => {
      if (document.visibilityState !== 'visible' || checking || Date.now() - lastCheck.current < 5000) return;
      checking = true;
      lastCheck.current = Date.now();
      const userId = latest.current.userId;
      const epoch = draftSlot.getIdentityEpoch();
      try {
        const slot = await draftSlot.get();
        if (alive && userId === latest.current.userId && epoch === draftSlot.getIdentityEpoch()) latest.current.onSlotChange(slot);
      } catch { /* 상태 조회 실패는 지금 안내를 유지한다. */ }
      finally { checking = false; }
    };
    window.addEventListener('focus', check);
    document.addEventListener('visibilitychange', check);
    return () => {
      alive = false;
      window.removeEventListener('focus', check);
      document.removeEventListener('visibilitychange', check);
    };
  }, [variant]);
  const copy = COPY[variant];
  return <section className={s.page} aria-labelledby="handoff-status-title">
    <HangTag label={`${copy.tag} 라고 적힌 옷 태그`}>
      <text className={`${s.mono} ${s.accentFill}`} x="140" y="146" textAnchor="middle" fontSize="18" letterSpacing="1.6">{copy.tag}</text>
    </HangTag>
    <p className={s.eyebrow}>{copy.eyebrow}</p>
    <h1 id="handoff-status-title" className={s.title}>{copy.title}</h1>
    {variant === 'pc' && <div className={p.product}><DraftProductRow meta={meta} /></div>}
    {copy.desc && <p className={s.desc}>{copy.desc}</p>}
    <div className={`${s.actions} ${p.actions}`}>
      {variant === 'error' ? <>
        <Button variant="primary" disabled={busy} onClick={onRetry}>다시 확인</Button>
        {onUpload && <Button variant="ghost" disabled={busy} onClick={onUpload}>사진부터 올리기</Button>}
      </> : <Button variant="primary" disabled={busy} onClick={onNext}>다음 상품 올리기</Button>}
    </div>
  </section>;
}
