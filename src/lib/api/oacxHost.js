/* OACX 위젯이 붙을 DOM 호스트 관리.

   위젯은 Vue 2 이고 `new Vue({ el: "#oacxDiv" })` 로 마운트한다 — Vue 2 의 el 마운트는
   대상 엘리먼트를 렌더 결과로 **교체**하므로, 인증창이 뜨는 순간 진짜 #oacxDiv 노드는
   DOM 에서 사라진다.

   그래서 #oacxDiv 를 React 가 그리면 안 된다. React 가 그리면 그 노드는 형제 재조정의
   기준(insertBefore 의 두 번째 인자)이 되는데, Vue 가 그걸 치워 버리면
   `NotFoundError: ... not a child of this node` 로 앱이 죽는다(2026-09-14 프로덕션).

   React 는 빈 #oacxHost 만 그리고, 그 안의 #oacxDiv 는 여기서 직접 만든다. React 는
   자기가 그린 적 없는 자식을 재조정하지 않으므로 Vue 가 그 안에서 무슨 짓을 해도 안전하다. */

export const OACX_HOST_ID = 'oacxHost';
export const OACX_MOUNT_ID = 'oacxDiv';

/* 호스트를 비우고 새 #oacxDiv 를 만들어 넣는다. 매번 새로 만드는 이유: 직전 호출에서
   Vue 가 교체해 둔 노드가 남아 있을 수 있고, 그걸 재사용하면 위젯이 남의 트리 위에
   마운트한다. doc 은 테스트에서 최소 대역을 넣기 위한 주입점이다. */
export function mountOacxHost(host, doc = globalThis.document) {
  if (!host) return null;
  host.replaceChildren();
  const mount = doc.createElement('div');
  mount.id = OACX_MOUNT_ID;
  host.appendChild(mount);
  return mount;
}
