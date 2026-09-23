# OpenDID DB 복구 — 오케스트레이터 없이

2026-09-23, OpenDID 스택이 쓰던 Supabase 프로젝트가 **백업 없이 삭제**되면서 `tas`·`issuer`·`cas`
세 데이터베이스가 통째로 사라졌다. 이 문서는 그때 실제로 돌린 복구 절차다. 같은 일이 생기면
여기서부터 시작하면 된다 — 원인 추적에 쓴 두 시간은 이미 지불했다.

## 0. 먼저 알아야 할 것 — 무엇이 어디 사는가

| 자산 | 사는 곳 | DB 가 날아가도 |
|---|---|---|
| 엔티티 지갑(tas·issuer·cas·wallet·verifier) | **SSM** `WALLET_*_B64` | ✅ 무사 |
| 엔티티 키 | SSM `OMNIONE_TAS_KEY`·`OMNIONE_ISSUER_KEY` | ✅ 무사 |
| DID 앵커·VC 메타 | **OmniOne Chain**(외부) | ✅ 무사 |
| 모델(유저) 지갑·DID 문서·발급 멱등 기록 | **EFS** `fs-0bebd2e1…` (`/opt/opendid/data`) | ✅ 무사 |
| 엔티티 **등록 레코드**·VC 플랜·발급 이력 | tas/issuer/cas **DB** | ❌ 사라짐 |

핵심은 **키가 DB 에 없다**는 것이다. 지갑이 살아 있으면 DID 는 그대로 복원된다 —
DID 문서는 지갑에서 결정적으로 파생되기 때문이다. 그래서 "기존 VC 가 전부 무효가 된다"는
최악의 시나리오는 오지 않는다.

## 1. DB 재생성

OpenDID 는 서버마다 **별도 데이터베이스**를 쓴다(`jdbc:.../tas` 식). 테이블은 서버가 첫 부팅에
스스로 만든다 — 스키마를 옮길 필요가 없다.

```bash
psql "$DB_URL" -c 'CREATE DATABASE tas'
psql "$DB_URL" -c 'CREATE DATABASE issuer'
psql "$DB_URL" -c 'CREATE DATABASE cas'
```

Supabase 라면 **세션 풀러(5432)** 로도 `CREATE DATABASE` 가 된다(2026-09-23 실측).
`deploy/opendid/setup-supabase-dbs.sh` 주석의 "직결만 가능" 은 이 경로에서는 사실이 아니었다.

그다음 SSM 세 줄을 새 DB 로 돌린다 — `OPENDID_DB_HOST`·`OPENDID_DB_USER`·`OPENDID_DB_PASSWORD`.
기존 파라미터를 `--overwrite` 하면 copilot 태그가 유지된다(새로 만들면 태그가 없어 태스크가 못 읽는다).

## 2. DID 문서 복원 — 오케스트레이터는 필요 없다

등록 API 는 `${setup.path}/TA/tas.did` 같은 **파일**을 읽는다. 그 파일은 오케스트레이터가
만드는 게 아니라, 오케스트레이터가 **CLI 도구를 호출해서** 만든다(`did-orchestrator-server`
의 `OrchestratorServiceImpl.createDidDocument` → `tool/create_did_doc.sh`). 그래서 무거운
오케스트레이터 대신 CLI 도구 JAR 하나만 있으면 된다.

```bash
gh release download V2.0.0 --repo OmniOneID/did-cli-tool-server --pattern '*.jar'

# SSM 지갑 복원 (tas→TA, issuer→Issuer, cas→CA, holder→Wallet)
aws ssm get-parameter --name .../WALLET_TAS_B64 --with-decryption \
  --query Parameter.Value --output text | base64 -d > TA/tas.wallet

# DID 문서 생성 — 지갑이 같으면 DID 문서도 같다
J=did-cli-tool-server-2.0.0.jar
echo "$PW" | java -jar $J did createDid -m TA/tas.wallet -f TA/tas.did \
  -id did:omn:tas -ci did:omn:tas -mi assert -ai auth -ki keyagree -ii invoke -p
# 나머지 엔티티는 -ii invoke 없이, controller 는 전부 did:omn:tas
#   CA/cas.did → did:omn:cas · Issuer/issuer.did → did:omn:issuer
#   Wallet/wallet.did → did:omn:wallet · Verifier/verifier.did → did:omn:verifier
```

DID 문자열은 고정 관용값이다(`did:omn:{tas,issuer,cas,wallet,verifier}`). 확신이 없으면
EFS 의 발급 기록에서 직접 확인할 수 있다:

```bash
grep -o 'did:omn:[a-zA-Z0-9]*' /opt/opendid/data/issue-idempotency/*.result | sort -u
```

⚠️ **verifier 지갑은 원래 SSM 에 없었다**(런타임에 안 쓰므로). 2026-09-23 복구 때 새로 만들어
`WALLET_VERIFIER_B64` 로 저장했다. 등록 루프는 verifier 까지 돌기 때문에 이 파일이 없으면
`SSRVTRA14008 Failed to register quick entity` 로 막힌다.

## 3. 파일을 EFS 로 — 청크 전송하지 마라

ECS exec 은 명령·응답이 길면 **조용히 잘린다**. `echo <base64chunk> >> file` 을 반복하면
중간이 사라진 파일이 만들어지고(실측: 1778바이트 파일이 954바이트로), md5 를 비교하기 전에는
성공한 것처럼 보인다.

S3 presigned URL + 컨테이너의 curl 을 써라. 한 번에 받고, md5 로 확인하고, 객체는 지운다.

```bash
aws s3 cp TA/tas.did "s3://$BUCKET/opendid-recovery/TA/tas.did"
U=$(aws s3 presign "s3://$BUCKET/opendid-recovery/TA/tas.did" --expires-in 600)
aws ecs execute-command --cluster "$C" --task "$T" --container opendid --interactive \
  --command "sh -c 'mkdir -p /opt/opendid/data/setup/TA && curl -sS -o /opt/opendid/data/setup/TA/tas.did \"$U\" && md5sum /opt/opendid/data/setup/TA/tas.did'"
aws s3 rm "s3://$BUCKET/opendid-recovery/" --recursive
```

`while read` 루프 안에서 `aws ecs execute-command` 를 돌리면 **stdin 을 뺏겨 한 번만 실행된다.**
`< /dev/null` 을 붙이거나 `for` 로 돌려라.

## 4. 등록

`SETUP_PATH`·`SETUP_BASE_URL` 이 태스크에 있어야 한다(매니페스트에 선언돼 있고,
`test_opendid_manifest_declares_setup_path` 가 지킨다). 그다음 컨테이너 안에서:

```bash
curl -X POST http://localhost:8090/tas/admin/v1/ta/register-simple \
  -H 'Content-Type: application/json' -d '{"serverUrl":"http://localhost:8090"}'
curl -X POST http://localhost:8090/tas/admin/v1/entities/register-simple \
  -H 'Content-Type: application/json' -d '{}'
```

확인은 API 말고 **DB 로** 한다(응답이 커서 exec 이 끊긴다):

```sql
select name, did, role, status from public.entity;   -- tas DB, 4행 COMPLETED
select name, did, status from public.tas;            -- 1행 COMPLETED
```

체인에 같은 DID 가 이미 앵커돼 있어도 **거절되지 않는다** — 지갑이 같으면 DID 문서가 같기 때문이다.
여기서 키 불일치 에러가 난다면 지갑 가정이 틀린 것이니 멈추고 다시 봐라.

## 5. VC 플랜(facelicense-v2)

`scripts/issuer-provision-facelicense-ecs.sh` 가 정석이지만, **GET 응답이 커서 exec 세션이
끊기면 실패로 오판한다**(POST 는 이미 성공한 뒤다). 그때는 DB 로 상태를 보고 남은 것만 직접 POST 한다.

```sql
-- issuer DB
select count(*) from namespace;      -- 1
select id, vc_schema_id from vc_schema;
select vc_plan_id from issue_profile;
-- tas DB
select count(*) from list_vc_plan;   -- 1  ← TAS 가 플랜을 인식한다는 신호
```

`list_vc_plan` 이 0 이면 발급이 `unknown plan 'facelicense-v2'` 로 죽는다(2026-09-13 사고와 같은 증상).

## 6. E2E 확인

`deploy/opendid/smoke.sh` 는 docker 를 전제한 단일서버용이라 ECS 에서는 못 쓴다. 대신 api 와
같은 경로를 컨테이너 안에서 직접 태운다 — HMAC 서명은 `server/app/holder_client.py` 와 동일하게
`v1\nPOST\n<path>\n<ts>\n<nonce>\n<sha256(body)>` 를 `OPENDID_HOLDER_HMAC_SECRET` 으로 HMAC-SHA256.

```
POST /holder/models/<smoke-id>/wallet        {}                    → 201
POST /holder/models/<smoke-id>/register-did  {}                    → 200  userDid 반환
POST /holder/models/<smoke-id>/issue-vc      {plan, idempotencyKey, claims} → 200  vcId 반환
POST /holder/vc/verify                       {"vcId": ...}         → 200  verified:true, onChain:true
```

모델 id 는 `fm-smoke-<epoch>` 처럼 버리는 값을 쓴다. **바디는 base64 로 컨테이너에 넣고
`--data-binary @file` 로 보내라** — 셸 이스케이프가 따옴표를 건드리면 바디가 달라져서
HMAC 이 401 로 떨어진다(실측).

`onChain:true` 가 나오면 복구가 끝난 것이다.

## 7. 함정 모음

- **오토스케일러와 싸우지 마라.** `OPENDID_AUTOSCALE=on` 이면 수요가 없을 때 서비스를 0 으로
  내린다. 복구 작업은 `run-task` 로 **서비스 밖 독립 태스크**를 띄워서 해라 — 오토스케일러는
  서비스의 desiredCount 만 본다. `--enable-execute-command` 를 잊지 말 것.
- exec 에이전트가 `STOPPED` 면 태스크를 새로 띄워야 한다. 서비스에 `--enable-execute-command`
  를 켜도 **이미 돌던 태스크에는 적용되지 않는다**.
- CAS 포트는 **8094** 다(8092 아님). 헬스체크를 8092 로 하면 멀쩡한 서비스를 죽은 줄 안다.
- holder(8100) 의 `/actuator/health` 는 **401 이 정상**이다(HMAC 필터). 살아 있다는 뜻이다.
- 수동 태스크 정의 리비전은 다음 copilot 배포 때 덮인다. 설정은 반드시 매니페스트로 넣어라.
