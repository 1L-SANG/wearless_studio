/* 없는 주소로 들어왔을 때 보여주는 화면. Wearless·FaceMarket 두 앱이 함께 쓴다.
   문구는 두 사이트가 같고(2026-09-23 오너 결정), 태그에 찍히는 브랜드 이름과 버튼 아래
   보조 링크만 다르다. 색은 --fm-* 토큰을 따른다. FaceMarket 에서는 .fm-theme 값이,
   Wearless 에서는 facemarketTheme.css :root 의 앱 기본값이 들어온다. */
import { Link, useNavigate } from 'react-router-dom';
import s from './NotFound.module.css';

// 바코드 막대 [x, 폭]. 그림 전용 장식이라 값에 의미는 없다.
const BARS = [[104, 2], [109.8, 1], [116.6, 1], [122.4, 1], [127.2, 2], [135, 1], [139.8, 2], [145.6, 3], [152.4, 2], [158.2, 1], [164, 1], [170.8, 1]];

function HangTag({ brand }) {
  return (
    <svg className={s.art} viewBox="0 0 280 230" role="img" aria-label="PAGE NOT FOUND 라고 적힌 옷 태그">
      <g className={s.sway}>
        <path className={`${s.stroke} ${s.thin}`} d="M140 4 C 126 22 154 36 140 62" />
        <path className={`${s.stroke} ${s.paper}`} d="M84 70 L106 46 H174 L196 70 V214 Q196 222 188 222 H92 Q84 222 84 214 Z" />
        <circle className={`${s.stroke} ${s.hole}`} cx="140" cy="62" r="6" />
        <text className={`${s.mono} ${s.mutedFill}`} x="140" y="92" textAnchor="middle" fontSize="8" letterSpacing="2.4">{brand}</text>
        <path className={`${s.stroke} ${s.thin} ${s.dashed}`} d="M98 102 H182" />
        <text className={`${s.display} ${s.inkFill}`} x="140" y="146" textAnchor="middle" fontSize="46">404</text>
        <text className={`${s.mono} ${s.accentFill}`} x="140" y="164" textAnchor="middle" fontSize="7.6" letterSpacing="1.6">PAGE NOT FOUND</text>
        {/* 세탁 기호 다섯 개: 물세탁, 표백, 건조기, 다림질, 드라이클리닝 */}
        <g className={`${s.stroke} ${s.care}`}>
          <path d="M100 181 L101.5 189 H110.5 L112 181 M100 181 Q102 178.5 104 181 Q106 183.5 108 181 Q110 178.5 112 181" />
          <path d="M123 178.5 L129.5 189 H116.5 Z" />
          <rect x="134.5" y="178.5" width="11" height="11" rx="1.5" />
          <circle cx="140" cy="184" r="3" />
          <path d="M150.5 188.5 H163 L161.5 182.5 Q158 179.5 154 181.5 L150.5 188.5 M156 181.2 V178.5 H161" />
          <circle cx="174" cy="184" r="5.5" />
        </g>
        {BARS.map(([x, width]) => <rect key={x} className={s.inkFill} x={x} y="198" width={width} height="14" />)}
      </g>
    </svg>
  );
}

// brand: 태그에 찍힐 이름. secondary: { to, label } 이면 그 링크를, 없으면 '이전 화면으로'를 보여준다.
export function NotFound({ brand, secondary }) {
  const navigate = useNavigate();
  // 사이트 안에서 넘어왔을 때만 뒤로 가기를 준다. 주소를 직접 쳐서 들어왔으면 뒤로 가기가
  // 사이트 밖으로 나가 버린다. react-router 가 history.state.idx 에 사이트 안 이동 횟수를 적는다.
  const canGoBack = (globalThis.window?.history?.state?.idx ?? 0) > 0;
  return (
    <section className={s.page} aria-labelledby="not-found-title">
      <HangTag brand={brand} />
      <p className={s.eyebrow}>ERROR 404</p>
      <h1 id="not-found-title" className={s.title}>찾는 페이지가 목록에 없어요</h1>
      <p className={s.desc}>주소가 바뀌었거나 사라진 페이지예요.<br />홈에서 다른 페이지를 찾아 주세요.</p>
      <div className={s.actions}>
        <Link className="btn btn-primary btn-block" to="/">홈으로 가기</Link>
        {secondary
          ? <Link className={s.secondary} to={secondary.to}>{secondary.label}</Link>
          : canGoBack && <button type="button" className={s.secondary} onClick={() => navigate(-1)}>이전 화면으로</button>}
      </div>
    </section>
  );
}

export default NotFound;
