/* 배포본 공증 (FaceMarket 출처증명 층①·②).

   캔버스 PNG 를 R2 로 직접 올리고(ALB 우회), 서버가 해시·원장·C2PA 서명을 한 뒤
   서명본을 돌려준다. 렌더는 그대로 브라우저가 한다 — 이 픽셀이 정본이고, 서버가 그걸
   재현할 방법은 없다(설계 결정 #2).

   🔴 실패해도 다운로드를 막지 않는다. 생성은 이미 끝났고 크레딧도 차감됐다.
      공증이 안 됐다고 셀러의 결과물을 인질로 잡지 않는다(설계 §6.2). */

const WARNING = '출처 기록을 남기지 못했어요. 파일은 그대로 저장됩니다.';

export async function notarize(blob, { projectId, kind }, deps = {}) {
  const api = deps.api || (await import('../../lib/api/facemarket.js'));
  const fetchImpl = deps.fetchImpl || fetch;
  const fail = () => ({ blob, verifyUrl: null, warning: WARNING });
  try {
    const { uploadToken, uploadUrl } = await api.presignPublication({
      projectId, kind, byteSize: blob.size,
    });
    const put = await fetchImpl(uploadUrl, {
      method: 'PUT', body: blob, headers: { 'Content-Type': blob.type || 'image/png' },
    });
    if (!put.ok) return fail();

    const res = await api.signPublication({ uploadToken });
    // 서명본 재다운로드가 실패해도 원장·앵커는 이미 남았다 — 검증 URL 은 살린다.
    const got = await fetchImpl(res.downloadUrl);
    if (!got.ok) return { blob, verifyUrl: res.verifyUrl, warning: null };
    return { blob: await got.blob(), verifyUrl: res.verifyUrl, warning: null };
  } catch {
    return fail();
  }
}
