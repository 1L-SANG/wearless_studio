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

### Linux Server 3 preflight

Besu를 시작하기 전에 UTC/NTP와 hardware clock 모드를 확인한다. 세 검사가 모두 통과하지 않으면 Besu를 시작하지 않는다.

```bash
timedatectl show -p Timezone --value | grep -qx UTC
timedatectl show -p NTPSynchronized --value | grep -qx yes
timedatectl show -p LocalRTC --value | grep -qx no
date -u --iso-8601=seconds
```

동기화가 실패하면 해당 Linux 배포판의 승인된 time-sync 서비스(`systemd-timesyncd` 또는 운영 NTP agent)를 복구하고 다시 검사한다. OpenDID 서비스 재시작으로 시계 문제를 덮지 않는다.

## 복구 (순서 중요: besu 먼저, 엔티티 나중)

1. besu 컨테이너 재시작 → healthy 대기.
2. TAS(:8090)·Issuer(:8091) 등 엔티티 서버 재시작.
3. issue-vc 재시도.

(2026-07-17 실증: besu 12:50 재시작 + 엔티티 13:33 재시작 → 13:34:45 발급 성공, vc_id DB 기록 확인.)

Linux Server 3에서는 위 preflight를 통과한 뒤 systemd 의존 순서대로 복구한다.

```bash
sudo systemctl stop fm-holder opendid-cas opendid-issuer opendid-tas
sudo systemctl restart opendid-infra
docker inspect -f '{{.State.Health.Status}}' opendid-besu-node | grep -qx healthy
sudo systemctl start opendid-tas
curl -fsS http://127.0.0.1:8090/actuator/health >/dev/null
sudo systemctl start opendid-issuer opendid-cas
curl -fsS http://127.0.0.1:8091/actuator/health >/dev/null
curl -fsS http://127.0.0.1:8094/actuator/health >/dev/null
sudo systemctl start fm-holder
curl -fsS http://127.0.0.1:8100/holder/health >/dev/null
```

TAS보다 Issuer/CAS/Holder를 먼저 시작하거나, Besu health 실패 상태에서 발급을 재시도하지 않는다.

## 예방

- 시스템 설정 > 날짜와 시간 자동 동기화 확인.
- `sudo sntp -sS time.apple.com` (수동 동기, 사용자 권한).
- NTP 가 막힌 네트워크(사내망 등)면 다른 네트워크에서 동기 후 진행.
- Linux Server 3은 UTC timezone, `NTPSynchronized=yes`, `LocalRTC=no`를 배포 preflight와 재시작 점검에 포함한다.

## 별개 함정 — hang 없는 즉시 500

holder issue-vc 를 curl 로 직접 칠 때 claims 키는 **camelCase** 다:
`allowedUse`, `unitPrice`(int), `licenseValidUntil`, `faceImageDigest`, `modelName` (facemarket.py `_issue_face_vc` 참조).
snake_case 로 보내면 Jackson 이 전부 무시 → issuer user.data 가 `{}` 로 upsert → issuer `NullPointerException: claimList is null` 즉시 500.
다음 정상 호출이 upsert 로 자가 치유된다. (정확한 페이로드로 2.9초 발급 실증.)
