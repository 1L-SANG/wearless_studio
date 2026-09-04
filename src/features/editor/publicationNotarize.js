/* 배포본 공증 (FaceMarket 출처증명 층①·②).

   캔버스 PNG 를 R2 로 직접 올리고(ALB 우회), 서버가 해시·원장·C2PA 서명을 한 뒤
   서명본을 돌려준다. 렌더는 그대로 브라우저가 한다 — 이 픽셀이 정본이고, 서버가 그걸
   재현할 방법은 없다(설계 결정 #2).

   🔴 실패해도 다운로드를 막지 않는다. 생성은 이미 끝났고 크레딧도 차감됐다.
      공증이 안 됐다고 셀러의 결과물을 인질로 잡지 않는다(설계 §6.2).

   리뷰 라운드 1 — 두 가지 실패 모드를 더 막는다:
   ① 무한 대기: 네트워크 leg 4개(presign·PUT·sign·GET) 중 하나가 응답 없이 멈추면
      (하프오픈 TCP, 응답 없는 R2 등) await 가 영원히 안 끝난다 — 다운로드가 통째로
      막힌다(이 파일이 막으려는 바로 그 사고). 그래서 매 leg 을 타이머와 경합시킨다.
      AbortSignal 대신 순수 타이머-경합을 쓴 이유: api.presignPublication/signPublication
      은 signal 파라미터를 안 받는 계약이고(facemarket.js), 테스트가 주입하는 fetchImpl
      은 진짜 Response 가 아니라 평범한 객체라 AbortSignal 을 관찰할 수도 없다 — 시간
      기준 경합이 유일하게 프로덕션·테스트 양쪽에서 결정적이다.
   ② sign 이 성공한 뒤부터는(원장 행·체인 앵커가 이미 서버에 있다) 그 아래 실패가
      ok:false 로 오든 throw 로 오든 같은 결과여야 한다: verifyUrl 은 무조건 살린다.
      예전 코드는 이 구간을 감싼 try 가 바깥 catch 하나로 합쳐져 있어서 GET 이 throw
      하면(네트워크 끊김 등) verifyUrl 이 null 로 떨어졌다 — 기록은 진짜로 존재하는데
      "아무것도 안 남았다"고 거짓말하는 셈이었다. */

const WARNING = '출처 기록을 남기지 못했어요. 파일은 그대로 저장됩니다.';
// sign 이후 실패 전용 — 원장·앵커는 이미 있다. "안 남았다"가 아니라 "이 파일엔 못 담았다".
const PARTIAL_WARNING = '출처 기록은 이미 남았지만, 이 파일에는 인증서를 담지 못했어요. 검증 링크로 기록을 확인할 수 있어요.';

// presign/sign 은 작은 JSON 왕복 — 빨리 실패해야 한다. PUT/GET 은 수 MB PNG 바디를
// 옮기므로 진짜 전송 시간이 필요하다. 셀러가 먼저 포기할 만큼 관대하면 의미가 없다.
const NETWORK_TIMEOUT_MS = 8_000;
const TRANSFER_TIMEOUT_MS = 60_000;

// 프라미스가 절대 안 끝나도(하프오픈 TCP·무응답 R2) notarize 가 무한 대기하지 않도록
// 타이머와 경합시킨다. 원본 프라미스가 타임아웃 뒤에 실제로 정착해도 여기서 이미
// then/catch 를 걸어놨기 때문에 unhandled rejection 을 만들지 않는다.
function withTimeout(promise, ms, label) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`notarize: ${label} 응답이 없어 시간 초과됐어요`)),
      ms,
    );
    Promise.resolve(promise).then(
      (value) => { clearTimeout(timer); resolve(value); },
      (err) => { clearTimeout(timer); reject(err); },
    );
  });
}

export async function notarize(blob, { projectId, kind }, deps = {}) {
  const api = deps.api || (await import('../../lib/api/facemarket.js'));
  const fetchImpl = deps.fetchImpl || fetch;
  // deps.timeouts 는 테스트 전용 주입점 — 프로덕션은 항상 위 기본값을 쓴다.
  const t = { network: NETWORK_TIMEOUT_MS, transfer: TRANSFER_TIMEOUT_MS, ...(deps.timeouts || {}) };
  const fail = () => ({ blob, verifyUrl: null, warning: WARNING });

  let res;
  try {
    const { uploadToken, uploadUrl } = await withTimeout(
      api.presignPublication({ projectId, kind, byteSize: blob.size }), t.network, 'presign',
    );
    const put = await withTimeout(
      fetchImpl(uploadUrl, {
        method: 'PUT', body: blob, headers: { 'Content-Type': blob.type || 'image/png' },
      }),
      t.transfer, '업로드',
    );
    if (!put.ok) return fail();

    res = await withTimeout(api.signPublication({ uploadToken }), t.network, 'sign');
  } catch {
    return fail();
  }

  // sign 이 여기까지 왔다는 건 원장 행과 체인 앵커가 이미 서버에 실재한다는 뜻이다.
  // 이 아래 실패는 ok:false 든 throw 든(네트워크 끊김·malformed body 등) 전부 같은
  // 취급이다: 로컬 원본을 저장하되 verifyUrl 은 절대 버리지 않는다.
  try {
    const got = await withTimeout(fetchImpl(res.downloadUrl), t.transfer, '서명본 다운로드');
    if (!got.ok) return { blob, verifyUrl: res.verifyUrl, warning: PARTIAL_WARNING };
    const signed = await withTimeout(got.blob(), t.transfer, '서명본 파싱');
    return { blob: signed, verifyUrl: res.verifyUrl, warning: null };
  } catch {
    return { blob, verifyUrl: res.verifyUrl, warning: PARTIAL_WARNING };
  }
}
