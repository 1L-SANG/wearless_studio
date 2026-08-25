# 런북 — OpenDID issue-vc 150초 hang / 500 (besu 시계 역행)

데모·발표 직전에 로컬 OpenDID 스택에서 VC 발급이 갑자기 죽을 때 보는 문서.
근본 원인은 코드가 아니라 **macOS 호스트 시계 역행**이라 코드 수정으로 못 잡는다 — 증상 인지와 복구 순서가 전부다.

## 증상

- `POST /holder/models/{id}/issue-vc` 가 **약 150초 hang 후 500**, `fm_licenses.vc_id` 는 null 유지.
- TAS 응답: `Failed to communicate with issuer: unknown error`.

## 원인 사슬 (2026-07-17 확진)

1. macOS 시계가 NTP 동기 실패 후 한 번에 크게 되돌아감(역행). sntp 측정 당시 +2.0s 드리프트, 이 네트워크에서 NTP(UDP 123) 응답이 자주 타임아웃.
2. besu(`opendid-besu-node`) 채굴이 꼬임 — 로그에 `Invalid block header: timestamp X is greater than the timestamp margin (X-2)` + `Illegal block mined`.
3. issuer 가 "Registering VC to B/C" 단계에서 무한 대기 → TAS read timeout(~150s) → holder 500.
4. JVM 방증: TAS/issuer 로그의 `Retrograde clock change detected` (HikariCP housekeeper).

## 진단

```bash
sntp time.apple.com                          # 호스트 시계 오프셋 확인
docker logs opendid-besu-node 2>&1 | grep -c "Illegal block mined"   # 최근 발생 여부
```

## 복구 (순서 중요: besu 먼저, 엔티티 나중)

1. besu 컨테이너 재시작 → healthy 대기.
2. TAS(:8090)·Issuer(:8091) 등 엔티티 서버 재시작.
3. issue-vc 재시도.

(2026-07-17 실증: besu 12:50 재시작 + 엔티티 13:33 재시작 → 13:34:45 발급 성공, vc_id DB 기록 확인.)

## 예방

- 시스템 설정 > 날짜와 시간 자동 동기화 확인.
- `sudo sntp -sS time.apple.com` (수동 동기, 사용자 권한).
- NTP 가 막힌 네트워크(사내망 등)면 다른 네트워크에서 동기 후 진행.

## Linux 대상 preflight (단일 서버 이전 이후)

macOS 로컬뿐 아니라 세 번째 Linux 서버(`docs/runbooks/facemarket-opendid-single-server.md`)에서도
같은 시계 역행 함정이 재현된다. **Besu 를 켜기 전에** host 시계/NTP 를 먼저 확인한다.

### Besu 시작 전 점검

```bash
timedatectl                      # System clock synchronized: yes, NTP service: active 여야 함
timedatectl show -p NTPSynchronized --value   # -> yes
date -u                          # UTC 가 실제 시각과 일치하는지 육안 확인
chronyc tracking 2>/dev/null || ntpq -p 2>/dev/null   # 드리프트/오프셋 확인
```

- `System clock synchronized: no` 또는 NTP 비활성이면 **Besu 를 켜지 말 것**.
  먼저 동기화한다:
  ```bash
  sudo timedatectl set-ntp true
  sudo systemctl restart chronyd 2>/dev/null || sudo systemctl restart systemd-timesyncd
  sudo chronyc makestep 2>/dev/null || true    # 큰 오프셋을 한 번에 스텝(역행 유발 방지)
  ```
- 컨테이너는 host 시계를 그대로 물려받는다. host 가 UTC 동기 상태여야 Besu 채굴 timestamp 가 안정된다.
- 사내망 등 NTP(UDP 123)가 막힌 네트워크면 다른 경로로 동기 후 진행한다(로컬 함정과 동일).

### 재시작 순서 (Linux, 순서 중요: besu 먼저, 엔티티 나중)

증상(issue-vc 150초 hang → 500, `Illegal block mined`)이 재발하면:

```bash
# 1) 시계부터 바로잡는다
sudo chronyc makestep 2>/dev/null || sudo timedatectl set-ntp true
# 2) besu 재시작 → healthy 대기
docker compose -f deploy/opendid/infra.compose.yml restart besu
docker compose -f deploy/opendid/infra.compose.yml ps    # besu healthy 확인
# 3) 엔티티 서버 재시작 (TAS → Issuer → CAS → Holder)
sudo systemctl restart opendid-tas opendid-issuer opendid-cas fm-holder
# 4) issue-vc 재시도
```

postgres 는 재시작하지 않아도 된다(시계 함정의 당사자가 아니다). besu → 엔티티 순서를 지키는
이유는 로컬과 같다: 엔티티가 꼬인 besu 에 물리면 다시 hang 된다.

## 별개 함정 — hang 없는 즉시 500

holder issue-vc 를 curl 로 직접 칠 때 claims 키는 **camelCase** 다:
`allowedUse`, `unitPrice`(int), `licenseValidUntil`, `faceImageDigest`, `modelName` (facemarket.py `_issue_face_vc` 참조).
snake_case 로 보내면 Jackson 이 전부 무시 → issuer user.data 가 `{}` 로 upsert → issuer `NullPointerException: claimList is null` 즉시 500.
다음 정상 호출이 upsert 로 자가 치유된다. (정확한 페이로드로 2.9초 발급 실증.)
