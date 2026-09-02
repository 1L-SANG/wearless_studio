import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/* 문서 진입점 두 벌 — 같은 앱(src/main.jsx)을 물지만 head 가 다르다.
   공유 미리보기(og:*)와 <title> 은 정적 head 가 전부이고 크롤러는 JS 를 실행하지 않아서,
   호스트별로 다른 문서를 내보내야 카카오톡·슬랙 카드가 맞는다. 배급은 vercel.json 의
   rewrites 가 host 조건으로 한다 — **이 input 과 그 rewrite 는 한 쌍이다.**
   (input 만 두면 아무도 안 보고, rewrite 만 걸면 facemarket 루트가 404 다.) */
const htmlEntry = (name) => fileURLToPath(new URL(`./${name}.html`, import.meta.url));

/* dev 서버에서 어느 **문서**를 줄지 고른다. 프로덕션은 vercel.json 의 host rewrite 가 하는
   일인데 dev 서버에는 그게 없다.

   두 문서를 다 여기서 배급한다 — index.html 이라는 이름을 쓰지 않기 때문이다.
   그 이름을 피한 이유가 이 파일 밖에 있으니 적어 둔다: **Vercel 은 rewrite 보다 파일
   시스템을 먼저 본다.** dist/index.html 이 있으면 `/` 요청이 그 파일로 곧장 나가고 host
   rewrite 는 아예 돌지 않는다 — facemarket.wearless.kr 루트가 셀러 문서를 받아
   /create/input 으로 튕겼다(프로덕션에서 실제로 그랬다). `/` 에 응답할 정적 파일이 없어야
   두 호스트 모두 rewrite 를 탄다. 그래서 셀러 문서는 seller.html 이다 — index.html 로
   되돌리지 마라.

   주의: 쿼리 없이 새로고침하면 셀러 문서가 나간다. IS_FACEMARKET 의 sessionStorage 기억은
   브라우저 안에 있어 서버가 볼 수 없기 때문이다 — 로컬에서는 주소에 ?facemarket=1 을
   달고 다녀라. */
const facemarketDevDocument = {
  name: 'facemarket-dev-document',
  apply: 'serve',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.method !== 'GET') return next();
      const url = new URL(req.url, 'http://localhost');
      // 문서 요청만 건드린다 — 모듈(/src/…, /@vite/…)과 확장자 있는 파일은 그대로 흘린다.
      if (url.pathname.includes('.') || url.pathname.startsWith('/@')) return next();
      const host = (req.headers.host || '').toLowerCase();
      const wantsFacemarket = url.searchParams.get('facemarket') === '1'
        || /(^|\.)facemarket\./.test(host);
      req.url = wantsFacemarket
        ? `/facemarket.html${url.search}`
        : `/seller.html${url.search}`;
      return next();
    });
  },
};

// Vite handles .ts/.tsx out of the box, so TS can be adopted incrementally
// (contracts → store → api) without a full migration. JS/JSX stays default.
export default defineConfig({
  plugins: [react(), facemarketDevDocument],
  build: {
    rollupOptions: {
      input: {
        seller: htmlEntry('seller'),
        facemarket: htmlEntry('facemarket'),
      },
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 의존성 사전번들 스캔 대상 = 진입 문서 둘(seller.html·facemarket.html).
  // vite 기본값은 루트의 모든 *.html 을 진입점으로 훑는데, 이 저장소에는 리포트·목업 HTML 이
  // 수십 개 있어 애초에 스캔 대상이 아니다. 게다가 그중 qa-review-gate.html →
  // qa/reviewGateHarness.jsx 가 이미 삭제된 모듈(VaryReviewModal.jsx·reviewGate.js)을 물고
  // 있어 스캔이 통째로 실패했고, 사전번들이 안 만들어져 브라우저가 504
  // (Outdated Optimize Dep) → 흰 화면이 됐다(2026-08-16 재현·수정).
  // ※ 앱 진입점을 새로 추가하면 이 목록에도 반드시 넣어야 한다(안 넣으면 같은 504 가 난다).
  // dev 전용이라 프로덕션 빌드(rollup 진입점 = 위 두 문서)에는 영향이 없다.
  // Amplify liveness(@aws-amplify/ui-react-liveness)는 lazy(FaceLivenessStep)로만 쓰인다.
  // 사전번들(include)이 필요한 이유: 하위에 CommonJS 인 use-sync-external-store(@xstate/react 가
  //   named import: useSyncExternalStoreWithSelector)가 있어, 번들 없이 raw 로 서빙되면 named
  //   export 가 없어 "does not provide an export named 'useSyncExternalStoreWithSelector'" 로
  //   깨진다. esbuild 로 번들하면 CJS→ESM interop 이 자동 처리된다.
  // 그런데 amplify 전체를 include 하면 @smithy/core·@aws-sdk/core 의 submodule 진입점 8개가 모두
  //   basename 이 index.browser 라 esbuild 최적화 출력에서 충돌해 일부가 안 써지고, 브라우저의
  //   청크 요청이 404("The file does not exist ... in the optimize deps directory ... incompatible
  //   ... Try adding it to optimizeDeps.exclude") → "Failed to fetch dynamically imported module"
  //   가 난다(2026-08-24 재현: .vite/deps 가 안 만들어지고 deps_temp_* 만 쌓임).
  // → 해법: amplify 는 include(하위의 use-sync 까지 번들돼 interop 해결), 충돌원 @smithy/core·
  //   @aws-sdk/core 만 exclude 해 raw ESM(dist-es)으로 서빙 → 최적화기 충돌 회피. 남는 index.browser
  //   는 @aws-amplify/storage 1개뿐이라 동일 basename 충돌이 없다.
  // pnpm 은 transitive 를 root node_modules 에 올리지 않아 use-sync-external-store 를 직접 include
  //   할 수 없다(resolve 실패) — 그래서 직접 의존인 amplify 를 include 해 하위로 번들한다.
  // dev 전용이다 — 프로덕션 빌드는 Rollup 이라 이 트리를 정상 번들한다(영향 없음).
  optimizeDeps: {
    entries: ['seller.html', 'facemarket.html'],
    include: ['@aws-amplify/ui-react-liveness', '@aws-amplify/ui-react'],
    exclude: ['@smithy/core', '@aws-sdk/core'],
  },
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
