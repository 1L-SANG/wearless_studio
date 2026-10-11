import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';

const slot = { blockId: 'page-block', elementId: 'empty-image' };
const contextFill = { slot, label: '스튜디오 · 옆모습', path: 'new', cutType: 'horizon', colorId: 'col1' };
const image = { id: 'new-photo', src: '/v1/assets/new-photo/file', ai: true, cutType: 'horizon',
  contextFill: { slot, label: '스튜디오 · 옆모습', colorId: 'col1' } };
const returnedImage = { ...image, src: 'https://api.test/v1/assets/new-photo/file?e=1' };
const accepted = { jobId: 'fill-job', contextFill };
const completed = { status: 'done', progress: 100, result: { data: image, credits: 81, creditsCharged: 19 } };

async function loadApi(mode = 'http') {
  const server = await createServer({
    configFile: false,
    root: new URL('../..', import.meta.url).pathname,
    resolve: { alias: { '@': new URL('../../src', import.meta.url).pathname } },
    server: { middlewareMode: true, watch: null, hmr: false, ws: false },
    appType: 'custom',
    optimizeDeps: { noDiscovery: true, include: [] },
    define: { 'import.meta.env.VITE_API_MODE': JSON.stringify(mode), 'import.meta.env.VITE_API_BASE_URL': '"https://api.test"' },
    plugins: [{
      name: 'context-fill-session', enforce: 'pre',
      resolveId(id) { if (id === '@/lib/supabase.js' || id.endsWith('/src/lib/supabase.js')) return '\0context-fill-session'; },
      load(id) { if (id === '\0context-fill-session') return 'export const supabase = { auth: { getSession: async () => ({ data: { session: { access_token: "test-token", user: { id: "test-user" } } } }) } };'; },
    }],
  });
  const facade = await server.ssrLoadModule('/src/lib/api/index.js');
  const { DB } = await server.ssrLoadModule('/src/mock/db.js');
  return { ...facade, DB, close: () => server.close() };
}

async function withRemote(run, responses = [accepted, completed]) {
  const fixture = await loadApi();
  const originalFetch = globalThis.fetch;
  const originalError = console.error;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options.method || 'GET', headers: new Headers(options.headers),
      body: options.body == null ? undefined : JSON.parse(options.body) });
    const next = responses.shift();
    if (next instanceof Error) throw next;
    if (typeof next === 'function') return next();
    assert.ok(next, '예상 밖의 추가 요청을 보내지 않는다');
    return new Response(JSON.stringify(next), { status: next.jobId ? 202 : 200, headers: { 'Content-Type': 'application/json' } });
  };
  console.error = () => {};
  try {
    assert.equal(typeof fixture.api.fillEmptySlot, 'function', 'API 경계가 AI로 채우기 서비스를 제공한다');
    return await run(fixture, calls);
  } finally {
    globalThis.fetch = originalFetch;
    console.error = originalError;
    await fixture.close();
  }
}

test('AI로 채우기는 위치와 필수 키만 보내고 접수 정보를 폴링 전에 전달한다', async () => {
  await withRemote(async ({ api }, calls) => {
    const events = [];
    const result = await api.fillEmptySlot('project-1', { slot, idempotencyKey: 'fill-key',
      cutType: 'product', colorId: 'client-color', _contextFill: { label: '클라이언트 판단' },
      onJobStarted: (jobId, decision) => events.push({ jobId, decision, requestCount: calls.length }),
      onProgress: (progress) => events.push({ progress }) });
    assert.equal(calls[0].url, 'https://api.test/v1/projects/project-1/editor:generate-image');
    assert.equal(calls[0].method, 'POST');
    assert.deepEqual(calls[0].body, { mode: 'fill', slot });
    assert.equal(calls[0].headers.get('Idempotency-Key'), 'fill-key');
    assert.deepEqual(events[0], { jobId: 'fill-job', decision: contextFill, requestCount: 1 });
    assert.ok(events.some((event) => event.progress === 100));
    assert.equal(calls[1].url, 'https://api.test/v1/jobs/fill-job');
    assert.deepEqual(result, { data: returnedImage, credits: 81, contextFill });
  });
});

test('AI로 채우기의 완료 재호출은 새 작업과 폴링을 만들지 않는다', async () => {
  await withRemote(async ({ api }, calls) => {
    const result = await api.fillEmptySlot('project-1', { slot, idempotencyKey: 'same-key',
      onJobStarted: () => assert.fail('완료된 요청은 접수 콜백을 다시 부르지 않는다') });
    assert.deepEqual(result, { data: returnedImage, credits: 81, contextFill });
    assert.equal(calls.length, 1);
  }, [{ data: image, credits: 81, contextFill }]);
});

test('AI로 채우기의 완료 응답만 남아도 결과 판단 기록을 반환한다', async () => {
  await withRemote(async ({ api }) => {
    const result = await api.fillEmptySlot('project-1', { slot, idempotencyKey: 'same-key' });
    assert.deepEqual(result.contextFill, image.contextFill);
  }, [{ data: image, credits: 81 }]);
});

test('AI로 채우기는 키가 없거나 비어 있으면 요청 전에 거절한다', async () => {
  await withRemote(async ({ api }, calls) => {
    for (const idempotencyKey of [undefined, null, '', '   ']) {
      await assert.rejects(api.fillEmptySlot('project-1', { slot, idempotencyKey }), {
        code: 'fill_slot_invalid', status: 400, message: '이 칸을 찾지 못했어요.', requestNotSent: true,
      });
    }
    assert.equal(calls.length, 0);
  }, []);
});

for (const [name, response] of [
  ['응답을 잃은 통신 오류', new Error('연결 중단')],
  ['서버 내부 오류', () => new Response(JSON.stringify({ error: { code: 'internal', message: '내부 오류' } }), { status: 500 })],
  ['작업 번호 없는 접수 응답', {}],
]) {
  test(`AI로 채우기의 ${name}는 같은 키로 접수를 다시 확인할 수 있다`, async () => {
    await withRemote(async ({ api }) => {
      await assert.rejects(api.fillEmptySlot('project-1', { slot, idempotencyKey: 'original-key' }), {
        submissionUnknown: true, message: '접수를 확인하지 못했어요. 다시 누르면 같은 요청을 확인해요.',
      });
    }, [response]);
  });
}

test('AI로 채우기의 확실한 거절은 접수 미확인으로 바꾸지 않는다', async () => {
  await withRemote(async ({ api }) => {
    await assert.rejects(api.fillEmptySlot('project-1', { slot, idempotencyKey: 'fill-key' }), (error) => {
      assert.equal(error.status, 409);
      assert.equal(error.code, 'fill_slot_not_empty');
      assert.equal(error.message, '이미 사진이 들어 있는 칸이에요.');
      assert.equal(error.submissionUnknown, undefined);
      return true;
    });
  }, [() => new Response(JSON.stringify({ error: { code: 'fill_slot_not_empty', message: '이미 사진이 들어 있는 칸이에요.' } }), { status: 409 })]);
});

test('AI로 채우기의 전송 전 오프라인은 접수 미확인으로 바꾸지 않는다', async () => {
  await withRemote(async ({ api }, calls) => {
    const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
    Object.defineProperty(globalThis, 'navigator', { value: { onLine: false }, configurable: true });
    try {
      await assert.rejects(api.fillEmptySlot('project-1', { slot, idempotencyKey: 'fill-key' }), (error) => {
        assert.equal(error.requestNotSent, true);
        assert.equal(error.submissionUnknown, undefined);
        return true;
      });
    } finally {
      if (previousNavigator) Object.defineProperty(globalThis, 'navigator', previousNavigator);
      else delete globalThis.navigator;
    }
    assert.equal(calls.length, 0);
  });
});

test('AI로 채우기는 에디터의 3분 대기 한도로 작업 번호를 보존한다', async () => {
  const originalNow = Date.now;
  let now = originalNow();
  Date.now = () => now;
  try {
    await withRemote(async ({ api }) => {
      await assert.rejects(api.fillEmptySlot('project-1', { slot, idempotencyKey: 'fill-key' }), {
        code: 'job_timeout', jobId: 'fill-job',
        message: '이미지가 아직 만들어지고 있어요. 완성되면 의류 탭에 자동으로 나타나요.',
      });
    }, [accepted, () => {
      now += 180_001;
      return new Response(JSON.stringify({ status: 'running', progress: 25 }), { status: 200 });
    }]);
  } finally { Date.now = originalNow; }
});

test('AI로 채우기의 작업 실패는 예약 해제 안내를 그대로 돌려준다', async () => {
  await withRemote(async ({ api }) => {
    await assert.rejects(api.fillEmptySlot('project-1', { slot, idempotencyKey: 'fill-key' }), (error) => {
      assert.equal(error.code, 'job_failed');
      assert.equal(error.submissionUnknown, undefined);
      assert.match(error.message, /크레딧/);
      return true;
    });
  }, [accepted, { status: 'error', progress: 0, errorCode: 'image_generation_failed', errorMessage: '사진 생성 실패', result: { errorCode: 'image_generation_failed' } }]);
});

test('목 AI로 채우기는 결정적 판단과 자리표시 사진을 의류 탭에 만들고 한 장 값만 차감한다', async () => {
  const fixture = await loadApi('mock');
  try {
    assert.equal(typeof fixture.api.fillEmptySlot, 'function');
    assert.equal(typeof fixture.fillEmptySlot, 'function', '같은 함수가 서비스 경계의 이름 있는 내보내기로도 연결된다');
    fixture.DB.account.credits = 100;
    fixture.DB.product.colors = [{ id: 'col1', name: '블랙', isBase: true, images: [{ id: 'front', slot: 'Front', src: '/front.jpg' }] }];
    fixture.DB.editorBlocks = [{ id: slot.blockId, kind: 'styling', contentRole: 'custom', elements: [{ id: slot.elementId, type: 'image', src: null }] }];
    fixture.DB.wardrobe = {};
    const events = [];
    const result = await fixture.fillEmptySlot('mock-project', { slot, idempotencyKey: 'mock-fill-key',
      onJobStarted: (jobId, decision) => events.push({ jobId, decision }),
      onProgress: (progress) => events.push({ progress }) });
    assert.equal(result.credits, 81);
    assert.deepEqual(result.contextFill, { slot, label: '스타일링 · 새 컷', path: 'new', cutType: 'styling', colorId: 'col1' });
    assert.deepEqual(result.data.contextFill, { slot, label: '스타일링 · 새 컷', colorId: 'col1' });
    assert.match(result.data.src, /^data:image\/svg\+xml/);
    assert.equal(result.data.ai, true);
    assert.equal(result.data.wardrobeGroup, 'col1');
    assert.deepEqual(fixture.DB.wardrobe.col1, [result.data]);
    assert.equal(fixture.DB.editorBlocks[0].elements[0].src, null, '채울 위치에 넣는 책임은 화면에 있다');
    assert.equal(events[0].jobId.startsWith('fill'), true);
    assert.deepEqual(events[0].decision, result.contextFill);
    assert.ok(events.some((event) => event.progress === 100));
  } finally { await fixture.close(); }
});

test('목 AI로 채우기도 필수 키가 없으면 크레딧과 의류 탭을 바꾸지 않는다', async () => {
  const fixture = await loadApi('mock');
  try {
    assert.equal(typeof fixture.api.fillEmptySlot, 'function');
    fixture.DB.account.credits = 100;
    const before = structuredClone(fixture.DB.wardrobe);
    await assert.rejects(fixture.api.fillEmptySlot('mock-project', { slot }), { code: 'fill_slot_invalid', status: 400 });
    assert.equal(fixture.DB.account.credits, 100);
    assert.deepEqual(fixture.DB.wardrobe, before);
  } finally { await fixture.close(); }
});
