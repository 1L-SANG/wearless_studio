/* 지원서 화면 이관 — 껍데기만 바꾸고 동작은 그대로인지.

   되돌아가면: 스타일을 갈아엎다가 사진 objectURL 해제나 409 재조회 같은 "안 보이는 동작"이
   함께 사라진다. 그 손실은 화면을 봐서는 모른다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (name) => readFileSync(fileURLToPath(new URL(name, root)), 'utf8');
const source = read('src/features/admin/AdminApplications.jsx');

test('CSS 모듈을 버리고 admin-ui 를 쓴다 — ui.jsx 에서는 토스트 훅만 빌린다', () => {
  assert.ok(!source.includes('AdminApplications.module.css'));
  assert.ok(source.includes('@/components/admin-ui/'));
  // ToastProvider 는 AppProviders 에 남아 있고 스타일은 studio 레이어가 준다.
  // 훅 하나를 위해 토스트를 다시 구현하지 않는다. 대신 **시각 컴포넌트는** 가져오지 않는다.
  const uiImport = source.match(/import\s*\{([^}]*)\}\s*from\s*'@\/components\/ui\.jsx';/);
  if (uiImport) {
    const named = uiImport[1].split(',').map((s) => s.trim()).filter(Boolean);
    assert.deepEqual(named, ['useToast'], `ui.jsx 에서 토스트 훅 말고 더 가져온다: ${named}`);
  }
});

test('관리자 API 다섯 개를 그대로 호출한다', () => {
  for (const fn of [
    'adminListApplications', 'adminApproveApplication', 'adminRejectApplication',
    'adminResendEmail', 'adminFetchApplicationPhotoUrl',
  ]) {
    assert.ok(source.includes(fn), `호출이 사라졌다: ${fn}`);
  }
});

test('사진 objectURL 을 계속 해제한다', () => {
  assert.ok(source.includes('URL.revokeObjectURL'), 'objectURL 누수');
});

test('거절은 사유 입력을 요구한다', () => {
  assert.ok(source.includes('reason'), '거절 사유 상태가 사라졌다');
});

test('거절 사유는 여러 줄 textarea 로 받는다 — 한 줄 Input 으로 좁아지면 안 된다', () => {
  // 재-스킨 전엔 <textarea rows={2}> 였다. shadcn 이관 때 <Input>(=<input type="text">)으로
  // 좁혀졌던 적이 있다 — 거절 사유는 지원자에게 메일로 전달되는 프로즈라 한 줄 입력이 아니다.
  // reason 상태만 보는 위 테스트는 이 폭을 못 잡는다(Input 이든 Textarea 든 'reason' 은 있다).
  assert.ok(source.includes('@/components/admin-ui/textarea.jsx'), 'Textarea 컴포넌트를 안 쓴다');
  // 'pending && rejecting &&' 로 정확히 자른다 — 'rejecting &&' 만 찾으면 그 앞줄의
  // '!rejecting &&'(승인/거절 버튼 행)에도 부분 일치해 시작점이 한 블록 위로 밀린다.
  const reasonBlock = source.slice(source.indexOf('pending && rejecting &&'));
  assert.ok(/<Textarea\b/.test(reasonBlock), '거절 사유 입력이 Textarea 가 아니다');
});
