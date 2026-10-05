# OpenAI 운영 키 전환

## 키 역할

- `OPENAI_API_KEY`는 학교 지원 크레딧 계정의 주 키다. 프로세스가 시작되면 이 키를 먼저 쓴다.
- `OPENAI_API_KEY_FALLBACK`은 오너 계정의 예비 키다. 주 키가 영구 계정 오류를 반환할 때만 쓴다.

예비 키가 설정되지 않으면 기존과 같이 주 키의 오류를 그대로 반환한다. 전환된 프로세스는 주 키로 자동 복귀하지 않는다. 재배포나 재시작으로 새 프로세스가 뜨면 다시 주 키부터 시도한다.

## 전환 조건

다음 응답에서만 같은 요청을 예비 키로 즉시 한 번 다시 보낸다.

- HTTP 429이고 오류 코드가 `insufficient_quota` 또는 `credit_balance_exhausted`인 경우
- 오류 코드가 `billing_hard_limit_reached`인 경우
- HTTP 401이고 오류 코드가 `invalid_api_key`인 경우

일반 `rate_limit_exceeded` 429, 5xx, 타임아웃과 연결 오류는 계정을 전환하지 않는다. 이미지 호출의 기존 429 백오프도 그대로 유지된다. 예비 키가 같은 영구 오류를 반환하면 추가 전환이나 무한 재시도 없이 그 오류로 끝난다.

## 전환 확인

최초 전환 때 프로세스마다 한 번 다음 `CRITICAL` 로그가 남는다. 기존 CloudWatch 로그 구독이 이 줄을 운영 Slack으로 전달한다.

```text
학교 계정 OpenAI 잔액 소진, 오너 계정으로 전환
```

API와 상세 워커 로그에서 확인한다.

```bash
aws logs filter-log-events --region us-east-1 \
  --log-group-name /copilot/wearless-use1-api \
  --filter-pattern '"학교 계정 OpenAI 잔액 소진, 오너 계정으로 전환"'

aws logs filter-log-events --region us-east-1 \
  --log-group-name /copilot/wearless-use1-detail-worker \
  --filter-pattern '"학교 계정 OpenAI 잔액 소진, 오너 계정으로 전환"'
```

이미지 호출은 `image_usage_events.usage_raw.openai_account`에 `primary` 또는 `fallback`을 남긴다.

## 키 생성과 교체

값은 명령행 인자에 넣지 않는다. 대화형 입력을 사용한다. 처음 배포하기 전에 예비 키 SSM 값을 반드시 만든다.

```bash
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 \
  copilot-aws secret init --name OPENAI_API_KEY_FALLBACK
```

주 키나 예비 키를 교체할 때는 해당 이름으로 덮어쓴다.

```bash
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 \
  copilot-aws secret init --name OPENAI_API_KEY --overwrite

AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 \
  copilot-aws secret init --name OPENAI_API_KEY_FALLBACK --overwrite
```

환경별 값을 물으면 `use1`에만 새 값을 입력한다. SSM을 바꾼 뒤에는 `deploy-server` 워크플로를 수동 실행해 `api`와 `detail-worker`를 모두 새 태스크로 교체한다. 두 서비스의 새 태스크가 정상 상태가 된 뒤 위 로그와 생성 스모크를 확인한다.
