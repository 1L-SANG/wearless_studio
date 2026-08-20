# FaceMarket OpenDID 단일 서버 이전 Implementation Plan

> **Execution gate:** Holder build 복구와 비파괴 로컬 검증 외 운영 이전은 승인된 실행 세션에서만 수행한다. 기존 모델 freeze, 얼굴 자산 purge, 운영 재등록 cutover는 `docs/research/2026-08-20-facemarket-external-contract-and-biometric-legal-gates.md`의 모든 선행 조건을 통과하기 전에는 실행하지 않는다.

**Goal:** 기존 OpenDID DB·Besu·wallet 상태를 보존하면서 FaceMarket VC 데이터면을 단일 Linux 서버로 이전하고, 전체 인프라를 총 3대만 운영한다.

**Architecture:** API/SAM 서버는 현행 유지. 세 번째 서버에서 PostgreSQL·Besu와 TAS·Issuer·CAS·`fm-holder`를 localhost로 운영한다. Orchestrator는 런타임에서 제거한다.

**Design:** `docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md`

## Global constraints

- 실제 운영 데이터 export/restore, 서버 접속, 방화벽 변경, 환경변수 배포는 명시적 운영 단계에서만 실행한다.
- wallet, DID, private key, DB dump, Besu archive, Holder data는 Git과 빌드 이미지에 포함하지 않는다.
- PostgreSQL 16.4, Besu 25.5.0, OpenDID V2.0.0, Java 21을 고정한다.
- 소스 상태는 대상 검증이 끝날 때까지 삭제하지 않는다.
- 기존 API/SAM 배포 구조는 수정하지 않는다.
- 현재 없는 `fm-holder/data`를 복구된 것으로 가정하지 않는다.

---

### Task 1: Holder clean build 복구

**Files:**

- Modify: `services/fm-holder/build.gradle`
- Create: `services/fm-holder/src/main/java/org/omnione/did/base/datamodel/data/*.java`
- Modify: `services/fm-holder/README.md` 또는 기존 서비스 문서가 없으면 `services/fm-holder/NOTICE`
- Test: `services/fm-holder/src/test/**`

- [ ] **Step 1: 실패를 고정한다**

Run:

```bash
cd services/fm-holder
./gradlew clean test
```

Expected: `org.omnione.did.base.datamodel.data.*` 누락으로 `compileJava` 실패. 실패 로그는 클래스 목록 확인에만 사용하고 저장소에 커밋하지 않는다.

- [ ] **Step 2: V2.0.0 서버 datamodel을 소스 의존성으로 고정한다**

OmniOneID 공식 V2.0.0 소스의 `org/omnione/did/base/datamodel/data` package를 동일 package로 포함한다. 전체 package를 한 버전으로 가져와 transitive DTO 누락을 방지한다. Apache-2.0 license/NOTICE를 보존한다.

금지:

- TA fat JAR 전체를 Holder compile dependency로 추가
- `BOOT-INF/classes`를 수동 classpath로 사용
- 버전이 다른 SDK/서버 DTO 혼합

- [ ] **Step 3: clean build와 기존 테스트를 통과시킨다**

Run:

```bash
cd services/fm-holder
./gradlew clean test
```

Expected: `BUILD SUCCESSFUL`, test failure 0.

- [ ] **Step 4: 실행 JAR을 검증한다**

Run:

```bash
cd services/fm-holder
./gradlew bootJar
jar tf build/libs/fm-holder-0.1.0.jar | rg 'BOOT-INF/classes/kr/wearless/fmholder|BOOT-INF/classes/org/omnione/did/base/datamodel/data/Proof.class'
```

Expected: Holder 앱 클래스와 필요한 OpenDID DTO가 JAR 안에 존재.

---

### Task 2: 대상 서버용 설정과 서비스 정의

**Files:**

- Create: `deploy/opendid/README.md`
- Create: `deploy/opendid/infra.compose.yml`
- Create: `deploy/opendid/env.example`
- Create: `deploy/opendid/config/ta.yml`
- Create: `deploy/opendid/config/issuer.yml`
- Create: `deploy/opendid/config/cas.yml`
- Create: `deploy/opendid/config/holder.yml`
- Create: `deploy/opendid/systemd/opendid-tas.service`
- Create: `deploy/opendid/systemd/opendid-issuer.service`
- Create: `deploy/opendid/systemd/opendid-cas.service`
- Create: `deploy/opendid/systemd/fm-holder.service`

- [ ] **Step 1: 디렉터리 계약을 고정한다**

대상 경로:

```text
/opt/opendid/
  jars/{TA,Issuer,CA,Holder}/
  config/
  secrets/{TA,Issuer,CA,Wallet}/
  state/holder/
  state/migration/
```

비밀 디렉터리는 운영 계정만 읽을 수 있게 한다. JAR는 공식 V2.0.0 release와 이 저장소에서 빌드한 Holder만 사용한다.

- [ ] **Step 2: PostgreSQL/Besu Compose를 작성한다**

요구사항:

- PostgreSQL `16.4`, Besu `25.5.0` pin
- 기존 volume 복원용 named volume
- localhost bind 또는 host firewall로 외부 접근 차단
- PostgreSQL/Besu healthcheck
- 자동 restart
- Orchestrator service 없음

- [ ] **Step 3: Linux 절대경로용 entity config를 작성한다**

모든 macOS `/Users/...` 경로를 `/opt/opendid/...`로 교체한다. 서비스 URL은 localhost를 유지한다.

```yaml
blockchain:
  file-path: /opt/opendid/secrets/.../blockchain.properties
setup:
  base-url: http://127.0.0.1
  path: /opt/opendid/jars
tas:
  url: http://127.0.0.1:8090
```

Holder config는 provider wallet 경로와 pepper/password를 환경변수로만 받는다.

- [ ] **Step 4: systemd unit을 작성한다**

의존 순서와 restart policy를 선언한다. 각 unit의 `ExecStart`는 다음 형태만 사용한다.

```text
java -jar <pinned jar> --server.port=<port> --spring.config.additional-location=file:<config>
```

로그에는 wallet password나 private key가 출력되지 않아야 한다.

- [ ] **Step 5: 정적 검증한다**

Run:

```bash
docker compose -f deploy/opendid/infra.compose.yml config
rg -n '/Users/|192\.168\.|omnioneopendid12|fm-holder-dev-pepper' \
  deploy/opendid/config deploy/opendid/systemd deploy/opendid/env.example
```

Expected: Compose config 성공. 마지막 검색은 문서의 금지 예시 외 실제 설정에서 0건.

---

### Task 3: 상태 export 도구

**Files:**

- Create: `deploy/opendid/export-state.sh`
- Create: `deploy/opendid/inventory-state.sh`
- Test: `deploy/opendid/test-export-state.sh`

- [ ] **Step 1: read-only inventory를 작성한다**

출력에는 다음만 포함한다.

- 컨테이너/volume 존재 여부와 버전
- DB 이름, 크기, public table 수
- FaceLicense namespace/schema/plan 개수
- entity/issuer/CAS 개수
- wallet/DID/config 파일 존재 여부
- Holder data 존재 여부

비밀번호, private key, PII, wallet 내용은 출력하지 않는다.

- [ ] **Step 2: 명시적 export를 작성한다**

`export-state.sh <new-empty-output-dir>` 계약:

1. Holder/TAS/Issuer/CAS/Besu가 정지했는지 확인하고 아니면 실패한다.
2. PostgreSQL `pg_dumpall`을 생성한다.
3. Besu volume을 read-only로 archive한다.
4. entity wallet/DID와 blockchain 설정을 archive한다.
5. Holder data가 있으면 archive하고, 없으면 manifest에 `holder_data=missing`을 기록한다.
6. archive 권한을 `0600`으로 제한한다.
7. `sha256` manifest를 생성한다.

스크립트는 기존 디렉터리를 덮어쓰지 않는다.

- [ ] **Step 3: fixture 검증을 작성한다**

임시 PostgreSQL/Besu volume과 가짜 wallet 파일을 사용해 다음을 확인한다.

- output dir 덮어쓰기 차단
- dump/archive/checksum 생성
- private file 권한
- Holder data missing 기록
- source volume 변경 없음

- [ ] **Step 4: 로컬 dry-run inventory를 실행한다**

Run:

```bash
deploy/opendid/inventory-state.sh
bash -n deploy/opendid/export-state.sh deploy/opendid/inventory-state.sh
```

Expected: 현재 관찰값과 일치하고 비밀값이 출력되지 않음.

---

### Task 4: 대상 restore 도구

**Files:**

- Create: `deploy/opendid/restore-state.sh`
- Test: `deploy/opendid/test-restore-state.sh`

- [ ] **Step 1: checksum 선검증을 구현한다**

하나라도 불일치하면 volume/DB를 건드리기 전에 실패한다.

- [ ] **Step 2: fresh target 전용 restore를 구현한다**

`restore-state.sh <archive-dir> --apply` 계약:

- `--apply` 없으면 계획만 출력한다.
- 대상 PostgreSQL/Besu volume이 비어 있지 않으면 실패한다.
- Besu archive를 빈 volume에 복원한다.
- PostgreSQL 16.4를 기동하고 `pg_dumpall`을 복원한다.
- wallet/DID/config는 `/opt/opendid/secrets`에 `0600`으로 복원한다.
- Holder data가 있으면 `/opt/opendid/state/holder`에 복원한다.
- 소스 archive는 수정하지 않는다.

- [ ] **Step 3: round-trip 테스트를 작성한다**

임시 volume에서 export → restore 후 다음을 비교한다.

- DB/table/fixture row
- Besu fixture file checksum
- wallet/DID checksum
- Holder data missing/present 양쪽

- [ ] **Step 4: 검증한다**

Run:

```bash
bash -n deploy/opendid/restore-state.sh
deploy/opendid/test-export-state.sh
deploy/opendid/test-restore-state.sh
```

Expected: 모두 exit 0.

---

### Task 5: 운영 런북과 preflight

**Files:**

- Create: `docs/runbooks/facemarket-opendid-single-server.md`
- Modify: `docs/runbooks/opendid-besu-clock.md`

- [ ] **Step 1: source cutover 런북을 작성한다**

순서:

1. 쓰기 프로세스 중지
2. PostgreSQL/Besu/wallet/Holder 상태 export
3. checksum 확인
4. source 상태 보존

- [ ] **Step 2: target restore/boot 런북을 작성한다**

순서:

1. infra restore/health
2. TAS health
3. Issuer/CAS health
4. Holder health
5. FaceLicense plan/contract 상태 확인
6. Orchestrator `:9001` closed 확인

- [ ] **Step 3: Holder 상태 공백 처리 규칙을 적는다**

기존 VC는 조회 가능하더라도 모델 wallet이 없으면 폐기 서명이 불가능하다고 표시한다. 기존 데이터를 자동으로 지우거나 `vc_id`를 변경하지 않는다.

- [ ] **Step 4: 시계 장애 런북에 Linux 대상 점검을 추가한다**

Besu 시작 전 host UTC/NTP 상태 확인과 재시작 순서를 기존 런북에 추가한다.

---

### Task 6: 로컬 통합 검증

**Files:**

- Modify: 필요 시 `scripts/opendid-provision.sh`
- Modify: 필요 시 `scripts/issuer-provision-facelicense.sh`
- Test: `deploy/opendid/smoke.sh`

- [ ] **Step 1: 상태 조회 smoke를 작성한다**

검증 대상:

- PostgreSQL/Besu/TAS/Issuer/CAS/Holder health
- FaceLicense plan 존재
- contract address와 chain ID 일치
- Orchestrator 미실행

- [ ] **Step 2: 실제 VC lifecycle smoke를 작성한다**

새로운 일회성 모델 ID로 다음을 수행한다.

```text
wallet -> register-did -> issue-vc -> verify(valid)
       -> revoke-vc -> verify(revoked)
```

응답 본문 전체 VC와 키는 로그에 남기지 않고 ID/status만 출력한다.

- [ ] **Step 3: 재시작 지속성을 검증한다**

전체 Server 3 서비스를 재시작한 뒤 동일 VC가 revoked로 남고 새 VC 발급도 성공해야 한다.

- [ ] **Step 4: 전체 검증을 실행한다**

Run:

```bash
cd services/fm-holder && ./gradlew clean test
docker compose -f deploy/opendid/infra.compose.yml config
bash -n deploy/opendid/*.sh
deploy/opendid/smoke.sh
```

Expected: 모든 명령 성공, Orchestrator 없이 lifecycle 완주.

---

### Task 7: 대상 서버 cutover

이 Task는 외부 서버 접속, 운영 데이터 이동, 방화벽/환경변수 변경 권한이 확보된 배포 창에서만 실행한다.

- [ ] **Step 1: source state archive 생성**
- [ ] **Step 2: archive를 대상 서버로 안전하게 전송하고 checksum 확인**
- [ ] **Step 3: 대상 fresh volume에 restore**
- [ ] **Step 4: Server 3 내부 smoke와 재시작 검증**
- [ ] **Step 5: Security Group/방화벽에서 Server 1 → `:8100`만 허용**
- [ ] **Step 6: Server 1에 `OPENDID_HOLDER_URL=http://<private-host>:8100` 설정**
- [ ] **Step 7: 실제 FaceMarket 신규 라이선스에서 `vc_id` 저장 확인**
- [ ] **Step 8: 누락 VC dry-run**

Run:

```bash
cd server
.venv/bin/python -m scripts.retry_pending_face_vcs
```

Expected: 대상 목록만 출력. `--apply`는 데이터 범위를 확인한 뒤 별도 실행한다.

## Completion evidence

- Holder clean build/test 로그
- export/restore round-trip 테스트 로그
- DB/Besu/wallet checksum manifest
- 대상 health 결과
- 신규 VC issue/valid/revoke/revoked 결과
- 전체 재시작 후 동일 상태 결과
- `:9001` closed와 외부 포트 차단 확인

## Stop conditions

- Holder build가 실패하면 배포 자산 작업으로 넘어가지 않는다.
- DB dump와 Besu/wallet snapshot 시점이 일치하지 않으면 이전하지 않는다.
- contract address/chain ID가 복원 전후 다르면 API cutover를 하지 않는다.
- 신규 VC lifecycle 또는 재시작 지속성 중 하나라도 실패하면 `OPENDID_HOLDER_URL`을 변경하지 않는다.
