/* 스튜디오 두 진입점(셀러·facemarket)의 부트스트랩 — 전역 스타일 + 공용 프로바이더.
   admin 은 mountAdminApp.jsx 를 쓴다(Tailwind 레이어 때문에 스타일 조립이 다르다). */
import '@/styles/tokens.css';
import '@/styles/app.css';
/* FaceMarket 도메인 테마. 규칙이 전부 `.fm-theme` 하위라 그 클래스를 쓰지 않는
   ai.wearless.kr 화면에는 한 줄도 적용되지 않는다. app.css 뒤에 와야 전역
   레이아웃 클래스(.wizard·.surface 등)를 이 스코프에서 덮을 수 있다. */
import '@/styles/facemarketTheme.css';
import '@/styles/features.css';
import '@/styles/moveable.css';
import { renderApp } from './AppProviders.jsx';

export function mountApp(App) {
  renderApp(App);
}
