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
      }).finally(() => {
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
