# CloudWatch 알람 → Slack

## 알림이 두 갈래인 이유

| | 보는 것 | 파일 |
|---|---|---|
| 로그 알림 | 로그에 찍힌 **글자** (`CRITICAL`·`Traceback`·`http error status=5`) | `copilot/environments/addons/log-slack-alerts.yml` |
| 지표 알람 | AWS 가 세는 **숫자** (5xx 건수·응답시간·정상 서버 수) | `copilot/environments/addons/alarm-slack.yml` |

앱이 조용히 느려지거나 붙을 서버가 없어지면 **로그에는 아무것도 안 남는다**. 2026-08-26 장애가 그랬다 — 동기 이미지 작업이 이벤트루프를 얼려 `/healthz` 가 37초 공백이었고 ALB 가 서버를 죽은 것으로 판정했다. 그건 숫자로만 보인다.

도착지는 같은 Slack 채널(`SLACK_ALERT_WEBHOOK_URL`)이다.

## 지금 걸린 알람 셋

| 알람 | 조건 | 무슨 뜻 |
|---|---|---|
| `…-target-5xx` | Target 5xx 5분에 10건 이상 | 앱이 에러를 뱉는다 = 사용자가 에러 화면을 본다 |
| `…-slow-response` | 요청 5건 이상인 5분의 응답 **중앙값** 2초 초과가 10분(5분 × 2회) | 일부가 아니라 대부분의 요청이 느리다 |
| `…-no-healthy-target` | api 타깃그룹 정상 서버 0대가 2분(1분 × 2회) | ALB 가 붙을 서버가 없다. 이벤트루프 동결로 `/healthz` 가 막혀도 여기서 잡힌다 |

복구(`OK`)도 Slack 으로 온다 — 울고 끝나면 회복됐는지 모른다.

## 처음 임계를 잡은 근거 (2026-09-23 prod 3시간 실측, 503·p95 는 아래에서 교체됨)

- 요청 3,283건, **Target 5xx 0건**, 응답 p95 **0.69초**
- **ELB 503 43건(1.3%) — 우리 장애가 아니다.** ALB 뒤 Copilot 기본 타깃그룹은 타깃이 0대인데, 호스트명 없이 IP 로 직접 들어오는 봇 스캔이 거기로 떨어져 503 이 난다. 5분당 1~7건이 바닥.
- 그래서 503 임계는 바닥의 4배인 **30**. api 가 실제로 내려가면 사용자 요청이 전부 503 이 되어 이 선을 금방 넘는다.

## 2026-09-26 개정: 헛경보 둘을 고쳤다

9/25 에 알람이 세 번 울렸는데 셋 다 장애가 아니었다.

- `slow-response` 두 번(10:59, 14:12 KST): FaceMarket 2차 등록 사진 업로드가 장당 5~9초라서 울렸다. 트래픽이 5분당 중앙값 0건이라 p95 는 느린 요청 몇 개가 정한다. 그래서 p95 대신 **중앙값**을 본다. 원래 오래 걸리는 요청은 꼬리만 끌어올리고, 서버가 실제로 느려지면 모든 요청이 느려져 중앙값이 오른다. 요청 5건 미만인 5분은 0 으로 쳐서 한두 건이 중앙값을 정하는 새벽을 거른다.
- `no-healthy-target` 한 번(21:21 KST): ELB 503 이 3분에 95건 몰렸는데, 그동안 api 정상 서버는 계속 1대였고 api 타깃그룹 요청은 0건이었다. 기본 타깃그룹으로 떨어진 봇 트래픽이다. 그래서 503 개수 대신 **api 타깃그룹 정상 서버 수**를 본다. 타깃그룹은 서비스 스택이 만들어 환경 애드온이 직접 지목할 수 없으므로 Metrics Insights 쿼리(`SELECT MIN(HealthyHostCount) … WHERE LoadBalancer = …`)로 이 LB 의 타깃그룹을 훑는다. 기본 타깃그룹은 이 지표를 내지 않아 결과는 api 값이 된다.

60일(8/26 use1 이전 이후 전체) 실측에 새 규칙을 대입한 결과:

| 알람 | 옛 규칙 | 새 규칙 |
|---|---|---|
| `slow-response` | 7회 울림(9/25 두 번은 사진 업로드, 나머지는 원인 미조사) | 1회: 8/30 22:00, `POST /licenses` 가 8~11초 걸려 503 을 8분 넘게 반복한 **진짜 장애** |
| `no-healthy-target` | 17회 울렸을 것(9/25 21:22 포함). 그때마다 api 정상 서버는 1대 이상이었다 | 0회. 정상 서버 0대인 분 0회, 지표가 빈 구간 0회(배포 중에도 새 서버가 먼저 붙는다) |

대가: 요청이 5분에 5건 미만인 한산한 시간의 느려짐은 `slow-response` 가 못 본다. 서버가 아예 멈추는 경우는 `/healthz` 실패로 `no-healthy-target` 이 잡고, 에러로 번지면 `target-5xx` 가 잡는다.

정상 서버 알람은 빈 데이터를 장애로 본다(`TreatMissingData: breaching`). 서버가 모두 빠지면 타깃그룹도 지표를 멈추기 때문이다. 이 알람이 새로 생긴 서비스나 LB 교체 직후 울리면 쿼리의 LoadBalancer 값부터 확인한다.

## ECS CPU 알람이 없는 이유

`AWS/ECS CPUUtilization` 은 `(ClusterName, ServiceName)` 차원으로만 온다. 서비스 이름이 `wearless-use1-api-Service-QliObqw9TgGI` 처럼 난수가 붙고 Copilot 이 export 하지 않아 환경 애드온에서 지목할 수 없다(클러스터 차원만으로는 지표 자체가 안 온다 — 확인함).

포화는 응답시간으로 먼저 보인다(부하 실측: 65 rps 에서 CPU 17%·서버 응답 8ms). `…-slow-response` 가 그 역할을 한다. 진짜 CPU 알람이 필요해지면 서비스 이름을 런타임에 찾아 알람을 만드는 커스텀 리소스가 필요하다.

## 배포

```bash
copilot-aws env deploy --name use1
```

앱 재배포와 무관하다 — 환경 애드온만 바뀐다. ⚠️ **CF 원본 잠금(#315)과 순서가 얽힌다**: 그 작업도 `env deploy` 를 쓴다. 두 개를 같은 시점에 밀면 어느 쪽이 깨졌는지 못 가른다. 하나씩.

## 알람을 추가할 때

SNS 토픽이 `${App}-${Env}-AlarmTopicArn` 으로 export 돼 있다. 새 알람의 `AlarmActions`·`OKActions` 에 그걸 주면 같은 채널로 온다.

## 되돌리기

`alarm-slack.yml` 을 지우고 `env deploy`. 알람·토픽·램다가 같이 사라진다. 로그 알림은 별개 파일이라 영향 없다.

## 시끄러우면

- 특정 알람만 끄기: 해당 리소스의 `ActionsEnabled: false` → `env deploy`
- 임계 조정: `Threshold` / `EvaluationPeriods` 만 고쳐 `env deploy`
- 새벽에 데이터가 없어 우는 경우: `target-5xx`·`slow-response` 는 이미 `TreatMissingData: notBreaching` 로 막아 뒀다. `no-healthy-target` 만 일부러 `breaching` 이다(위 개정 절 참고)
