import test from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { jobFailure } from '../../src/lib/api/jobFailure.js';
import { jobFailureMessage } from '../../src/lib/api/jobFailure.js';
import { createInitialGenerationAttempts } from '../../src/features/mannequin/initialGenerationAttempts.js';

const require = createRequire(import.meta.url);
const { parse } = createRequire(require.resolve('vite'))('@babel/parser');
const root = new URL('../../', import.meta.url);
const directories = ['product-input', 'analysis', 'storyboard', 'mannequin', 'editor', 'generating', 'library', 'shell']
  .map((name) => `src/features/${name}`).concat('src/store');
const libs = ['src/lib/api/httpAdapter.js', 'src/lib/api/jobFailure.js', 'src/lib/detailPageJobFailure.js',
  'src/lib/customMatchPromotion.js', 'src/lib/editorWaitSkeleton.js', 'src/lib/smoothProgress.js'];
const connection = '서버 연결이 불안정해요. 잠시 후 다시 시도해 주세요.';
const uncharged = `${connection} 크레딧은 빠지지 않았어요.`;
const kept = new Map([
  ['src/features/editor/editorExport.js', ['이미지 인코딩에 실패했어요. 페이지가 길면 나눠서 받아주세요.']],
]);

function filesIn(path) {
  return readdirSync(new URL(`${path}/`, root), { withFileTypes: true }).flatMap((entry) => {
    const child = `${path}/${entry.name}`;
    return entry.isDirectory() ? filesIn(child) : /\.(?:js|jsx)$/.test(child) ? [child] : [];
  });
}

function failureStrings(source) {
  const ast = parse(source, { sourceType: 'module', plugins: ['jsx'] });
  const found = [];
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (node.type === 'CallExpression' && node.callee?.type === 'MemberExpression'
        && node.callee.object?.name === 'console') return;
    if (['StringLiteral', 'JSXText', 'TemplateElement'].includes(node.type)) {
      const value = node.type === 'TemplateElement' ? node.value.cooked : node.value;
      if (value?.includes('실패')) found.push({ line: node.loc.start.line, value: value.trim() });
    }
    for (const [key, value] of Object.entries(node)) {
      if (['comments', 'leadingComments', 'trailingComments', 'innerComments', 'loc', 'extra'].includes(key)) continue;
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') visit(value);
    }
  }
  visit(ast);
  return found;
}

test('상세페이지 범위의 실패 문구는 사용자 조치 안내 예외만 남긴다', () => {
  const unexpected = [];
  const seen = new Map();
  for (const path of [...directories.flatMap(filesIn), ...libs]) {
    for (const { line, value } of failureStrings(readFileSync(new URL(path, root), 'utf8'))) {
      if (kept.get(path)?.includes(value)) seen.set(path, value);
      else unexpected.push(`${path}:${line}: ${value}`);
    }
  }
  assert.deepEqual(unexpected, []);
  assert.equal(seen.size, kept.size, '조치 안내 예외가 사라지면 예외 목록도 검토해요');
});

test('문구 검사는 주석과 콘솔만 제외하고 JSX, 템플릿, 인라인 오류를 검사한다', () => {
  const strings = failureStrings('// 실패\n/* 실패 */\nconsole.warn("실패");\n'
    + 'const view = <span title="실패">실패</span>; const text = `실패 ${count}`; throw new Error("실패");');
  assert.equal(strings.length, 4);
});

test('잡 오류 문구는 연결 안내로 바꾸고 기존 오류 코드와 구체적인 서버 안내를 보존한다', () => {
  const failed = jobFailure({ status: 'error', errorMessage: '품질 검수 실패', result: { errorCode: 'mannequin_quality_failed' } });
  assert.equal(failed.message, connection);
  assert.equal(failed.code, 'mannequin_quality_failed');
  assert.equal(jobFailure({ status: 'error' }).message, connection);
  const detail = '아직 못 만든 컷이 있어요. 완성된 컷은 보관돼 있어요.';
  assert.equal(jobFailure({ status: 'error', errorMessage: detail }).message, detail);
});

test('유료 잡의 오류 종결을 확인한 경우에만 미차감 안내를 붙인다', () => {
  for (const status of ['error', 'cancelled', 'running']) {
    const error = jobFailure({ status, errorMessage: '생성 실패' }, { creditsReleased: status === 'error' });
    assert.equal(error.message, status === 'error' ? uncharged : connection);
    assert.equal(error.code, status === 'cancelled' ? 'job_cancelled' : 'job_failed');
  }
  assert.equal(jobFailure({ status: 'error' }, { creditsReleased: true }).message, uncharged);
  assert.equal(jobFailure({ status: 'error', errorMessage: '필수 사진을 추가해 주세요.' }, { creditsReleased: true }).message,
    '필수 사진을 추가해 주세요.');
});

test('복원된 마네킹은 오류 종결인 경우에만 미차감 안내를 한다', () => {
  for (const status of ['error', 'unknown', 'cancelled']) {
    const attempts = createInitialGenerationAttempts({ storage: null });
    assert.equal(attempts.seed('p', { id: 'j', status, errorCode: 'mannequin_quality_failed' }).message,
      status === 'error' ? uncharged : connection);
  }
});

const adapter = readFileSync(new URL('src/lib/api/httpAdapter.js', root), 'utf8');
test('서버의 연결 오류 문구는 셀러 API에만 적용하고 입력, 로그인, 크레딧 안내는 보존한다', async () => {
  const source = adapter.slice(adapter.indexOf('export async function http('), adapter.indexOf('/* 톤 에디터'))
    .replace('export ', '');
  for (const [path, status, message, expected] of [
    ['/v1/projects/p/editor-blocks', 500, '저장 실패', connection],
    ['/v1/projects?view=library', 503, '목록 조회 실패', connection],
    ['/v1/projects/p/editor-blocks', 500, '잠시 점검 중이에요.', '잠시 점검 중이에요.'],
    ['/v1/projects/p/editor-blocks', 400, '필수 사진 검사 실패. 사진을 추가해 주세요.', '필수 사진 검사 실패. 사진을 추가해 주세요.'],
    ['/v1/projects/p/editor:generate-image', 402, '크레딧 확인 실패. 충전해 주세요.', '크레딧 확인 실패. 충전해 주세요.'],
    ['/v1/projects/p/editor-blocks', 401, '로그인 확인 실패. 다시 로그인해 주세요.', '로그인 확인 실패. 다시 로그인해 주세요.'],
    ['/v1/payments/toss/confirm', 500, '결제 실패', '결제 실패'],
    ['/v1/subscriptions/me', 500, '구독 조회 실패', '구독 조회 실패'],
    ['/v1/facemarket/admin/applications', 500, '관리자 조회 실패', '관리자 조회 실패'],
    ['/v1/facemarket/models', 500, '모델 조회 실패', '모델 조회 실패'],
    ['/v1/personalization/profile', 500, '프로필 조회 실패', '프로필 조회 실패'],
  ]) {
    const http = new Function('supabase', 'isBrowserOffline', 'readDeviceToken', 'clientApiFetch', 'BASE_URL',
      'absolutizeAssetUrls', 'jobFailureMessage', 'attachResponseError', 'clientEvents', `${source}; return http;`)(
      { auth: { getSession: async () => ({ data: { session: { access_token: 'test' } } }) } },
      () => false, () => null,
      async () => ({ ok: false, status, json: async () => ({ error: { code: 'unchanged', message } }) }),
      '', (value) => value, jobFailureMessage, (error) => error, { getAccountGeneration: () => 0, bindGeneration: (value) => value },
    );
    await assert.rejects(http(path, { suppressErrorLog: true }),
      { message: expected, status, code: 'unchanged' });
  }
});

test('마네킹 생성과 재생성, 에디터 이미지 잡은 오류 종결에서만 미차감을 표시한다', async () => {
  const pollSource = adapter.slice(adapter.indexOf('async function pollJob('), adapter.indexOf('// 사진 1장 업로드'));
  for (const [method, end] of [
    ['generateMannequins', '  async cancelMannequinGeneration'],
    ['regenerateMannequin', '  async getWardrobe'],
    ['generateImage', '\n};'],
  ]) {
    const start = adapter.indexOf(`  async ${method}(`);
    const methodSource = adapter.slice(start, adapter.indexOf(end, start));
    for (const outcome of ['error', 'timeout', 'network']) {
      const http = async (path) => {
        if (!path.startsWith('/v1/jobs/')) return { jobId: 'j' };
        if (outcome === 'network') throw new Error('조회가 끊겼어요.');
        return { status: outcome === 'error' ? 'error' : 'running', errorMessage: '이미지 생성 실패' };
      };
      const pollJob = new Function('http', 'jobFailure', 'DEFAULT_JOB_TIMEOUT_MESSAGE',
        `${pollSource}; return (id, options) => pollJob(id, {...options, timeoutMs: -1});`)(http, jobFailure, '진행 중이에요.');
      const api = new Function('http', 'pollJob', 'LONG_IMAGE_JOB_TIMEOUT_MS', 'MANNEQUIN_JOB_TIMEOUT_MESSAGE',
        'EDITOR_IMAGE_JOB_TIMEOUT_MS', 'EDITOR_IMAGE_JOB_TIMEOUT_MESSAGE', 'recordClientAction', `return ({${methodSource}});`)(
        http, pollJob, 1, '계속 만들고 있어요.', 1, '이미지가 아직 만들어지고 있어요.', () => {},
      );
      await assert.rejects(api[method]('p'), { message: outcome === 'error' ? uncharged
        : outcome === 'network' ? '조회가 끊겼어요.'
          : method === 'generateImage' ? '이미지가 아직 만들어지고 있어요.' : '계속 만들고 있어요.' });
    }
  }
});
