/* admin.wearless.kr 의 진입점. seller/facemarket 과 같은 다중 진입 패턴이며
   공통 부트스트랩(프로바이더·스타일·루프백 정규화)은 mountApp 하나를 함께 쓴다. */
import AppAdmin from './App.jsx';
import { mountApp } from '../mountApp.jsx';

mountApp(AppAdmin);
