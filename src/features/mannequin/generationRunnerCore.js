/* 마네킹 생성 발사의 순수 코어 — 같은 프로젝트의 중복 호출을 하나의 in-flight 요청으로 합류시킨다.
   콘티(백그라운드 발사)와 마네킹(진입 시 발사)이 같은 러너를 공유해야 유료 생성이 두 번 나가지 않는다.
   api·store 의존은 배선 래퍼(generationRunner.js)가 주입한다 — 이 파일은 node --test 로 직접 검증된다. */
export function createMannequinGenerationRunner({
  generate,
  readProgress,
  onJobChange,
  onRequested = () => {},
}) {
  let inflight = null;
  let inflightProjectId = null;

  return {
    request(projectId) {
      if (!projectId) return Promise.resolve(null);
      if (inflight && inflightProjectId === projectId) return inflight;

      onJobChange(projectId, {
        status: 'running',
        progress: readProgress(projectId),
        errorMessage: '',
      });

      inflightProjectId = projectId;
      onRequested(projectId);
      inflight = generate(projectId, {
        onProgress: (next) => onJobChange(projectId, {
          status: 'running',
          progress: next,
          errorMessage: '',
        }),
      }).then(
        // 성공/실패 모두 여기서 종결 상태를 알린다 — 콘티 화면(리본)은 진행 중 알림만 받고
        // 그 이후를 스스로 확인할 방법이 없다(백그라운드 발사엔 .then() 이 없다). 러너가
        // 잡의 생애주기를 끝까지 책임져야 두 호출자(콘티·마네킹) 모두 결과를 공유받는다.
        (result) => {
          onJobChange(projectId, { status: 'idle', progress: 100, errorMessage: '' });
          return result;
        },
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
