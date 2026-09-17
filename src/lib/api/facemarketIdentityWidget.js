import { mountOacxHost, OACX_HOST_ID } from './oacxHost.js';

const CX_ORIGIN = 'https://cx.raonsecure.co.kr:17543';
const CX_CONFIG_URL = import.meta.env.VITE_CX_CONFIG_URL || `${CX_ORIGIN}/ent/esign/config/config.mid.json`;
// 간편인증(ENT_SIMPLE_AUTH) 전용 설정 URL — mid 설정(config.mid.json, v1.0 경로)과 API 경로
// 버전이 다르다(v1.5). 이 값이 없는데 mid 설정으로 간편인증 위젯을 열면 v1.0 경로를 타서
// 조용히 실패하므로, 없으면 위젯을 아예 열지 않는다(등록 화면이 버튼도 미리 막는다).
export const CX_AUTH_CONFIG_URL = import.meta.env.VITE_CX_AUTH_CONFIG_URL || '';
let cxLoader;
export function loadCxWidget() {
  if (window.OACX) return Promise.resolve();
  if (cxLoader) return cxLoader;
  const owned = [];
  let stopped = false;
  let timeout;
  const addScript = (id, src) => new Promise((resolve, reject) => {
    if (stopped) return reject(new Error('인증 화면을 불러오지 못했어요.'));
    if (document.getElementById(id)) return resolve();
    const script = document.createElement('script');
    script.id = id; script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error('인증 화면을 불러오지 못했어요.'));
    owned.push(script); document.head.appendChild(script);
  });
  const loading = (async () => {
    if (!document.getElementById('oacx-ux-css')) {
      const link = document.createElement('link');
      link.id = 'oacx-ux-css'; link.rel = 'stylesheet'; link.href = `${CX_ORIGIN}/ent/esign/oacx-ux.css`;
      // 위젯 CSS의 html { font-size: 62.5% }가 앱 전체를 축소하므로 인증창 안에서만 켜요.
      link.media = 'not all';
      document.head.appendChild(link);
    }
    await addScript('oacx-vendor', `${CX_ORIGIN}/ent/esign/oacx-vendor.js`);
    await addScript('oacx-ux', `${CX_ORIGIN}/ent/esign/oacx-ux.js`);
    for (let attempt = 0; attempt < 50; attempt++) {
      if (stopped) throw new Error('인증 화면을 불러오지 못했어요.');
      if (window.OACX) return;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('인증 화면이 아직 준비되지 않았어요.');
  })();
  const deadline = new Promise((resolve, reject) => { timeout = setTimeout(() => { stopped = true; reject(new Error('인증 화면을 불러오지 못했어요. 다시 시도해 주세요.')); }, 15000); });
  cxLoader = Promise.race([loading, deadline]).catch((error) => { stopped = true; owned.forEach((element) => element.remove()); cxLoader = undefined; throw error; }).finally(() => clearTimeout(timeout));
  return cxLoader;
}

// identityMethod: 'mid' = OACX 모바일 신분증(기존 경로), 'simple_auth' = 간편인증(PASS·
// 카카오·네이버·토스). 한 위젯 호출을 공유할 수 없다 — 실측: ENT_MID 를 얹으면 위젯이
// 자체 카테고리 목록을 강제한다. 그래서 설정 URL·옵션을 인증 수단별로 통째로 가른다.
export async function runIdentityWidget({ identityMethod = 'mid', signal } = {}) {
  const isSimpleAuth = identityMethod === 'simple_auth';
  // 이미 simple_auth 로 시작해 이어서 진행 중인 등록은 선택 화면의 게이트를 거치지 않고
  // 바로 여기로 온다 — 이 빌드에 설정이 빠져 있으면 빈 URL 로 LOAD_MODULE('') 을 여는
  // 대신 명확한 에러로 멈춘다.
  if (isSimpleAuth && !CX_AUTH_CONFIG_URL) {
    throw new Error('간편인증 설정이 없어 인증 화면을 열 수 없어요. 잠시 후 다시 시도해 주세요.');
  }
  await loadCxWidget();
  if (signal?.aborted) throw new Error('인증이 중단됐어요.');
  await new Promise((resolve) => requestAnimationFrame(resolve));
  if (signal?.aborted) throw new Error('인증이 중단됐어요.');
  // React 가 그린 빈 호스트 안에 #oacxDiv 를 새로 만들어 넣는다. React 가 #oacxDiv 를
  // 직접 그리면 Vue 가 그 노드를 교체할 때 형제 앵커가 깨져 앱이 죽는다(oacxHost.js 참고).
  const host = document.getElementById(OACX_HOST_ID);
  mountOacxHost(host);
  const stylesheet = document.getElementById('oacx-ux-css');
  if (stylesheet) stylesheet.media = 'all';
  try {
    return await new Promise((resolve, reject) => {
      let settled = false;
      const finish = (error, token) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
        document.removeEventListener('click', close, true);
        if (error) reject(error); else resolve(token);
      };
      const abort = () => finish(new Error('인증이 중단됐어요. 다시 인증해 주세요.'));
      const close = (event) => { if (event.target?.closest?.('.popup-close')) abort(); };
      const timer = setTimeout(() => finish(new Error('인증 대기 시간이 지났어요. 다시 인증해 주세요.')), 180000);
      signal?.addEventListener('abort', abort, { once: true });
      document.addEventListener('click', close, true);
      const options = isSimpleAuth
        ? { contentInfo: { signType: 'ENT_SIMPLE_AUTH' }, compareCI: false, isBirth: true }
        : { contentInfo: { signType: 'ENT_MID' }, compareCI: false, isBirth: true, useConvertor: false };
      const configUrl = isSimpleAuth ? CX_AUTH_CONFIG_URL : CX_CONFIG_URL;
      try {
        window.OACX.LOAD_MODULE(configUrl, options, (response) => {
          try {
            const token = (typeof response === 'string' ? JSON.parse(response) : response)?.token;
            if (!token) throw new Error('본인 확인 정보를 받지 못했어요. 다시 시도해 주세요.');
            finish(null, token);
          } catch (error) { finish(error); }
        });
      } catch (error) { finish(error); }
    });
  } finally {
    // 성공·취소·시간 초과·언마운트 모두 같은 정리를 거쳐 원래 페이지 크기로 돌아가요.
    if (stylesheet) stylesheet.media = 'not all';
    host?.replaceChildren();
  }
}
