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

## 별개 함정 — hang 없는 즉시 500

holder issue-vc 를 curl 로 직접 칠 때 claims 키는 **camelCase** 다:
`allowedUse`, `unitPrice`(int), `licenseValidUntil`, `faceImageDigest`, `modelName` (facemarket.py `_issue_face_vc` 참조).
snake_case 로 보내면 Jackson 이 전부 무시 → issuer user.data 가 `{}` 로 upsert → issuer `NullPointerException: claimList is null` 즉시 500.
다음 정상 호출이 upsert 로 자가 치유된다. (정확한 페이로드로 2.9초 발급 실증.)
