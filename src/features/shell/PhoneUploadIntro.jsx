import { HangTag } from './HangTag.jsx';
import s from '@/components/NotFound.module.css';

export function PhoneUploadIntro({ onLogin }) {
  return (
    <section className={s.page} aria-labelledby="phone-upload-title">
      <HangTag label="UPLOAD ON PHONE 이라고 적힌 옷 태그"
        footer={<text className={`${s.mono} ${s.mutedFill}`} x="140" y="187" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">FINISH ON PC</text>}>
        <g className={s.stroke}>
          <rect x="129" y="110" width="22" height="38" rx="4" />
          <path d="M137 115 H143 M140 140 V123 M134 129 L140 123 L146 129" />
        </g>
        <text className={`${s.mono} ${s.accentFill}`} x="140" y="164" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">UPLOAD ON PHONE</text>
      </HangTag>
      <p className={s.eyebrow}>PHONE UPLOAD</p>
      <h1 id="phone-upload-title" className={s.title}>휴대폰으로 사진부터 업로드해요</h1>
      <p className={s.desc}>로그인하고 사진을 업로드하면 PC에서 바로 이어서 만들어요.</p>
      <div className={s.actions}>
        <button type="button" className="btn btn-primary btn-block" onClick={onLogin}>로그인하고 사진 업로드</button>
      </div>
    </section>
  );
}
