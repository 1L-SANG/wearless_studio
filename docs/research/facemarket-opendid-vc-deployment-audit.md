# FaceMarket OpenDID VC 서버 배포 전수조사

- 조사일: 2026-08-20
- 범위: FaceLicense VC 생성·발급·상태 확인·폐기, OpenDID Orchestrator의 런타임 필요성, 독립 배포 가능성
- 결론 신뢰도: 높음. OpenDID V2.0.0 공식 문서·소스와 FaceMarket 실제 호출 그래프를 교차 검증했다.

## 결론

**OpenDID Orchestrator(기본 9001)는 FaceLicense VC 발급·상태 확인의 런타임 필수 서버가 아니다.** Orchestrator는 OpenDID 구성요소 JAR 다운로드, PostgreSQL/Besu 준비, 엔티티 월렛·DID 생성, 설정 생성, 각 독립 서버의 시작·정지를 담당하는 **빌드·프로비저닝 제어면(control plane)** 이다. 공식 설치 가이드도 전체 서버 기동 뒤 “Orchestrator의 역할은 완료”된다고 설명하고, 구현은 별도 JAR 프로세스를 `java -jar`로 실행한다. [공식 Orchestrator 설치·운영 가이드](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/docs/installation/OpenDID_orchestrator_InstallationAndOperation_Guide_ko.md), [공식 기동 구현](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/source/did-orchestrator-server/src/main/java/org/omnione/did/orchestrator/service/OrchestratorServiceImpl.java), [공식 `start.sh`](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/source/did-orchestrator-server/jars/start.sh)

따라서 해커톤 서버에는 Orchestrator를 상시 띄우지 않고도 현재 코드가 구현한 **FaceLicense VC 발급·온체인 상태 확인·폐기 기능 범위**를 유지할 수 있다. 다만 이는 아래 build/provision/state/smoke-test 조건을 충족한 뒤의 목표 상태다. 현재 production manifest는 Holder를 연결하지 않아 실제 VC 발급을 skip한다. 또한 **소스 코드 또는 JAR만 복사하고 프로비저닝 없이 실행하는 것은 불가능**하다. OpenDID는 DB, Besu 원장과 배포 계약, 엔티티 DID/개인키 월렛, 발급 스키마·플랜, 홀더별 월렛 상태가 결합된 stateful 시스템이다. 코드와 bootstrap 스크립트를 가져가 서버에서 이 상태를 새로 생성하는 것은 가능하다.

Orchestrator를 빼면 제어용 JVM 하나는 줄지만 OpenDID 데이터면 자체가 사라지는 것은 아니다. 현재 기능 기준 최소 구성도 `fm-holder`, TAS, Issuer, CAS의 JVM 4개와 PostgreSQL, Besu이므로, 이를 FastAPI 코드 한 프로세스로 합치는 것은 별도 재구현이다.

현재 가장 현실적인 방식은 다음과 같다.

1. 대상 VC 서버의 일회성 init 환경에서 Orchestrator V2.0.0으로 키·DID·Besu·DB를 새로 프로비저닝한다.
2. 서버에는 **TAS + Issuer + CAS + `fm-holder` + PostgreSQL + Besu**만 각각 독립 프로세스/컨테이너로 배포한다.
3. 생성된 상태와 키를 영속 볼륨/비밀 저장소로 이관하고 Orchestrator 9001은 종료하거나 배포하지 않는다.
4. FastAPI에 내부 주소 `OPENDID_HOLDER_URL=http://fm-holder:8100`만 연결한다.

서버 수는 사용자가 원하는 3대로 유지된다. Server 1은 FastAPI와 FaceMarket 카탈로그·라이선스·생성 gate를 유지하고, Server 2는 SAM을 유지한다. Server 3은 `fm-holder`와 OpenDID 데이터면을 묶는 VC 서버다. “FaceMarket 서버”라는 이름 때문에 FaceMarket API 전체가 Server 3으로 이동하는 것으로 해석하면 안 된다. 애플리케이션/Supabase DB도 Server 1 측 현행 위치에 남고, Server 3에는 OpenDID PostgreSQL·Besu와 wallet/DID/holder 상태만 둔다.

## 제어면과 데이터면

| 구분 | 구성요소 | 역할 | 상시 런타임 |
|---|---|---|---|
| 제어면 | Orchestrator :9001 | JAR 다운로드, 저장소 설치·기동, 설정·월렛·DID 생성, 서버 시작·정지·상태 점검 | **불필요**. 최초/복구 프로비저닝 때만 사용 가능 |
| 데이터면 | `fm-holder` :8100 | FaceMarket의 모델별 홀더 월렛·DID 관리, OpenDID 발급 프로토콜 조율 | **필수** |
| 데이터면 | TAS :8090 | 사용자/월렛 등록, VC 발급 제안·토큰·프로토콜 조율 | **필수** |
| 데이터면 | Issuer :8091 | VC 발급, VC metadata 온체인 기록·조회·폐기 | **필수** |
| 데이터면 | CAS :8094 | 현재 Flow A의 KYC/PII 시드·조회와 CAS 서명 역할 | **필수**(신규 모델 등록 기능 유지 시) |
| 상태면 | PostgreSQL | TAS/CAS/Issuer 프로토콜 및 발급 상태, FaceLicense schema/profile | **필수** |
| 상태면 | Besu + OpenDID contract | DID 문서 및 VC metadata/status 원장 | **필수**(현재 구현 기준) |

Orchestrator 소스는 `requestStartupAll()`에서 포트별 독립 서버를 순회해 시작하고, `startServer()`는 각 서버 JAR·`application.yml`을 인자로 별도 스크립트를 실행한다. 그 스크립트는 `nohup java -jar ... &`로 프로세스를 분리한다. 즉 9001이 발급 요청을 프록시하거나 중계하지 않는다. [로컬 소스 `OrchestratorServiceImpl.java`](../../../did-orchestrator-server/source/did-orchestrator-server/src/main/java/org/omnione/did/orchestrator/service/OrchestratorServiceImpl.java), [공식 V2.0.0 소스](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/source/did-orchestrator-server/src/main/java/org/omnione/did/orchestrator/service/OrchestratorServiceImpl.java)

공식 TAS, Issuer, CAS 설치 문서도 각각 Gradle 빌드 후 독립 `java -jar` 실행과 Docker 배포 방법을 제공한다. 따라서 구성요소 산출물을 Orchestrator와 분리해 배포하는 것은 공식 배포 모델과 일치한다. [TAS 설치 가이드](https://github.com/OmniOneID/did-ta-server/blob/V2.0.0/docs/installation/OpenDID_TAServer_Installation_Guide.md), [Issuer 설치 가이드](https://github.com/OmniOneID/did-issuer-server/blob/V2.0.0/docs/installation/OpenDID_IssuerServer_Installation_Guide.md), [CAS 설치 가이드](https://github.com/OmniOneID/did-ca-server/blob/V2.0.0/docs/installation/OpenDID_CAServer_Installation_Guide.md)

## FaceMarket 실제 호출 그래프

현재 백엔드는 라이선스 생성 시 `fm-holder`의 `/wallet` → `/register-did` → `/issue-vc`만 호출하고, 결과 `vcId`와 사용자 DID를 FaceMarket DB에 저장한다. 사용 전 검증은 `/holder/vc/verify`, 폐기는 `/revoke-vc`를 호출한다. 이 경로 어디에도 Orchestrator 9001 호출이 없다. [`facemarket.py` 발급 경로](../../server/app/facemarket.py#L1338), [`facemarket.py` 검증 경로](../../server/app/facemarket.py#L1477), [`facemarket.py` 폐기 경로](../../server/app/facemarket.py#L1565)

```text
Internet
  -> Wearless FastAPI
      -> fm-holder :8100
          -> TAS :8090
          -> Issuer :8091
          -> CAS :8094
              -> PostgreSQL
          -> Besu :8545 / OpenDID contracts
```

`fm-holder`의 런타임 설정에도 TAS, Issuer, CAS 주소와 키 파일만 실제 경로에 필요하다. `verifier-url`, `api-server-url` 설정은 존재하지만 Java 코드에서 참조되지 않는다. [`application.yml`](../../services/fm-holder/src/main/resources/application.yml#L21)

### “검증” 의미의 중요한 제한

현재 `/holder/vc/verify`는 OpenDID Verifier 서버나 VC 본문 서명 검증을 호출하지 않는다. Issuer의 `/issuer/api/v1/inspect-propose-revoke`를 호출해 Besu의 VC metadata가 존재하고 폐기되지 않았는지 확인한다. 따라서 현재 기능에서 “verify”는 **온체인 존재·폐기 상태 확인**이지, presentation 제출이나 VC payload의 범용 암호학적 검증이 아니다. [`VerifyVcService.java`](../../services/fm-holder/src/main/java/kr/wearless/fmholder/protocol/VerifyVcService.java#L10), [`IssuerAgentClient.java`](../../services/fm-holder/src/main/java/kr/wearless/fmholder/protocol/IssuerAgentClient.java#L8)

해커톤 심사 요건이 단순 발급·온체인 상태 확인·폐기라면 Verifier를 제외해도 현재 기능이 유지된다. “검증 서버 사용”, VP 제출, VC 본문 서명 검증이 명시 요건이면 그때 별도 기능으로 Verifier를 연결해야 한다.

## 제외 가능한 프로세스

현재 호출 그래프와 동작을 그대로 유지할 때 다음 프로세스는 서버 상시 실행에서 제외 가능하다.

- **Orchestrator :9001** — 최초 생성·재프로비저닝·운영 편의용 제어면
- **Verifier :8092** — 현재 FaceMarket이 호출하지 않음
- **API :8093** — 현재 FaceMarket이 호출하지 않음
- **Wallet server :8095** — `fm-holder`가 SDK와 파일 월렛으로 커스터디얼 홀더 역할을 직접 수행함
- **Demo :8099** — 샘플 UI/클라이언트
- **LSS** — 현재 FaceMarket 구성은 Besu를 직접 사용함. LSS로 바꾸는 것은 별도 마이그레이션이며 이번 최소 배포안에 포함하지 않음

로컬 `scripts/start.sh`가 6개 엔티티 모두를 Orchestrator로 시작하는 것은 개발 편의 스크립트일 뿐 런타임 프로토콜 요구가 아니다. [`scripts/start.sh`](../../scripts/start.sh#L35)

로컬 JAR 크기 관찰값 기준 TAS 157 MB + Issuer 131 MB + CAS 129 MB는 약 417 MB이며, Orchestrator 48 MB + Verifier 132 MB + API 101 MB + Wallet 145 MB를 제외하면 약 426 MB의 JAR 디스크 payload를 줄인다. 이는 압축 JAR 크기일 뿐 메모리 절감량으로 해석하면 안 된다.

## 왜 “코드만 가져가기”가 안 되는가

### 반드시 함께 만들거나 이전할 상태

| 상태/산출물 | 필요한 이유 | 권장 보관 |
|---|---|---|
| TAS/CAS/Issuer PostgreSQL DB | 엔티티·사용자·트랜잭션·VC schema/profile/plan 상태 | 영속 DB 볼륨 + 백업 |
| Besu 데이터 디렉터리 | DID 문서, VC metadata/status, 배포 계약 상태 | 영속 볼륨 + snapshot |
| `blockchain.properties` 등 | chain RPC, contract address, 트랜잭션 계정 정보 | 환경별 설정/secret |
| TAS/Issuer/CAS/Wallet provider DID 문서와 월렛 | 프로토콜 서명 및 온체인 등록 주체의 개인키 | secret/KMS 또는 암호화 볼륨 |
| `wallet.wallet`, `cas.wallet` | 현재 `fm-holder`가 wallet provider/CAS 서명을 직접 수행 | secret volume; 공개 이미지에 포함 금지 |
| `fm-holder/data` | 모델별 wallet, user wallet, DID 문서, Flow A 완료 marker | 영속 볼륨 + 백업 |
| FaceLicense namespace/schema/issue-profile/list plan | `facelicense` VC 발급 규격과 TAS 라우팅 | DB 프로비저닝 스크립트 + DB 상태 |
| 비밀번호·pepper | 파일 월렛 복호화와 holder wallet 파생/보호 | secret manager/env |

`fm-holder`는 모델별 월렛·DID·등록 완료 marker를 로컬 파일에 저장한다. DB/Besu만 초기화하고 이 marker를 남기면 실제 등록 상태와 불일치하는 stale marker가 될 수 있으므로 상태를 한 묶음으로 백업·복원해야 한다. [`HolderWalletService.java`](../../services/fm-holder/src/main/java/kr/wearless/fmholder/wallet/HolderWalletService.java#L52)

또한 현재 홀더는 Orchestrator가 생성한 Wallet Provider와 CAS 파일 월렛의 개인키를 직접 읽어 서명한다. 파일 없이 소스만 배포하면 등록 토큰/attestation 서명이 성립하지 않는다. [`ProviderKeyService.java`](../../services/fm-holder/src/main/java/kr/wearless/fmholder/protocol/ProviderKeyService.java#L11), [`CasKeyService.java`](../../services/fm-holder/src/main/java/kr/wearless/fmholder/protocol/CasKeyService.java#L13)

## 독립 배포 선택지

### A. 권장: Orchestrator는 bootstrap-only, 구성요소는 독립 배포

- 대상 서버의 일회성 init job에서 V2.0.0 Orchestrator로 Besu 계약, DB, 엔티티 월렛·DID를 새로 생성한다. 기존 개발 키를 복사하는 것보다 이 경로가 안전하다.
- TAS/Issuer/CAS 공식 JAR을 각 컨테이너/서비스로 실행한다.
- FaceLicense namespace/schema/profile을 [`issuer-provision-facelicense.sh`](../../scripts/issuer-provision-facelicense.sh#L14)로 등록한다. 이 스크립트는 Issuer와 PostgreSQL을 직접 사용하며 Orchestrator가 필요 없다.
- 생성 상태를 영속화한 뒤 Orchestrator를 종료·제거한다.

장점은 기존 프로비저닝 로직을 재사용하면서 런타임을 가볍게 만드는 것이다. 단, Orchestrator 프로세스가 띄운 자식 프로세스에 기대지 말고 각 구성요소를 Docker/서비스 매니저가 직접 소유하게 해야 재시작과 관측이 안전하다.

### B. 공식 구성요소 소스에서 선택 빌드

TAS, Issuer, CAS의 V2.0.0 태그를 각각 빌드해 이미지/JAR만 배포한다. 공식 설치 문서가 지원하는 방식이지만 키·DB·Besu 프로비저닝은 별도로 자동화해야 한다. `fm-holder`가 OpenDID 2.0.0 SDK JAR을 사용하므로 전 구성요소를 우선 V2.0.0으로 고정하고, 혼합 버전은 호환성 테스트 전 피한다.

### C. Orchestrator가 다운로드한 release JAR만 이관

공식 Orchestrator의 `download.sh`도 각 OpenDID 저장소의 release JAR을 개별 다운로드한다. 따라서 생성된 JAR 중 TAS/Issuer/CAS만 이미지에 넣을 수 있다. [공식 `download.sh`](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/source/did-orchestrator-server/download.sh)

가장 빠르지만 로컬 `application.yml`의 절대 경로와 IP를 그대로 복사하면 안 된다. 서버용 내부 DNS, 볼륨 경로, DB/RPC 주소, secret 주입으로 다시 작성해야 한다.

### D. FastAPI에 VC 발급만 재구현

기술적으로는 가능하지만 현재 OpenDID DID 등록, TAS 발급 프로토콜, Issuer 온체인 VC metadata, 폐기 흐름과 달라진다. “OpenDID 활용”이 심사 기준이면 기능 동등성이 깨질 가능성이 높아 권장하지 않는다.

## 현재 배포 차단점

### 1. `fm-holder`가 clean build 되지 않는다

2026-08-20에 `services/fm-holder`에서 `./gradlew clean test`를 실행했으며 `compileJava`에서 61개 오류로 실패했다. 대표 오류는 `org.omnione.did.base.datamodel.data.AccE2e`, `Proof`, `DidAuth` 등 내부 프로토콜 DTO package 누락이다. 현재 빌드는 `libs/*.jar`만 fileTree 의존성으로 넣는데 해당 SDK JAR에는 이 서버 내부 DTO가 없다. [`build.gradle`](../../services/fm-holder/build.gradle#L13)

따라서 지금 상태는 **저장소 코드만 서버에서 clone하여 재현 빌드할 수 없다.** 배포 전에 정확히 V2.0.0에 맞는 프로토콜 모델을 재현 가능한 정식 의존성으로 만들거나, 필요한 최소 모델 소스를 라이선스 고지와 함께 저장소에 포함한 뒤 clean build와 테스트를 통과시켜야 한다. 로컬 TA fat JAR의 내부 클래스를 우연히 classpath에 얹는 방식은 CI 재현성이 없으므로 피한다.

### 2. 현재 production manifest에는 Holder가 없다

Copilot API manifest는 `OPENDID_HOLDER_URL`을 의도적으로 비워 VC 발급을 비치명적으로 skip한다고 명시한다. 즉 현재 production 구성은 FaceLicense 레코드는 만들 수 있어도 VC를 발급하지 않는다. [`copilot/api/manifest.yml`](../../copilot/api/manifest.yml#L202)

### 3. 개발용 키·경로를 그대로 배포할 수 없다

`fm-holder/application.yml`에는 로컬 절대 경로, 기본 pepper, wallet/CAS 개발 비밀번호 fallback이 있으며, 주석 자체가 “그대로 배포 금지”를 경고한다. 또한 holder가 Wallet Provider와 CAS 개인키까지 보유하는 단일 운영 신뢰 모델이다. 해커톤 단일 VM/private network에는 사용할 수 있지만 공개 네트워크에 8090/8091/8094/8100/8545/DB를 노출하지 말고 FastAPI만 외부에 공개해야 한다. [`application.yml`](../../services/fm-holder/src/main/resources/application.yml#L8)

### 4. 공식 Orchestrator 운영 가정은 단일 머신 중심이다

V2.0.0 Orchestrator 문서는 구성요소가 같은 머신에 있다고 가정한다. 해커톤에서는 단일 VM의 Docker Compose/private bridge가 가장 안전한 최소 범위다. 다중 호스트 분산은 각 구성요소의 URL·키·방화벽·복구를 별도로 설계·검증해야 한다. [공식 Orchestrator 설치·운영 가이드](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/docs/installation/OpenDID_orchestrator_InstallationAndOperation_Guide_ko.md)

## 해커톤 권장 실행 순서

1. **빌드 봉쇄 해제:** clean clone에서 `fm-holder` 의존성을 고치고 `./gradlew clean test` 통과.
2. **버전 고정:** TAS/Issuer/CAS/SDK를 OpenDID V2.0.0으로 맞춤.
3. **한 VM에 private Compose 구성:** Besu, PostgreSQL, TAS, Issuer, CAS, `fm-holder`; healthcheck와 restart policy 추가.
4. **일회성 bootstrap:** Orchestrator 또는 동등 스크립트로 Besu 계약·엔티티 월렛/DID·DB를 만들고 온체인 등록. 기존 [`opendid-provision.sh`](../../scripts/opendid-provision.sh#L10)는 이 단계에서만 9001을 사용한다.
5. **FaceLicense provision:** namespace/schema/issue profile/list plan 생성.
6. **secret/volume 이관:** 키와 wallet은 이미지 밖 secret volume, PostgreSQL/Besu/holder data는 영속 볼륨.
7. **Orchestrator 종료:** 9001 비공개·미배포. 선택한 구성요소는 Docker/서비스 매니저가 직접 실행.
8. **백엔드 연결:** `OPENDID_HOLDER_URL`을 private holder 주소로 설정.
9. **재시작 포함 smoke test:** 모델 wallet/DID 등록 → 실제 VC 발급 → `vc_id` 저장 → 온체인 valid → revoke → revoked → 전체 재시작 후 동일 상태 확인.

## 최종 판단

- “Orchestrator가 없으면 VC를 서버에서 못 쓰나?” → **아니다.** 최초 프로비저닝 수단일 뿐 런타임 데이터면이 아니다.
- “기능을 다 유지할 수 있나?” → **현재 구현의 발급·온체인 상태 확인·폐기 기능은 유지 가능하다.** TAS/Issuer/CAS/Holder와 상태 저장소를 함께 배포해야 한다.
- “그냥 코드만 가져가면 되나?” → **아니다.** 코드/JAR + DB + Besu 원장/계약 + DID/개인키 월렛 + FaceLicense 프로필 + holder data/secrets가 하나의 배포 단위다.
- “지금 바로 서버에서 빌드 가능한가?” → **아니다.** `fm-holder`의 OpenDID DTO 의존성 누락으로 clean compile이 실패하며, 이것이 첫 번째 배포 차단점이다.

## 1차 출처

- [OpenDID Orchestrator V2.0.0 README](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/README_ko.md)
- [OpenDID Orchestrator V2.0.0 설치·운영 가이드](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/docs/installation/OpenDID_orchestrator_InstallationAndOperation_Guide_ko.md)
- [OpenDID Orchestrator V2.0.0 서버 기동 구현](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/source/did-orchestrator-server/src/main/java/org/omnione/did/orchestrator/service/OrchestratorServiceImpl.java)
- [OpenDID TAS V2.0.0 설치 가이드](https://github.com/OmniOneID/did-ta-server/blob/V2.0.0/docs/installation/OpenDID_TAServer_Installation_Guide.md)
- [OpenDID Issuer V2.0.0 설치 가이드](https://github.com/OmniOneID/did-issuer-server/blob/V2.0.0/docs/installation/OpenDID_IssuerServer_Installation_Guide.md)
- [OpenDID CAS V2.0.0 설치 가이드](https://github.com/OmniOneID/did-ca-server/blob/V2.0.0/docs/installation/OpenDID_CAServer_Installation_Guide.md)
- [OpenDID V2.0.0 Apache-2.0 license](https://github.com/OmniOneID/did-orchestrator-server/blob/V2.0.0/LICENSE)
- FaceMarket 및 `fm-holder` 로컬 소스(이 문서 내 파일 링크 참조)

Apache-2.0 소스·산출물 이관 시 라이선스와 NOTICE 조건을 유지해야 한다. 이는 기술 조사이며 법률 자문이 아니다.
