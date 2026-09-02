/* facemarket.wearless.kr 의 진입점. index.html/main.jsx(셀러)와 짝을 이룬다 —
   왜 둘인지는 AppFacemarket.jsx 머리말 참고. 공통 부트스트랩(프로바이더·스타일·
   루프백 정규화)은 mountApp 하나를 함께 쓴다. */
import AppFacemarket from './App.jsx';
import { mountApp } from '../mountApp.jsx';

mountApp(AppFacemarket);
