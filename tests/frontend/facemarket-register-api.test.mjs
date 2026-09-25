import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
async function apiHarness() {
  const key=`__c2api${Math.random().toString(36).slice(2)}`;
  const calls=[];globalThis[key]=calls;
  const server=await createServer({configFile:false,logLevel:'silent',root:new URL('../..',import.meta.url).pathname,server:{middlewareMode:true,watch:null},plugins:[{
    name:'c2-api-test',enforce:'pre',resolveId(id){if(id==='@/lib/api/httpAdapter.js')return '\0c2-http';if(id==='@/lib/supabase.js')return '\0c2-auth';},
    load(id){if(id==='\0c2-http')return `export const http=async(path,options)=>{globalThis[${JSON.stringify(key)}].push({path,options});return {};};`;if(id==='\0c2-auth')return `export const supabase={auth:{getSession:async()=>({data:{session:{access_token:'local-test'}}})}};`;}
  }]});
  return {api:await server.ssrLoadModule('/src/lib/api/facemarket.js'),widget:await server.ssrLoadModule('/src/lib/api/facemarketIdentityWidget.js'),calls,close:async()=>{await server.close();delete globalThis[key];}};
}

test('신분증 어댑터는 가린 파일과 직접 선택한 영역 및 확인값을 함께 보내요', async () => {
  const h = await apiHarness();
  const previousFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response('{}', { status: 201 }); };
  try {
    const file = new Blob(['masked-only'], { type: 'image/jpeg' });
    const region = { xr: .2, yr: .3, wr: .4, hr: .1 };
    await h.api.uploadIdDocument('e/1', { file, documentType: 'rrc', maskedConfirmed: true, maskRegion: region });
    assert.match(calls[0].url, /e%2F1\/id-document$/);
    assert.equal(await calls[0].options.body.get('file').text(), 'masked-only');
    assert.equal(calls[0].options.body.get('maskedConfirmed'), 'true');
    assert.deepEqual(JSON.parse(calls[0].options.body.get('maskRegion')), region);
  } finally { globalThis.fetch = previousFetch; await h.close(); }
});

test('완료 어댑터는 전달받더라도 초상이나 인증 토큰을 직렬화하지 않아요',async()=>{
  const h=await apiHarness();try{
    await h.api.completeEnrollment('enrollment/1',{idPhotoHex:'never-forward',token:'never-forward'});
    assert.deepEqual(h.calls[0].options.body,{});assert.match(h.calls[0].path,/enrollment%2F1\/complete$/);
    await h.api.completeEnrollment('enrollment/1',{sessionId:'live-session',idPhotoHex:'never-forward'});
    assert.deepEqual(h.calls[1].options.body,{sessionId:'live-session'});
  }finally{await h.close();}
});

test('신규 등록 어댑터는 두 개의 동의와 문서 버전을 전송하고 국외 이전 동의는 보내지 않아요',async()=>{
  const h=await apiHarness();try{
    await h.api.createEnrollment({documentVersion:'2026-09-v1',deviceId:'device'});
    assert.deepEqual(h.calls[0].options.body,{deviceId:'device',biometricConsent:{accepted:true,documentVersion:'2026-09-v1'},termsConsent:{accepted:true,documentVersion:'2026-09-v1'}});
  }finally{await h.close();}
});

test('라이선스 어댑터는 사용자 단가와 유효기간을 전송하지 않아요',async()=>{
  const h=await apiHarness();try{
    await h.api.createLicense({enrollmentId:'e1',allowedUse:['일반 의류'],unitPrice:1,validDays:365});
    assert.deepEqual(h.calls[0].options.body,{enrollmentId:'e1',allowedUse:['일반 의류'],forbiddenUse:[]});
  }finally{await h.close();}
});

test('사진 어댑터는 슬롯과 파일을 함께 보내고 비공개 조회에 인증을 붙여요',async()=>{
  const h=await apiHarness();const fetch=globalThis.fetch;const calls=[];
  globalThis.fetch=async(url,options)=>{calls.push({url,options});return new Response(options.method==='POST'?JSON.stringify({slot:'full05'}):'photo',{status:200});};
  try{
    const file=new File(['local'],'full.jpg',{type:'image/jpeg'});
    await h.api.uploadEnrollmentPhoto({enrollmentId:'e1',slot:'full05',fileBlob:file,filename:file.name});
    assert.equal(calls[0].options.body.get('slot'),'full05');assert.equal(calls[0].options.body.has('angle'),false);
    assert.equal(calls[0].options.body.get('photo').name,'full.jpg');
    const url=await h.api.fetchEnrollmentPhotoUrl('e1','full05');
    assert.match(calls[1].url,/\/enrollments\/e1\/photos\/full05$/);assert.equal(calls[1].options.headers.Authorization,'Bearer local-test');URL.revokeObjectURL(url);
  }finally{globalThis.fetch=fetch;await h.close();}
});

test('지원 사진 삭제 어댑터는 사진 식별자를 인코딩해 인증된 DELETE를 보낸다', async () => {
  const h = await apiHarness(); const originalFetch = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(null, { status: 204 }); };
  try {
    await h.api.deleteStagedApplicationPhoto('profile', 'stage/id');
    assert.match(calls[0].url, /\/applications\/photo-staging\/profile\/stage%2Fid$/);
    assert.equal(calls[0].options.method, 'DELETE');
    assert.equal(calls[0].options.headers.Authorization, 'Bearer local-test');
  } finally { globalThis.fetch = originalFetch; await h.close(); }
});

test('본인확인 위젯은 사진 변환을 끄고 성공 토큰만 돌려줘요',async()=>{
  const h=await apiHarness();const saved={window:globalThis.window,document:globalThis.document,raf:globalThis.requestAnimationFrame};let options;
  globalThis.window={OACX:{LOAD_MODULE:(url,opts,callback)=>{options=opts;callback({token:'auth-token',data:{dlphotoimage:'must-not-return'}});}}};
  // 위젯은 이제 #oacxDiv 를 찾아 쓰지 않고 호스트(#oacxHost) 안에 새로 만들어 붙인다
  // (Vue 가 마운트 대상 노드를 교체해 React 형제 앵커가 깨지던 것 — oacxHost.js).
  // 그래서 스텁도 createElement/appendChild 를 갖춰야 한다.
  globalThis.document={getElementById:()=>({replaceChildren(){},appendChild(){}}),createElement:()=>({}),addEventListener(){},removeEventListener(){}};
  globalThis.requestAnimationFrame=(callback)=>callback();
  try {assert.equal(await h.widget.runIdentityWidget(),'auth-token');assert.equal(options.useConvertor,false);assert.equal(options.contentInfo.signType,'ENT_MID');}
  finally{globalThis.window=saved.window;globalThis.document=saved.document;globalThis.requestAnimationFrame=saved.raf;await h.close();}
});

test('인증 스크립트가 응답하지 않으면 제한 시간 뒤 정리하고 재시도할 수 있어요',async()=>{
  const h=await apiHarness();const saved={window:globalThis.window,document:globalThis.document,setTimeout:globalThis.setTimeout,clearTimeout:globalThis.clearTimeout};const elements=[];const timers=[];
  globalThis.window={};globalThis.document={getElementById:()=>null,createElement:(tag)=>{const el={tag,remove(){el.removed=true;}};elements.push(el);return el;},head:{appendChild(){}}};
  globalThis.setTimeout=(callback)=>{timers.push(callback);return timers.length;};globalThis.clearTimeout=()=>{};
  try{
    const pending=h.widget.loadCxWidget();assert.equal(timers.length,1,'스크립트 로딩에도 제한 시간이 필요해요');
    timers[0]();await assert.rejects(pending,/불러오지 못했어요/);
    assert.equal(elements.filter(el=>el.tag==='script').every(el=>el.removed),true);
    assert.equal(elements.find(el => el.tag === 'link').media, 'not all', '로딩 실패 뒤 위젯 CSS가 페이지 글자 크기를 바꾸면 안 돼요');
    globalThis.window.OACX={};await h.widget.loadCxWidget();
  }finally{Object.assign(globalThis,{window:saved.window,document:saved.document,setTimeout:saved.setTimeout,clearTimeout:saved.clearTimeout});await h.close();}
});

for (const outcome of ['success', 'cancel', 'timeout', 'invalid', 'throw', 'abort']) {
  test(`인증 위젯 ${outcome} 뒤에는 전역 CSS와 호스트를 정리해요`, async t => {
    const h = await apiHarness();
    const saved = { window: globalThis.window, document: globalThis.document, requestAnimationFrame: globalThis.requestAnimationFrame };
    const sheet = { media: 'not all' };
    const host = { children: [], replaceChildren() { this.children = []; }, appendChild(el) { this.children.push(el); } };
    const events = new Map();
    const callbacks = [];
    let timeout;
    const controller = new AbortController();
    const realTimeout = globalThis.setTimeout;
    t.mock.method(globalThis, 'setTimeout', (callback, delay, ...args) => {
      if (delay === 180000) { timeout = callback; return undefined; }
      return realTimeout(callback, delay, ...args);
    });
    globalThis.document = {
      getElementById: id => id === 'oacx-ux-css' ? sheet : id === 'oacxHost' ? host : null,
      createElement: () => ({}),
      addEventListener: (name, listener) => events.set(name, listener),
      removeEventListener: (name, listener) => { if (events.get(name) === listener) events.delete(name); },
    };
    globalThis.requestAnimationFrame = callback => callback();
    globalThis.window = { OACX: { LOAD_MODULE(_url, _options, callback) {
      callbacks.push(callback);
      if (outcome === 'throw') throw new Error('widget failed');
    } } };
    let pending;
    try {
      pending = h.widget.runIdentityWidget({ signal: controller.signal });
      const result = pending.then(token => ({ token }), error => ({ error }));
      for (let i = 0; i < 10 && !callbacks.length; i++) await Promise.resolve();
      if (outcome !== 'throw') assert.equal(sheet.media, 'all', '열린 인증창은 자체 스타일을 사용해요');
      if (outcome === 'success') callbacks[0]({ token: 'token' });
      if (outcome === 'invalid') callbacks[0]({});
      if (outcome === 'cancel') events.get('click')({ target: { closest: () => ({}) } });
      if (outcome === 'timeout') timeout();
      if (outcome === 'abort') controller.abort();
      const returned = await result;
      if (outcome === 'success') assert.equal(returned.token, 'token');
      else assert.ok(returned.error);
      assert.equal(sheet.media, 'not all');
      assert.equal(host.children.length, 0);
      assert.equal(events.size, 0);
      // 이전 인증의 늦은 콜백이 새 인증창의 CSS를 끄지 않아야 해요.
      sheet.media = 'all';
      callbacks[0]({ token: 'late-token' });
      assert.equal(sheet.media, 'all');
    } finally {
      controller.abort();
      await pending?.catch(() => {});
      Object.assign(globalThis, saved);
      await h.close();
    }
  });
}

test('인증 준비 시간 초과는 기존 스크립트를 보존하고 새 스크립트만 다시 불러와요', async t => {
  const h = await apiHarness();
  const saved = { window: globalThis.window, document: globalThis.document };
  const existingVendor = { id: 'oacx-vendor', tagName: 'script' };
  const elements = new Map([[existingVendor.id, existingVendor]]);
  const appended = [];
  let readyOnLoad = false;
  globalThis.window = {};
  globalThis.document = {
    getElementById: id => elements.get(id) || null,
    createElement: tagName => ({ tagName, remove() { elements.delete(this.id); } }),
    head: {
      appendChild(element) {
        elements.set(element.id, element);
        if (element.tagName === 'script') {
          appended.push(element.id);
          queueMicrotask(() => {
            if (readyOnLoad) globalThis.window.OACX = {};
            element.onload?.();
          });
        }
      },
    },
  };
  const realTimeout = globalThis.setTimeout;
  t.mock.method(globalThis, 'setTimeout', (callback, delay, ...args) => {
    if (delay === 100) { queueMicrotask(callback); return undefined; }
    return realTimeout(callback, delay, ...args);
  });
  try {
    await assert.rejects(h.widget.loadCxWidget(), /아직 준비되지 않았어요/);
    assert.equal(elements.get('oacx-vendor'), existingVendor);
    assert.equal(elements.has('oacx-ux'), false);
    readyOnLoad = true;
    await h.widget.loadCxWidget();
    assert.deepEqual(appended, ['oacx-ux', 'oacx-ux']);
    assert.equal(elements.get('oacx-vendor'), existingVendor);
    assert.ok(elements.has('oacx-ux'));
  } finally { Object.assign(globalThis, saved); await h.close(); }
});

test('협찬 저장은 증서 API와 분리하고 허용한 프로필 필드만 전송해요', async () => {
  const h = await apiHarness();
  try {
    await h.api.updateModelSponsorship('model/1', { sponsorshipEnabled: false, userId: 'other', instagramFollowersReportedAt: 'forged', allowedUse: ['other'] });
    assert.equal(h.calls[0].path, '/v1/facemarket/models/model%2F1/sponsorship');
    assert.equal(h.calls[0].options.method, 'PATCH');
    assert.deepEqual(h.calls[0].options.body, { sponsorshipEnabled: false });
    await h.api.getSponsorshipInterest('model/1');
    await h.api.requestSponsorshipInterest('model/1');
    assert.equal(h.calls[1].path, '/v1/facemarket/models/model%2F1/sponsorship-interest');
    assert.equal(h.calls[2].options.method, 'POST');
    assert.deepEqual(h.calls[2].options.body, {});
  } finally { await h.close(); }
});
