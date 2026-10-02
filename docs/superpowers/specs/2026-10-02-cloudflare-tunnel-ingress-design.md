# api 인그레스 ALB → Cloudflare Tunnel — 설계 (2026-10-02)

> 정본. 오너 확정 2026-10-02: **사이드카 방식** · **시험 주소 없이 `api.wearless.kr` 직행** ·
> ALB 지표 알람은 **외부 무료 업타임 서비스**로 대체 · opendid 는 그대로 둔다.
> ARM64 전환(별건, 같은 비용 조사에서 나온 3번 레버)은 이 문서 범위 밖이다.

## 0. 오너 확정 사항

| # | 결정 | 고른 것 | 버린 것 |
|---|---|---|---|
| 1 | 터널을 어디서 돌리나 | api 태스크의 **사이드카** | 별도 cloudflared 서비스(월 +$9~13 — 절감액 절반이 날아감) |
| 2 | 전환 순서 | PR A 배포 직후 **`api.wearless.kr` 바로 전환** → 3일 관찰 | 시험 주소 `api-tunnel.wearless.kr` 사전 점검(반나절 더 걸림) |
| 3 | ALB 지표 알람 대체 | **외부 무료 업타임 서비스**(Slack 연동) + Cloudflare Tunnel Health Alert | AWS 프로브 람다(IaC·월 ~$0.2), 대체 없이 로그 알림만 |

결정 2 의 대가: 배포 중 502 같은 문제를 실사용자 트래픽에서 처음 본다. 완화 = 저트래픽 시간대 전환,
전환·첫 롤링 배포 동안 1초 간격 폴링, ALB 를 3일간 살려 둬서 **DNS 한 번 바꾸면 수 초 안에 원복**.

---

## 1. 왜 하는가 — 실측

신규 Free Tier 크레딧 $200 이 2026-09 에 바닥났다(7월 $42.85 + 8월 $108.00 + 9월 $48.24 소진,
`aws freetier get-account-plan-state` → `accountPlanRemainingCredits 0.0`). 9월 인보이스 $20.86(₩28,622)
은 부분 청구였고, **10월부터 전액 청구 ~$77~84/월(VAT 포함)** 이다.

10월 예상(VAT 제외, 31일 정가):

| 항목 | 월 | 이 설계로 |
|---|---|---|
| api Fargate 1 vCPU/4GB 상시 1대 | ~$43 | 그대로 |
| **ALB** (LoadBalancerUsage) | **~$16.7** | **0** |
| 공인 IPv4 (평균 3.08개) | ~$11.5 | **ALB 몫 2개(~$7.4) 제거** → ~$4 |
| Spot 3종 · KMS·ECR·R53 | ~$4.5 | 그대로 |

**절감 ~$24/월.** Cloudflare Tunnel 은 "Available on all plans"(무료 포함), 트래픽 과금 없음.
한도는 계정당 터널 1,000개 · 터널당 커넥터 25개로 문제없다.

### 채택하지 않은 대안

- **ALB 유지 + Savings Plan**: ALB·IPv4 는 Savings Plan 대상이 아니다.
- **ALB 를 `dualstack-without-public-ipv4` 로**: IPv4 $7.4 만 줄고 ALB $16.7 은 그대로, VPC IPv6 재설계 필요.
- **api 태스크 공인 IP 제거**: 외부 호출(OpenAI·Supabase·R2) egress 가 NAT($32+/월)를 요구해 오히려 비싸다.

---

## 2. 목표 · 비목표

**목표**
- ALB 와 그 공인 IPv4 2개를 없앤다(월 ~$24).
- 사용자에게 보이는 변화 0 — 도메인 `api.wearless.kr`, CORS, 토스 웹훅 경로, 카카오 로그인, 업로드, 요청 한도 판정이 그대로.
- 단계마다 되돌릴 수 있다. 서비스 배포는 CI, 환경 배포(`env deploy`)·시크릿·Cloudflare 대시보드는 오너.

**비목표**
- 실트래픽 응답 중앙값(p50) 알람의 동등 대체(§7 에 사각지대로 남긴다).
- ARM64 전환, opendid, Argo(유료), 리전 이전.
- 보안그룹 잠금 — PR #315 로 2026-10-02 이미 적용됐다(ALB SG 80·443 = Cloudflare 15대역). ALB 를 걷어내면
  인바운드 포트가 0개라 잠금은 ALB 와 함께 사라진다. 되돌리기(ALB 복구) 중에도 Cloudflare 경유라 잠금과 충돌하지 않는다.

---

## 3. 최종 구조

```
브라우저 ─HTTPS→ Cloudflare 엣지 ═(터널, 태스크가 바깥으로 연 연결)═ cloudflared ─http→ localhost:8080 api
                                                                   └──── 같은 Fargate 태스크 ────┘
```

- 인바운드 포트 0개. ALB·HTTPS 리스너·ALB 보안그룹·Elastic IP 2개 소멸.
- api 태스크 공인 IP 1개는 남는다(egress 용).
- 터널은 Cloudflare 대시보드에서 관리하는 원격 설정(remotely-managed) 1개. 라우트 `api.wearless.kr → http://localhost:8080`.

---

## 4. 설계 전제 — 확인한 사실

| 사실 | 근거 | 설계에 주는 영향 |
|---|---|---|
| api 를 `Backend Service` 로 바꾸면 **그 배포 안에서** 환경 스택의 `ALBWorkloads` 에서 api 가 빠지고, `CreateALB` 조건이 거짓이 되어 ALB·리스너·export 가 삭제된다 | Copilot v1.34.1 `cf-custom-resources/lib/env-controller.js`(parametersToRemove), `templates/environment/cf.yml` `CreateALB: !Not [!Equals [!Ref ALBWorkloads, ""]]` | ALB 철거에 별도 `env deploy` 가 필요 없다. 대신 **`PublicLoadBalancerFullName` export 를 import 하는 스택이 하나라도 남아 있으면 그 환경 스택 업데이트가 실패 → api 배포 롤백**. 알람 정리를 먼저 끝내야 한다(§5 3단계) |
| 서비스 스택 안에서 애드온 중첩 스택 삭제는 업데이트 정리 단계(맨 끝)에 일어난다 | CloudFormation 업데이트 순서 | api-alarms 애드온 삭제와 서비스 타입 전환을 **같은 배포에 넣으면 안 된다**(env-controller 가 먼저 돌아 export 가 아직 사용 중) |
| ECS `dependsOn` 은 기동 순서를 정하고 **종료는 그 역순** | AWS ECS 태스크 정의 파라미터 문서: "When a dependency is defined for container startup, for container shutdown it is reversed." | cloudflared 가 `api: healthy` 에 의존하면 배포 때 터널이 api 보다 **나중에 붙고 먼저 빠진다** |
| Cloudflare 는 `X-Forwarded-For` 가 있으면 접속자 IP 를 **뒤에 덧붙이고**, 없으면 `CF-Connecting-IP` 와 같은 값으로 만든다 | Cloudflare HTTP 헤더 문서 | XFF 마지막 값은 늘 Cloudflare 가 쓴 값 → 꾸민 헤더로 한도 판정을 우회할 수 없다 |
| uvicorn 은 `forwarded_allow_ips` 기본값 `127.0.0.1` 에서 온 `X-Forwarded-For`/`-Proto` 를 믿는다 | uvicorn 기본 설정(`FORWARDED_ALLOW_IPS` 미설정) | 터널 경로에선 `request.client` 가 방문자 IP, scheme 이 `https` 로 바뀐다. 코드가 `request.url` 은 `.path` 만 쓰고 카카오 `redirect_uri` 는 서버 화이트리스트라 영향 없음 |
| Cloudflare 무료 플랜 응답 대기 상한 100초는 **이미 적용 중**(api 가 9/27 부터 Proxied) | `cloudflare-origin-lock` 런북 | ALB 유휴 180초 패치(`cfn.patches.yml`)는 이미 의미가 없다. 분석 상한 30×2+12=72초 < 100 |
| cloudflared 이미지는 distroless(셸·curl 없음), `ENTRYPOINT ["cloudflared","--no-autoupdate"]`, 토큰은 환경변수 `TUNNEL_TOKEN`, 종료 유예 플래그 `--grace-period` | cloudflared 2026.9.3 `Dockerfile`, `cmd/cloudflared/tunnel/subcommands.go`, `flags/flags.go` | 사이드카에 컨테이너 헬스체크를 걸 수 없다 → 터널 끊김은 Tunnel Health Alert·업타임 모니터가 본다 |
| 프론트는 SSE(`/v1/jobs/{id}/events`)를 쓰지 않고 `?poll=1` JSON 폴링을 쓴다 | `src/lib/api/httpAdapter.js:617` | 장시간 스트림 없음 — 터널 재접속 영향 작음 |

---

## 5. 단계

| 단계 | 내용 | 누가 | 끝나는 조건 | 되돌리기 |
|---|---|---|---|---|
| 0. 준비 | 터널 생성, 토큰을 SSM 에 저장 | 오너 | §6.4 확인 명령이 태그까지 출력 | 터널 삭제 |
| 1. PR A | 사이드카 + api 컨테이너 헬스체크 + `client_ip` 보강 + 테스트 + 런북. **ALB 그대로** | 나(PR) → CI | 배포 후 Cloudflare 대시보드 터널 HEALTHY, cloudflared 로그 `Registered tunnel connection` 4회 | PR revert |
| 2. DNS 전환 | `api.wearless.kr` 을 터널로. 같은 날 업타임 모니터·Tunnel Health Alert 설정. **3일 관찰** | 오너(대시보드), 나(폴링·검증) | §8 전환 판정 통과, 3일간 장애 0 | CNAME 을 ALB 로(수 초) |
| 3. PR B1 | ALB 지표 알람 정리 | 나(PR) → CI + 오너 `env deploy` ① | 계정에 `PublicLoadBalancerFullName` 을 import 하는 스택 0개(§6.5 확인 명령) | PR revert + `env deploy` |
| 4. PR B2 | api `type: Backend Service`, `http` 블록 삭제 → ALB 자동 철거 | 나(PR) → CI | ALB 0개 · Elastic IP 0개 · CI `/readyz` 게이트 green | PR revert → ALB 가 **새 DNS 이름**으로 재생성 → CNAME 교체(~10분) |
| 5. 마무리 | 죽은 설정(인증서·#315 SG 잠금·유휴 180초 패치) 정리, 메모리·런북 | 나(PR) + 오너 `env deploy` ② | §9 완료 조건 | PR revert + `env deploy` |

3 → 4 를 나누는 이유는 §4 첫째·둘째 줄. 마무리를 철거 며칠 뒤로 미루는 이유: 환경 매니페스트의 ACM 인증서가
남아 있어야 4단계 revert 때 ALB 가 HTTPS 리스너까지 그대로 살아난다.

---

## 6. 컴포넌트 상세

### 6.1 `copilot/api/manifest.yml` (PR A)

```yaml
image:
  # (build·port 기존 그대로)
  # 컨테이너 헬스체크 — cloudflared 의 depends_on: healthy 가 이걸 기다린다. ALB 를 걷어낸 뒤에는
  # 이것이 유일한 헬스 판정이다. 기동 유예 60s = 지금 ALB 서비스의 HealthCheckGracePeriod 와 같다.
  healthcheck:
    command: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=4)\" || exit 1"]
    interval: 10s
    retries: 3
    timeout: 5s
    start_period: 60s

sidecars:
  cloudflared:
    # 2026.9.3 멀티아키 인덱스 digest(amd64·arm64 둘 다 — ARM64 전환 때 그대로 쓴다).
    image: cloudflare/cloudflared@sha256:072c067d25ccbe61d46e18f0d0723255f2bb5304f7317caa95b27031520ff92c
    essential: true            # 터널이 죽으면 그 태스크는 아무도 못 부른다 → 태스크째 교체
    command: ["tunnel", "--grace-period", "55s", "run"]
    depends_on:
      api: healthy             # 기동: api healthy 뒤에 붙는다 / 종료: 역순이라 api 보다 먼저 빠진다
    secrets:
      TUNNEL_TOKEN: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/CLOUDFLARE_TUNNEL_TOKEN
```

- 종료 시간: `--grace-period 55s` + `taskdef_overrides` 의 `ContainerDefinitions[1].StopTimeout: 60`(사이드카가
  하나뿐이라 인덱스 1 — 렌더 실측). 공개 분석(동기)이 실측 최대 53.5초라 그걸 담는다. 30s 는 ECS **기본값**일 뿐
  한도가 아니다(PR A 리뷰 지적으로 25s → 55s). 종료는 cloudflared(≤60s) → api(≤90s) 순이고 각각 Fargate
  컨테이너 상한 120s 안. 지금 ALB 드레인(`deregistration_delay: 10s`)보다 훨씬 덜 끊긴다.
- 사이드카에 cpu/memory 를 따로 주지 않는다 — 태스크 1 vCPU/4GB 를 같이 쓴다(추가 비용 0).
- 이미지를 Docker Hub 에서 받는다. 익명 풀 한도(2025-04 부터 IPv4 당 시간 10회)는 태스크마다 공인 IP 가 달라 문제없다.
- `test_api_stop_timeout_covers_dispatcher_drain_and_shutdown_finalize` 가 `ContainerDefinitions[0]`
  (= api) 를 보는 가정은 사이드카를 추가해도 유지돼야 한다 — 렌더 결과로 확인(계획 단계).

### 6.2 `server/app/client_ip.py` (PR A)

현재: XFF 마지막 값이 Cloudflare 대역일 때만 `CF-Connecting-IP` 를 믿는다.
추가: XFF 마지막 값이 **loopback(127.0.0.0/8, ::1)** 일 때도 믿는다.

- cloudflared 가 XFF 에 자기 주소를 덧붙이는 구현이든 아니든 맞게 동작하게 하려는 보강이다.
  덧붙이지 않으면 XFF 마지막 값 = Cloudflare 가 쓴 방문자 IP 라 지금 코드로도 맞다.
- 안전성: loopback 에서 들어오는 건 같은 태스크의 cloudflared(그리고 Service Connect 프록시)뿐이다.
  내부 호출자는 `CF-Connecting-IP` 를 보내지 않으므로 기존 폴백(XFF 마지막 값)으로 떨어진다.
- 모듈 독스트링을 터널 경로까지 포함하도록 고친다.

### 6.3 테스트 (PR A)

- `server/tests/test_client_ip.py` 에 터널 헤더 형태 3가지:
  ① XFF=`방문자` (cloudflared 무첨가) ② XFF=`방문자, 127.0.0.1` (첨가) ③ XFF=`꾸민값, 방문자` + CF-Connecting-IP=방문자.
- `server/tests/test_deploy_manifest_qc_flags.py` 에 가드:
  사이드카 `cloudflared` 존재 · `depends_on: {api: healthy}` · `essential: true` · 토큰이 SSM 경로 ·
  이미지가 `@sha256:` digest 고정(`latest`·태그 단독 금지) · `image.healthcheck` 존재(`/healthz`).

### 6.4 준비 — 오너 작업 (0단계)

1. Cloudflare Zero Trust → Networks → Tunnels → Create tunnel → Cloudflared, 이름 `wearless-api-use1`.
   토큰만 복사. 이 단계에서는 공개 라우트를 만들지 않는다.
2. `AWS_REGION=ap-northeast-2 copilot-aws secret init --name CLOUDFLARE_TUNNEL_TOKEN` — 대화형으로
   `use1` 값만 입력(토큰이 셸 히스토리에 남지 않게 `--values` 를 쓰지 않는다).
   raw `aws ssm put-parameter` 금지 — copilot 태그가 없으면 태스크가 못 읽고 배포가 롤백된다(2026-07-17 사고).
3. 확인:
   `aws ssm list-tags-for-resource --region us-east-1 --resource-type Parameter --resource-id /copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN`
   → `copilot-application=wearless`, `copilot-environment=use1` 두 태그.

### 6.5 알람 정리 (PR B1)

| 대상 | 처리 |
|---|---|
| `copilot/api/addons/api-alarms.yml` (no-healthy-target) | 삭제 |
| `copilot/api/addons/addons.parameters.yml` (`TargetGroupFullName` 만 있음) | 삭제 — Backend Service 에는 `TargetGroup` 이 없어 남기면 B2 배포가 깨진다 |
| `alarm-slack.yml` `Alarm5xx` · `AlarmLatency` · `AlarmElb5xx` | 삭제 |
| `alarm-slack.yml` SNS 토픽 · Slack 람다 · `AlarmTopicArn` export · 예산 알림 2개 | **유지** — ALB 알람이 빠져도 예산 알림(`MonthlyNetBudget`·`MonthlyGrossBudget`, #451 · 2026-10-02)이 이 토픽을 쓴다(sam·opendid 는 `sam-autoscale.yml` 의 별도 `SamAlertTopic`). 삭제 범위는 ALB 알람 3개 리소스까지만 |
| `test_api_addon_parameters_are_declared_by_an_addon_template` | 파일 부재를 허용하도록 고친다 |
| `docs/runbooks/cloudwatch-alarms.md` | ALB 알람 절을 업타임 모니터·Tunnel Health Alert 로 교체 |

확인 명령(B2 착수 조건): `aws cloudformation list-imports --region us-east-1 --export-name wearless-use1-PublicLoadBalancerFullName`
→ 비어 있음(`ValidationError: ... is not imported by any stack` 도 통과로 본다).

### 6.6 서비스 타입 전환 (PR B2)

- `type: Backend Service`, `http:` 블록 전체 삭제(헬스체크는 6.1 에서 이미 컨테이너로 옮김).
- 머지 전: `AWS_REGION=ap-northeast-2 copilot-aws svc package --name api --env use1 --diff`(읽기 전용)로
  ECS `Service` 가 **교체(Replacement)되지 않고** 지워지는 것이 `TargetGroup`·리스너 규칙·`EnvControllerAction`
  파라미터 변경뿐인지 확인한다. 교체가 보이면 멈추고 설계로 돌아온다(Service Connect 이름 충돌 함정 — `aws-cost-audit` 메모).
- 2026-10-02 로컬 렌더 실측(`svc package`, 읽기 전용): 지워지는 리소스는 `TargetGroup`·`HTTPListenerRuleWithDomain`·
  `HTTPRuleWithDomainPriorityAction`·`HTTPSListenerRule`·`HTTPSRulePriorityAction`·`RulePriorityFunction`·
  `RulePriorityFunctionRole` 7개, 추가 0개. `Service` 는 같은 논리 ID 이고 바뀌는 속성은 `HealthCheckGracePeriodSeconds`·
  `LoadBalancers`·`ServiceRegistries`(값은 동일) — CloudFormation 문서상 셋 다 "No interruption", `ServiceName` 불변.
  `EnvControllerAction` 파라미터 `[ALBWorkloads, Aliases] → []`.
- `server/tests/test_analyze.py` 의 `alb_idle_timeout = 180` 을 Cloudflare 100초 상한 기준으로 바꾼다.

### 6.7 마무리 (5단계)

- 환경 매니페스트 `http:` 블록 삭제(`certificates` + #315 의 `ingress.source_ips`), `cfn.patches.yml` 의 유휴 180초
  패치 삭제 → `env deploy` ②.
- `test_alb_security_group_admits_exactly_the_ranges_trusted_as_cloudflare` 삭제(검사 대상 SG 가 없어짐).
- `scripts/smoke-api.sh` — ALB DNS `--resolve` 방식에서 기본 URL 인자 방식으로.
- `docs/runbooks/cloudflare-origin-lock.md` — 2단계(SG 잠금, #315)가 ALB 와 함께 사라졌다고 상태 블록을 단다.
- ACM 인증서 `8120f0ff` 는 무료라 남겨도 되지만, 남길 이유가 사라지면 삭제.
- 1주 뒤 Cost Explorer 로 `LoadBalancerUsage` 0, `PublicIPv4:InUseAddress` 일 ~24시간(=1개) 확인.

### 6.8 런북 `docs/runbooks/cloudflare-tunnel.md` (PR A 에 포함)

0단계 절차, DNS 전환·원복 절차(원복용 ALB 주소 `wearle-Publi-n7eZ27fYallL-825254557.us-east-1.elb.amazonaws.com`
명시), 업타임 모니터·Tunnel Health Alert 설정, 토큰 교체 절차, 4단계 revert 시 새 ALB 주소로 CNAME 교체 절차.

---

## 7. 관측 — 남는 것 · 대체 · 사각지대

| 신호 | 지금 | 이후 |
|---|---|---|
| 앱 5xx | ALB `target-5xx` + 로그 `http error status=5` → Slack | **로그 알림만**(이미 건별로 즉시 온다) |
| 기동 실패 | 로그 `Application startup failed` → Slack | 그대로 |
| 정상 서버 0대 · 502/504 | ALB 지표 | **외부 업타임 모니터**(`/healthz`, 5분 이하, Slack) — DNS→Cloudflare→터널→api 전 구간 |
| 터널 끊김 | (없음) | **Cloudflare Tunnel Health Alert**(Zero Trust 전 플랜 포함, 메일) |
| 이벤트루프 동결(8/26 유형) | ALB 헬스 2회 실패 → 타깃 제외 | 컨테이너 헬스체크 3회 실패 → 태스크 교체 + 업타임 모니터 |
| 서버 전체 느려짐(실트래픽 p50) | ALB `slow-response` | **사각지대** — 업타임 모니터의 `/healthz` 응답시간 그래프로만 본다. 근본 대체는 앱이 지연 지표를 직접 내는 일(범위 밖) |

업타임 서비스 고르는 기준(설정 때 확정): 무료로 Slack 알림 · 확인 간격 5분 이하 · HTTPS + 응답 본문 키워드 확인.
레포 밖 설정이라 런북에 설정값을 남긴다.

---

## 8. 위험 · 완화

| 위험 | 완화 |
|---|---|
| DNS 전환 때 기존 CNAME 삭제 → 터널 라우트 추가 사이 공백 | 저트래픽 시간대. 런북에 현재 대시보드 기준 정확한 순서. 공백 목표 ≤1분. 여유가 생기면 Cloudflare API 토큰(터널·DNS 한정)으로 원자적 전환 가능 |
| 배포 중 502 (커넥터 교대) | dependsOn 역순 종료 + `--grace-period 55s`(StopTimeout 60). 전환 당일 롤링 배포 1회를 1초 폴링으로 실측 — 200 아닌 응답이 1건이라도 나오면 DNS 원복 후 원인 조사 |
| 방문자 IP 판정 오류 → 요청 한도(공개 분석 10/h, 카카오 30/h)가 엉뚱하게 묶임 | §6.2 loopback 보강 + 테스트 3종 + 전환 직후 로그에서 실제 방문자 IP 확인 |
| 토큰 시크릿 누락·태그 누락 → 배포 롤백 | 0단계 확인 명령을 PR A 머지 조건으로 |
| B2 배포에서 ECS 서비스 교체 → Service Connect 이름 충돌·롤백 | 머지 전 `svc package --diff` 로 교체 여부 확인 |
| 컨테이너 헬스체크가 ALB 보다 민감/둔감 | 10s×3회(ALB 10s×2회보다 약간 관대), 기동 유예 60s. 1~2단계 관찰에서 오탐 교체 여부 확인 |
| Docker Hub 장애로 cloudflared 이미지 못 받음 | 기존 태스크는 영향 없음, 새 태스크만 기동 실패 → 롤백. 반복되면 ECR pull-through cache(별건) |

---

## 9. 완료 조건

1. us-east-1 ALB 0개, Elastic IP 0개(`describe-load-balancers`, `describe-addresses`).
2. `https://api.wearless.kr` 응답에 `server: cloudflare`, CI `/readyz`·CORS 게이트 green.
3. 전환·첫 롤링 배포 동안 1초 폴링에서 200 이 아닌 응답 0건.
4. 로그의 방문자 IP 가 Cloudflare 엣지·127.0.0.1 이 아닌 실제 IP.
5. 업타임 모니터·Tunnel Health Alert 설정 완료, 런북에 기록.
6. 1주 뒤 Cost Explorer 로 ALB 시간 0, 공인 IPv4 ~1개 확인. 메모리 `aws-cost-audit` 갱신.
