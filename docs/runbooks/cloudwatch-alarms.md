# CloudWatch 알람 → Slack

## 알림이 두 갈래인 이유

| | 보는 것 | 파일 |
|---|---|---|
| 로그 알림 | 로그에 찍힌 **글자** (`CRITICAL`·`Traceback`·`http error status=5`) | `copilot/environments/addons/log-slack-alerts.yml` |
| 지표 알람 | AWS 가 세는 **숫자** (5xx 건수·응답시간·정상 서버 수) | `copilot/environments/addons/alarm-slack.yml`, 정상 서버 수만 `copilot/api/addons/api-alarms.yml` |

앱이 조용히 느려지거나 붙을 서버가 없어지면 **로그에는 아무것도 안 남는다**. 2026-08-26 장애가 그랬다 — 동기 이미지 작업이 이벤트루프를 얼려 `/healthz` 가 37초 공백이었고 ALB 가 서버를 죽은 것으로 판정했다. 그건 숫자로만 보인다.

도착지는 같은 Slack 채널(`SLACK_ALERT_WEBHOOK_URL`)이다.

## 지금 걸린 알람 넷

| 알람 | 조건 | 무슨 뜻 |
|---|---|---|
| `wearless-use1-target-5xx` | Target 5xx 5분에 10건 이상 | 앱이 에러를 돌려준다. 사용자가 에러 화면을 본다 |
| `wearless-use1-elb-502-504` | ELB 가 만든 502·504 합계 5분에 5건 이상 | 앱이 응답하지 못했다(응답 도중 죽음, 또는 멈춰서 타임아웃). 앱이 응답한 게 아니라 target-5xx 에는 안 잡힌다 |
| `wearless-use1-slow-response` | 응답 표본 10건 이상인 5분의 응답 **중앙값** 2초 초과가 10분(5분 × 2회) | 일부가 아니라 대부분의 요청이 느리다 |
| `wearless-use1-api-no-healthy-target` | api 타깃그룹 정상 서버 1분 최솟값이 0 | ALB 가 붙을 서버가 없다. 이벤트루프 동결로 `/healthz` 가 오래 막혀도 잡힌다. 20~40초짜리 동결은 놓칠 수 있다(아래 사각지대) |

복구(`OK`)도 Slack 으로 온다 — 울고 끝나면 회복됐는지 모른다.

## 처음 임계를 잡은 근거 (2026-09-23 prod 3시간 실측, 503·p95 는 아래에서 교체됨)

- 요청 3,283건, **Target 5xx 0건**, 응답 p95 **0.69초**
- **ELB 503 43건(1.3%) — 우리 장애가 아니다.** ALB 뒤 Copilot 기본 타깃그룹은 타깃이 0대인데, 호스트명 없이 IP 로 직접 들어오는 봇 스캔이 거기로 떨어져 503 이 난다. 5분당 1~7건이 바닥.
- 그래서 503 임계는 바닥의 4배인 **30**. api 가 실제로 내려가면 사용자 요청이 전부 503 이 되어 이 선을 금방 넘는다.

## 2026-09-26 개정: 헛경보를 고치고 사각지대를 메웠다 (PR #414, 사후 리뷰 후속)

9/25 에 알람이 세 번 울렸는데 셋 다 장애가 아니었다.

- `slow-response` 두 번(10:59, 14:12 KST): FaceMarket 2차 등록 사진 업로드가 장당 5~9초라서 울렸다. 트래픽이 5분당 중앙값 0건이라 p95 는 느린 요청 몇 개가 정한다. 그래서 p95 대신 **중앙값**을 본다. 원래 오래 걸리는 요청은 꼬리만 끌어올리고, 서버가 실제로 느려지면 모든 요청이 느려져 중앙값이 오른다. 응답 표본(`TargetResponseTime` 의 `SampleCount`)이 10건 미만인 5분은 값을 내지 않아(`IF(n >= 10, p50)`), 한 사람의 업로드 몇 장이 중앙값을 정하는 한산한 시간을 거른다. else 값을 0 으로 주면 아직 채워지는 최신 5분이 진짜 0 이 되어 연속 판정이 끊긴다. "두 번 연속" 조건은 실제로 필요하다. 9/25 14:15 구간은 사진 20장 업로드만으로 표본 10건 이상, 중앙값 2.64초였다(한 구간이라 안 울렸다).
- `no-healthy-target` 한 번(21:21 KST): ELB 503 이 3분에 95건 몰렸는데, 그동안 api 정상 서버는 계속 1대였고 api 타깃그룹 요청은 0건이었다. 기본 타깃그룹으로 떨어진 봇 트래픽이다. 그래서 503 개수 대신 **api 타깃그룹 정상 서버 수**를 본다. 타깃그룹은 서비스 스택이 만들므로 알람을 api 서비스 애드온에 두고 `addons.parameters.yml` 로 `TargetGroupFullName` 을 넘겨받는다.

처음(#414)에는 정상 서버 알람을 환경 애드온에서 Metrics Insights 로 LB 의 모든 타깃그룹을 훑게 했고 2분 연속을 요구했다. 사후 리뷰에서 세 가지가 지적돼 옮겼다. 다른 타깃그룹이 섞일 수 있고, api 가 없는 환경에서는 영구 ALARM 이 되며, 헬스체크(10초 간격, 2회 실패)상 37초 동결은 20~40초 비정상으로 끝나 2분 연속에 안 걸린다. 503 알람을 없애면서 비는 ELB 502·504 는 새 알람으로 메웠다(`SUM([e502, e504])`. `FILL(…, 0)` 은 아직 지표가 안 온 최신 5분까지 0 으로 만들어 늦게 도착한 에러를 묻어서 쓰지 않는다). 새 알람이 처음 판정을 마치며 INSUFFICIENT_DATA 에서 OK 로 가는 것은 복구가 아니므로 Slack 램다가 보내지 않는다.

60일(8/26 use1 이전 이후 전체) 실측 대입. 연속은 시간이 실제로 붙은 구간만 셌다.

| 알람 | 옛 규칙 | 새 규칙 |
|---|---|---|
| `slow-response` | 7회. 8/30 진짜 장애 2회(21:55, 22:20), 사진 업로드 4회(8/27 두 번, 8/31, 9/25), 에디터 사용 중 에러 없는 튐 1회(9/7) | 0회. 8/30 장애는 `target-5xx` 가 21:50 에 먼저 잡는다(5분에 14건, 28건) |
| 붙을 서버 없음 | ELB 503 규칙으로 17회. 그때마다 api 정상 서버는 1대 이상이었다 | 0회. 정상 서버 0대인 분 0회, 지표가 빈 구간 0회(배포 중에도 새 서버가 먼저 붙는다) |
| `elb-502-504` | 없음 | 0회. 60일 동안 502 2건, 504 2건, 모두 5분에 1건씩 따로 |

## 남는 사각지대

- **20~40초짜리 동결**: `HealthyHostCount` 는 1분에 두 번만 찍힌다(실측 SampleCount 2). 헬스체크가 10초 간격, 2회 실패라 37초 동결은 20~40초 비정상으로 끝나고, 두 번의 측정 사이에 끼면 안 보인다. 더 촘촘한 지표가 ALB 에는 없다.
- **한산한 시간의 느려짐**: 응답 표본이 5분에 10건 미만이면 `slow-response` 는 보지 않는다. 서버가 멈추면 정상 서버 알람이, 에러로 번지면 `target-5xx`·`elb-502-504` 가 잡는다.
- **서버가 통째로 사라지는 경우**: 타깃이 0개가 되면 타깃그룹이 `HealthyHostCount` 를 아예 안 낸다. 빈 데이터는 장애로 보게 했지만(`breaching`), CloudWatch 는 평가 구간에 이전 값(1)이 남아 있는 동안 그 값으로 판정하므로 몇 분 늦게 울 수 있다. 부팅이 실패하면 로그 알림(`Application startup failed`)이 먼저 온다.
- **리스너 규칙이 깨져 사용자 요청이 기본 타깃그룹으로 가는 경우**: ELB 503 만 늘고 api 쪽 지표는 조용하다. 봇 503 과 구분할 수 없어서 알람이 없다. 근본 원인은 봇 요청이 빈 기본 타깃그룹에서 503 을 받는 것이다. Cloudflare 원본 잠금(#315)이 붙거나 리스너 기본 동작을 고정 응답(예: 404)으로 바꿔 봇 503 바닥이 0 이 되면 ELB 503 알람을 다시 걸 수 있다.

## ECS CPU 알람이 없는 이유

`AWS/ECS CPUUtilization` 은 `(ClusterName, ServiceName)` 차원으로만 온다. 서비스 이름이 `wearless-use1-api-Service-QliObqw9TgGI` 처럼 난수가 붙고 Copilot 이 export 하지 않아 환경 애드온에서 지목할 수 없다(클러스터 차원만으로는 지표 자체가 안 온다 — 확인함).

포화는 응답시간으로 먼저 보인다(부하 실측: 65 rps 에서 CPU 17%·서버 응답 8ms). `…-slow-response` 가 그 역할을 한다. 진짜 CPU 알람이 필요해지면 서비스 이름을 런타임에 찾아 알람을 만드는 커스텀 리소스가 필요하다.

## 배포

두 곳이 따로 배포된다.

- `copilot/api/addons/**`(정상 서버 알람): main 에 머지되면 `deploy-server` 워크플로가 반영한다. ⚠️ 알람만 바꿔도 **api 전체 배포**(이미지 빌드, 롤링 재시작)가 돈다. 옛 태스크는 SIGTERM 때 실행 중인 마네킹·분석 잡을 `error(worker_shutdown)` 로 닫으므로(`copilot/api/manifest.yml`), 실행 중인 잡이 없는 한가한 시간에 머지한다.
- `copilot/environments/addons/**`(나머지 알람): CI 가 배포하지 않는다. 아래 명령을 IAM 사용자 자격으로 돌린다. 루트 자격(`aws login` 루트)으로는 copilot 이 역할 전환을 못 해서 실패한다(`Roles may not be assumed by root accounts`).

```bash
copilot-aws env deploy --name use1
```

앱 재배포와 무관하다. 환경 애드온만 바뀐다. 두 곳을 같이 바꾼 변경은 **둘 다 반영해야 끝난다.** 한쪽만 나가면 옛 알람과 새 알람이 같이 돌거나 main 설명과 실제가 어긋난다. ⚠️ **CF 원본 잠금(#315)과 순서가 얽힌다**: 그 작업도 `env deploy` 를 쓴다. 두 개를 같은 시점에 밀면 어느 쪽이 깨졌는지 못 가른다. 하나씩.

## 알람을 추가할 때

SNS 토픽이 `${App}-${Env}-AlarmTopicArn` 으로 export 돼 있다. 새 알람의 `AlarmActions`·`OKActions` 에 그걸 주면 같은 채널로 온다. `api-alarms.yml` 이 이미 이 export 를 가져다 쓰므로 export 이름을 바꾸거나 지우면 env 배포가 막힌다.

## 되돌리기

먼저 `copilot/api/addons/api-alarms.yml` 과 `addons.parameters.yml` 을 지워 api 를 배포한다(토픽 export 를 가져다 쓰는 쪽이 먼저 빠져야 한다). 그다음 `alarm-slack.yml` 을 지우고 `env deploy`. 알람·토픽·램다가 같이 사라진다. 로그 알림은 별개 파일이라 영향 없다.

## 시끄러우면

- 특정 알람만 끄기: 해당 리소스에 `ActionsEnabled: false` 를 넣고 `env deploy`. `api-no-healthy-target` 은 `api-alarms.yml` 에 넣고 api 배포(env 배포로는 안 바뀐다)
- 임계 조정: `Threshold`·`EvaluationPeriods` 를 고쳐 `env deploy`(정상 서버 알람은 api 배포). `DatapointsToAlarm` 을 쓰는 알람은 없으니 연속 횟수는 `EvaluationPeriods` 하나로 정해진다
- 새벽에 데이터가 없어 우는 경우: 환경 애드온 알람은 모두 `TreatMissingData: notBreaching` 이다. `api-no-healthy-target` 만 일부러 `breaching` 이다(위 사각지대 절 참고)
