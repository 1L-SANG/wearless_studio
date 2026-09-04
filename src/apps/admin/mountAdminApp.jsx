/* admin.wearless.kr 부트스트랩. 스타일은 admin.css 한 파일이 전부 조립한다
   (레이어 순서 때문에 — admin.css 주석 참조). 프로바이더는 스튜디오와 공유한다. */
import './admin.css';
import { renderApp } from '../AppProviders.jsx';

export function mountAdminApp(App) {
  renderApp(App);
}
