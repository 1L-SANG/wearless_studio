const CX_ORIGIN = 'https://cx.raonsecure.co.kr:17543';
const CX_CONFIG_URL = import.meta.env.VITE_CX_CONFIG_URL || `${CX_ORIGIN}/ent/esign/config/config.mid.json`;
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

export async function runIdentityWidget({ signal } = {}) {
  await loadCxWidget();
  if (signal?.aborted) throw new Error('인증이 중단됐어요.');
  await new Promise((resolve) => requestAnimationFrame(resolve));
  document.getElementById('oacxDiv')?.replaceChildren();
  return new Promise((resolve, reject) => {
    const finish = (error, token) => {
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
    try {
      window.OACX.LOAD_MODULE(CX_CONFIG_URL, {
        contentInfo: { signType: 'ENT_MID' }, compareCI: false, isBirth: true, useConvertor: false,
      }, (response) => {
        try {
          const token = (typeof response === 'string' ? JSON.parse(response) : response)?.token;
          if (!token) throw new Error('본인 확인 정보를 받지 못했어요. 다시 시도해 주세요.');
          finish(null, token);
        } catch (error) { finish(error); }
      });
    } catch (error) { finish(error); }
  });
}
