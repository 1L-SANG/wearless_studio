import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { HangTag } from './HangTag.jsx';
import { AddressCopyChip } from './AddressCopyChip.jsx';
// PC 전용 안내는 404 페이지의 시각 체계를 함께 쓴다.
import s from '@/components/NotFound.module.css';
import p from './PcOnlyPage.module.css';

export function PcOnlyPage() {
  const navigate = useNavigate();
  useEffect(() => {
    const previous = document.title;
    document.title = 'PC에서 이어서 만들어요';
    return () => { document.title = previous; };
  }, []);

  return (
    <section className={s.page} aria-labelledby="pc-only-title">
      <HangTag label="CONTINUE ON PC 라고 적힌 옷 태그">
        <g className={s.stroke}>
          <rect x="120" y="114" width="40" height="27" rx="3" />
          <path d="M140 141 V149 M131 149 H149" />
        </g>
        <text className={`${s.mono} ${s.accentFill}`} x="140" y="164" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">CONTINUE ON PC</text>
      </HangTag>
      <p className={s.eyebrow}>PC ONLY</p>
      <h1 id="pc-only-title" className={s.title}>이 단계는 PC에서 이어서 만들어요</h1>
      <p className={`${s.desc} ${p.description}`}>PC에서 같은 계정으로 로그인하면 바로 이어져요.</p>
      <div className={p.addressRow}><AddressCopyChip /></div>
      <div className={s.actions}>
        <button type="button" className="btn btn-primary btn-block" onClick={() => navigate('/create/input')}>사진 담기 화면으로</button>
      </div>
    </section>
  );
}
