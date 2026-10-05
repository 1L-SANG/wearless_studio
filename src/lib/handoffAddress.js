export async function copyHandoffAddress(pushToast, {
  navigator = globalThis.navigator,
  origin = globalThis.window?.location?.origin,
} = {}) {
  let host = 'ai.wearless.kr';
  let message;
  try {
    host = new URL(origin).host;
    await navigator.clipboard.writeText(origin);
    message = '주소를 복사했어요';
  } catch {
    message = `PC에서 ${host}로 접속해 주세요`;
  }
  try { await pushToast(message); }
  catch { /* 알림 실패도 주소 버튼의 오류로 전파하지 않는다. */ }
}
