import { ASSET_CACHE_VERSION } from '../../lib/assetUrl.js';

// `/v1/assets/{id}/file`(상대·절대 모두) → 같은 자산의 `/bytes`. 그 외 URL 은 그대로.
// id 는 서버(/file 라우트)가 uuid 검증을 담당하므로 여기선 경로 모양만 본다 — 프론트가
// 더 엄격하면(예: uuid 강제) 서버는 받는데 프론트만 못 바꾸는 어긋남이 생긴다(리뷰 반영).
const ASSET_FILE_RE = /^(.*\/v1\/assets\/[^/?#]+)\/file$/;
/* capability 버전은 예전 빌드가 crossOrigin 없이 먼저 받은 immutable 사본을 우회한다.
   불러왔고, 그 응답에는 Origin 이 없어 CORS 헤더도 Vary 도 붙지 않았다. 응답이
   `immutable, max-age=1년` 이라 그 "허가 도장 없는" 사본이 브라우저 캐시에 눌러앉고,
   뒤이은 fetch(CORS 필수)가 같은 사본을 재사용해 영구히 차단됐다 — 실서버 재현 확인.
   이미 오염된 캐시는 서버 수정으로 못 풀기 때문에 주소를 한 번 갈아 준다. */
const ASSET_BYTES_RE = /\/v1\/assets\/[^/?#]+\/bytes(\?|$)/;
export function toBytesUrl(src) {
  const m = ASSET_FILE_RE.exec(String(src || '').split('?')[0]);
  return m ? `${m[1]}/bytes?e=${ASSET_CACHE_VERSION}` : src;
}
/** 핵심 상품컷(자산 바이트) 여부. 쿼리가 붙어도 판정이 깨지면 실패가 soft 로 강등돼
    빈 이미지인 채 "저장 완료" 로 속인다 — 이 파일이 막으려는 실패 모드다. */
export function isAssetBytesUrl(src) {
  return ASSET_BYTES_RE.test(String(src || ''));
}

