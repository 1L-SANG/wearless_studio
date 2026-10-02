# api.wearless.kr — Cloudflare Tunnel 인그레스

`api.wearless.kr` 은 ALB 가 아니라 **Cloudflare Tunnel** 로 들어온다(2026-10 전환). api 태스크 안의
`cloudflared` 사이드카가 Cloudflare 로 바깥 연결을 열어 두고, 요청을 같은 태스크의 `localhost:8080`
으로 넘긴다. 인바운드 포트는 없다. 설계: `docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md`.

| 무엇 | 어디 |
|---|---|
| 터널 | Cloudflare Zero Trust → Networks → Tunnels → `wearless-api-use1` |
| 라우트 | `api.wearless.kr` → `http://localhost:8080` |
| 토큰 | SSM `/copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN`(us-east-1) → 사이드카 환경변수 `TUNNEL_TOKEN` |
| 사이드카 | `copilot/api/manifest.yml` 의 `sidecars.cloudflared`(cloudflared 2026.9.3, digest 고정) |
| 로그 | CloudWatch `/copilot/wearless-use1-api`, 스트림 `copilot/cloudflared/<task-id>` |

아래 명령은 `export AWS_PROFILE=wearless` 를 전제로 한다. ⚠️ copilot 명령은 `AWS_PROFILE=wearless` 가 꼭 필요하다 —
`default` 프로필은 `aws login` 토큰이라 Copilot(Go SDK)이 못 읽고 `RequestCanceled … context deadline exceeded` 로 죽는다.

## 0. 터널 만들기 (처음 한 번)

1. Zero Trust → Networks → Tunnels → **Create a tunnel** → **Cloudflared** → 이름 `wearless-api-use1` → 저장.
2. 설치 안내 명령에서 `--token` 뒤 값(`eyJ…`)만 복사한다. 설치 명령은 실행하지 않는다.
3. 라우트(Public Hostname / Published application routes)는 **아직 만들지 않는다** — DNS 전환 때 만든다.
4. 토큰 저장 — 반드시 copilot 으로. raw `aws ssm put-parameter` 는 copilot 태그가 없어 태스크가 못 읽고
   배포가 롤백된다(2026-07-17 FM_CI_PEPPER 사고).
   ```bash
   AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws secret init --name CLOUDFLARE_TUNNEL_TOKEN
   ```
   환경별 값을 물으면 `use1` 에만 붙여 넣고 나머지는 빈칸으로 넘긴다(`--values` 는 셸 히스토리에 남아 쓰지 않는다).
5. 확인 — 두 태그가 나와야 한다:
   ```bash
   aws ssm list-tags-for-resource --region us-east-1 --resource-type Parameter \
     --resource-id /copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN \
     --query 'TagList[].[Key,Value]' --output text
   # copilot-application   wearless
   # copilot-environment   use1
   ```

## 1. 사이드카 배포 확인

사이드카가 든 PR 이 머지되면 `deploy-server` 가 api 를 배포한다. 끝나면:

```bash
aws logs filter-log-events --region us-east-1 --log-group-name /copilot/wearless-use1-api \
  --log-stream-name-prefix copilot/cloudflared/ --filter-pattern '"Registered tunnel connection"' \
  --start-time $(( ($(date +%s) - 1800) * 1000 )) --query 'events[].message' --output text
```

연결 4줄이 나오고, 대시보드에서 터널이 **HEALTHY** 면 된다.

## 2. DNS 전환 (`api.wearless.kr` → 터널)

트래픽이 적은 시간(KST 새벽)에 한다. 시작 전에 터미널 하나에 `scripts/api-watch.sh` 를 띄워 둔다.

1. DNS → Records 에서 `api` CNAME 의 지금 대상을 적어 둔다(되돌리기용).
   2026-10-02 값: `wearle-Publi-n7eZ27fYallL-825254557.us-east-1.elb.amazonaws.com`, Proxied.
2. 터널 `wearless-api-use1` → 라우트 추가: Subdomain `api` · Domain `wearless.kr` · Service `HTTP` · URL `localhost:8080`.
   "이미 레코드가 있다" 오류가 나면 DNS → Records 에서 `api` CNAME 을 지우고 **곧바로** 라우트를 다시 저장한다
   (그 사이가 공백이다 — 목표 1분 이내).
3. DNS → Records 의 `api` 가 `<터널 UUID>.cfargotunnel.com` · Proxied 인지 본다.
4. 확인 — 전부 통과해야 전환 완료:
   ```bash
   curl -sI https://api.wearless.kr/healthz | grep -iE '^(HTTP|server|cf-ray)'     # 200 · cloudflare
   curl -s https://api.wearless.kr/readyz                                          # {"status":"ok",…}
   curl -s -o /dev/null -D - -X OPTIONS https://api.wearless.kr/v1/projects \
     -H 'Origin: https://ai.wearless.kr' -H 'Access-Control-Request-Method: POST' \
     -H 'Access-Control-Request-Headers: authorization,content-type' | grep -i '^access-control-allow-origin'
   # → access-control-allow-origin: https://ai.wearless.kr
   curl -s -o /dev/null -w '%{http_code}\n' https://api.wearless.kr/v1/webhooks/toss/not-the-secret
   # → 전환 전에 같은 명령으로 잰 값과 같아야 한다(앱 404 — Cloudflare 403 이면 봇 차단 설정을 본다)
   ```
5. 방문자 IP — 내 IP 가 api 접근 로그에 그대로 찍혀야 한다(127.0.0.1·Cloudflare 대역이면 실패):
   ```bash
   MYIP=$(curl -s https://www.cloudflare.com/cdn-cgi/trace | sed -n 's/^ip=//p'); echo "$MYIP"
   curl -s -o /dev/null https://api.wearless.kr/healthz; sleep 20
   aws logs filter-log-events --region us-east-1 --log-group-name /copilot/wearless-use1-api \
     --log-stream-name-prefix copilot/api/ --filter-pattern "\"$MYIP\"" \
     --start-time $(( ($(date +%s) - 300) * 1000 )) --query 'events[].message' --output text | head -3
   ```
6. 롤링 배포 실측 — api-watch 를 띄운 채 `gh workflow run deploy-server.yml --ref main`. 배포가 끝날 때까지
   api-watch 의 `bad` 가 0 이어야 한다. 하나라도 늘면 아래 되돌리기 후 원인을 본다.

## 되돌리기

| 시점 | 방법 |
|---|---|
| DNS 전환 후 ~ ALB 철거 전 | 터널 라우트 `api.wearless.kr` 삭제 → DNS → Records 에 `api` CNAME → 위 ALB 주소 · Proxied 로 추가. 수 초 |
| ALB 철거 후(api 가 Backend Service) | 전환 PR 을 revert → CI 가 Load Balanced Web Service 로 재배포하며 ALB 가 **새 주소**로 생긴다 → `aws elbv2 describe-load-balancers --region us-east-1 --query 'LoadBalancers[0].DNSName' --output text` → 그 주소로 CNAME 교체(위와 같이). 환경 매니페스트에 인증서가 남아 있어야 HTTPS 리스너가 같이 생긴다 |
| 마무리(인증서 제거) 후 | 마무리 PR revert + `AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws env deploy --name use1` → 그다음 위 줄 |

## 알림

ALB 지표 알람을 걷어낸 자리를 둘이 메운다.

- **외부 업타임 모니터** — 고르는 기준: 무료로 Slack 알림, 확인 간격 5분 이하, HTTPS + 응답 본문 키워드.
  대상 `https://api.wearless.kr/healthz`, 키워드 `ok`.
- **Cloudflare Tunnel Health Alert** — 대시보드 → Notifications → Add → *Tunnel Health Alert* →
  터널 `wearless-api-use1` → 메일(오너 주소).

5xx 는 계속 로그 알림(`log-slack-alerts.yml` 의 `http error status=5`)이 건별로 Slack 에 보낸다.

## 토큰 교체

토큰을 새로 받으면 옛 토큰으로 이미 붙은 커넥터는 계속 돌지만 **새 연결은 못 연다**(Cloudflare 문서
"Tunnel tokens"). 새 태스크는 기동할 때 SSM 값을 읽으므로, **토큰을 새로 받은 순간부터 SSM 을 바꿀
때까지**가 위험 구간이다 — 그 사이에 태스크가 교체되면(헬스체크 실패·Fargate 정비·AZ 재배치) 새 태스크의
cloudflared 가 옛 토큰으로 못 붙는다. 그 태스크로는 터널 요청이 하나도 안 들어오고(사용자는 Cloudflare
530/1033), cloudflared 가 재시도 끝에 종료하면 `essential: true` 라 태스크째 죽어 교체가 반복된다.
배포 중이 아니면 서킷브레이커도 없다(ALB 가 남아 있는 동안엔 태스크가 죽을 때 ALB 경로도 같이 끊긴다).

**1·2 를 한자리에서 연달아(1분 안에) 한다.** 미리 두 창을 열어 두고 시작한다.

1. 대시보드에서 터널 토큰을 새로 받는다(Refresh token). 지금 태스크는 계속 트래픽을 받는다.
2. **곧바로** `AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws secret init --name CLOUDFLARE_TUNNEL_TOKEN --overwrite`
   로 새 값을 넣는다. 이 순간부터 새로 뜨는 태스크는 새 토큰을 쓴다.
3. `gh workflow run deploy-server.yml --ref main` — 옛 토큰으로 붙어 있던 커넥터까지 새 토큰으로 바꾼다.
   api-watch 로 `bad=0` 을 본다.

증상(1·2 사이가 벌어졌거나 SSM 에 틀린 값이 들어갔을 때) — 로그 스트림 `copilot/cloudflared/<task-id>` 에:
- `Unauthorized` 가 반복 → 폐기된 옛 토큰(1 다음에 2 를 안 했다). 터널 대시보드에 커넥터가 0개로 보인다.
- `Provided Tunnel token is not valid.` 후 종료 → 형식이 깨진 값(복사 실수). 태스크가 뜨자마자 멈추기를 반복한다.

처치: 대시보드의 **현재** 토큰을 다시 복사해 2 를 실행한 뒤 3 으로 태스크를 새로 띄운다
(폐기된 토큰으로 재시도 중인 태스크는 스스로 새 값을 읽지 않는다).

## Cloudflare 앞단 제약 (터널 전과 같다)

- 응답 대기 상한 100초(무료 플랜) — 넘으면 524. 공개 분석 상한은 30×2+12=72초.
- 요청 본문 100MB(무료 플랜) — 앱 업로드 상한 25MB 보다 크다.
