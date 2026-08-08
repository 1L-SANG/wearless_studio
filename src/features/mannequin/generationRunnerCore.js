/* 마네킹 생성 발사의 순수 코어 — 같은 프로젝트의 중복 호출을 하나의 in-flight 요청으로 합류시킨다.
   콘티(백그라운드 발사)와 마네킹(진입 시 발사)이 같은 러너를 공유해야 유료 생성이 두 번 나가지 않는다.
   api·store 의존은 배선 래퍼(generationRunner.js)가 주입한다 — 이 파일은 node --test 로 직접 검증된다.

   **핵심 계약: 요청(request)과 발화(start)는 다르다.**
   서버는 컷이 이미 있으면 job 없이 200 으로 답한다(무차감·무작업). 그 경우 이 러너는 아무것도
   알리지 않는다 — `onJobStarted`(=최초 생성 소유권 주장)도, `status:'running'`(=리본)도, 종결
   `idle/100`(=완료 배지)도 쓰지 않는다. 시작하지 않은 일을 시작했다고 말하면 두 가지가 깨진다:
   ① 리본이 하지도 않은 생성을 진행 중/완료로 보고하고, ② 최초 생성 소유권 플래그가 오염돼
   "컷이 원래 있었나" 판정이 뒤집히고 유료 재생성(분석 수정 반영)이 조용히 건너뛰어진다. */
export function createMannequinGenerationRunner({
  generate,
  readProgress,
  onJobChange,
  onJobStarted = () => {},
}) {
  let inflight = null;
  let inflightProjectId = null;

  return {
    request(projectId) {
      if (!projectId) return Promise.resolve(null);
      if (inflight && inflightProjectId === projectId) return inflight;

      // job 이 실제로 생겼다는 증거를 처음 본 순간 한 번만 발화한다. 증거는 두 가지 —
      // 어댑터의 명시적 202 신호(onJobStarted), 그리고 진행률 콜백(진행률이 오면 job 이 돈다).
      // 둘 중 무엇이 먼저 와도 같은 지점을 지나게 해서 종결 보고와 짝이 맞도록 한다.
      let started = false;
      const markStarted = () => {
        if (started) return;
        started = true;
        onJobStarted(projectId);
        onJobChange(projectId, {
          status: 'running',
          progress: readProgress(projectId),
          errorMessage: '',
        });
      };

      inflightProjectId = projectId;
      inflight = generate(projectId, {
        onJobStarted: markStarted,
        onProgress: (next) => {
          markStarted();
          onJobChange(projectId, { status: 'running', progress: next, errorMessage: '' });
        },
      }).then(
        // 성공/실패 모두 여기서 종결 상태를 알린다 — 콘티 화면(리본)은 진행 중 알림만 받고
        // 그 이후를 스스로 확인할 방법이 없다(백그라운드 발사엔 .then() 이 없다). 러너가
        // 잡의 생애주기를 끝까지 책임져야 두 호출자(콘티·마네킹) 모두 결과를 공유받는다.
        (result) => {
          // 발화하지 않은 성공 = 200 캐시(컷이 이미 있었음). 알릴 완료가 없다.
          if (started) onJobChange(projectId, { status: 'idle', progress: 100, errorMessage: '' });
          return result;
        },
        // 실패는 발화 여부와 무관하게 알린다. 여기서만 걸리는 실패(POST 자체가 실패)도
        // "생성을 시도했는데 안 됐다" 는 진짜 사건이고, 백그라운드 발사는 rejection 을
        // 삼키므로(콘티) 리본이 사용자에게 알릴 유일한 통로다. 없는 일을 알리는 게 아니다.
        (error) => {
          onJobChange(projectId, {
            status: 'error',
            progress: readProgress(projectId),
            errorMessage: error?.message || '마네킹컷 생성에 실패했어요.',
          });
          throw error;
        },
      ).finally(() => {
        if (inflightProjectId === projectId) {
          inflight = null;
          inflightProjectId = null;
        }
      });

      return inflight;
    },

    isRunning(projectId) {
      return inflight != null && inflightProjectId === projectId;
    },
  };
}
