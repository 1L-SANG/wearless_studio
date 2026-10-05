/* 에디터 블록 저장의 낙관적 동시성(editor_revision, 2026-10-02).

   두 탭(또는 두 기기)이 같은 상세페이지를 열어 두면 나중에 저장한 쪽이 앞의 편집을 통째로
   덮었다(LWW). 여기서 프로젝트별로
   ① 마지막으로 본 서버 리비전을 기억해 PUT 에 싣고(다르면 서버가 409 editor_conflict),
   ② 저장을 한 번에 하나씩 보내 같은 탭 안에서 응답 순서가 뒤집히지 않게 하고(밀린 것은
      최신 하나로 합친다),
   ③ 불러온 그대로인 문서는 다시 PUT 하지 않는다(열기만 해도 리비전이 올라 다른 탭을 밀어내지 않게).
   네트워크는 load/put 으로 주입받는다 — httpAdapter 가 실서버를, 테스트가 가짜를 꽂는다.
   normalize 는 비교 기준(서버가 절대화해 돌려준 에셋 URL 을 저장 본문처럼 상대경로로). */

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map((v) => (v === undefined ? 'null' : stableJson(v))).join(',')}]`;
  if (value && typeof value === 'object') {
    const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${stableJson(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'null';
}

/** 키 순서를 무시한 JSON 동등 — 서버 jsonb 는 키 순서를 바꿔 돌려준다. */
export function sameEditorJson(a, b) {
  return stableJson(a) === stableJson(b);
}

const SUPERSEDED = Symbol('superseded');

export function createEditorRevisionSync({ load, put, normalize = (blocks) => blocks }) {
  const states = new Map();
  const stateOf = (projectId) => {
    if (!states.has(projectId)) {
      states.set(projectId, { revision: null, saved: null, tail: Promise.resolve(), latestTicket: 0 });
    }
    return states.get(projectId);
  };

  return {
    /** 서버 블록을 읽고 리비전을 기억한다. 옛 서버(배열 응답)면 리비전은 모름(null). */
    async load(projectId) {
      const res = await load(projectId);
      const state = stateOf(projectId);
      if (Array.isArray(res)) {
        state.revision = null;
        state.saved = stableJson(normalize(res));
        return res;
      }
      const blocks = Array.isArray(res?.blocks) ? res.blocks : [];
      state.revision = Number.isInteger(res?.revision) ? res.revision : null;
      state.saved = stableJson(normalize(blocks));
      return blocks;
    },

    revisionOf(projectId) {
      return states.get(projectId)?.revision ?? null;
    },

    /** 저장 — 앞 저장이 끝난 뒤에 나가고, 그사이 더 새 저장이 들어왔으면 이건 건너뛴다
        (그 새 저장이 이 내용을 포함한다). 건너뛴 호출은 **그 새 저장의 결과**로 끝난다 —
        새 저장이 409 면 건너뛴 쪽도 실패한다(성공으로 끝내면 [저장]이 "저장했어요" 를 띄운다,
        2026-10-02 로컬 두 탭 실측). 409 면 editor_conflict 에러를 그대로 던진다. */
    save(projectId, body) {
      const state = stateOf(projectId);
      const ticket = ++state.latestTicket;
      const run = state.tail.then(async () => {
        if (ticket !== state.latestTicket) return SUPERSEDED;
        const json = stableJson(normalize(body));
        if (json === state.saved) return undefined;
        const res = await put(projectId, body, state.revision);
        if (Number.isInteger(res?.revision)) state.revision = res.revision;
        state.saved = json;
        return undefined;
      });
      state.tail = run.catch(() => {});
      // 체인(tail) 밖에서 최신 저장을 기다린다 — 체인 안에서 기다리면 최신 저장이 이 호출 뒤에
      // 줄 서 있어 서로를 기다린다.
      const result = run.then((outcome) => (outcome === SUPERSEDED ? state.latestResult : outcome));
      state.latestResult = result;
      return result;
    },
  };
}
