import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { registerCta } from '../../src/features/facemarket-landing/registerCta.js';

/* 2026-09-02: 지원서 게이트(applicationRequired)가 기본 true 다. 아무것도 없는 방문자의 CTA 는
   허브를 거치지 않고 곧장 지원서(/model/apply)로 간다(사용자 지시). 게이트를 끄면 종전 등록 시작. */
test('아무것도 없으면 지원서로 보낸다 — 게이트 기본값', () => {
  assert.deepEqual(registerCta(null, null), { label: '얼리버드 지원하기', to: '/model/apply' });
  assert.deepEqual(registerCta(undefined, undefined), { label: '얼리버드 지원하기', to: '/model/apply' });
  assert.deepEqual(registerCta(null, null, {}), { label: '얼리버드 지원하기', to: '/model/apply' });
});

test('게이트를 끄면 종전처럼 등록으로 보낸다 — 문구는 통일된 모델 등록하기', () => {
  assert.deepEqual(
    registerCta(null, null, { applicationRequired: false }),
    { label: '모델 등록하기', to: '/model/register' },
  );
  // 게이트가 꺼져 있으면 지원서 상태는 보지 않는다.
  assert.deepEqual(
    registerCta(null, null, { applicationRequired: false, application: { status: 'under_review' } }),
    { label: '모델 등록하기', to: '/model/register' },
  );
});

test('지원서 상태별 다음 행동 — 검토 중은 상태 보기, 승인은 등록, 거절·취소는 다시 지원', () => {
  const cta = (status) => registerCta(null, null, { application: { id: 'a1', status } });
  assert.deepEqual(cta('under_review'), { label: '지원 상태 보기', to: '/status' });
  assert.deepEqual(cta('approved'), { label: '모델 등록하기', to: '/model/register' });
  assert.deepEqual(cta('rejected'), { label: '다시 지원하기', to: '/model/apply' });
  assert.deepEqual(cta('cancelled'), { label: '얼리버드 지원하기', to: '/model/apply' });
});

test('진행 중 등록·모델이 있으면 지원서 상태보다 등록 여정이 우선이다', () => {
  assert.deepEqual(
    registerCta(null, { id: 'e1', status: 'photos_pending' }, { application: { status: 'approved' } }),
    { label: '모델 등록하기', to: '/model/register' },
  );
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'verified' }, null, { application: { status: 'approved' } }),
    { label: '내 모델 정보', to: '/status' },
  );
});

test('등록이 진행 중이어도 같은 문구로 등록으로 보낸다', () => {
  assert.deepEqual(
    registerCta(null, { id: 'e1', status: 'photos_pending' }),
    { label: '모델 등록하기', to: '/model/register' },
  );
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'pending' }, null),
    { label: '모델 등록하기', to: '/model/register' },
  );
});

test('재검증이 필요한 모델도 등록으로 보낸다', () => {
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'reverification_required' }, null),
    { label: '모델 등록하기', to: '/model/register' },
  );
});

test('검증된 모델은 자기 정보(등록 상태 페이지)로 보낸다', () => {
  // /model 허브는 /status 로 옮겨 갔다(StatusPage) — 직접 그리로 보낸다.
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'verified' }, null),
    { label: '내 모델 정보', to: '/status' },
  );
});

/* ── 여기부터는 실제 상태 어휘와의 대조 ─────────────────────────
   fm_models.status CHECK 는 네 가지다 (supabase/migrations/
   20260821010100_facemarket_biometric_runtime.sql):
   pending · verified · suspended · reverification_required.
   ModelHub 의 라벨 표에는 suspended 가 빠져 있어 그냥 원문이 노출되는데,
   랜딩 CTA 는 라벨을 안 쓰므로 영향이 없다 — 다만 verified 가 아닌 상태를
   전부 등록 경로로 보낸다는 판정은 여기서 못 박아 둔다.
   ───────────────────────────────────────────────────────────── */

const MODEL_STATUSES = ['pending', 'verified', 'suspended', 'reverification_required'];

test('모델 상태 네 가지를 전부 판정한다 — verified 만 내 모델 정보로 간다', () => {
  for (const status of MODEL_STATUSES) {
    const cta = registerCta({ id: 'm1', status }, null);
    assert.ok(cta.label, `${status} 에 문구가 없다`);
    assert.ok(cta.to.startsWith('/model') || cta.to === '/status', `${status} 의 경로가 이상하다: ${cta.to}`);
    if (status === 'verified') assert.deepEqual(cta, { label: '내 모델 정보', to: '/status' });
    else assert.deepEqual(cta, { label: '모델 등록하기', to: '/model/register' });
  }
});

test('ModelHub 의 모델 상태 어휘가 늘어나면 여기서 걸린다', () => {
  const source = readFileSync(new URL('../../src/features/model/ModelHub.jsx', import.meta.url), 'utf8');
  const block = source.match(/const MODEL_STATUS_LABEL = \{([\s\S]*?)\};/);
  assert.ok(block, 'ModelHub 에서 MODEL_STATUS_LABEL 을 찾지 못했다');
  const keys = [...block[1].matchAll(/^\s*([a-z_]+):/gm)].map((m) => m[1]);
  assert.ok(keys.length > 0, '상태 키를 하나도 못 읽었다');
  for (const key of keys) {
    assert.ok(MODEL_STATUSES.includes(key), `registerCta 가 모르는 모델 상태: ${key}`);
  }
});

/* GET /v1/facemarket/enrollments/current 은 진행 중인 등록만 돌려준다
   (server/app/facemarket_enrollment.py 의 _load_current_enrollment).
   끝난 등록은 404 → 호출부가 null 을 넘긴다. 그러니 여기 오는 일곱 가지는
   전부 "이어서" 여야 한다. identity_pending 은 신분증-먼저 순서 개편에서
   추가된 상태로, ModelHub 라벨 표에는 아직 없다. */
const IN_PROGRESS_ENROLLMENT_STATUSES = [
  'identity_pending', 'photos_pending', 'liveness_pending', 'processing',
  'asset_building', 'license_pending', 'vc_pending',
];

test('진행 중인 등록 상태도 같은 문구다', () => {
  for (const status of IN_PROGRESS_ENROLLMENT_STATUSES) {
    assert.deepEqual(
      registerCta(null, { id: 'e1', status }),
      { label: '모델 등록하기', to: '/model/register' },
      `${status} 에서 CTA 가 어긋났다`,
    );
  }
});

test('재등록 중이어도 같은 문구다', () => {
  /* 재등록(갈아타기)은 새 모델 행을 만들지 않는다. server/app/facemarket_enrollment.py
     의 create_enrollment 가 등록을 넣는 것과 **같은 트랜잭션**에서 기존 모델 행을
     `status = 'reverification_required'` 로 강등한다. /models/me 는 created_at desc 라
     랜딩이 보는 models[0] 이 바로 그 강등된 행이다.
     그래서 랜딩이 실제로 만나는 재등록 조합은 verified 가 아니라 이쪽이다. */
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'reverification_required' }, { id: 'e1', status: 'photos_pending' }),
    { label: '모델 등록하기', to: '/model/register' },
  );
});

test('verified 와 진행 중 등록이 함께 오면 모델 정보가 이긴다 (서버상 도달 불가, 우선순위만 고정)', () => {
  // 위 강등 때문에 런타임에는 안 나오는 조합이다. 그래도 registerCta 의 분기 순서가
  // 뒤집히면(등록 먼저 보기) verified 모델이 등록 위저드로 끌려가므로 여기서 묶어 둔다.
  assert.deepEqual(
    registerCta({ id: 'm1', status: 'verified' }, { id: 'e1', status: 'photos_pending' }),
    { label: '내 모델 정보', to: '/status' },
  );
});

test('빈 객체나 상태 없는 값에도 문구가 나온다', () => {
  assert.deepEqual(registerCta({}, null), { label: '모델 등록하기', to: '/model/register' });
  assert.deepEqual(registerCta(null, {}), { label: '모델 등록하기', to: '/model/register' });
});
