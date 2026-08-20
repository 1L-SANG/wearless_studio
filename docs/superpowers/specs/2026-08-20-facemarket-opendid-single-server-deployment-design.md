# FaceMarket OpenDID 단일 서버 이전 — 설계

- 날짜: 2026-08-20
- 상태: 인프라 설계 완료, 보안 cutover는 외부 계약·법무·재등록 준비도 승인 전까지 차단
- 배경 조사: `docs/research/facemarket-opendid-vc-deployment-audit.md`
- 선행 게이트: `docs/research/2026-08-20-facemarket-external-contract-and-biometric-legal-gates.md`
- 배포 목표: 기존 API 서버 1대 + SAM 서버 1대 + FaceMarket/OpenDID 서버 1대, 총 3대

## 1. 목표와 완료 기준

FaceMarket/OpenDID 서버 한 대에 VC 데이터면을 묶고, 기존에 프로비저닝된 OpenDID 상태를 이전한다. Orchestrator는 상시 실행하지 않는다.

이 문서는 VC 인프라 배포 설계다. 기존 FaceMarket 모델 freeze, 얼굴 자산 purge, 운영 재등록 cutover를 승인하지 않는다. 해당 작업은 외부 portrait/liveness/matcher 계약, 개인정보 법무 승인, 실제 enrollment E2E와 dry-run sign-off 이후에만 수행한다.

완료 조건:

1. 기존 API 서버가 내부망의 `fm-holder:8100`만 호출한다.
2. 새 모델의 wallet/DID 등록, FaceLicense VC 발급, valid 확인, revoke, revoked 확인이 실제로 동작한다.
3. PostgreSQL, Besu, 엔티티 wallet/DID, holder data가 재시작 후 유지된다.
4. 기존 FaceLicense namespace/schema/issue-profile과 발급 이력이 유지된다.
5. Orchestrator `:9001` 없이 TAS, Issuer, CAS, Holder가 재기동된다.
6. 외부에 공개되는 OpenDID 포트가 없다. `:8100`은 API 서버에서만 접근할 수 있다.

## 2. 확정 아키텍처

```text
Server 1 — API
  FastAPI + FaceMarket routes
            |
            | private HTTP :8100
            v
Server 3 — FaceMarket/OpenDID
  fm-holder :8100
      |-- TAS    :8090
      |-- Issuer :8091
      `-- CAS    :8094
            |-- PostgreSQL :5432
            `-- Besu       :8545

Server 2 — SAM model server
  현행 유지
```

Server 1의 FaceMarket 라우트는 이동하지 않는다. Server 3은 FaceMarket의 VC 실행 인프라다. OpenDID 내부 구성요소는 한 호스트의 localhost 네트워크에서만 통신한다.

## 3. Orchestrator 결정

- VC 발급 런타임에는 Orchestrator가 필요하지 않다.
- 기존 DB, Besu 원장, 엔티티 wallet/DID를 함께 이전하므로 대상 서버에서 bootstrap을 다시 할 필요도 없다.
- Orchestrator는 상태를 새로 만들거나 재해 복구할 때만 사용하는 별도 도구로 보관한다.
- 운영 프로세스 목록에는 포함하지 않고 `:9001`도 열지 않는다.

## 4. 이전할 상태

이전 대상 DB는 OpenDID의 TAS/CAS/Issuer PostgreSQL이다. `fm_models`, `fm_licenses`가 있는 기존 애플리케이션/Supabase DB는 Server 1의 현행 운영 위치에 그대로 두고 Server 3으로 옮기지 않는다.

2026-08-20 로컬 조사값:

| 상태 | 현재 위치 | 관찰값 | 이전 방식 |
|---|---|---:|---|
| OpenDID PostgreSQL | Docker volume `postgre_postgre_opendid_data` | 약 125MB | PostgreSQL 16.4 logical dump 전체 |
| Besu 원장 | Docker volume `besu_besu_opendid_data` | 약 1.8GB | Besu 정지 상태의 cold archive |
| TAS wallet/DID | Orchestrator `jars/TA` | 존재 | 암호화된 secret archive |
| Issuer wallet/DID | Orchestrator `jars/Issuer` | 존재 | 암호화된 secret archive |
| CAS wallet/DID | Orchestrator `jars/CA` | 존재 | 암호화된 secret archive |
| Wallet Provider wallet/DID | Orchestrator `jars/Wallet` | 존재 | 암호화된 secret archive |
| Besu 계약 설정 | `shells/Besu/*.properties`, `besu.dat` | 존재 | 암호화된 secret archive |
| 모델별 Holder 상태 | `services/fm-holder/data` | **현재 없음** | 존재 시 반드시 별도 archive |

OpenDID DB에는 TAS 1건, 엔티티 5건, FaceLicense plan 1건, Issuer/CAS 설정, 발급 VC 12건이 존재한다. DB만 복사하면 DID 공개키와 VC metadata가 저장된 Besu, 서명 개인키가 든 wallet 파일과 불일치하므로 반드시 같은 이전 단위로 취급한다.

### Holder 상태 공백

현재 `services/fm-holder/data`가 없어 과거 12개 VC에 사용한 모델별 개인키를 이 작업공간에서 찾지 못했다.

- 과거 VC의 온체인 존재·폐기 상태 조회는 유지된다.
- 해당 모델의 기존 VC를 Holder가 서명해 폐기하는 기능은 wallet 백업을 찾지 못하면 복원할 수 없다.
- 새 서버에서 새로 생성하는 모델 wallet/DID부터는 영속 볼륨과 백업 대상에 포함한다.
- 기존 라이선스 재발급·DB 재연결은 별도 데이터 변경이므로 자동 실행하지 않는다.

## 5. 이전 전략

### PostgreSQL

Raw volume 복사 대신 PostgreSQL 16.4의 logical dump를 사용한다. 전체 DB와 role을 함께 가져가기 위해 `pg_dumpall`을 사용한다. 현재 전체 크기가 작아 TAS/CAS/Issuer만 선별하는 것보다 전체 이전이 단순하고 안전하다.

### Besu

쓰기 주체인 TAS/Issuer/CAS/Holder를 먼저 중지하고 Besu를 종료한 뒤 volume을 cold archive한다. 대상도 Besu `25.5.0`으로 고정하고 빈 volume에 복원한다. DB dump와 Besu archive는 같은 cutover 시점에 만든다.

### Wallet과 설정

다음 파일은 Git과 컨테이너 이미지에 넣지 않는다.

- `*.wallet`, `*.did`, `*.zkpwallet`
- `blockchain.properties`, `besu.dat`
- wallet password, DB password, `FM_HOLDER_PEPPER`
- `fm-holder/data`

대상 서버의 `/opt/opendid/secrets`와 `/opt/opendid/state`에 소유자 전용 권한으로 배치한다. 전송 archive에는 checksum manifest를 만들고 안전한 전송 채널을 사용한다.

## 6. 프로세스 운영

대상은 Linux 단일 VM이다. 기존 localhost 기반 프로토콜과 하드코딩을 유지하기 위해 다음 중 가장 작은 운영면을 사용한다.

- PostgreSQL/Besu: Docker Compose + named volume
- TAS/Issuer/CAS/Holder: Java 21 JAR을 systemd가 직접 관리
- Orchestrator: 운영하지 않음

각 Java 서비스는 `/opt/opendid` 아래의 고정 경로를 사용한다. macOS 절대경로와 `192.168.0.23`이 들어간 현재 `application.yml`은 대상용 템플릿으로 교체한다.

기동 순서:

```text
PostgreSQL healthy
  -> Besu RPC healthy
    -> TAS healthy
      -> Issuer + CAS healthy
        -> fm-holder healthy
```

## 7. 네트워크와 비밀

- Server 1 → Server 3 `:8100`만 허용한다.
- `:5432`, `:8090`, `:8091`, `:8094`, `:8545`, `:8546`, `:9001`은 외부와 Server 1 양쪽에 공개하지 않는다.
- Holder API에는 현재 애플리케이션 인증이 없으므로 네트워크 ACL/Security Group이 필수 경계다.
- Entity admin API도 dev profile에서 인증이 없으므로 localhost 전용으로 유지한다.
- 기본 pepper와 wallet password fallback은 제거하고 환경변수/secret 파일로 주입한다.

## 8. 빌드 선행조건

`services/fm-holder`는 현재 OpenDID 서버 내부 DTO `org.omnione.did.base.datamodel.data.*` 누락으로 `compileJava` 61건이 실패한다.

배포 전에 OpenDID V2.0.0 공식 소스의 해당 datamodel package를 Apache-2.0 고지와 함께 재현 가능한 소스로 포함하고 다음 명령을 통과시킨다.

```bash
cd services/fm-holder
./gradlew clean test
```

TA fat JAR의 `BOOT-INF/classes`를 런타임 classpath에 우연히 얹는 방식은 사용하지 않는다.

## 9. Cutover와 롤백

Cutover:

1. 소스 OpenDID 쓰기 프로세스 중지.
2. PostgreSQL dump, Besu cold archive, wallet/config archive 생성과 checksum 확인.
3. 대상 서버 복원 후 내부 health check.
4. 새 테스트 모델로 실제 발급 → valid → revoke → revoked 검증.
5. 전체 서비스 재시작 후 상태 재검증.
6. Server 1의 `OPENDID_HOLDER_URL`을 대상 private 주소로 설정.
7. `vc_id is null`인 활성 라이선스는 dry-run 확인 후 별도 승인된 범위만 재발급.

롤백은 Server 1의 `OPENDID_HOLDER_URL`을 이전 값 또는 미설정으로 되돌리는 것이다. 기존 FaceMarket 라이선스 기능은 Holder 장애를 best-effort로 처리하므로 API 전체를 롤백할 필요가 없다. 소스 volume과 archive는 검증 완료 전 삭제하지 않는다.

## 10. 테스트 계약

- 빌드: Holder clean build/test 통과.
- 상태: dump/restore 전후 DB별 테이블 수와 FaceLicense plan 수 일치.
- 원장: contract address, chain ID, 기존 VC metadata 조회 일치.
- 기능: 신규 모델 wallet → register-did → issue-vc → valid → revoke → revoked.
- 지속성: PostgreSQL/Besu/Holder/JVM 전체 재시작 후 위 상태 유지.
- 운영: Orchestrator 미실행, `:9001` closed.
- 보안: API 서버 외의 `:8100` 접근 차단, 나머지 OpenDID 포트 외부 차단.

## 11. 비목표

- FaceMarket FastAPI 라우트를 Server 3으로 분리
- OpenDID LSS 전환
- Verifier/VP 제출 프로토콜 추가
- OpenDID 서비스를 하나의 Java 프로세스로 합치기
- 기존 VC를 wallet 백업 없이 강제 재발급·폐기
- 이번 계획에서 실제 운영 서버나 데이터에 변경 적용
