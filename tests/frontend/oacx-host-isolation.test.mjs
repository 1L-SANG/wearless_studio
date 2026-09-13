/* OACX 위젯 ↔ React DOM 충돌 회귀 방어.

   2026-09-14 프로덕션 크래시: 모바일 신분증 인증을 마치고 다음 단계로 넘어가는 순간
   `NotFoundError: Failed to execute 'insertBefore' ... not a child of this node`.

   기제(실측):
     · 위젯은 Vue 2 이고 `new Vue({ el: "#oacxDiv" })` 로 마운트한다 — Vue 2 의 el 마운트는
       그 엘리먼트를 렌더 결과로 **교체**한다. 즉 인증창이 뜨는 순간 진짜 #oacxDiv 노드는
       DOM 에서 사라진다.
     · 그런데 #oacxDiv 가 React 가 그린 {content} 의 **형제**였다. 스텝이 바뀌어 React 가
       parent.insertBefore(새 content, oacxDiv) 를 부르면 그 기준 노드가 더 이상 부모의
       자식이 아니라 터진다.

   그래서 불변식: **React 는 #oacxDiv 를 절대 그리지 않는다.** React 는 빈 호스트만 그리고,
   위젯 모듈이 그 안에 #oacxDiv 를 직접 만들어 넣는다. React 는 자기가 그린 적 없는 자식을
   재조정하지 않으므로, Vue 가 그 안에서 무슨 짓을 해도 React 의 형제 목록은 멀쩡하다. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = new URL('../../', import.meta.url);
const read = (p) => readFileSync(fileURLToPath(new URL(p, root)), 'utf8');

test('React 는 #oacxDiv 를 그리지 않는다 — 빈 호스트만 그린다', () => {
  const source = read('src/features/model/ModelRegister.jsx');
  assert.ok(
    !/id=["']oacxDiv["']/.test(source),
    'ModelRegister 가 #oacxDiv 를 직접 그리면 Vue 가 그 노드를 교체할 때 React 형제 앵커가 깨진다',
  );
  assert.match(source, /id=["']oacxHost["']/, '위젯이 붙을 빈 호스트가 있어야 한다');
});

test('위젯 모듈은 #oacxDiv 를 찾아 쓰지 않고 호스트에 새로 만들어 붙인다', () => {
  const widget = read('src/lib/api/facemarketIdentityWidget.js');
  assert.ok(
    !/getElementById\(['"]oacxDiv['"]\)/.test(widget),
    '#oacxDiv 를 찾아 쓰면 Vue 가 교체해 간 남의 노드를 잡는다 — 호스트에 새로 만들어야 한다',
  );
  assert.match(widget, /mountOacxHost/, '호스트 모듈을 통해 붙어야 한다');

  const host = read('src/lib/api/oacxHost.js');
  assert.match(host, /createElement\(['"]div['"]\)/, '#oacxDiv 를 새로 만드는 곳은 여기다');
});

test('mountOacxHost: 호출할 때마다 새 #oacxDiv 를 남긴다', async () => {
  const { mountOacxHost } = await import('../../src/lib/api/oacxHost.js');
  // 최소 DOM 대역 — replaceChildren/appendChild 만 있으면 이 함수는 검증된다.
  const made = [];
  const doc = { createElement: () => { const el = { id: '', tag: 'div' }; made.push(el); return el; } };
  const host = { children: [], replaceChildren() { this.children = []; }, appendChild(el) { this.children.push(el); } };

  const first = mountOacxHost(host, doc);
  assert.equal(host.children.length, 1);
  assert.equal(first.id, 'oacxDiv');

  // Vue 가 교체해 간 상황을 흉내 — 호스트에 남의 노드가 들어 있다.
  host.children = [{ id: 'vue-root' }];
  const second = mountOacxHost(host, doc);
  assert.equal(host.children.length, 1, '이전 잔해는 치워야 한다');
  assert.equal(second.id, 'oacxDiv');
  assert.notEqual(second, first, '재사용이 아니라 매번 새 노드여야 한다');
});
