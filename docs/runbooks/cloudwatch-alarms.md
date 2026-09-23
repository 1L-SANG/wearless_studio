# CloudWatch 알람 → Slack

## 알림이 두 갈래인 이유

| | 보는 것 | 파일 |
|---|---|---|
| 로그 알림 | 로그에 찍힌 **글자** (`CRITICAL`·`Traceback`·`http error status=5`) | `copilot/environments/addons/log-slack-alerts.yml` |
| 지표 알람 | AWS 가 세는 **숫자** (5xx 건수·응답시간·503) | `copilot/environments/addons/alarm-slack.yml` |

앱이 조용히 느려지거나 붙을 서버가 없어지면 **로그에는 아무것도 안 남는다**. 2026-08-26 장애가 그랬다 — 동기 이미지 작업이 이벤트루프를 얼려 `/healthz` 가 37초 공백이었고 ALB 가 서버를 죽은 것으로 판정했다. 그건 숫자로만 보인다.

도착지는 같은 Slack 채널(`SLACK_ALERT_WEBHOOK_URL`)이다.

## 지금 걸린 알람 셋

| 알람 | 조건 | 무슨 뜻 |
|---|---|---|
| `…-target-5xx` | Target 5xx 5분에 10건 이상 | 앱이 에러를 뱉는다 = 사용자가 에러 화면을 본다 |
| `…-slow-response` | 응답 p95 3초 초과가 10분(5분 × 2회) | 느려짐·이벤트루프 동결 |
| `…-no-healthy-target` | ELB 503 5분에 30건 이상이 10분 | ALB 가 붙을 서버를 못 찾는다 |

복구(`OK`)도 Slack 으로 온다 — 울고 끝나면 회복됐는지 모른다.

## 임계를 이렇게 잡은 근거 (2026-09-23 prod 3시간 실측)

- 요청 3,283건, **Target 5xx 0건**, 응답 p95 **0.69초**
- **ELB 503 43건(1.3%) — 우리 장애가 아니다.** ALB 뒤 Copilot 기본 타깃그룹은 타깃이 0대인데, 호스트명 없이 IP 로 직접 들어오는 봇 스캔이 거기로 떨어져 503 이 난다. 5분당 1~7건이 바닥.
- 그래서 503 임계는 바닥의 4배인 **30**. api 가 실제로 내려가면 사용자 요청이 전부 503 이 되어 이 선을 금방 넘는다.
- **Cloudflare 원본 잠금(PR #315)이 붙으면 이 바닥이 0 으로 내려간다** → 그때 임계를 10 쯤으로 조일 것.

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
- 새벽에 데이터가 없어 우는 경우: 이미 `TreatMissingData: notBreaching` 로 막아 뒀다
