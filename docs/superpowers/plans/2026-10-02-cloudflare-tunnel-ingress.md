# api 인그레스 ALB → Cloudflare Tunnel 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** api 앞단을 ALB 에서 api 태스크 안 `cloudflared` 사이드카(Cloudflare Tunnel)로 바꾸고 ALB·공인 IPv4 2개를 걷어내 월 ~$24 를 줄인다.

**Architecture:** 4개의 PR(A 사이드카 → B1 ALB 알람 삭제 → B2 Backend Service 전환 → C 정리)과 그 사이 오너 작업(터널·시크릿·DNS 전환·`env deploy`)으로 나눈다. ALB 철거는 B2 배포 안에서 Copilot env-controller 가 자동으로 하므로, `PublicLoadBalancerFullName` export 를 쓰는 알람을 B1 에서 **먼저** 지운다. 각 PR 은 직전 PR 이 main 에 머지된 뒤 **새 `origin/main` 에서** 브랜치를 판다(스택 PR 금지 — 메모 `stacked-pr-merge-trap`).

**Tech Stack:** AWS Copilot v1.34.1(`copilot-aws`), ECS Fargate, CloudFormation 애드온, Cloudflare Tunnel(cloudflared 2026.9.3), FastAPI/Starlette, pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md`

## Global Constraints

- 작업 위치: 워크트리 `/Users/nojeong-un/devs/wearless_studio/.claude/worktrees/cf-tunnel`. 메인 트리(`/Users/nojeong-un/devs/wearless_studio`)에서 커밋하지 않는다.
- 테스트 실행: `cd server && $PY -m pytest …`, `PY=/Users/nojeong-un/devs/wearless_studio/server/.venv/bin/python`(워크트리 `server/` 에서 돌리면 워크트리의 `app` 을 import 한다 — 확인함).
- AWS: `export AWS_PROFILE=wearless`. 리소스 리전 `us-east-1`, Copilot 앱 메타데이터 리전 `ap-northeast-2` → copilot 명령은 항상 `AWS_REGION=ap-northeast-2 copilot-aws …`. 환경 이름 `use1`. 바이너리는 `copilot-aws`(`copilot` 은 GitHub Copilot CLI).
- 서비스 배포는 CI(`deploy-server`)로만. `env deploy`·`secret init`·Cloudflare 대시보드·PR 머지·`gh workflow run`·PR 닫기는 **오너가 하거나 오너 승인 후**.
- cloudflared 이미지: `cloudflare/cloudflared@sha256:072c067d25ccbe61d46e18f0d0723255f2bb5304f7317caa95b27031520ff92c`(2026.9.3 멀티아키 인덱스).
- 시크릿: SSM `/copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN` → 사이드카 환경변수 `TUNNEL_TOKEN`. 터널 이름 `wearless-api-use1`, 라우트 `api.wearless.kr → http://localhost:8080`.
- 사이드카 명령 `["tunnel", "--grace-period", "55s", "run"]` + `taskdef_overrides` `ContainerDefinitions[1].StopTimeout: 60`, `depends_on: {api: healthy}`, `essential: true` (PR A 리뷰로 25s → 55s — 원장 Final 참고).
- api 컨테이너 헬스체크: `localhost:8080/healthz`, interval 10s · retries 3 · timeout 5s · start_period 60s.
- **알람 삭제(B1)와 서비스 타입 전환(B2)을 같은 배포에 넣지 않는다**(스펙 §4).
- 원복용 ALB 주소: `wearle-Publi-n7eZ27fYallL-825254557.us-east-1.elb.amazonaws.com`.
- 커밋 메시지: 한국어 `type: …해요` 제목 + 끝줄 `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. PR 본문 끝줄 `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Review Focus

1. **IPv6 방문자가 터널로 들어올 때**(XFF 에 loopback 이 덧붙은 경우 포함) — 한도 판정이 IPv6 방문자 주소를 써야 한다. → Task 1 테스트 케이스.
2. **터널 경로인데 `CF-Connecting-IP` 가 없거나 깨진 요청** — 느슨해지지 않고 `127.0.0.1` 한 버킷으로 좁혀 묶여야 한다. → Task 1 테스트 케이스.
3. **롤링 배포 중 오래 걸리는 요청**(공개 분석 실측 최대 53.5초) — 커넥터 교대 때 55초까지는 끝까지 응답해야 하고, 200 아닌 응답이 사용자에게 가지 않아야 한다. → Task 2 `test_cloudflared_drains_the_longest_known_request_before_it_is_killed`, Task 5 실측.
4. **부팅이 헬스체크 예산보다 느린 경우** — 재시작 루프에 빠지지 않아야 한다. → Task 2 `start_period ≥ 60` 가드 + 실측 부팅 시간 확인 단계.
5. **누군가 `http:` 블록이나 ALB export import 를 되살리는 경우** — ALB 가 몰래 다시 생기거나(월 $24) ALB 없는 환경에서 env 배포가 깨진다. → Task 6·8 가드.

---

## 파일 구조

| 파일 | PR | 책임 |
|---|---|---|
| `server/app/client_ip.py` | A | 방문자 IP 판정 — Cloudflare 엣지 **또는 loopback(터널 사이드카)** 뒤면 `CF-Connecting-IP` |
| `server/tests/test_client_ip.py` | A, C | 터널 헤더 형태 고정(A), ALB SG 대조 테스트 삭제(C) |
| `copilot/api/manifest.yml` | A, B2 | 헬스체크·사이드카(A), `Backend Service` 전환(B2) |
| `server/tests/test_deploy_manifest_qc_flags.py` | A, B1, B2 | 사이드카 가드(A), export·TargetGroup 가드와 애드온 파라미터 테스트(B1), 타입 가드(B2) |
| `scripts/api-watch.sh` (신규) | A | 1초 폴링 — 전환·배포·원복 때 200 아닌 응답 세기 |
| `docs/runbooks/cloudflare-tunnel.md` (신규) | A, B1 | 터널 운영 런북(A), 알림 설정 기록(B1) |
| `copilot/api/addons/api-alarms.yml`, `addons.parameters.yml` | B1 | 삭제 |
| `copilot/environments/addons/alarm-slack.yml` | B1 | ALB 알람 3개 삭제, 토픽·램다·export·예산 알림(#451) 유지 |
| `docs/runbooks/cloudwatch-alarms.md` | B1 | 현재 상태 갱신 |
| `server/tests/test_analyze.py` | B2 | ALB 180s → Cloudflare 100s 기준 |
| `copilot/environments/use1/manifest.yml`, `copilot/environments/overrides/` | C | 인증서·SG 잠금(#315)·유휴 180초 패치 제거 |
| `scripts/smoke-api.sh`, `docs/runbooks/cloudflare-origin-lock.md` | C | ALB 전제 제거 |

---

## Part A — PR A: 사이드카 (ALB 그대로)

브랜치: 현재 `feat/cloudflare-tunnel-ingress`(스펙·계획 커밋 포함).

### Task 1: 방문자 IP — 터널 사이드카 경로 보강

**Files:**
- Modify: `server/app/client_ip.py` (독스트링, `_CLOUDFLARE_NETWORKS` 아래, `client_ip()` 의 대역 검사 한 줄)
- Test: `server/tests/test_client_ip.py`

**Interfaces:**
- Consumes: 없음
- Produces: `client_ip(request: Request) -> str` 시그니처 불변. 신규 모듈 상수 `_TRUSTED_PROXY_NETWORKS`(엣지 + loopback).

- [ ] **Step 1: 실패하는 테스트 작성** — `server/tests/test_client_ip.py` 의 `test_client_ip_falls_back_to_socket_peer_without_forwarded_header` 바로 아래에 추가:

```python
@pytest.mark.parametrize(("headers", "want"), [
    # cloudflared 가 XFF 를 그대로 넘기는 경우 — 마지막 값이 Cloudflare 가 쓴 방문자다.
    ({"x-forwarded-for": "203.0.113.7", "cf-connecting-ip": "203.0.113.7"}, "203.0.113.7"),
    # cloudflared 가 자기 주소(loopback)를 덧붙이는 경우 — 그때도 CF-Connecting-IP 를 쓴다.
    ({"x-forwarded-for": "203.0.113.7, 127.0.0.1", "cf-connecting-ip": "203.0.113.7"},
     "203.0.113.7"),
    ({"x-forwarded-for": "203.0.113.7, ::1", "cf-connecting-ip": "203.0.113.7"}, "203.0.113.7"),
    # 방문자가 XFF 를 꾸며 보내도 Cloudflare 가 진짜 IP 를 뒤에 덧붙인다.
    ({"x-forwarded-for": "192.0.2.99, 203.0.113.7", "cf-connecting-ip": "203.0.113.7"},
     "203.0.113.7"),
    ({"x-forwarded-for": "192.0.2.99, 203.0.113.7, 127.0.0.1", "cf-connecting-ip": "203.0.113.7"},
     "203.0.113.7"),
    # IPv6 방문자 + loopback 첨가.
    ({"x-forwarded-for": "2001:db8::1, 127.0.0.1", "cf-connecting-ip": "2001:db8::1"},
     "2001:db8::1"),
    # loopback 인데 CF-Connecting-IP 가 없거나 깨졌다 → loopback 한 버킷으로 좁혀 묶는다
    # (한도가 느슨해지는 쪽이 아니다).
    ({"x-forwarded-for": "127.0.0.1"}, "127.0.0.1"),
    ({"x-forwarded-for": "127.0.0.1", "cf-connecting-ip": "not-an-ip"}, "127.0.0.1"),
])
def test_client_ip_through_tunnel_sidecar(headers, want):
    """api 태스크 안 cloudflared → localhost:8080 경로(설계 2026-10-02). 접속자는 loopback 이다."""
    assert client_ip(_request(headers, peer="127.0.0.1")) == want
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && $PY -m pytest tests/test_client_ip.py -q`
Expected: FAIL 4건 — XFF 가 `203.0.113.7, 127.0.0.1` · `203.0.113.7, ::1` · `192.0.2.99, 203.0.113.7, 127.0.0.1` · `2001:db8::1, 127.0.0.1` 인 케이스가 방문자 대신 `'127.0.0.1'`/`'::1'` 을 돌려준다. 나머지 4건은 지금 코드로도 통과한다(고정용).

- [ ] **Step 3: 구현** — `server/app/client_ip.py`

독스트링 첫 단락 교체:

```python
"""요청의 실제 방문자 IP — ALB 직결, Cloudflare 프록시 → ALB, Cloudflare Tunnel 사이드카 경로 모두.

ALB 는 들어온 X-Forwarded-For 뒤에 자기가 관측한 접속자 IP 를 append 한다. 앞쪽 값은
클라이언트가 꾸밀 수 있지만 마지막 값은 ALB 가 쓴 것이라 믿을 수 있다.

api.wearless.kr 가 Cloudflare 프록시 뒤에 있으면 그 마지막 값은 방문자가 아니라
Cloudflare 엣지다. 그때만 Cloudflare 가 덮어쓴 CF-Connecting-IP 를 방문자로 쓴다.
엣지가 아닌 접속자가 보낸 CF-Connecting-IP 는 꾸민 값일 수 있으니 무시한다.

터널 경로(2026-10, api 태스크 안 cloudflared → localhost:8080)에서는 XFF 마지막 값이 Cloudflare 가
덧붙인 방문자 IP 이거나, cloudflared 가 자기 주소를 덧붙였다면 loopback 이다. loopback 에서 오는
요청은 같은 태스크의 cloudflared(와 Service Connect 프록시)뿐이라 그때도 CF-Connecting-IP 를 쓴다.
```

(나머지 독스트링 — 대역 출처·SG 문단 — 은 그대로 둔다. SG 문단은 Task 9 에서 지운다.)

`_CLOUDFLARE_NETWORKS = (...)` 정의 바로 아래에 추가:

```python
# 같은 태스크의 cloudflared 사이드카(터널)가 붙는 주소.
_LOOPBACK_NETWORKS = (ip_network("127.0.0.0/8"), ip_network("::1/128"))
_TRUSTED_PROXY_NETWORKS = _CLOUDFLARE_NETWORKS + _LOOPBACK_NETWORKS
```

`client_ip()` 안의 한 줄 교체:

```python
    if any(peer in network for network in _TRUSTED_PROXY_NETWORKS):
```

(기존: `if any(peer in network for network in _CLOUDFLARE_NETWORKS):`)

- [ ] **Step 4: 통과 확인**

Run: `cd server && $PY -m pytest tests/test_client_ip.py -q`
Expected: 전부 PASS. SG 대조 테스트도 실행돼 통과한다(2026-10-02 #315 머지로 매니페스트에 `ingress` 가 생겼다).

- [ ] **Step 5: 한도 판정 호출부 회귀**

Run: `cd server && $PY -m pytest -q -k "client_ip or rate or public_analyze or kakao or sponsorship" 2>&1 | tail -3`
Expected: 실패 0.

- [ ] **Step 6: 커밋**

```bash
git add server/app/client_ip.py server/tests/test_client_ip.py
git commit -m "fix: 터널 사이드카(loopback) 뒤에서도 방문자 IP 를 CF-Connecting-IP 로 판정해요" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: api 컨테이너 헬스체크 + cloudflared 사이드카

**Files:**
- Modify: `copilot/api/manifest.yml` (`image:` 블록의 `port: 8080` 아래, `network:` 블록 아래)
- Test: `server/tests/test_deploy_manifest_qc_flags.py` (파일 끝에 추가)

**Interfaces:**
- Consumes: 없음
- Produces: 매니페스트 키 `image.healthcheck`, `sidecars.cloudflared` — Task 6·8 가드와 Task 3 런북이 이 이름을 쓴다. 테스트 헬퍼 `_api_manifest() -> dict`, 상수 `CLOUDFLARED_IMAGE_RE`(Task 8 이 재사용).

- [ ] **Step 1: 실패하는 테스트 작성** — `server/tests/test_deploy_manifest_qc_flags.py` 끝에 추가:

```python
# ── Cloudflare Tunnel 인그레스(설계 docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md) ──

CLOUDFLARED_IMAGE_RE = re.compile(r"^cloudflare/cloudflared@sha256:[0-9a-f]{64}$")


def _api_manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def test_api_container_has_its_own_healthcheck():
    """ALB 를 걷어내면 컨테이너 헬스체크가 유일한 헬스 판정이고, cloudflared 사이드카의
    depends_on: healthy 가 이걸 기다린다. 기동 유예가 짧으면 느린 부팅이 재시작 루프에 빠진다
    — 지금 ALB 서비스의 HealthCheckGracePeriod(60s)보다 짧게 두지 않는다."""
    hc = _api_manifest()["image"]["healthcheck"]
    assert "localhost:8080/healthz" in " ".join(hc["command"])
    assert hc["retries"] >= 2
    assert int(str(hc["start_period"]).rstrip("s")) >= 60


def test_cloudflared_sidecar_is_wired_for_tunnel_ingress():
    side = _api_manifest()["sidecars"]["cloudflared"]
    assert CLOUDFLARED_IMAGE_RE.match(side["image"]), "digest 로 고정 — 태그·latest 는 조용히 바뀐다"
    assert side["essential"] is True  # 터널이 죽은 태스크는 아무도 못 부른다 → 태스크째 교체
    assert side["depends_on"] == {"api": "healthy"}  # 기동은 api 뒤, 종료는 역순으로 api 앞
    assert side["secrets"]["TUNNEL_TOKEN"] == (
        "/copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/CLOUDFLARE_TUNNEL_TOKEN"
    )
    assert "TUNNEL_TOKEN" not in (side.get("variables") or {}), "토큰을 평문 변수로 두지 않는다"
    cmd = side["command"]
    assert cmd[0] == "tunnel" and cmd[-1] == "run"
    grace = int(cmd[cmd.index("--grace-period") + 1].rstrip("s"))
    # 진행 중 요청을 마칠 시간: 지금 ALB 드레인(10s)보다 길고 ECS 기본 StopTimeout(30s) 안.
    assert 20 <= grace < 30
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && $PY -m pytest tests/test_deploy_manifest_qc_flags.py -q -k "healthcheck or cloudflared"`
Expected: FAIL 2건 — `KeyError: 'healthcheck'`, `KeyError: 'sidecars'`.

- [ ] **Step 3: 매니페스트 수정** — `copilot/api/manifest.yml`

`image:` 블록의 `  port: 8080` 줄 바로 아래에 삽입:

```yaml
  # 컨테이너 헬스체크(2026-10-02). cloudflared 사이드카의 depends_on: healthy 가 이걸 기다리고,
  # ALB 를 걷어낸 뒤에는 이것이 유일한 헬스 판정이다. 기동 유예 60s = 지금 ALB 서비스의
  # HealthCheckGracePeriod 와 같다. python 은 이미지에 항상 있다(curl 은 빌드 도구라 빠질 수 있다).
  healthcheck:
    command: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz', timeout=4)\" || exit 1"]
    interval: 10s
    retries: 3
    timeout: 5s
    start_period: 60s
```

`network:` 블록(`  connect: true` 줄) 바로 아래, `variables:` 앞에 삽입:

```yaml

# Cloudflare Tunnel 커넥터(2026-10-02). api.wearless.kr 요청이 이 컨테이너를 거쳐 같은 태스크의
# localhost:8080 으로 들어온다 — 설계 docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md,
# 런북 docs/runbooks/cloudflare-tunnel.md.
#  · 이미지는 2026.9.3 멀티아키 인덱스 digest(amd64·arm64)로 고정. 올릴 때 digest 를 같이 바꾼다.
#  · depends_on healthy: 기동은 api 가 healthy 가 된 뒤, 종료는 그 역순이라 api 보다 먼저 빠진다
#    (ECS dependsOn 규칙) → 배포 때 죽어 가는 api 로 새 요청이 가지 않는다.
#  · --grace-period 25s: 진행 중 요청을 마칠 시간. ECS 기본 StopTimeout 30s 안이어야 한다.
#  · ⚠️ 토큰 시크릿이 SSM 에 copilot 태그와 함께 **먼저** 있어야 한다(없으면 태스크 기동 실패→롤백):
#      AWS_REGION=ap-northeast-2 copilot-aws secret init --name CLOUDFLARE_TUNNEL_TOKEN
sidecars:
  cloudflared:
    image: cloudflare/cloudflared@sha256:072c067d25ccbe61d46e18f0d0723255f2bb5304f7317caa95b27031520ff92c
    essential: true
    command: ["tunnel", "--grace-period", "25s", "run"]
    depends_on:
      api: healthy
    secrets:
      TUNNEL_TOKEN: /copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/CLOUDFLARE_TUNNEL_TOKEN
```

- [ ] **Step 4: 통과 확인**

Run: `cd server && $PY -m pytest tests/test_deploy_manifest_qc_flags.py -q`
Expected: 전부 PASS(기존 `test_api_stop_timeout_covers_dispatcher_drain_and_shutdown_finalize` 포함).

- [ ] **Step 5: 렌더 확인(읽기 전용)** — 사이드카가 1번, api 가 0번 컨테이너로 남는지(StopTimeout 90 override 가 api 에 걸려야 한다).

```bash
S=$(mktemp -d)
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws svc package --name api --env use1 --output-dir "$S"
$PY - "$S/api-use1.stack.yml" <<'EOF'
import sys, yaml
class L(yaml.SafeLoader): pass
L.add_multi_constructor("!", lambda l, s, n: None)
t = yaml.load(open(sys.argv[1]), Loader=L)
for i, c in enumerate(t["Resources"]["TaskDefinition"]["Properties"]["ContainerDefinitions"]):
    print(i, c.get("Name"), c.get("StopTimeout"), c.get("DependsOn"), bool(c.get("HealthCheck")))
EOF
```

Expected (2026-10-02 실측과 같아야 한다):
```
0 None 90 None True
1 cloudflared None [{'Condition': 'HEALTHY', 'ContainerName': 'api'}] False
```

- [ ] **Step 6: 실측 부팅 시간 확인** — `start_period 60s` 가 충분한지(Review Focus 4). 지금 떠 있는 api 태스크의 첫 로그 → `Application startup complete` 간격:

```bash
$PY - <<'EOF'
import boto3
logs = boto3.Session(profile_name="wearless").client("logs", region_name="us-east-1")
g = "/copilot/wearless-use1-api"
streams = [s for page in logs.get_paginator("describe_log_streams").paginate(
               logGroupName=g, logStreamNamePrefix="copilot/api/") for s in page["logStreams"]]
streams = sorted(streams, key=lambda s: s.get("firstEventTimestamp", 0), reverse=True)[:3]
for s in streams:
    ev = logs.get_log_events(logGroupName=g, logStreamName=s["logStreamName"], startFromHead=True, limit=500)["events"]
    first = ev[0]["timestamp"]
    done = next((e["timestamp"] for e in ev if "Application startup complete" in e["message"]), None)
    print(s["logStreamName"], "boot_s=", None if done is None else round((done - first) / 1000, 1))
EOF
```

Expected: 최근 3개 태스크 모두 `boot_s` < 45. 45 이상이 하나라도 있으면 `start_period` 를 `max(boot_s)×1.5` 로 올리고(가드 하한 60 유지), Step 4 를 다시 돌린다.

- [ ] **Step 7: 커밋**

```bash
git add copilot/api/manifest.yml server/tests/test_deploy_manifest_qc_flags.py
git commit -m "feat(ops): api 에 컨테이너 헬스체크와 Cloudflare Tunnel 사이드카를 붙여요" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: 런북 + 폴링 스크립트 + PR A

**Files:**
- Create: `scripts/api-watch.sh`
- Create: `docs/runbooks/cloudflare-tunnel.md`

**Interfaces:**
- Consumes: Task 2 의 `sidecars.cloudflared`, 시크릿 이름
- Produces: `scripts/api-watch.sh [URL]` — 종료(Ctrl-C) 시 200 아닌 응답이 0 이면 exit 0, 아니면 1. Task 5·8 이 쓴다.

- [ ] **Step 1: 폴링 스크립트 작성** — `scripts/api-watch.sh`

```bash
#!/usr/bin/env bash
# api.wearless.kr 를 1초마다 찔러 상태코드·지연·엣지(cf-ray)를 찍고 200 이 아닌 응답 수를 센다.
# DNS 전환·롤링 배포·되돌리기 동안 띄워 둔다. Ctrl-C 로 끝내면 요약을 찍고,
# 200 아닌 응답이 0 이면 exit 0, 하나라도 있으면 exit 1.
#
# 사용: scripts/api-watch.sh [URL]    (기본 https://api.wearless.kr/healthz)
set -uo pipefail

URL="${1:-https://api.wearless.kr/healthz}"
total=0
bad=0

summary() {
  echo
  echo "요청 ${total}건 · 200 아닌 응답 ${bad}건"
  [ "$bad" -eq 0 ] && exit 0
  exit 1
}
trap summary INT TERM

while true; do
  total=$((total + 1))
  out=$(curl -sS -o /dev/null --max-time 10 -D - \
        -w 'HTTPCODE=%{http_code} TIME=%{time_total}\n' "$URL" 2>&1) || true
  code=$(sed -n 's/^HTTPCODE=\([0-9]*\).*/\1/p' <<<"$out")
  secs=$(sed -n 's/.* TIME=\([0-9.]*\)$/\1/p' <<<"$out")
  ray=$(grep -i '^cf-ray:' <<<"$out" | tr -d '\r' | awk '{print $2}')
  [ "${code:-000}" = "200" ] || bad=$((bad + 1))
  printf '%s  %s  %ss  cf-ray=%s  bad=%d\n' "$(date +%H:%M:%S)" "${code:-000}" "${secs:-?}" "${ray:--}" "$bad"
  sleep 1
done
```

- [ ] **Step 2: 스크립트 동작 확인**

```bash
chmod +x scripts/api-watch.sh
timeout -s INT 4 scripts/api-watch.sh; echo "exit=$?"
timeout -s INT 3 scripts/api-watch.sh https://api.wearless.kr/does-not-exist; echo "exit=$?"
```

Expected: 첫째는 `200 … cf-ray=…-HKG` 같은 줄 3~4개 + `200 아닌 응답 0건` + `exit=0`. 둘째는 `404` 줄 + `200 아닌 응답 N건`(N≥1) + `exit=1`.

- [ ] **Step 3: 런북 작성** — `docs/runbooks/cloudflare-tunnel.md` 전체:

````markdown
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

아래 명령은 `export AWS_PROFILE=wearless` 를 전제로 한다.

## 0. 터널 만들기 (처음 한 번)

1. Zero Trust → Networks → Tunnels → **Create a tunnel** → **Cloudflared** → 이름 `wearless-api-use1` → 저장.
2. 설치 안내 명령에서 `--token` 뒤 값(`eyJ…`)만 복사한다. 설치 명령은 실행하지 않는다.
3. 라우트(Public Hostname / Published application routes)는 **아직 만들지 않는다** — DNS 전환 때 만든다.
4. 토큰 저장 — 반드시 copilot 으로. raw `aws ssm put-parameter` 는 copilot 태그가 없어 태스크가 못 읽고
   배포가 롤백된다(2026-07-17 FM_CI_PEPPER 사고).
   ```bash
   AWS_REGION=ap-northeast-2 copilot-aws secret init --name CLOUDFLARE_TUNNEL_TOKEN
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
| 마무리(인증서 제거) 후 | 마무리 PR revert + `AWS_REGION=ap-northeast-2 copilot-aws env deploy --name use1` → 그다음 위 줄 |

## 알림

ALB 지표 알람을 걷어낸 자리를 둘이 메운다.

- **외부 업타임 모니터** — 고르는 기준: 무료로 Slack 알림, 확인 간격 5분 이하, HTTPS + 응답 본문 키워드.
  대상 `https://api.wearless.kr/healthz`, 키워드 `ok`.
- **Cloudflare Tunnel Health Alert** — 대시보드 → Notifications → Add → *Tunnel Health Alert* →
  터널 `wearless-api-use1` → 메일(오너 주소).

5xx 는 계속 로그 알림(`log-slack-alerts.yml` 의 `http error status=5`)이 건별로 Slack 에 보낸다.

## 토큰 교체

토큰을 새로 받으면 옛 토큰으로 이미 붙은 커넥터는 계속 돌지만 **새 연결은 못 연다**(Cloudflare 문서
"Tunnel tokens"). 그래서 순서가 중요하다.

1. 대시보드에서 터널 토큰을 새로 받는다(Refresh token). 지금 태스크는 계속 트래픽을 받는다.
2. `AWS_REGION=ap-northeast-2 copilot-aws secret init --name CLOUDFLARE_TUNNEL_TOKEN --overwrite` 로 새 값을 넣는다.
3. `gh workflow run deploy-server.yml --ref main` — 새 태스크가 새 토큰으로 붙고 옛 태스크가 빠진다.
   api-watch 로 `bad=0` 을 본다.

## Cloudflare 앞단 제약 (터널 전과 같다)

- 응답 대기 상한 100초(무료 플랜) — 넘으면 524. 공개 분석 상한은 30×2+12=72초.
- 요청 본문 100MB(무료 플랜) — 앱 업로드 상한 25MB 보다 크다.
````

- [ ] **Step 4: 전체 테스트**

Run: `cd server && $PY -m pytest -q 2>&1 | tail -3`
Expected: 실패 0(기존 skip 은 그대로).

- [ ] **Step 5: 커밋**

```bash
git add scripts/api-watch.sh docs/runbooks/cloudflare-tunnel.md
git commit -m "docs: Cloudflare Tunnel 런북과 전환용 1초 폴링 스크립트를 더해요" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: 푸시 + PR A 생성** (오너가 실행 방식을 고르며 승인한 범위)

```bash
git push -u origin feat/cloudflare-tunnel-ingress
gh pr create --base main --title "feat(ops): api 에 Cloudflare Tunnel 사이드카를 붙여요 (ALB 유지)" --body "$(cat <<'EOF'
## 무엇
- api 태스크에 cloudflared 사이드카(Cloudflare Tunnel)와 컨테이너 헬스체크를 붙인다. **ALB 는 그대로** — 사용자 경로 변화 0.
- 터널(loopback) 뒤에서도 방문자 IP 를 `CF-Connecting-IP` 로 판정한다.
- 런북 `docs/runbooks/cloudflare-tunnel.md`, 전환용 폴링 `scripts/api-watch.sh`.

## 왜
크레딧 소진으로 ALB($16.7)+공인 IPv4 2개($7.4)가 월 ~$24 실청구가 됐다. 설계: `docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md`, 계획: `docs/superpowers/plans/2026-10-02-cloudflare-tunnel-ingress.md`.

## ⛔ 머지 전 (오너)
`CLOUDFLARE_TUNNEL_TOKEN` 이 SSM 에 copilot 태그와 함께 있어야 한다 — 없으면 배포가 롤백된다. 런북 0단계 5번 확인 명령이 두 태그를 출력하면 머지.

## 검증
- `pytest` 전체 통과, 터널 헤더 형태 8케이스·사이드카 가드 2건 추가
- `copilot-aws svc package` 렌더: api=컨테이너 0(StopTimeout 90), cloudflared=1(DependsOn HEALTHY api)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: PR URL 출력. CI `deploy-server` 의 PR 테스트 잡 green.

---

## Part 0 — 오너 작업: 터널·시크릿 (PR A 머지 전)

### Task 4: 터널 생성과 시크릿 확인

**Files:** 없음(대시보드·SSM)

**Interfaces:**
- Consumes: 런북 0단계
- Produces: SSM 파라미터 `/copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN`(copilot 태그 2개), 대시보드 터널 `wearless-api-use1`(라우트 없음)

- [ ] **Step 1 (오너):** 런북 0단계 1~4 수행.
- [ ] **Step 2: 확인(읽기 전용)**

```bash
aws ssm list-tags-for-resource --region us-east-1 --resource-type Parameter \
  --resource-id /copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN --query 'TagList[].[Key,Value]' --output text
aws ssm get-parameter --region us-east-1 --name /copilot/wearless/use1/secrets/CLOUDFLARE_TUNNEL_TOKEN \
  --query 'Parameter.[Type,Version]' --output text
```

Expected: `copilot-application wearless` · `copilot-environment use1` 두 줄, 그리고 `SecureString 1`. 값 자체는 출력하지 않는다(`--with-decryption` 금지).

- [ ] **Step 3 (오너):** PR A 머지 → `deploy-server` 완료까지 대기.
- [ ] **Step 4: 사이드카 확인** — 런북 1단계 명령. Expected: `Registered tunnel connection` 4줄, 대시보드 HEALTHY. CI `/readyz` 게이트 green(아직 ALB 경유).

실패 시: `aws ecs describe-services --region us-east-1 --cluster wearless-use1-Cluster-hYAqwp9HcIdj --services $(aws ecs list-services --region us-east-1 --cluster wearless-use1-Cluster-hYAqwp9HcIdj --query 'serviceArns[?contains(@,`api`)]' --output text) --query 'services[0].events[:10].message'` 로 원인을 보고, 고칠 수 없으면 PR A revert.

---

## Part 2 — DNS 전환과 3일 관찰

### Task 5: `api.wearless.kr` 전환 + 실측 + 알림 설정

**Files:** 없음(대시보드·실측). 결과 기록은 Task 7 에서 런북에 남긴다.

**Interfaces:**
- Consumes: Task 3 `scripts/api-watch.sh`, 런북 2단계
- Produces: 오너가 알려 주는 업타임 모니터 설정값(서비스명·확인 간격·키워드·Slack 채널) — Task 7 이 런북에 기록

- [ ] **Step 1: 전환 전 기준값** — 토스 웹훅 경로 응답 코드를 적어 둔다.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://api.wearless.kr/v1/webhooks/toss/not-the-secret
```

- [ ] **Step 2:** 터미널 하나에서 `scripts/api-watch.sh` 실행(전환 끝까지 유지).
- [ ] **Step 3 (오너):** 런북 2단계 1~3(저트래픽 시간대).
- [ ] **Step 4: 확인** — 런북 2단계 4·5. Expected: `server: cloudflare`, readyz ok, CORS origin 일치, 웹훅 코드가 Step 1 과 같음, 내 IP 가 로그에 보임.
- [ ] **Step 5: 롤링 배포 실측 (오너 승인 후)** — api-watch 를 띄운 채:

```bash
gh workflow run deploy-server.yml --ref main
sleep 10   # 방금 만든 실행이 목록에 잡힐 때까지
gh run watch "$(gh run list --workflow deploy-server.yml --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Expected: 배포 성공, api-watch 를 Ctrl-C 했을 때 `200 아닌 응답 0건`. 1건이라도 있으면 런북 되돌리기 1행 실행 후 cloudflared·api 로그를 보고 멈춘다(B1 진행 금지).

- [ ] **Step 6 (오너):** 업타임 모니터·Tunnel Health Alert 설정(런북 "알림"). 설정값 4개(서비스명, 간격, 키워드, Slack 채널)를 알려 받는다.
- [ ] **Step 7: 3일 관찰 — 매일 1회**

```bash
# 5xx 건수(로그 알림 입력과 같은 패턴)
aws logs filter-log-events --region us-east-1 --log-group-name /copilot/wearless-use1-api \
  --filter-pattern '"http error status=5"' --start-time $(( ($(date +%s) - 86400) * 1000 )) \
  --query 'length(events)'
# 헬스체크 실패로 태스크가 교체됐는지
aws ecs describe-services --region us-east-1 --cluster wearless-use1-Cluster-hYAqwp9HcIdj \
  --services $(aws ecs list-services --region us-east-1 --cluster wearless-use1-Cluster-hYAqwp9HcIdj --query 'serviceArns[?contains(@,`api`)]' --output text) \
  --query 'services[0].events[:20].message' --output text | grep -i unhealthy
```

Expected: 5xx 가 전환 전 하루 평균 수준, `unhealthy` 줄 없음, 업타임 모니터 다운 0, 터널 HEALTHY 유지. 셋째 날 통과하면 Part B1 로.

---

## Part B1 — PR B1: ALB 지표 알람 삭제

브랜치: PR A 머지 후 `git fetch origin && git switch -c feat/cf-tunnel-drop-alb-alarms origin/main`.

### Task 6: ALB 알람·타깃그룹 참조 제거

**Files:**
- Delete: `copilot/api/addons/api-alarms.yml`, `copilot/api/addons/addons.parameters.yml`
- Modify: `copilot/environments/addons/alarm-slack.yml` (헤더 주석, 알람 3개 리소스, Outputs 주석)
- Modify: `server/tests/test_deploy_manifest_qc_flags.py` (`test_api_addon_parameters_are_declared_by_an_addon_template` 교체, 가드 2개 추가)

**Interfaces:**
- Consumes: 없음
- Produces: 테스트 `test_no_stack_imports_the_public_load_balancer_export`, `test_api_addons_do_not_reference_the_removed_target_group` — Task 8 머지 조건이 이 둘의 green 이다. 상수 `COPILOT_DIR`.

- [ ] **Step 1: 실패하는 가드 작성** — `server/tests/test_deploy_manifest_qc_flags.py` 끝에 추가:

```python
COPILOT_DIR = MANIFEST.parents[1]


def test_no_stack_imports_the_public_load_balancer_export():
    """api 를 Backend Service 로 바꾸는 배포 안에서 Copilot env-controller 가 ALB 를 지운다. 그때
    `${App}-${Env}-PublicLoadBalancerFullName` export 를 import 하는 스택이 남아 있으면 환경 스택
    업데이트가 실패하고 api 배포가 롤백된다(설계 2026-10-02 §4)."""
    offenders = sorted(
        str(p.relative_to(COPILOT_DIR))
        for p in COPILOT_DIR.rglob("*.yml")
        if "PublicLoadBalancerFullName" in p.read_text(encoding="utf-8")
    )
    assert offenders == []


def test_api_addons_do_not_reference_the_removed_target_group():
    """Backend Service 스택에는 TargetGroup 이 없다 — 애드온이 참조하면 B2 배포가 깨진다."""
    addons_dir = MANIFEST.parent / "addons"
    offenders = sorted(p.name for p in addons_dir.glob("*.yml") if "TargetGroup" in p.read_text(encoding="utf-8"))
    assert offenders == []
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && $PY -m pytest tests/test_deploy_manifest_qc_flags.py -q -k "public_load_balancer or target_group"`
Expected: FAIL 2건 — offenders 에 `api/addons/api-alarms.yml`·`environments/addons/alarm-slack.yml`, 그리고 `addons.parameters.yml`·`api-alarms.yml`.

- [ ] **Step 3: api 애드온 삭제**

```bash
git rm copilot/api/addons/api-alarms.yml copilot/api/addons/addons.parameters.yml
```

- [ ] **Step 4: 애드온 파라미터 테스트 교체** — 기존 `test_api_addon_parameters_are_declared_by_an_addon_template` 함수 전체를 아래로 바꾼다:

```python
def test_api_addon_parameters_are_declared_by_an_addon_template():
    """addons.parameters.yml 의 값과 addon 템플릿 Parameters(Copilot 이 주는 App·Env·Name 제외)는
    양쪽으로 짝이 맞아야 한다. 한쪽만 지우거나 이름을 바꾸거나 새 파라미터에 값을 안 주면
    PR CI 는 통과하고 main 의 copilot svc deploy 에서야 깨진다.

    addons.parameters.yml 이 없으면 Copilot 은 App·Env·Name 만 넘긴다 — 그때는 어느 템플릿도 그 밖의
    파라미터를 요구하면 안 된다(2026-10-02 api-alarms.yml 과 함께 파일을 지웠다)."""
    addons_dir = MANIFEST.parent / "addons"
    params_file = addons_dir / "addons.parameters.yml"
    provided = set()
    if params_file.exists():
        provided = set(yaml.load(params_file.read_text(encoding="utf-8"), Loader=_CfnLoader)["Parameters"])
    declared = set()
    for tpl in [*addons_dir.glob("*.yml"), *addons_dir.glob("*.yaml")]:
        if tpl.name.startswith("addons.parameters."):
            continue
        declared |= set(yaml.load(tpl.read_text(encoding="utf-8"), Loader=_CfnLoader).get("Parameters") or {})
    assert declared - {"App", "Env", "Name"} == provided
```

- [ ] **Step 5: `alarm-slack.yml` 에서 알람 3개 삭제 + 주석 교체** — 아래 스크립트를 워크트리 루트에서 실행:

```bash
python3 - <<'EOF'
p = "copilot/environments/addons/alarm-slack.yml"
s = open(p, encoding="utf-8").read()

header = '''# CloudWatch 알람·예산 알림 → Slack 경로(SNS 토픽 + 램다). ALB 지표 알람은 0개, 지금 이 경로를 쓰는 건
# 아래 예산 알림 2개(MonthlyNetBudget·MonthlyGrossBudget, #451)다.
#
# ── 2026-10-02: ALB 지표 알람을 걷어냈다 ─────────────────────────────────
#   api 앞단이 ALB 에서 Cloudflare Tunnel 로 바뀌면서(설계
#   docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md) 여기 있던 target-5xx·
#   slow-response·elb-502-504 와 api-no-healthy-target(copilot/api/addons/api-alarms.yml)이 지표
#   출처를 잃었다. 대체: 5xx 는 로그 알림(log-slack-alerts.yml 의 "http error status=5")이 건별로
#   잡고, 서버 0대·502/504 는 외부 업타임 모니터, 터널 끊김은 Cloudflare Tunnel Health Alert 가
#   본다(docs/runbooks/cloudflare-tunnel.md). 실트래픽 응답 중앙값은 사각지대로 남았다.
#   ⚠️ ALB 이름 export(환경 스택의 PublicLoadBalancer… 출력)를 다시 import 하지 말 것 — ALB 가 없는
#   환경에서 env 배포가 실패한다(server/tests/test_deploy_manifest_qc_flags.py 가 막는다).
#   이 주석에도 그 export 이름을 그대로 적지 않는다 — 가드가 문자열로 검사한다.
#
# ── 토픽·램다를 남기는 이유 ────────────────────────────────────────────────
#   예산 알림이 이 토픽 → 램다 → Slack 으로 온다(램다가 평문 예산 메시지도 처리한다). 다음 지표 알람
#   (예: 앱이 직접 내는 지연 지표)도 AlarmActions·OKActions 에 AlarmTopicArn export 를 주면 같은 채널로 온다.
#   sam·opendid 알림은 이 토픽이 아니라 copilot/api/addons/sam-autoscale.yml 의 SamAlertTopic 을 쓴다.
#
# 램다를 log-slack-alert 와 따로 두는 이유: 그쪽 인라인 소스가 4,060/4,096 바이트라 SNS 처리를
# 얹을 수 없다.
#
# 되돌리기: 이 파일을 지우면 예산 알림도 같이 사라진다. export 를 가져다 쓰는 스택이 있으면 그것부터
# 지운다(2026-10-02 기준 없음).
'''
s = header + s[s.index("Parameters:\n"):]

# ALB 알람 3개만 지운다 — 바로 뒤의 예산 알림 절(#451)은 남긴다.
start = s.index("  # ── 알람 셋 ")
end = s.index("  # ── 예산 알림 (2026-10-02)")
s = s[:start] + s[end:]

old = """    # copilot/api/addons/api-alarms.yml 이 ImportValue 로 쓴다. 이 export 를 지우거나 이름을
    # 바꾸면 env 배포가 막힌다(가져다 쓰는 스택이 있으면 CloudFormation 이 거부한다).
"""
new = """    # 2026-10-02 기준 가져다 쓰는 스택은 없다(api-alarms.yml 삭제). 누군가 ImportValue 로 쓰기
    # 시작하면 이 export 를 지우거나 이름을 바꿀 때 env 배포가 막힌다.
"""
assert old in s
s = s.replace(old, new)
open(p, "w", encoding="utf-8").write(s)
EOF
grep -c "AWS::CloudWatch::Alarm" copilot/environments/addons/alarm-slack.yml
$PY -c "import yaml,sys; L=type('L',(yaml.SafeLoader,),{}); L.add_multi_constructor('!', lambda l,s,n: None); d=yaml.load(open('copilot/environments/addons/alarm-slack.yml'),Loader=L); print(sorted(d['Resources']), sorted(d['Outputs']))"
```

Expected: `0`, 그리고 `['AlarmSlackFunction', 'AlarmSlackOwnLogGroup', 'AlarmSlackPermission', 'AlarmSlackRole', 'AlarmSlackSubscription', 'AlarmTopic', 'AlarmTopicPolicy', 'MonthlyGrossBudget', 'MonthlyNetBudget'] ['AlarmTopicArn']` — 예산 2개가 남아 있어야 한다.

- [ ] **Step 6: 통과 확인**

Run: `cd server && $PY -m pytest tests/test_deploy_manifest_qc_flags.py -q`
Expected: 전부 PASS.

- [ ] **Step 7: 커밋**

```bash
git add -A copilot/api/addons copilot/environments/addons/alarm-slack.yml server/tests/test_deploy_manifest_qc_flags.py
git commit -m "chore(ops): ALB 지표 알람 4개를 걷어내요 (터널 전환 B1)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 알람 런북 갱신 + 알림 설정 기록 + PR B1 배포

**Files:**
- Modify: `docs/runbooks/cloudwatch-alarms.md` (맨 위 상태 블록 추가, 두 제목 변경)
- Modify: `docs/runbooks/cloudflare-tunnel.md` ("알림" 절 끝에 설정 기록 표)

**Interfaces:**
- Consumes: Task 5 Step 6 의 설정값 4개
- Produces: 머지 조건 — `list-imports` 결과가 "not imported by any stack"

- [ ] **Step 1: 알람 런북 상태 갱신**

```bash
python3 - <<'EOF'
p = "docs/runbooks/cloudwatch-alarms.md"
s = open(p, encoding="utf-8").read()
banner = '''# CloudWatch 알람 → Slack

> **2026-10 현재: ALB 지표 알람 0개.** api 앞단이 ALB 에서 Cloudflare Tunnel 로 바뀌면서 아래 ALB
> 지표 알람 4개를 모두 걷어냈다. 지금 장애 신호는 ① 로그 알림(이 문서 첫 표의 윗줄, 그대로)
> ② 외부 업타임 모니터 ③ Cloudflare Tunnel Health Alert 다 — `docs/runbooks/cloudflare-tunnel.md` "알림".
> SNS 토픽·Slack 램다·`AlarmTopicArn` export 는 그대로다 — 예산 알림(아래 "예산 알림" 절)이 계속 쓴다.
> 아래 "알람 넷"부터 "남는 사각지대"까지는 ALB 시절(2026-09-23 ~ 10-02) 기록이다.
'''
assert s.startswith("# CloudWatch 알람 → Slack\n")
s = banner + s[len("# CloudWatch 알람 → Slack\n"):]
for a, b in [("## 지금 걸린 알람 넷", "## (기록) ALB 시절 알람 넷"),
             ("## 남는 사각지대", "## (기록) ALB 시절 사각지대")]:
    assert a in s
    s = s.replace(a, b, 1)
open(p, "w", encoding="utf-8").write(s)
EOF
head -8 docs/runbooks/cloudwatch-alarms.md
```

Expected: 상태 블록이 맨 위에 보인다.

- [ ] **Step 2: 알림 설정 기록** — `docs/runbooks/cloudflare-tunnel.md` 의 `5xx 는 계속 로그 알림(…)이 건별로 Slack 에 보낸다.` 줄 바로 아래에, Task 5 Step 6 에서 받은 실제 값으로 표를 넣는다(형식 고정):

```markdown

### 설정 기록

| 항목 | 값 |
|---|---|
| 업타임 서비스 | <오너가 고른 서비스명> |
| 대상 · 간격 | `https://api.wearless.kr/healthz` · <분> |
| 키워드 | `ok` |
| 알림 | Slack <채널명> |
| Tunnel Health Alert | 메일 <받는 주소> · 설정일 <YYYY-MM-DD> |
```

(꺾쇠 칸은 받은 값으로 바꿔서 커밋한다. 꺾쇠가 남은 채로 커밋하지 않는다 — `grep -n '<' docs/runbooks/cloudflare-tunnel.md` 로 확인.)

- [ ] **Step 3: 전체 테스트 + 커밋 + PR**

```bash
cd server && $PY -m pytest -q 2>&1 | tail -2 && cd ..
git add docs/runbooks/cloudwatch-alarms.md docs/runbooks/cloudflare-tunnel.md
git commit -m "docs: 알람 런북을 터널 시대 상태로 갱신하고 알림 설정을 기록해요" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -u origin feat/cf-tunnel-drop-alb-alarms
gh pr create --base main --title "chore(ops): ALB 지표 알람을 걷어내요 (터널 전환 B1)" --body "$(cat <<'EOF'
## 무엇
- `api-alarms.yml`(no-healthy-target)·`addons.parameters.yml` 삭제, `alarm-slack.yml` 의 ALB 알람 3개 삭제(토픽·램다·export 유지).
- 가드: 어떤 스택도 `PublicLoadBalancerFullName` 을 import 하지 않음 · api 애드온이 `TargetGroup` 을 참조하지 않음.

## 왜
다음 PR(B2)에서 api 를 Backend Service 로 바꾸면 그 배포 안에서 ALB 가 지워진다. ALB export 를 쓰는 알람이 남아 있으면 그 배포가 롤백된다(설계 §4). 대체 알림(업타임 모니터·Tunnel Health Alert)은 이미 켜져 있다.

## ⛔ 배포 순서 (오너)
1. 머지 → CI 가 api 배포(api-alarms 삭제)
2. `AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws env deploy --name use1` (`--diff` 에는 AddonsStack `TemplateURL` 한 줄만 나온다 — 애드온 리소스 diff 는 안 보인다. 확인은 배포 뒤 Step 5 명령으로)
3. 둘 다 끝나야 B2 진행

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4 (오너):** PR B1 머지 → CI api 배포 완료 → `env deploy`(PR #458 리뷰 I1: `--diff` 는 애드온 내용을 보여 주지 않는다).
- [ ] **Step 5: 머지 조건 확인(읽기 전용)**

```bash
aws cloudformation list-imports --region us-east-1 --export-name wearless-use1-PublicLoadBalancerFullName 2>&1
aws cloudwatch describe-alarms --region us-east-1 --alarm-name-prefix wearless-use1- --query 'MetricAlarms[].AlarmName' --output text
```

Expected: 첫째 `… is not imported by any stack.` 오류 문구(= 통과). 둘째 출력에 `target-5xx`·`slow-response`·`elb-502-504`·`api-no-healthy-target` 이 없다.

---

## Part B2 — PR B2: Backend Service 전환 (ALB 철거)

브랜치: PR B1 머지 + Task 7 Step 5 통과 후 `git fetch origin && git switch -c feat/cf-tunnel-backend-service origin/main`.

### Task 8: api 를 Backend Service 로

**Files:**
- Modify: `copilot/api/manifest.yml` (머리 주석 1~3행, `type:`, `http:` 블록 삭제)
- Modify: `server/tests/test_analyze.py:579-587`
- Test: `server/tests/test_deploy_manifest_qc_flags.py` (끝에 추가)

**Interfaces:**
- Consumes: Task 2 의 `_api_manifest()`, Task 6 의 가드 2개(green 이어야 한다)
- Produces: 테스트 `test_api_is_a_backend_service_behind_the_tunnel`

- [ ] **Step 1: 실패하는 테스트 작성** — `server/tests/test_deploy_manifest_qc_flags.py` 끝에 추가:

```python
def test_api_is_a_backend_service_behind_the_tunnel():
    """ALB 를 걷어낸 뒤 api 는 인바운드 없는 Backend Service 다. `http:` 블록이 되살아나면 Copilot 이
    ALB 를 다시 만든다(ALB $16.7 + 공인 IPv4 2개 $7.4/월) — 설계 2026-10-02."""
    doc = _api_manifest()
    assert doc["type"] == "Backend Service"
    assert "http" not in doc
    assert "cloudflared" in doc["sidecars"]
    assert doc["image"]["port"] == 8080  # Service Connect 와 사이드카가 이 포트로 붙는다
    assert doc["network"]["connect"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `cd server && $PY -m pytest tests/test_deploy_manifest_qc_flags.py -q -k backend_service_behind`
Expected: FAIL — `'Load Balanced Web Service' == 'Backend Service'`.

- [ ] **Step 3: 매니페스트 전환** — 워크트리 루트에서:

```bash
python3 - <<'EOF'
p = "copilot/api/manifest.yml"
s = open(p, encoding="utf-8").read()
old_head = '''# The manifest for the "api" service.
# Read the full specification for the "Load Balanced Web Service" type at:
#  https://aws.github.io/copilot-cli/docs/manifest/lb-web-service/
'''
new_head = '''# The manifest for the "api" service.
# Read the full specification for the "Backend Service" type at:
#  https://aws.github.io/copilot-cli/docs/manifest/backend-service/
#
# 2026-10-02 Load Balanced Web Service → Backend Service. 공개 진입점은 ALB 가 아니라 아래
# cloudflared 사이드카(Cloudflare Tunnel)다 — 설계 docs/superpowers/specs/2026-10-02-cloudflare-tunnel-ingress-design.md.
# ⚠️ `http:` 블록을 되살리지 말 것: Copilot 이 ALB 를 다시 만든다(ALB $16.7 + 공인 IPv4 2개 $7.4/월).
'''
assert s.startswith(old_head)
s = new_head + s[len(old_head):]
s = s.replace("type: Load Balanced Web Service\n", "type: Backend Service\n", 1)
start = s.index("# Distribute traffic to your service.\nhttp:")
end = s.index("# Configuration for your containers and service.")
s = s[:start] + s[end:]
open(p, "w", encoding="utf-8").write(s)
EOF
grep -n "^type:\|^http:\|alias:" copilot/api/manifest.yml
```

Expected: `type: Backend Service` 한 줄만, `http:`·`alias:` 없음.

- [ ] **Step 4: `test_analyze.py` 기준 교체** — `test_provider_timeout_leaves_room_for_the_fallback_under_the_alb_limit` 함수 전체를 아래로:

```python
def test_provider_timeout_leaves_room_for_the_fallback_under_the_edge_limit():
    """provider 상한이 앞단 응답 대기 상한과 같으면 폴백이 구조적으로 무의미하다.

    1차가 상한을 다 쓰는 순간 앞단은 이미 타임아웃(Cloudflare 524)을 내보낸 뒤라 2차가 돌 시간이 없다.
    api 앞단은 Cloudflare 무료 플랜(응답 대기 100초)이다 — 2026-10-02 ALB(유휴 180초)를 걷어냈다.
    """
    s = make_settings()
    cloudflare_proxy_read_timeout = 100
    assert s.analysis_timeout_seconds * 2 + s.analysis_aux_timeout_seconds < cloudflare_proxy_read_timeout
```

- [ ] **Step 5: 통과 확인**

Run: `cd server && $PY -m pytest -q 2>&1 | tail -2`
Expected: 실패 0.

- [ ] **Step 6: 렌더 diff 확인(읽기 전용)** — 배포된 스택 대비:

```bash
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws svc package --name api --env use1 --diff 2>&1 | tee /tmp/b2-diff.txt | head -120
grep -nE "^\s*[-+]\s+(TargetGroup|HTTPListenerRuleWithDomain|HTTPRuleWithDomainPriorityAction|HTTPSListenerRule|HTTPSRulePriorityAction|RulePriorityFunction|RulePriorityFunctionRole):" /tmp/b2-diff.txt
grep -nE "ServiceName|Replacement" /tmp/b2-diff.txt
```

Expected: 지워지는 리소스가 위 7개뿐이고 추가 리소스 0, `Service` 에서는 `HealthCheckGracePeriodSeconds`·`LoadBalancers`·`ServiceRegistries`(+ `DependsOn`) 만 바뀐다. `ServiceName` 변경이 보이면 **멈추고** 설계로 돌아간다(교체 = Service Connect 이름 충돌 롤백).

- [ ] **Step 7: 커밋 + PR**

```bash
git add copilot/api/manifest.yml server/tests/test_deploy_manifest_qc_flags.py server/tests/test_analyze.py
git commit -m "feat(ops): api 를 Backend Service 로 바꿔 ALB 를 걷어내요 (터널 전환 B2)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -u origin feat/cf-tunnel-backend-service
gh pr create --base main --title "feat(ops): api 를 Backend Service 로 바꿔 ALB 를 걷어내요 (터널 전환 B2)" --body "$(cat <<'EOF'
## 무엇
api `type: Backend Service`, `http:` 블록 삭제. 이 배포 안에서 Copilot env-controller 가 ALB·리스너·Elastic IP 2개를 지운다(월 ~$24).

## 머지 조건
- B1 배포 + `env deploy` 완료, `aws cloudformation list-imports --region us-east-1 --export-name wearless-use1-PublicLoadBalancerFullName` → not imported
- `svc package --diff`: 지워지는 리소스 7개(TargetGroup·리스너 규칙·RulePriority 함수), `Service` 교체 없음(바뀌는 속성 3개 모두 CloudFormation "No interruption")

## 되돌리기
revert → ALB 가 새 주소로 재생성 → CNAME 교체(런북 `docs/runbooks/cloudflare-tunnel.md` 되돌리기 2행).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 8 (오너):** 머지. 배포 동안 `scripts/api-watch.sh` 를 띄워 둔다.
- [ ] **Step 9: 철거 확인(읽기 전용)**

```bash
aws elbv2 describe-load-balancers --region us-east-1 --query 'length(LoadBalancers)'
aws ec2 describe-addresses --region us-east-1 --query 'length(Addresses)'
aws cloudformation describe-stacks --region us-east-1 --stack-name wearless-use1 \
  --query "Stacks[0].Parameters[?ParameterKey=='ALBWorkloads'].ParameterValue" --output text
aws ec2 describe-network-interfaces --region us-east-1 \
  --query 'NetworkInterfaces[?Association.PublicIp!=`null`].Description' --output text
```

Expected: `0`, `0`, 빈 줄, ECS attachment 1개(api — Spot 서비스가 떠 있으면 그만큼 더). api-watch `200 아닌 응답 0건`, CI `/readyz`·CORS 게이트 green.

---

## Part C — PR C: 마무리 (철거 후 3일 이상 지나서)

브랜치: PR B2 머지 후 3일 이상 문제없으면 `git fetch origin && git switch -c chore/cf-tunnel-cleanup origin/main`.

### Task 9: ALB 전제 설정·문서 정리

**Files:**
- Modify: `copilot/environments/use1/manifest.yml` (`http:` 블록 삭제 — 인증서 + `ingress.source_ips`)
- Delete: `copilot/environments/overrides/` (디렉터리째 — `cfn.patches.yml` 의 유일한 패치가 ALB 유휴 180초)
- Modify: `server/tests/test_client_ip.py` (SG 대조 테스트·관련 import 삭제)
- Modify: `server/app/client_ip.py` (독스트링의 SG 문단 삭제)
- Modify: `scripts/smoke-api.sh` (ALB `--resolve` → 기본 URL)
- Modify: `docs/runbooks/cloudflare-origin-lock.md` (상태 블록)

**Interfaces:**
- Consumes: Task 8 완료(ALB 0개)
- Produces: 없음

- [ ] **Step 1: 환경 매니페스트에서 `http:` 블록 제거** — `copilot/environments/use1/manifest.yml` 의 `# Configure the load balancers in your environment, once created.` 줄부터 `ingress.source_ips` 목록의 마지막 줄(`        - 131.0.72.0/22`)까지 — 인증서와 #315 의 SG 잠금(2026-10-02 머지·env deploy) 전부 — 를 아래로 교체:

```yaml
# 로드밸런서 없음(2026-10). api 는 Cloudflare Tunnel 사이드카로 들어온다 — docs/runbooks/cloudflare-tunnel.md.
# ACM 인증서(8120f0ff)와 ALB 보안그룹 잠금(source_ips, #315)을 여기서 뺐다. ALB 를 되살리려면 이 블록과
# api 의 http: 를 함께 되돌린다 — 잠금 없이 ALB 만 되살리면 0.0.0.0/0 으로 열린다.
```

- [ ] **Step 2: 오버라이드 디렉터리 삭제**

```bash
git rm -r copilot/environments/overrides
```

- [ ] **Step 3: 환경 렌더 확인(읽기 전용)**

```bash
AWS_PROFILE=wearless AWS_REGION=ap-northeast-2 copilot-aws env package --name use1 --diff 2>&1 | head -80
```

Expected: 바뀌는 것이 HTTPS 리스너·인증서·`idle_timeout.timeout_seconds` 처럼 `CreateALB` 조건 아래(이미 거짓) 리소스 정의뿐이고, VPC·서브넷·클러스터·보안그룹·애드온 변경이 없다. 그 밖의 변경이 보이면 멈춘다.

- [ ] **Step 4: SG 대조 테스트 삭제** — `server/tests/test_client_ip.py` 에서
  `test_alb_security_group_admits_exactly_the_ranges_trusted_as_cloudflare` 함수 전체, `ENV_MANIFEST = (...)` 정의,
  그리고 더 쓰지 않는 `import pathlib`·`import yaml` 을 지우고 import 줄을 `from app.client_ip import client_ip` 로 바꾼다.

Run: `cd server && $PY -m pytest tests/test_client_ip.py -q`
Expected: PASS, skip 0.

- [ ] **Step 5: `client_ip.py` 독스트링 정리** — 아래 문단을

```
대역 출처는 https://www.cloudflare.com/ips-v4 · ips-v6 (2026-09-15 확인). IPv4 목록은
ALB 보안그룹(copilot/environments/use1/manifest.yml 의 http.public.ingress.source_ips)과
같아야 한다 — tests/test_client_ip.py 가 잠근다.
```

다음으로 교체:

```
대역 출처는 https://www.cloudflare.com/ips-v4 · ips-v6 (2026-09-15 확인). 2026-10 ALB 를 걷어낸 뒤로
요청은 터널(loopback)로만 들어오지만, 대역 판정은 되돌리기(ALB 복구) 경로를 위해 남겨 둔다.
```

- [ ] **Step 6: `scripts/smoke-api.sh` 를 기본 URL 방식으로** — 머리 주석·인자·`check()`·마지막 안내를 아래로 교체(체크 4개는 그대로):

```bash
#!/usr/bin/env bash
# api 가 실제로 살아 있는지 확인한다(배포 직후·DNS 변경 직후·되돌리기 직후).
# 2026-10 부터 api.wearless.kr 은 Cloudflare Tunnel 로 들어온다 — ALB 주소를 직접 때리는 방식은 없앴다.
#
# 사용: scripts/smoke-api.sh [BASE_URL]    (기본 https://api.wearless.kr)
set -uo pipefail

BASE="${1:-https://api.wearless.kr}"
fail=0

check() {  # name expected_status path
  local name="$1" want="$2" path="$3"
  local got
  got=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${BASE}${path}" 2>/dev/null) || got="curl_error"
  if [ "$got" = "$want" ]; then
    echo "  ok   ${name}  (${got})"
  else
    echo "  FAIL ${name}  expected ${want}, got ${got}"
    fail=$((fail + 1))
  fi
}

echo "대상: $BASE"
```

마지막 블록:

```bash
echo
if [ "$fail" -eq 0 ]; then
  echo "통과."
else
  echo "실패 ${fail}건 — AWS_REGION=ap-northeast-2 copilot-aws svc logs --name api --env use1 로 확인."
  exit 1
fi
```

Run: `scripts/smoke-api.sh`
Expected: `ok` 4줄 + `통과.`

- [ ] **Step 7: 원본 잠금 런북 상태 블록** — `docs/runbooks/cloudflare-origin-lock.md` 첫 줄(`# api.wearless.kr Cloudflare 프록시 + ALB 원본 잠금`) 바로 아래에 삽입:

```markdown

> **2026-10 이후 기록용.** ALB 를 걷어내고 Cloudflare Tunnel 로 바꿨다 — 인바운드 포트가 없어 2단계(SG 잠금,
> #315 · 2026-10-02 적용)는 ALB 와 함께 사라졌다. 현행 런북: `docs/runbooks/cloudflare-tunnel.md`. 1단계의
> `client_ip()` 판정은 터널 경로까지 넓혀 그대로 쓴다.
```

- [ ] **Step 8: 전체 테스트 + 커밋 + PR**

```bash
cd server && $PY -m pytest -q 2>&1 | tail -2 && cd ..
git add -A copilot/environments server/tests/test_client_ip.py server/app/client_ip.py scripts/smoke-api.sh docs/runbooks/cloudflare-origin-lock.md
git commit -m "chore(ops): ALB 를 전제로 한 인증서·패치·테스트·문서를 정리해요 (터널 전환 마무리)" -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
git push -u origin chore/cf-tunnel-cleanup
gh pr create --base main --title "chore(ops): ALB 전제 설정·문서를 정리해요 (터널 전환 마무리)" --body "$(cat <<'EOF'
## 무엇
- 환경 매니페스트 `http:` 블록(ACM 인증서 + #315 의 ALB SG 잠금) 제거, ALB 유휴 180초 패치(overrides 디렉터리) 제거
- ALB SG 대조 테스트 삭제, `smoke-api.sh` 기본 URL 방식, 원본 잠금 런북 상태 블록

## ⛔ 배포 (오너)
CI 는 env 를 배포하지 않는다. 머지 후 `AWS_REGION=ap-northeast-2 copilot-aws env deploy --name use1 --diff` 확인 → `env deploy --name use1`.
이 뒤로 ALB 되돌리기는 이 PR revert + env deploy 가 먼저다(런북 되돌리기 3행).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9 (오너):** 머지 → `env deploy --diff`(ALB 보안그룹 잠금·인증서 정의 제거만 보여야 한다) → `env deploy`.

---

### Task 10: 1주 뒤 절감 확인 + 기록

**Files:** 없음(메모리는 레포 밖)

**Interfaces:**
- Consumes: B2 배포일
- Produces: 메모리 `aws-cost-audit`·`cloudflare-tunnel-ingress` 갱신

- [ ] **Step 1: Cost Explorer 실측** (호출 1회 $0.01) — B2 배포 다음 날부터 7일:

```bash
$PY - <<'EOF'
import boto3, datetime
ce = boto3.Session(profile_name="wearless").client("ce", region_name="us-east-1")
end = datetime.date.today()
start = end - datetime.timedelta(days=7)
r = ce.get_cost_and_usage(
    TimePeriod={"Start": str(start), "End": str(end)}, Granularity="DAILY",
    Metrics=["UsageQuantity", "UnblendedCost"],
    GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
    Filter={"Dimensions": {"Key": "USAGE_TYPE", "Values": ["LoadBalancerUsage", "USE1-PublicIPv4:InUseAddress"]}},
)
for t in r["ResultsByTime"]:
    print(t["TimePeriod"]["Start"], {g["Keys"][0]: round(float(g["Metrics"]["UsageQuantity"]["Amount"]), 1) for g in t["Groups"]})
EOF
```

Expected: `LoadBalancerUsage` 0(또는 키 없음), `PublicIPv4:InUseAddress` 일 ~24시간(=1개, Spot 기동분만큼 약간 더).

- [ ] **Step 2:** 메모리 `aws-cost-audit.md` 에 절감 실측을, `cloudflare-tunnel-ingress.md` 에 완료 상태(PR 번호 4개)를 기록하고, 워크트리를 정리한다(`git worktree remove .claude/worktrees/cf-tunnel` — 브랜치가 모두 머지된 뒤).
