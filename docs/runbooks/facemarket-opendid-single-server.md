# 런북 — FaceMarket OpenDID 단일 서버 이전 (cutover / restore)

기존 OpenDID DB·Besu·wallet 상태를 보존하면서 VC 데이터면을 세 번째 Linux 서버 한 대로
옮길 때 보는 문서. 인프라는 총 3대(API/SAM 2대 유지 + OpenDID 1대)로 운영한다.

대상 서버에서 PostgreSQL·Besu와 TAS·Issuer·CAS·`fm-holder`를 전부 localhost로 돌리고,
Orchestrator(`:9001`)는 런타임에서 제거한다.

- 스크립트: `deploy/opendid/{inventory,export,restore}-state.sh`
- 설정/유닛: `deploy/opendid/{config,systemd,infra.compose.yml,env.example}`
- 대상 디렉터리 계약: `/opt/opendid/{jars,config,secrets,state/holder,state/migration}`

## 불변 규칙 (먼저 읽는다)

- **소스 상태는 대상 검증이 끝날 때까지 절대 삭제하지 않는다.**
- wallet / DID / private key / DB dump / Besu archive / Holder data는 Git·빌드 이미지에 넣지 않는다.
- PostgreSQL `16.4`, Besu `25.5.0`, OpenDID `V2.0.0`, Java 21 고정.
- contract address / chain ID가 복원 전후 다르면 API cutover를 **하지 않는다**.
- 스크립트 출력에 비밀번호·키·PII를 남기지 않는다. `export-state.sh`는 ID/status만 찍는다.

포트 지도: PostgreSQL `5432`, Besu RPC `8545`, TAS `8090`, Issuer `8091`, Verifier `8092`,
API-server `8093`, CAS `8094`, Holder `8100`, Orchestrator `9001`(제거 대상).

---

## 0. 사전 조회 (read-only)

바꾸는 것 없이 소스 상태만 확인한다. 비밀값은 출력되지 않는다.

```bash
deploy/opendid/inventory-state.sh
```

확인 포인트:

- postgres/besu 컨테이너·volume 존재, 이미지 버전(`postgres:16.4`, `besu:25.5.0`)
- DB 8종(cas/issuer/lss/omn/postgres/tas/verifier/wallet) 크기·public table 수
- `tas.entity` / `issuer.issuer` / `cas.cas` 개수
- FaceLicense: namespace `kr.wearless.facelicense`, vc_schema `facelicense`,
  plan `vcplanface0000000001` 각 1건
- Holder data 존재 여부(현재 소스는 `missing`)

> 소스 컨테이너/volume 이름이 다르면 `OPENDID_PG_CONTAINER`, `OPENDID_BESU_CONTAINER`,
> `OPENDID_PG_VOLUME`, `OPENDID_BESU_VOLUME`로 덮어쓴다.
> 관측 기본값: `postgre-opendid`, `opendid-besu-node`,
> `postgre_postgre_opendid_data`, `besu_besu_opendid_data`. superuser는 자동 탐지(`omn`).

---

## 1. 소스 cutover — 상태 export

**순서를 지킨다: 쓰기 프로세스 중지 → export → checksum → 소스 보존.**

### 1-1. 쓰기 프로세스 중지 (writer quiesce)

Holder/TAS/Issuer/CAS/Besu를 멈춘다. PostgreSQL은 **켠 채로 둔다**(dump 소스).

```bash
# 엔티티 Java 서비스 중지 (소스 환경에 맞게)
sudo systemctl stop fm-holder opendid-cas opendid-issuer opendid-tas 2>/dev/null || true
# Besu 중지 (컨테이너 이름은 소스 기준)
docker stop opendid-besu-node
```

확인 — 아래 포트가 전부 닫혀 있어야 한다(열려 있으면 export가 거부한다):

```bash
for p in 8090 8091 8094 8100 8545; do
  (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo "STILL OPEN: $p" || echo "closed: $p"
done
```

### 1-2. export (pg / besu / wallet / holder 한 번에)

```bash
# 소스에 wallet/DID/blockchain 설정이 있는 경로를 가리킨다
export OPENDID_SECRETS_DIR=/opt/opendid/secrets       # 소스 실제 경로로
export OPENDID_HOLDER_DATA_DIR=/opt/opendid/state/holder

deploy/opendid/export-state.sh /opt/opendid/exports/$(date -u +%Y%m%dT%H%M%SZ)
```

`export-state.sh`가 하는 일(계약):

1. Holder/TAS/Issuer/CAS/Besu가 살아 있으면 **거부하고 종료**.
2. `pg_dumpall`(roles + 전체 DB) → `pg_dumpall.sql` (0600).
3. Besu volume을 read-only 마운트로 archive → `besu-volume.tgz` (0600).
4. wallet/DID/blockchain 설정 → `secrets.tgz` (0600).
5. Holder data가 있으면 `holder-data.tgz`, 없으면 manifest에 `holder_data=missing`.
6. `metadata.txt`(비밀 없음) + `MANIFEST.sha256` 생성.
7. 기존 출력 디렉터리는 **덮어쓰지 않는다**(이미 있으면 거부).

> DB dump와 Besu/wallet snapshot **시점이 일치해야** 한다. 1-1에서 writer를 멈춘 뒤
> 한 번에 export하는 이유다. 중간에 서비스를 다시 켜지 않는다.

### 1-3. checksum 확인

```bash
OUT=/opt/opendid/exports/<위 타임스탬프>
# Linux
sha256sum -c "$OUT/MANIFEST.sha256"
# macOS 소스라면
( cd "$OUT" && while read -r h f; do echo "$(shasum -a256 "$f" | awk '{print $1}')  $f"; done < MANIFEST.sha256 )
grep -E '^holder_data=' "$OUT/metadata.txt"
```

전부 OK가 아니면 **여기서 멈춘다**. 전송/복원으로 넘어가지 않는다.

### 1-4. 소스 보존

- export 디렉터리를 대상 서버로 전송하고(전송 후 checksum 재확인),
  **소스 컨테이너·volume은 그대로 둔다**. 대상 검증(3장)이 끝나기 전에는 삭제·재기동 금지.
- 소스는 롤백 경로다. cutover 실패 시 소스를 다시 켜는 것이 복구다.

---

## 2. 대상 restore / boot

fresh(빈) 대상 volume에만 복원한다. `restore-state.sh`는 비어 있지 않은 volume을 거부한다.

### 2-1. 인프라 restore + health

```bash
ARCHIVE=/opt/opendid/exports/<전송된 디렉터리>

# 계획만 먼저 (checksum 선검증 포함, 아무것도 바꾸지 않음)
deploy/opendid/restore-state.sh "$ARCHIVE"

# 실제 복원
deploy/opendid/restore-state.sh "$ARCHIVE" --apply
```

`restore-state.sh`가 하는 일(계약):

- `MANIFEST.sha256`을 **먼저 전량 검증**. 하나라도 불일치면 volume/DB를 건드리기 전에 종료.
- 대상 PostgreSQL/Besu volume이 비어 있지 않으면 거부.
- Besu archive를 빈 volume에 물리 복원.
- PostgreSQL `16.4`를 빈 volume에 기동해 `pg_dumpall`을 논리 복원.
  (복원 로그의 `role "..." already exists`는 bootstrap superuser와 겹쳐서 나는 **정상** 메시지다.)
- wallet/DID/config를 `/opt/opendid/secrets`에 `0600`으로 복원.
- Holder data가 archive에 있으면 `/opt/opendid/state/holder`에 복원.
- **소스 archive는 읽기만 한다(수정 없음).**

복원 후 안정 인프라 named volume로 기동하고 health 확인:

```bash
docker compose -f deploy/opendid/infra.compose.yml \
  --env-file /opt/opendid/secrets/TA/postgres.env up -d
docker compose -f deploy/opendid/infra.compose.yml ps         # postgres/besu healthy
curl -fsS -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' \
  http://127.0.0.1:8545 | grep -o '"result":"[^"]*"'          # chainId 확인
docker exec opendid-postgres pg_isready -U omn -d tas
```

> Besu 시작 전에 host UTC/NTP를 반드시 확인한다 — `docs/runbooks/opendid-besu-clock.md`
> 의 "Linux 대상 preflight" 참조. 시계 역행은 issue-vc를 150초 hang → 500으로 죽인다.

### 2-2. Java 엔티티 기동 + health (순서 중요)

```bash
sudo install -o opendid -g opendid -m 0640 deploy/opendid/config/*.yml /opt/opendid/config/
sudo install -o root -g root -m 0644 deploy/opendid/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now opendid-tas opendid-issuer opendid-cas fm-holder
```

health를 **아래 순서로** 확인한다:

1. **TAS** `:8090`
   ```bash
   curl -fsS http://127.0.0.1:8090/actuator/health || curl -fsS http://127.0.0.1:8090/
   ```
2. **Issuer** `:8091` / **CAS** `:8094`
   ```bash
   curl -fsS http://127.0.0.1:8091/actuator/health || curl -fsS http://127.0.0.1:8091/
   curl -fsS http://127.0.0.1:8094/actuator/health || curl -fsS http://127.0.0.1:8094/
   ```
3. **Holder** `:8100`
   ```bash
   curl -fsS http://127.0.0.1:8100/actuator/health || curl -fsS http://127.0.0.1:8100/
   ```

### 2-3. FaceLicense plan / contract 상태 확인

```bash
# FaceLicense plan(20자) 이 issuer DB 에 그대로 있는지
docker exec opendid-postgres psql -U omn -d issuer -tAc \
  "SELECT vc_plan_id FROM issue_profile WHERE vc_plan_id='vcplanface0000000001'"
docker exec opendid-postgres psql -U omn -d issuer -tAc \
  "SELECT namespace_id FROM namespace WHERE namespace_id='kr.wearless.facelicense'"

# contract address / chain ID 가 복원 전후 동일한지 (blockchain.properties + on-chain)
grep -E 'contract|chain' /opt/opendid/secrets/TA/blockchain.properties
```

복원 전후 contract address 또는 chain ID가 **다르면 API cutover를 진행하지 않는다**(Stop condition).

### 2-4. Orchestrator `:9001` closed 확인

Orchestrator는 이 서버의 steady-state 서비스가 아니다. 떠 있으면 안 된다.

```bash
(exec 3<>/dev/tcp/127.0.0.1/9001) 2>/dev/null && echo "FAIL: 9001 OPEN" || echo "ok: 9001 closed"
systemctl is-active opendid-orchestrator 2>/dev/null || echo "ok: orchestrator not a unit"
```

`:9001`이 열려 있으면 원인을 제거한 뒤에만 cutover를 계속한다.

---

## 3. Holder 상태 공백 처리 규칙 (중요)

현재 소스 Holder data가 `missing`이라 이전 시 모델 wallet이 함께 넘어오지 않을 수 있다.

- 기존에 발급된 VC는 **조회는 가능**하다(issuer DB의 `vc` / `revoke_vc`, `vc_id` 보존).
- 그러나 해당 모델의 **wallet(개인키)이 없으면 폐기(revoke) 서명을 만들 수 없다.**
  즉 "읽을 수는 있으나 revoke는 불가"인 VC가 생긴다.
- **금지:** 이 공백을 메우려고 기존 VC를 자동으로 삭제하거나 `vc_id`를 바꾸지 않는다.
  새 wallet을 임의로 만들어 기존 `vc_id`에 붙이지도 않는다.
- 처리: 해당 모델은 "revoke 불가" 상태로 **표시만** 하고, 재발급이 필요하면
  새 일회성 모델 ID로 새 VC를 발급한다(기존 레코드는 그대로 둔다).
- `holder-data.tgz`가 실제로 확보되어 복원되면 이 제약은 해소된다. manifest의
  `holder_data=present|missing`가 판단 기준이다.

---

## 4. cutover 완료 게이트 (Stop conditions)

아래 중 하나라도 실패하면 Server 1의 `OPENDID_HOLDER_URL`을 바꾸지 않는다.

- [ ] restore checksum 전량 OK
- [ ] postgres/besu/TAS/Issuer/CAS/Holder health OK
- [ ] FaceLicense plan `vcplanface0000000001` 존재
- [ ] contract address / chain ID 복원 전후 동일
- [ ] 신규 일회성 모델로 issue → valid → revoke → revoked 완주
- [ ] 전체 재시작 후 동일 VC가 revoked 유지 + 신규 발급 성공
- [ ] `:9001` closed, 외부 포트(5432/8545/8090/8091/8094)는 사설망에서도 차단,
      Server 1 → Holder `:8100`만 허용

게이트 통과 후에만 Server 1에 `OPENDID_HOLDER_URL=http://<private-host>:8100`을 설정한다.
소스 상태는 이 시점 이후 신규 라이선스 `vc_id` 저장까지 확인한 뒤 정리한다.
