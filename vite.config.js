import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

// Vite handles .ts/.tsx out of the box, so TS can be adopted incrementally
// (contracts → store → api) without a full migration. JS/JSX stays default.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 의존성 사전번들 스캔은 앱 진입점만 본다. 저장소 루트·mockups 에 리포트/목업 HTML 이
  // 수십 개 있는데 vite 는 기본적으로 그것들도 진입점으로 훑는다. 그중 qa-review-gate.html →
  // qa/reviewGateHarness.jsx 가 이미 없어진 모듈(VaryReviewModal.jsx·reviewGate.js)을 물고
  // 있어 스캔이 통째로 실패했고, 그 결과 사전번들이 안 만들어져 브라우저가 504
  // (Outdated Optimize Dep)를 받고 흰 화면이 됐다(2026-08-16 재현). dev 전용 설정이라
  // 프로덕션 빌드(진입점 index.html)에는 영향이 없다.
  optimizeDeps: { entries: ['index.html'] },
  // allowedHosts: dev 서버를 cloudflared 터널(facemarket.wearless.kr)로 노출할 때
  // vite의 Host 검사가 막지 않게 허용(로컬 폰 CX E2E용, dev 전용 — 빌드 산출물엔 무영향).
  // proxy: same-origin('') API 호출을 localhost 직접 접근에서도 백엔드로 넘긴다.
  // (터널 경유는 cloudflared가 /v1→:8000 라우팅하므로 이 프록시는 localhost 직접용.)
  server: {
    port: 5173,
    strictPort: true,
    open: false,
    allowedHosts: ['facemarket.wearless.kr'],
    proxy: { '/v1': 'http://localhost:8000' },
  },
});
