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
  // allowedHosts: dev 서버를 cloudflared 터널(facemarket.wearless.kr)로 노출할 때
  // vite의 Host 검사가 막지 않게 허용(로컬 폰 CX E2E용, dev 전용 — 빌드 산출물엔 무영향).
  // proxy: same-origin('') API 호출을 localhost 직접 접근에서도 백엔드로 넘긴다.
  // (터널 경유는 cloudflared가 /v1→:8000 라우팅하므로 이 프록시는 localhost 직접용.)
  server: {
    port: 5173,
    open: false,
    allowedHosts: ['facemarket.wearless.kr'],
    proxy: { '/v1': 'http://localhost:8000' },
  },
});
