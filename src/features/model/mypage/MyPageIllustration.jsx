import { MYPAGE_ILLUSTRATIONS } from './mypageIllustrations.js';
import s from './MyPage.module.css';

// 저장소에 고정된 SVG만 이 한곳에서 렌더해요. 사용자 HTML을 넣지 않아요.
export function MyPageIllustration({ name, hero = false }) {
  return <div aria-hidden="true" className={`${s.illus} ${hero ? s.illusHero : s.illusSide}`}
    dangerouslySetInnerHTML={{ __html: MYPAGE_ILLUSTRATIONS[name] || '' }} />;
}
