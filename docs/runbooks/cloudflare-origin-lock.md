# api.wearless.kr Cloudflare 프록시 + ALB 원본 잠금

2026-09-15 점검에서 `api.wearless.kr` 가 Cloudflare **DNS 전용**(회색 구름)이라 ALB IP 가
그대로 노출되고, ALB 보안그룹이 80·443 을 `0.0.0.0/0` 에 열어두고 있었다. AWS WAF·GuardDuty
도 없다. 이 런북은 트래픽을 Cloudflare 엣지로만 받게 바꾼다.

2026-09-26 에 이유가 하나 더 생겼다 — **첫 연결 지연**. 서버 처리는 p50 9~20ms 인데, 한국에서
us-east-1 ALB 로 TCP+TLS 를 새로 여는 데 왕복 3~4번(≈1초)이 든다. 프록시를 켜면 연결은 서울
엣지에서 끝나고 엣지↔ALB 는 Cloudflare 가 열어 둔 연결을 재사용한다.

두 단계로 나눈다.

| 단계 | 내용 | 언제 |
|---|---|---|
| 1 | 앱(`client_ip.py`) 배포 → Cloudflare 프록시 켜기 | 지금 |
| 2 | ALB 보안그룹을 Cloudflare 대역으로 잠금(env deploy) | 발표(9/30) 뒤 |

1단계만으로 속도는 얻는다. 원본 IP 가 여전히 열려 있어 방어막으로는 2단계까지 가야 완성이다.

## 끝난 상태 (2단계까지)

- `api.wearless.kr` = Cloudflare 프록시(주황 구름). 응답에 `server: cloudflare`, `cf-ray`.
- ALB 보안그룹(80·443) = Cloudflare IPv4 15개 대역만. 정본은
  `copilot/environments/use1/manifest.yml` 의 `http.public.ingress.source_ips`(2단계에서 추가).
- 앱의 IP 기반 한도(공개 분석 · 정산 시뮬레이션)는 Cloudflare 엣지에서 온 요청일 때만
  `CF-Connecting-IP` 를 방문자로 쓴다(`server/app/client_ip.py`). 이게 없으면 같은 엣지를 타는
  방문자 전원이 시간당 10회 공개 분석 한도를 나눠 쓴다. 카카오 로그인 시작(시간당 30회)과
  정산 시뮬레이션·후원 VC 도 같은 함수를 쓴다.

## 순서가 전부다

**앱 배포 → Cloudflare 프록시 켜기 → 대기 → SG 잠금.** 되돌리기는 정확히 반대로.

SG 를 먼저 잠그면 아직 ALB 로 직접 오는 방문자(=프록시 켜기 전의 전원)가 막힌다.
프록시를 먼저 끄면 SG 가 모든 방문자를 막는다.

### 1. 앱 배포 (PR 머지)

`server/app/client_ip.py` 가 먼저 나가야 한다. 이 코드는 프록시 전에도 안전하다 — 접속자가
Cloudflare 대역이 아니면 `CF-Connecting-IP` 를 무시하고 기존처럼 XFF 마지막 값을 쓴다.
CI(deploy-server.yml)는 `svc deploy` 만 하고 **env deploy 는 안 한다** → 머지만으로 SG 는 안 잠긴다.

### 2. Cloudflare 대시보드 (wearless.kr zone)

1. **Rules → Configuration Rules → Create**
   - 조건: `Hostname equals api.wearless.kr`
   - SSL: **Full (strict)** — ALB 에 api.wearless.kr ACM 인증서가 있다. Flexible 이면 Cloudflare 가
     ALB 에 HTTP(80)로 붙고, ALB 80 리스너는 HTTPS 로 리다이렉트 → 무한 리다이렉트.
     zone 전체 SSL 모드를 바꾸지 않으려고 호스트 규칙으로 건다.
   - Browser Integrity Check: **Off** — 토스 웹훅(`/v1/webhooks/toss/<secret>`)·CI curl 같은
     비브라우저 클라이언트가 막히지 않게.
2. **Security → Bots → Bot Fight Mode: Off 확인.** 무료 플랜에선 경로별로 건너뛸 수 없어서 켜져
   있으면 토스 웹훅이 챌린지에 걸린다.
3. **Caching → Cache Rules**: `api.wearless.kr` 에 걸리는 "Cache everything" 류 규칙이 없는지 확인
   (images.wearless.kr 전용이어야 한다).
4. **DNS → `api` CNAME → Proxy status: Proxied** 로 변경.

### 3. 확인 (프록시 켠 직후)

```bash
curl -sI https://api.wearless.kr/healthz | grep -iE 'HTTP/|server|cf-ray'
curl -s https://api.wearless.kr/readyz
curl -sI -X OPTIONS https://api.wearless.kr/v1/projects -H 'Origin: https://ai.wearless.kr' -H 'Access-Control-Request-Method: GET' | grep -i access-control-allow-origin
```

- `server: cloudflare` + `cf-ray` 가 보여야 한다. 525/526 이면 SSL 모드 문제, 520/522 면 원본 연결 문제.
- 브라우저로 ai.wearless.kr · facemarket.wearless.kr 로그인·잡 폴링·이미지 로딩 확인.

첫 연결 시간 비교(프록시 전 ≈1.0초 — 2026-09-26 한국에서 측정):

```bash
curl -so /dev/null -w 'dns %{time_namelookup} tcp %{time_connect} tls %{time_appconnect} ttfb %{time_starttransfer} total %{time_total}\n' https://api.wearless.kr/healthz
```

로그의 방문자 IP(`http request` 줄 앞 uvicorn 접근 로그)가 Cloudflare 대역(`172.64.x`·`104.2x.x` 등)
으로 바뀌는 건 정상이다 — uvicorn 은 XFF 마지막 값(=엣지)을 찍는다. 한도 계산은
`client_ip()` 가 방문자 IP 로 한다.

### 4. 대기 → SG 잠금 (2단계 — 발표 뒤)

이 단계의 매니페스트 변경은 아직 레포에 없다(PR #315 에 남아 있음). `test_client_ip.py` 의
SG 대조 검사는 매니페스트에 `ingress` 가 생기는 순간부터 돈다.

DNS TTL 이 300초라 캐시된 ALB 주소로 오는 클라이언트가 잠깐 남는다. **최소 30분** 기다린 뒤:

```bash
cd <repo>
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws env deploy --name use1 --diff
# diff 가 PublicHTTP(S)LoadBalancerSecurityGroup 인그레스 변경뿐인지 보고
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws env deploy --name use1 --diff-yes
```

`AWS_REGION=ap-northeast-2` 는 앱 메타데이터가 서울 SSM 에 있어서다(env 자체는 us-east-1).

확인:

```bash
curl -sk --max-time 10 -o /dev/null -w '%{http_code}\n' https://wearle-Publi-n7eZ27fYallL-825254557.us-east-1.elb.amazonaws.com/   # 000 (타임아웃) 이어야 한다
curl -s -o /dev/null -w '%{http_code}\n' https://api.wearless.kr/healthz   # 200
```

## 되돌리기

1. **SG 먼저 연다.** 급하면 CLI 로 즉시(다음 env deploy 가 매니페스트대로 되돌리니 매니페스트도 같이 뺄 것):
   ```bash
   AWS_PROFILE=wearless AWS_REGION=us-east-1 aws ec2 authorize-security-group-ingress --group-id sg-0ca71e6ef5ed6548a --protocol tcp --port 443 --cidr 0.0.0.0/0
   AWS_PROFILE=wearless AWS_REGION=us-east-1 aws ec2 authorize-security-group-ingress --group-id sg-00c2a16bc00450fd7 --protocol tcp --port 80 --cidr 0.0.0.0/0
   ```
2. 그다음 Cloudflare DNS 를 DNS only 로.

## 유지보수

Cloudflare 대역은 드물게 바뀐다. 바뀌면 `server/app/client_ip.py` 와 env 매니페스트를 **같이**
고친다 — `tests/test_client_ip.py` 가 두 목록이 어긋나면 실패한다. SG 에만 새 대역이 있으면
그 엣지를 타는 방문자가 엣지 IP 하나로 묶여 429 를 받는다.

## Cloudflare 앞단에서 달라지는 제약

- 원본 응답 대기 **100초**(무료/Pro). 넘으면 Cloudflare 가 524 를 준다. 2026-09-12~26 일별
  최대 TargetResponseTime 은 대부분 25초 이하, 넘은 건 두 번이다: 9/14 45초, 9/25 **109초** —
  관리자 콘솔 등록 사진 1장(`GET /v1/facemarket/admin/enrollments/{id}/images/{kind}`)이
  R2 에서 늦었다. 같은 순간 사진 20장은 1~5초였고 서버는 멈추지 않았다(healthz 정상). 프록시
  뒤라면 그 사진만 524 로 끊기고 새로고침하면 된다. 공개 분석은 provider 상한 30초 + 폴백 30초.
- 요청 본문 **100MB**. 앱 상한은 업로드·신청 사진 25MB · 얼굴 40MB · 공개 분석·추적 60MB ·
  신분증 12MB. 배포본 PNG(최대 200MB)는 R2 presigned PUT 이라 API 를 안 지난다.
- SSE(`/v1/jobs/:id/events`)는 이벤트가 없으면 바이트를 안 보낸다. 프론트는 `?poll=1` 폴링이라
  현재 영향 없음. 스트림으로 되돌리면 heartbeat 부터 넣을 것.
