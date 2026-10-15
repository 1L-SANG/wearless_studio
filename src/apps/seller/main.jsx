/* ai.wearless.kr 의 진입점. facemarket 쪽은 mainFacemarket.jsx 다 —
   왜 둘인지는 AppFacemarket.jsx 머리말 참고. */
import App from './App.jsx';
import { mountApp } from '../mountApp.jsx';
import '@/styles/wl-light.css';
import '@/styles/sellerDesign.css';

// mountApp의 인증·임시저장 조회 전에 쿠키 없는 CORS 연결을 준비한다.
// HTML env 치환과 달리 빈 값·목 모드·상대 주소가 head에 남지 않는다.
if (import.meta.env.VITE_API_MODE === 'http' && import.meta.env.VITE_API_BASE_URL) {
  try {
    const apiUrl = new URL(import.meta.env.VITE_API_BASE_URL);
    if (apiUrl.protocol === 'https:' || apiUrl.protocol === 'http:') {
      const link = document.createElement('link');
      link.rel = 'preconnect';
      link.href = apiUrl.origin;
      link.crossOrigin = 'anonymous';
      document.head.appendChild(link);
    }
  } catch { /* 상대 주소나 잘못된 설정은 연결 힌트만 생략한다. */ }
}

mountApp(App);
