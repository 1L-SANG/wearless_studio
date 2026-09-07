/* =============================================================
   가입 출처 스탬프 — 이 브라우저가 셀러에서 왔는지 FaceMarket 에서 왔는지 서버에 한 번 알린다.

   두 서비스가 Supabase 프로젝트를 공유해서, 이걸 안 남기면 관리자 콘솔에서 두 서비스의
   가입자가 구분 없이 섞인다. 회원가입 트리거로 못 하는 이유는 OAuth 다 — auth.users 가
   생기는 시점에 서버가 보는 것은 구글·카카오 콜백뿐이고, 거기엔 사용자가 어느 호스트에서
   출발했는지가 없다.

   진짜 판정은 서버가 Origin 헤더로 한다(server/app/app_origin.py). 여기서 보내는 app 은
   호스트로 구분이 안 되는 로컬 개발용 폴백이다 — 이 값을 신뢰 근거로 쓰지 마라.
   ============================================================= */
import { http } from '@/lib/api/httpAdapter.js';
import { IS_ADMIN, IS_FACEMARKET } from '@/lib/host.js';

/* 탭당 한 번만 보낸다. 매 렌더·매 라우팅마다 보내면 로그인 세션 하나가 UPDATE 를 수십 번
   유발한다(서버가 값이 안 바뀌면 안 쓰긴 하지만, 요청 자체가 낭비다). */
const KEY = 'wl_appOriginStamped';

/* sessionStorage 는 **접근 자체가 던진다** — 사파리 프라이빗, 쿠키·사이트 데이터 차단,
   서드파티 컨텍스트. AuthProvider 의 wl_postLogin 과 같은 처방이다: 던지면 중복 방지만
   포기하고(요청이 한 번 더 나갈 뿐) 로그인 흐름은 그대로 간다. */
function alreadyStamped(userId) {
  try { return sessionStorage.getItem(KEY) === userId; } catch { return false; }
}

function rememberStamped(userId) {
  try { sessionStorage.setItem(KEY, userId); } catch { /* 중복 방지만 포기한다 */ }
}

/* 관리자 콘솔은 출처가 아니다. 관리자가 콘솔을 연 것을 가입 출처로 찍으면 그 계정의 진짜
   출처가 덮인다(서버도 admin.* Origin 을 같은 이유로 무시한다 — 여기서 안 보내는 건
   왕복 한 번을 아끼는 것뿐이고, 방어선은 서버 쪽이다). */
export function currentApp() {
  if (IS_ADMIN) return null;
  return IS_FACEMARKET ? 'facemarket' : 'seller';
}

/* fire-and-forget. 실패해도 사용자에게 아무것도 보여주지 않는다 — 이건 로그인 흐름의
   일부가 아니라 라벨 한 칸이다. 여기서 토스트를 띄우면, 네트워크가 잠깐 흔들릴 때마다
   로그인 직후 정체불명의 에러가 뜬다. */
/* 이 페이지 수명 동안의 중복 방지. sessionStorage 는 **성공했을 때만** 찍는다 —
   실패까지 기억해 버리면 서버가 잠깐 흔들린 탭은 그 세션 내내 라벨을 못 남긴다.
   그렇다고 실패 때 바로 다시 부르면 안 되니, 진행 중 여부는 여기서 따로 센다. */
const inflight = new Set();

export function stampAppOrigin(userId) {
  const app = currentApp();
  if (!app || !userId || inflight.has(userId) || alreadyStamped(userId)) {
    return Promise.resolve(null);
  }
  inflight.add(userId);
  return http('/v1/me/app-origin', { method: 'POST', body: { app } })
    .then((result) => {
      rememberStamped(userId);
      return result;
    })
    .catch((error) => {
      console.warn('[appOrigin] stamp failed', error?.message || error);
      return null;
    })
    .finally(() => { inflight.delete(userId); });
}
