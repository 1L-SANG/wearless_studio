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
  let activeRunId = 0;
  let nextRunId = 0;

  return {
    request(projectId) {
      if (!projectId) return Promise.resolve(null);
      if (inflight && inflightProjectId === projectId) return inflight;

      const runId = ++nextRunId;
      activeRunId = runId;
      const isActiveRun = () => activeRunId === runId;

      // job 이 실제로 생겼다는 증거를 처음 본 순간 한 번만 발화한다. 증거는 두 가지 —
      // 어댑터의 명시적 202 신호(onJobStarted), 그리고 진행률 콜백(진행률이 오면 job 이 돈다).
      // 둘 중 무엇이 먼저 와도 같은 지점을 지나게 해서 종결 보고와 짝이 맞도록 한다.
      let started = false;
      const markStarted = () => {
        if (started || !isActiveRun()) return;
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
          if (!isActiveRun()) return;
          markStarted();
          onJobChange(projectId, { status: 'running', progress: next, errorMessage: '' });
        },
      }).then(
        // 성공/실패 모두 여기서 종결 상태를 알린다 — 콘티 화면(리본)은 진행 중 알림만 받고
        // 그 이후를 스스로 확인할 방법이 없다(백그라운드 발사엔 .then() 이 없다). 러너가
        // 잡의 생애주기를 끝까지 책임져야 두 호출자(콘티·마네킹) 모두 결과를 공유받는다.
        (result) => {
          // 발화하지 않은 성공 = 200 캐시(컷이 이미 있었음). 알릴 완료가 없다.
          if (isActiveRun() && started) {
            onJobChange(projectId, { status: 'idle', progress: 100, errorMessage: '' });
          }
          return result;
        },
        // 실패는 발화 여부와 무관하게 알린다. 여기서만 걸리는 실패(POST 자체가 실패)도
        // "생성을 시도했는데 안 됐다" 는 진짜 사건이고, 백그라운드 발사는 rejection 을
        // 삼키므로(콘티) 리본이 사용자에게 알릴 유일한 통로다. 없는 일을 알리는 게 아니다.
        (error) => {
          // 취소 API 응답으로 이미 로컬 추적을 끊은 옛 폴러라면 뒤늦은 진행률·종결이
          // 그 사이 시작된 새 payload 작업의 상태를 덮지 못하게 한다.
          if (!isActiveRun()) throw error;
          // 사용자가 분석 편집 경고에서 직접 취소한 작업은 실패가 아니다. projectId 도 비워
          // running→idle 전환을 완료 배지로 오인하지 않게 하되, 기다리던 호출자에는 원 오류를
          // 그대로 돌려줘 컷 로드/후속 성공 처리가 이어지지 않게 한다.
          if (error?.code === 'job_cancelled') {
            onJobChange(projectId, {
              projectId: null,
              status: 'idle',
              progress: 0,
              errorMessage: '',
            });
            throw error;
          }
          onJobChange(projectId, {
            status: 'error',
            progress: readProgress(projectId),
            errorMessage: error?.message || '마네킹컷 생성에 실패했어요.',
          });
          throw error;
        },
      ).finally(() => {
        if (isActiveRun()) {
          inflight = null;
          inflightProjectId = null;
          activeRunId = 0;
        }
      });

      return inflight;
    },

    acknowledgeCancellation(projectId) {
      if (!inflight || inflightProjectId !== projectId) return false;
      // 서버 취소 응답은 이미 커밋된 뒤다. pollJob이 cancelled를 관찰하기 전이라도 이 실행을
      // 즉시 비활성화해 같은 프로젝트의 다음 요청이 옛 Promise에 합류하지 않게 한다.
      activeRunId = 0;
      inflight = null;
      inflightProjectId = null;
      onJobChange(projectId, {
        projectId: null,
        status: 'idle',
        progress: 0,
        errorMessage: '',
      });
      return true;
    },

    isRunning(projectId) {
      return inflight != null && inflightProjectId === projectId;
    },
  };
}

/* 입장 시 목록이 비어 공유 러너의 결과를 기다린 경우, 최종 컷으로 최초 생성 소유권을 다시
   판정한다. 최초 GET 시점의 빈 목록만으로 판정하면 수정 전에 시작한 in-flight 결과도 최신
   생성으로 오인하므로 request/extract/classify 순서를 하나의 주입 가능한 이음매로 고정한다. */
export async function resolveInitialGenerationCuts({
  projectId,
  initialCuts,
  requestGeneration,
  extractCuts,
  classifyCuts,
}) {
  if (initialCuts.length) {
    return {
      cuts: initialCuts,
      credits: undefined,
      cutsExisted: classifyCuts(projectId, initialCuts),
    };
  }

  const { data, credits } = await requestGeneration(projectId);
  const cuts = extractCuts(data);
  return {
    cuts,
    credits,
    cutsExisted: classifyCuts(projectId, cuts),
  };
}

/* 분석 수정 자동 재생성의 1회 실행 가드.
   handledRef 는 요청 전에 잠그되 dirty 신호는 성공 뒤에만 지운다. 같은 마운트의 effect 재발화는
   유료 요청을 늘리지 않고, 실패 신호는 다음 화면 진입에서 새 ref 로 다시 시도할 수 있다. */
export async function runGenerationRelevantEditsRefresh({
  handledRef,
  readDirtyRevision,
  cutsExisted,
  regenerate,
  clearDirty,
}) {
  const dirtyRevision = readDirtyRevision();
  if (handledRef.current || !dirtyRevision) return false;
  handledRef.current = true;

  // 최종 컷이 최신 편집 뒤 시작된 최초 생성의 결과라면 별도 유료 재생성은 필요 없다.
  // 이 판정은 최초의 빈 GET이 아니라, 공유 러너 결과가 도착한 뒤의 컷으로 끝나 있어야 한다.
  if (!cutsExisted) {
    clearDirty(dirtyRevision);
    return true;
  }

  try {
    let consumed = false;
    const consumeDirty = () => {
      if (consumed) return;
      consumed = true;
      clearDirty(dirtyRevision);
    };
    const succeeded = await regenerate(consumeDirty);
    // 주입 대역이나 방어적 호출부가 성공 콜백을 생략해도 성공 결과 자체는 소비 근거다.
    if (succeeded) consumeDirty();
    return succeeded === true;
  } catch {
    return false;
  }
}
