# FaceMarket 보안 개선 기술 보고서

작성일: 2026-08-20
범위: 현재 저장소에 구현된 FaceMarket의 신원 확인, 개인정보, 라이선스/VC, 정산, 자산, 배포 통제.
방법: 애플리케이션 소스, 마이그레이션, 컨트랙트, 테스트, 기존 OpenDID 배포 계획을 저장소의 1차 근거로 검토했다. 애플리케이션 코드는 수정하지 않았다.

검토 기준: `origin/main` / `aa45aa3561ce82147a9cbb5bfbdfa959d3609950`
작업 브랜치: `codex/facemarket-security-hardening`

2026-08-20 리더가 제공한 최신 검증 근거:

- `cd server && .venv/bin/python -m pytest tests/test_facemarket_identity.py tests/test_facemarket_licenses.py tests/test_facemarket_seller_loop.py tests/test_facemarket_settlement.py tests/test_fm_model_asset_job.py tests/test_face_qc.py tests/test_identity_source.py tests/test_cut_input_authority.py tests/test_detail_page_identity_source.py tests/test_personalization.py tests/test_personalization_dispatcher_guard.py -q` → 196 passed, 2 skipped, 1 warning in 17.30s.
- `pnpm test:frontend` → 906 passed, 0 failed.
- `cd services/fm-holder && ./gradlew clean test` → `org.omnione.did.base.datamodel.data.*` 누락으로 `compileJava`가 정확히 61 errors로 실패.

## 실행 결과 요약

현재 FaceMarket은 로그인한 사용자가 RAON/CX mobile-ID 거래 토큰을 제출하면 재사용 가능한 `verified` FaceMarket 모델 레코드를 받을 수 있음을 증명한다. 그러나 현재 구현은 마켓플레이스의 얼굴 자산이 그 mobile-ID 보유자의 얼굴이라는 점을 증명하지 못한다. 현재 얼굴 통제는 개인화 업로드 사진 3장에 대한 동일인/품질 보조 검사이며, government-ID portrait 검사도 아니고, live selfie liveness도 아니며, 공식 신원 portrait와의 1:1 match도 아니다.

따라서 승인된 개선 목표는 작은 hardening 패치가 아니라 보안 기준을 바꾸는 cutover로 다뤄야 한다.

- `verified`는 CX mobile-ID identity, government-ID portrait, live selfie liveness, 1:1 face match를 모두 요구해야 한다. 실패하거나 통제 수단을 사용할 수 없는 모든 상태는 fail closed여야 한다.
- 기존 `verified` FaceMarket 모델은 모두 `reverification_required`로 전환해야 하며, 활성 라이선스와 생성 사용은 중단해야 한다. Holder가 동작 가능해지면 VC revoke를 실행하도록 큐에 넣어야 한다.
- 기존 identity-bound가 아닌 원본 얼굴과 `fm_model_assets`의 `face_front` / `grid_sedcard` 객체는 삭제하고, pending asset job은 취소하며, 새 identity-bound 흐름에서 다시 업로드하도록 해야 한다.
- VC 개선은 이미 설계된 단일 서버 OpenDID 배포안과 함께 진행해야 한다. 구성은 API server, SAM server, FaceMarket/OpenDID server이며, Orchestrator는 runtime component가 아니다.

다만 이 목표를 바로 기존 데이터에 적용해서는 안 된다. 다음 세 항목은 구현·freeze·purge보다 먼저 닫혀야 하는 시작 블로커다.

1. **외부 계약 확정:** RAON/OACX transaction payload가 정부 ID portrait를 실제로 제공하는지, 별도 liveness와 1:1 matcher가 필요한지 공식 규격·샌드박스 응답·제공사 확인으로 확정한다. 현재 제공된 대화는 “신분증상의 모든 데이터와 CI, 전화번호”를 말하지만 portrait binary, selfie, liveness, face-match API를 명시하지 않는다.
2. **법무·동의·보존 승인:** 정부 ID portrait와 live selfie/liveness 처리를 시작하기 전에 별도 동의문, 처리 목적, 최소 수집, 위탁/국외 이전, 보존·파기, 사고 대응을 법무/개인정보 책임자가 승인한다. 얼굴 특징정보는 현행 시행령상 민감정보에 해당한다. Raw portrait/selfie/video는 생체인식 원본정보로 고위험 처리하고 매칭 결정 직후 삭제하는 것을 기본 hard requirement로 한다. Match score bucket도 자동으로 비식별 정보가 되는 것은 아니므로 법적 필요성과 보존기간이 승인된 경우에만 남기고, 그렇지 않으면 decision과 evidence/version metadata만 남긴다. 민간 FaceMarket에 법정 개인정보 영향평가가 자동 의무라고 단정하지 않고, 출시 전 내부 생체정보 위험평가와 법무 승인을 요구한다.
3. **재등록 준비도:** 계약된 실제 제공자 조합으로 sandbox E2E 등록, fail-closed, raw biometric deletion 증거, 재등록 UI/운영 경로가 모두 준비되기 전에는 기존 모델을 freeze하거나 asset을 삭제하지 않는다.

따라서 지금 단계의 허용 작업은 조사, 설계, 테스트 명세, 비파괴 schema/adapter 준비뿐이다. Production row 변경, object deletion, VC revoke 실행은 별도 cutover gate 전까지 금지한다.

외부 계약과 법무 근거의 상세 판정은 [FaceMarket 외부 계약·생체정보 법무 선행 게이트](./2026-08-20-facemarket-external-contract-and-biometric-legal-gates.md)에 분리했다.

Cutover 이후에는 identity-to-face binding이 명시적이고 fail-closed가 되므로 보안 수준은 객관적으로 강화된다. 다만 등록 과정에는 portrait 획득/캡처, live selfie liveness, 1:1 matching이 추가되므로 느려질 가능성이 높다. 자산을 다시 빌드한 이후의 생성 지연 시간은 동일한 준비된 private face reference를 runtime generation worker가 계속 소비하므로 그대로일 것으로 예상된다. 이 문장은 아키텍처상 판단이며, 측정된 성능 주장이 아니다.

## 현재 상태 근거 표

| 발견 사항 | 현재 근거 | 위험 / 개선 시사점 |
| --- | --- | --- |
| `verified`가 CX token/CI만으로 부여된다 | `server/app/facemarket.py:220-229`는 `IdentityVerifyRequest.token`만 읽고 CX `trans`를 조회한다. `server/app/facemarket.py:248-265`는 `fm_models.status='verified'`를 insert 또는 update한다. `server/app/facemarket.py:283-288`은 `verified: True`를 반환한다. | 현재 `verified`의 의미는 CX CI 보유이며, identity-face binding이 아니다. |
| Raw CX token이 FaceMarket verification log에 저장된다 | `server/app/facemarket.py:267-275`는 `fm_identity_verifications.cx_tx_id = token`을 insert한다. 마이그레이션은 `supabase/migrations/20260709000000_facemarket_core.sql:36-45`에서 이를 `cx_tx_id text not null unique`로 정의한다. | 토큰은 replay-sensitive capability이므로 raw 형태로 보관하지 않아야 한다. 계약상 허용된 irreversible digest 또는 transaction metadata만 저장해야 한다. |
| CX response는 의도적으로 whitelist 처리되지만, birth year는 raw birth에서 잘린다 | `server/app/facemarket.py:236-244`는 `nameMasked`, `birthYear`, `vcType`을 저장한다. 테스트는 `server/tests/test_facemarket_identity.py:135-146`에서 CI가 응답에 없음을 검증한다. | 응답/body 최소수집은 좋은 방향이지만, FaceMarket verification storage에는 여전히 raw token이 남고 face-binding evidence가 없다. |
| 미성년/성인 gate가 FaceMarket enrollment gate가 아니다 | Adult 기록은 FaceMarket commit 이후 non-critical side effect로 실행된다(`server/app/facemarket.py:166-181`, `server/app/facemarket.py:278-281`). `server/app/cx_identity.py:78-118`는 personalization용 보수적 adult parsing을 정의한다. | 목표 정책에서는 미성년 또는 나이 불명 identity가 모델 등록에서 실패하거나 명시적으로 부적격 처리되어야 한다. 현재는 여전히 verified model을 만들 수 있다. |
| 같은 CI가 모델 소유권을 update할 수 있다 | 기존 CI 분기는 `server/app/facemarket.py:253-258`에서 `fm_models.user_id`를 update한다. 테스트는 `server/tests/test_facemarket_identity.py:158-164`에서 같은 사람/new token이 모델 하나를 재사용함을 확인한다. | dedup에는 유용하지만 silent ownership transfer로는 안전하지 않다. account recovery/transfer 절차와 audit가 필요하다. |
| 현재 3-photo QC는 보조 품질/동일인 검사일 뿐이다 | Asset worker는 `server/app/workers/fm_model_asset_job.py:70-91`에서 `m.user_id` 기준 최신 personalization profile을 읽고, `server/app/workers/fm_model_asset_job.py:96-100`에서 세 angle을 요구하며, `server/app/workers/fm_model_asset_job.py:108-122`에서 업로드된 face photo끼리만 비교한다. | government-ID portrait 또는 live liveness capture와 비교하지 않는다. primary binding이 통과한 뒤의 secondary QC로만 남겨야 한다. |
| QC 구현을 사용할 수 없거나 꺼져 있으면 QC가 fail open될 수 있다 | `load_face_qc(s)`는 optional이고, `None`을 반환하면 worker는 `server/app/workers/fm_model_asset_job.py:108-122`에서 `qc_passed`로 계속 진행한다. production manifest는 `copilot/api/manifest.yml:194-197`에서 이를 켜지만, 코드 경로 자체는 fail-open이다. | 목표 상태에서는 QC/liveness/match dependency를 사용할 수 없을 때 fail closed해야 한다. |
| Face asset은 private이지만 unbound derivative다 | `fm_model_assets`는 `supabase/migrations/20260717000000_facemarket_model_assets.sql:1-18`에서 private `face_front`와 `grid_sedcard` key를 저장한다. worker는 `server/app/workers/fm_model_asset_job.py:124-160`에서 이를 쓰고 `assets_status='ready'`로 표시한다. | 기존 unbound originals/derived assets를 삭제하고, verified identity-face binding 이후에만 다시 빌드해야 한다. |
| `assets_source_hash`는 저장되지만 runtime에서 강제되지 않는다 | Hash는 `server/app/workers/fm_model_asset_job.py:128-160`에서 설정된다. Runtime asset resolution은 `server/app/agents/identity_source.py:34-66`에서 `assets_status`와 필수 view만 확인한다. | Source photo가 바뀌거나 purge되어도 runtime이 stale generated asset을 계속 신뢰할 수 있다. Hash/current-source binding을 강제하거나 source mutation 시 asset을 revoke해야 한다. |
| Direct license face upload가 profile-bound 흐름을 우회한다 | `create_license`는 `server/app/facemarket.py:530-567`에서 `face` upload 또는 `profile_id`를 받는다. Direct upload는 `server/app/facemarket.py:584-619`에서 R2에 저장된다. 테스트는 `server/tests/test_facemarket_licenses.py:191-214`에서 direct upload를 다룬다. | real verified model에는 direct face upload를 제거해야 한다. License face는 identity-bound approved asset만 참조해야 한다. |
| Allowed/forbidden terms는 저장·표시되지만 enforcement되지 않는다 | License creation은 `server/app/facemarket.py:608-635`에서 배열을 저장한다. Public verify는 `server/app/facemarket.py:839-873`에서 이를 반환한다. Frontend는 `src/features/analysis/AnalysisForm.jsx:97-110`과 `src/features/verify/PublicVerify.jsx:97-117`에서 표시한다. Request-time license resolution은 `server/app/facemarket.py:1194-1236`에서 use terms를 평가하지 않는다. | Licensed model을 수락하기 전에 machine-checkable use-policy decision을 generation에 포함해야 한다. |
| Model thumbnail route가 biometric derivative 접근에 너무 넓다 | `/models/{model_id}/thumbnail`은 `server/app/facemarket.py:732-769`에서 verified/ready model의 `face_front`를 인증된 seller 누구에게나 반환한다. | 목표 상태에서는 thumbnail을 non-biometric public-safe cover로 제공하거나, owner/authorized-use scope와 short-lived access를 요구해야 한다. |
| Detail-page 흐름에서 catalog selection이 `assetsReady`를 무시할 수 있다 | Analysis selection은 `src/features/analysis/AnalysisForm.jsx:662-732`에서 `hasActiveLicense`를 고려한다. `src/features/analysis/modelSelection.js:10-18`은 active license만으로 real model을 유효 처리한다. Editor panel은 `src/features/editor/EditorPanels.jsx:408-410`에서 `hasActiveLicense && assetsReady`를 요구한다. | 모든 selection/generation entry point를 reverified status, active license, valid VC, policy pass, ready/current assets 요구사항에 맞춰 정렬해야 한다. |
| Detail-page worker가 missing/revoked/dangling licensed face를 faceless success로 downgrade할 수 있다 | Worker docstring은 `server/app/workers/detail_page_job.py:54-67`에서 실패를 face-less generation으로 downgrade한다고 명시한다. Rejected real source는 `server/app/workers/detail_page_job.py:624-627`에서 face ref를 지운다. 테스트는 `server/tests/test_detail_page_license_face.py:255-311`에서 revoked/expired/dangling/missing storage job이 face 없이 성공함을 확인한다. | 선택된 real FaceMarket model에 대해서는 non-licensed/no-face 결과를 조용히 만들지 말고 fail closed해야 한다. |
| Request gate는 status/expiry를 검증하지만 Holder VC check는 best-effort다 | `verify_license`는 `server/app/facemarket.py:1254-1279`에서 local revoked/inactive/expired를 block한다. Holder는 `server/app/facemarket.py:1281-1294`에서 URL, `vc_id`, connectivity가 없으면 skip된다. 테스트는 `server/tests/test_facemarket_seller_loop.py:115-119`에서 unreachable Holder가 통과함을 확인한다. | 목표 상태에서는 Holder availability가 필요하거나, 승인된 degraded mode라도 real-model generation을 block해야 한다. |
| Holder VC issue/revoke는 설정되지 않으면 best-effort/no-op이다 | `_schedule_face_vc`는 `server/app/facemarket.py:1091-1106`에서 Holder URL이 없으면 no-op이다. Non-200 issue는 `server/app/facemarket.py:1126-1160`에서 log 후 return한다. Revoke도 `server/app/facemarket.py:1315-1329`에서 no-op/warn이다. Production manifest는 `copilot/api/manifest.yml:198-199`에서 Holder URL을 비워 둔다. | Holder deployment가 고쳐지고 enforcement되기 전까지 기존 VC/license를 hard runtime control로 신뢰할 수 없다. |
| Holder API에 application-layer auth가 없다 | Holder comment는 `services/fm-holder/src/main/java/kr/wearless/fmholder/api/VcLifecycleController.java:12-20`에서 Python backend가 server-to-server “no auth”로 호출한다고 설명한다. Verify/revoke endpoint는 `services/fm-holder/src/main/java/kr/wearless/fmholder/api/VcLifecycleController.java:34-45`, issue endpoint는 `services/fm-holder/src/main/java/kr/wearless/fmholder/api/IssueController.java:28-37`에 노출된다. | Holder는 private networking 뒤에 두고 mTLS/HMAC/service auth를 추가해야 한다. Public control plane으로 노출해서는 안 된다. |
| Holder deployability가 현재 막혀 있다 | 기존 OpenDID deployment design은 `docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:127-138`에서 Holder clean build failure 61 errors를 기록한다. Production manifest는 `copilot/api/manifest.yml:198-199`에서 Holder URL을 생략한다. | Cutover 중에는 VC revoke queue가 필요하다. Live enforcement는 Holder가 build/deploy된 뒤에야 go green 가능하다. |
| 현재 OpenDID deployment plan은 Orchestrator를 runtime에서 올바르게 제외한다 | Single-server design은 `docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:6-19`에서 3 servers를 정의하고, `docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:21-41`에서 Server 3 FaceMarket/OpenDID components를 보여주며, `docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:43-49`에서 Orchestrator는 bootstrap/recovery only라고 명시한다. | Remediation은 이 target을 재사용해야 한다. API server, SAM server, FaceMarket/OpenDID server를 두고 runtime Orchestrator는 두지 않는다. |
| Settlement는 record-only인데 UI는 settlement completed라고 표시한다 | Contract는 `contracts/FaceMarketSettlement.sol:4-13`에서 coin move가 없고 record-only settlement라고 말한다. Contracts README도 `contracts/README.md:3-17`에서 “audit record, not transfer”라고 반복한다. UI는 `src/features/editor/Editor.jsx:2411-2424`에서 split amount와 함께 “정산 완료”를 표시한다. | 실제 payment rail과 통합하기 전에는 UI copy가 on-chain settlement record/receipt를 말해야 하며 payment 또는 cash settlement로 보이면 안 된다. |
| Settlement simulation은 unbounded이고 idempotency가 약하다 | `/settlements/simulate`는 `server/app/facemarket.py:1042-1088`에서 호출마다 새 UUID payment id를 만든다. Contract duplicate는 `contracts/FaceMarketSettlement.sol:65-81`에서 같은 payment id에 대해서만 revert한다. | Simulation은 admin/dev로 제한하고, rate-limit를 걸며, deterministic idempotency key를 요구해야 한다. |
| Settlement signer는 단일 owner key이며 ownership transfer 운영 정의가 없다 | Chain client는 `server/app/config.py:204-210`에서 settings의 private key 하나를 사용한다. Recorder는 `server/app/facemarket_chain.py:123-150`에서 단일 owner key를 serialize한다. Contract는 `contracts/FaceMarketSettlement.sol:120-123`에서 `transferOwner`를 지원한다. | Production claim 전에 signing을 managed custody/HSM/KMS 또는 문서화된 multisig/admin process로 옮겨야 한다. |
| FaceMarket account deletion/purge가 불완전하다 | Personalization purge는 `server/app/workers/personalization_purge_job.py:100-127`, `server/app/workers/personalization_purge_job.py:144-191`, `server/app/workers/personalization_purge_job.py:205-233`에서 face rows/R2/generations를 삭제한다. FaceMarket `fm_models.user_id`는 `supabase/migrations/20260709000000_facemarket_core.sql:21-34`에서 `on delete set null`이므로, 모델 자체를 삭제하지 않으면 dependent licenses/assets가 남는다. | Models, licenses, assets, queued jobs, VC revocation, settlements privacy retention, audit/legal hold를 포함하는 FaceMarket-specific purge가 필요하다. |

## 승인된 목표 아키텍처와 정책

### 신원 및 생체 등록 정책

`verified`는 다음 네 가지 필수 통제를 모두 통과한 high-assurance 상태가 되어야 한다.

1. 승인된 RAON/OACX 계약 아래에서 CX mobile-ID identity transaction이 성공하고 stable CI를 얻는다.
2. Government-ID portrait를 승인된 공식 채널 또는 contract-defined payload에서 얻는다.
3. 사용자가 live selfie liveness check를 완료한다.
4. Government-ID portrait와 live selfie 사이의 1:1 face match가 승인된 threshold를 통과한다.

Token 누락, portrait 누락, liveness result 누락, face detection 실패, match 실패, biometric service 사용 불가, replay, account mismatch, underage/unknown eligibility state, ambiguous result는 모두 fail closed되어야 하며, `verified`를 만들거나 유지해서는 안 된다.

기존 3-photo same-person QC는 asset creation을 위한 secondary marketplace quality check로는 여전히 유용하다. 다만 primary identity-face binding을 대체할 수 없다.

### 기존 데이터 cutover 정책

기존 FaceMarket model status에는 `reverification_required` 같은 새 상태가 필요하다. 그러나 아래 cutover는 외부 계약·법무 승인·새 enrollment production readiness·dry-run count·business/security/operations sign-off가 모두 완료된 뒤에만 실행한다.

- 현재 `fm_models.status='verified'`인 모든 row를 `reverification_required`로 update한다.
- Active license를 blocked/reverification-required로 표시하고 generation에 사용할 수 없게 한다.
- Pending/running `fm_model_asset_build` job을 취소한다.
- Identity-bound가 아닌 기존 unbound original face upload를 삭제한다.
- `fm_model_assets` row와 `face_front`, `grid_sedcard` private object를 삭제한다.
- 모든 active `vc_id`에 대해 Holder VC revocation을 큐에 넣고, Holder deploy 후 큐를 실행한다.
- Re-verification 성공 후 reupload와 rebuild를 요구한다.

### Runtime generation 정책

Real FaceMarket generation은 하나의 server-side gate에서 아래 조건을 모두 요구해야 한다.

- Model state가 새 정책 기준의 `verified`다.
- Seller가 특정 model/license를 선택했다.
- License가 active이고 unexpired다.
- Holder VC에 도달 가능하며 valid status를 반환한다.
- Requested product/category/context에 대해 allowed/forbidden use policy가 통과한다.
- Required asset이 존재하고, private이며, current이고, 최신 verified enrollment evidence에 bound되어 있다.
- Asset source hash 또는 동등한 binding이 현재 approved source set과 일치한다.
- Pending deletion/revocation/reverification flag가 없다.

하나라도 실패하면 요청은 명확한 FaceMarket-specific error로 실패해야 한다. Virtual model을 조용히 사용하거나, face를 생략하거나, licensed-model success notice를 표시해서는 안 된다.

### 배포 아키텍처

기존 OpenDID single-server deployment design을 사용한다.

- Server 1: 현재 FastAPI API server와 FaceMarket API routes.
- Server 2: 현재 SAM server.
- Server 3: `fm-holder`, TAS, Issuer, CAS, PostgreSQL, Besu를 실행하는 FaceMarket/OpenDID server.
- Orchestrator는 runtime service가 아니다. Bootstrap/recovery artifact 용도로만 사용한다.

해당 설계는 이미 Server 1이 `fm-holder:8100`을 호출하고, OpenDID internal port를 닫으며, Holder를 public으로 노출하지 않아야 한다고 명시한다(`docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:6-19`, `docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:119-125`).

## 기술 변경: 이전 → 이후

| 영역 | 이전 | 이후 |
| --- | --- | --- |
| Model status | CX CI token verification 이후 `verified`가 기록된다(`server/app/facemarket.py:248-265`). | `reverification_required`를 추가한다. CX + government-ID portrait + liveness + 1:1 match가 성공한 뒤에만 `verified`를 기록한다. |
| Existing models | 기존 verified row가 catalog query로 계속 선택 가능하다(`server/app/facemarket.py:323-332`). | 현재 verified row를 모두 `reverification_required`로 batch migrate하고 catalog/generation에서 제외한다. |
| Minor/eligibility | Adult record는 non-critical personalization side effect다(`server/app/facemarket.py:166-181`). | Eligibility를 primary FaceMarket enrollment gate로 만들고, underage/unknown은 fail closed한다. |
| Raw token storage | Raw token이 `fm_identity_verifications.cx_tx_id`로 저장된다(`server/app/facemarket.py:267-275`). | Token digest 또는 official transaction id만 저장한다. RAON/OACX transaction verification 이후 raw token을 삭제한다. |
| Account transfer | 같은 CI가 `user_id`를 조용히 update한다(`server/app/facemarket.py:253-258`). | Recovery/transfer event로 취급하고 authenticated ceremony, audit trail, 필요 시 old session/license revocation을 요구한다. |
| Face source | Asset worker가 최신 personalization profile photos를 읽는다(`server/app/workers/fm_model_asset_job.py:70-91`). | Asset worker는 현재 승인된 identity-bound enrollment asset set만 읽는다. |
| QC availability | QC object가 없으면 asset 생성이 계속된다(`server/app/workers/fm_model_asset_job.py:108-122`). | QC/liveness/match dependency가 없으면 enrollment와 asset creation을 block한다. |
| License upload | Direct face upload 또는 profile reference가 허용된다(`server/app/facemarket.py:530-567`). | Verified real-model license에서는 direct face upload를 제거하고, license가 bound model asset만 참조하게 한다. |
| Allowed/forbidden terms | 저장/표시되지만 generation gate에는 걸리지 않는다(`server/app/facemarket.py:608-635`, `src/features/analysis/AnalysisForm.jsx:97-110`). | Policy evaluator를 추가하고 generation request마다 decision evidence를 저장한다. |
| Thumbnail | 인증된 seller 누구나 verified ready model의 `face_front` thumbnail을 가져올 수 있다(`server/app/facemarket.py:732-769`). | Non-biometric cover만 제공하거나, owner/authorized active-license scope와 short-lived access를 요구한다. |
| Runtime assets | Detail/editor resolution은 status/views만 보고 source hash를 확인하지 않는다(`server/app/agents/identity_source.py:34-66`). | Gate와 worker에서 current asset binding, source hash/evidence version, deletion/revocation flag를 강제한다. |
| Fallback behavior | Detail worker가 revoked/expired/dangling face를 faceless output으로 downgrade한다(`server/app/workers/detail_page_job.py:54-67`). | 선택된 real FaceMarket model은 fail closed한다. Virtual model path만 real face 없이 진행할 수 있다. |
| Holder VC | Missing/unreachable Holder는 live VC check를 skip한다(`server/app/facemarket.py:1281-1294`). | Cutover 이후 FaceMarket real-model generation에는 Holder check를 mandatory로 만든다. |
| Holder deployment | Prod에 `OPENDID_HOLDER_URL`이 unset이고(`copilot/api/manifest.yml:198-199`), Holder build는 61 errors로 막혀 있다(`docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:127-138`). | Official OpenDID V2.0.0 datamodel로 Holder build를 고치고, Server 3을 deploy하며, private-link/auth Holder를 구성한 뒤 mandatory check를 켠다. |
| VC revoke | Holder에 도달할 수 있을 때만 best-effort revoke한다(`server/app/facemarket.py:1315-1329`). | Cutover 중 revoke intent를 transactionally queue하고, revoke 가능한 모든 VC가 terminal state가 될 때까지 process/reconcile한다. |
| Settlement UI | Contract는 record-only이고 UI는 settlement completed라고 표시한다(`contracts/README.md:3-17`, `src/features/editor/Editor.jsx:2411-2424`). | 실제 payment settlement가 통합되기 전까지 UI copy는 on-chain settlement receipt/audit record라고 표현한다. |
| Simulation | 호출마다 새 random simulated payment id를 만든다(`server/app/facemarket.py:1042-1088`). | Admin/dev로 제한하고 rate-limit를 걸며 deterministic idempotency key를 요구한다. |
| Account deletion | Personalization purge는 명시적이지만 FaceMarket model FK는 user null로 둔다(`server/app/workers/personalization_purge_job.py:144-233`, `supabase/migrations/20260709000000_facemarket_core.sql:21-34`). | Asset deletion, license halt, VC revoke queue, row deletion/anonymization, retention rule을 포함하는 FaceMarket purge workflow를 추가한다. |

## 마이그레이션, 전환, 롤백

### 시퀀싱 대안과 결정

| 대안 | 장점 | 비용/위험 | 판정 |
| --- | --- | --- | --- |
| A. 계약·법무·새 enrollment 준비 후 freeze → purge | 기존 기능을 준비 기간 동안 유지하고, cutover 뒤 즉시 재등록 경로 제공 | 준비 기간 동안 기존 identity-face binding 결함이 남으므로 신규 등록 제한 등 가역적 완화가 별도 필요할 수 있음 | **권장** |
| B. 즉시 freeze, enrollment 준비 후 purge | 기존 unbound model의 신규 사용을 바로 차단 | 계약 지연이 곧 무기한 실물모델 중단이 됨 | 비상 사고 대응 외 비권장 |
| C. 즉시 freeze + purge | 기존 생체 사본 노출을 가장 빨리 제거 | 되돌릴 수 없고 재등록 경로 부재 시 모델을 복구할 수 없음 | **거부** |

기본 결정은 A다. 준비 기간에 위험을 더 늘리지 않기 위한 최소 가역 조치로는 신규 CX-only `verified` 등록과 direct face upload를 feature flag로 막고, 원본 `face_front` thumbnail 대신 기존 `cover_image_url` 또는 placeholder를 쓰는 선택지가 있다. 이 완화는 기존 row/object를 삭제하지 않으며, 별도 제품 승인 뒤에만 활성화한다.

### 전환 순서

0. 외부 계약과 법무 gate를 닫는다. 이 단계가 끝나기 전에는 freeze/purge를 시작하지 않는다.
   - RAON/OACX가 반환하는 필드, portrait 전달 여부·형식·TTL, 허용 목적, 저장 금지 조건을 공식 문서와 제공사 서면 답변으로 확정한다.
   - Portrait를 주지 않으면 별도 ID capture, liveness, 1:1 matcher 조합과 비용·SLA·data residency·DPA를 확정한다.
   - 일반 개인정보 동의와 구분된 얼굴 특징정보 민감정보 동의, 원본 생체 처리 고지, 처리 목적, 보존/즉시 파기, 위탁/국외 이전, 철회·삭제 절차를 법무/개인정보 책임자가 승인한다.
1. 비파괴 방식으로 새 enrollment와 Holder 경로를 준비한다.
   - Provider adapter는 계약된 최소 surface만 구현하고 disabled flag 뒤에 둔다.
   - Raw government portrait, selfie, liveness video/frame는 process memory 또는 제공자 session에서만 쓰고 결정 직후 삭제한다.
   - 저장 evidence는 provider/transaction digest, decision, policy/threshold version, timestamp, raw-deletion outcome으로 제한한다. Score bucket은 법무 승인 시에만 추가한다.
   - Official OpenDID artifact로 Holder build를 고치고 Server 3에 private/authenticated Holder를 배포한다.
   - Schema state와 idempotent VC revoke queue를 추가하되 기존 모델 상태나 object는 바꾸지 않는다.
2. Enrollment readiness gate를 통과한다.
   - 실제 mobile ID와 계약된 liveness/matcher sandbox로 CX → portrait → liveness → 1:1 match → secondary QC → VC issue까지 E2E를 통과한다.
   - Dependency unavailable, portrait missing, replay, mismatch, liveness fail이 모두 `verified` 생성 없이 종료되는지 검증한다.
   - Raw biometric이 성공/실패/timeout 모든 경로에서 삭제됨을 테스트와 운영 로그로 증명한다.
   - 재등록 UI, 사용자 통지, support/runbook, 예상 처리량과 비용이 준비되었음을 product/operations가 승인한다.
3. Cutover dry-run과 명시적 sign-off 후에만 기존 real-model 사용을 freeze한다.
   - 대상 model/license/asset/job/VC/object 수를 read-only dry-run으로 산출한다.
   - Product owner, security/privacy owner, operations가 exact count와 maintenance window를 승인한다.
   - 그 뒤에 기존 `verified` row를 `reverification_required`로 옮기고, 라이선스/생성을 block하며 VC revoke를 queue한다.
4. Freeze 검증이 끝난 뒤에만 unbound biometric asset을 purge한다.
   - Pending/running `fm_model_asset_build` job을 먼저 취소한다.
   - `face_front`, `grid_sedcard`, direct-upload license face와 관련 row/object를 idempotent manifest로 삭제한다.
   - DB/object store를 reconcile하고, 삭제 대상이 더 이상 thumbnail/generation 경로에서 200 또는 success가 되지 않음을 검증한다.
   - Immutable settlement audit row는 승인된 retention/anonymization rule 아래에서만 남긴다.
5. Reverification campaign과 runtime hardening을 진행한다.
   - 기존 모델에 재등록을 안내하고, 새 evidence에 bind된 asset만 rebuild한다.
   - Successful enrollment 뒤 새 FaceLicense VC를 issue하고 license를 activate한다.
   - Server-side use-policy, mandatory Holder status, asset currentness, no-delete/no-reverify 조건을 하나의 generation gate에서 강제한다.

### 롤백

롤백은 unbound verified status를 복구하기보다 FaceMarket real-model generation을 계속 비활성화하는 쪽을 우선해야 한다. 안전한 rollback 상태는 다음과 같다.

- API/frontend는 virtual/AI model을 계속 제공할 수 있다.
- 기존 FaceMarket model은 `reverification_required`로 남는다.
- License는 halted 또는 blocked 상태로 남는다.
- VC revoke queue는 durable하고 replay 가능하게 남는다.
- `OPENDID_HOLDER_URL`은 기존 OpenDID deployment design의 설명처럼 이전 값으로 되돌리거나 unset할 수 있다(`docs/superpowers/specs/2026-08-20-facemarket-opendid-single-server-deployment-design.md:140-152`). 다만 Holder를 사용할 수 없는 동안 real-model generation은 계속 비활성화되어야 한다.

Target enrollment evidence가 해당 model에 존재하지 않는 한, old model을 다시 `verified`로 표시하는 방식으로 롤백해서는 안 된다.

## 정량 점수표

아래 정의는 이 보고서에서 명시한 control을 count한 것이다. Performance 또는 biometric accuracy measurement가 아니다.

| 통제 영역 | 명시적 목표 통제 | 현재 구현/강제 통제 | 차이 |
| --- | ---: | ---: | --- |
| Primary identity-face enrollment | 4: CX identity, government-ID portrait, live selfie liveness, 1:1 match | 1/4 enforced: CX identity token/CI | Government portrait, liveness, 1:1 identity-face match가 없다. |
| Secondary face asset QC | 2: three-angle completeness, same-person/quality comparison fail-closed | 1/2 always enforced: three-angle completeness; same-person QC는 QC object에 conditionally 의존한다 | QC dependency unavailable = fail closed로 바꿔야 한다. |
| Runtime real-model gate | 7: verified state, active/unexpired license, mandatory Holder VC, allowed/forbidden policy, ready/current assets, asset binding/hash, no deletion/reverify flag | 2/7 consistently enforced at request gate: local license status와 expiry; Holder/assets/policy/hash/reverify는 incomplete 또는 best-effort다 | Authoritative server gate 하나와 fail-closed worker가 필요하다. |
| Existing-data remediation | 5: status downgrade, license halt, VC revoke queue, asset/object purge, pending-job cancel | 0/5 present as a migration/job in current repo | 새 migration + purge/revoke job이 필요하다. |
| Account deletion biometric cleanup | 4: model/license halt, derived asset delete, original face delete, VC revoke queue | 0/4 FaceMarket-specific controls found; personalization purge는 자체 table/asset만 다룬다 | FaceMarket purge workflow가 필요하다. |
| OpenDID runtime readiness | 4: Holder builds, Holder deployed, private/authenticated Holder API, mandatory verification | 0/4 production-ready: Holder URL absent, build blocked, no app auth, verification best-effort | Go-live 전에 해결해야 한다. |
| Settlement claim accuracy | 2: on-chain audit record, actual payment settlement | 1/2: on-chain audit record only | UI 문구를 바꾸거나 payment rail을 통합해야 한다. |

계획상 통제 충족률로 환산하면 primary identity-face enrollment는 현재 25%(1/4)에서 목표 100%(4/4), secondary face asset QC는 50%(1/2)에서 100%(2/2), runtime real-model gate는 28.6%(2/7)에서 100%(7/7), existing-data remediation·FaceMarket account deletion cleanup·OpenDID runtime readiness는 각각 0%에서 100%가 목표다. Settlement는 실제 payment rail을 이번 범위에 넣지 않으면 50%(1/2)에 머무르므로 UI에서 이를 실제 지급으로 주장해서는 안 된다. 이 비율은 이 보고서가 정의한 통제 항목의 구현 커버리지이지, 해킹 위험 감소율·생체 정확도·성능 향상률이 아니다. 심각도가 다른 통제를 동일 가중치로 합친 단일 “전체 보안 점수”는 제시하지 않는다.

### 미측정 항목과 제안 평가 방법

새 enrollment implementation이 존재하기 전까지 performance와 biometric accuracy는 미측정이다. 현재 저장소만으로 observed speed, FAR, FRR, TAR, APCER, BPCER를 주장해서는 안 된다.

제안하는 반복 가능한 benchmark:

- Enrollment latency:
  - CX transaction verification, government portrait retrieval/capture, live selfie/liveness, 1:1 match, asset build, total enrollment의 p50/p95를 측정한다.
  - Proposed acceptance targets는 launch 전에 승인되어야 한다. 시작점으로 제안하는 target은 user think time을 제외한 p95 total enrollment 120 seconds 미만, server-side non-biometric API step p95 5 seconds 미만이다.
- Biometric match evaluation:
  - Same-person genuine pairs, impostor pairs, bad-quality captures, 시장에 맞는 demographic coverage를 포함한 labeled dataset을 만든다.
  - 선택한 threshold에서 FAR, FRR, TAR, failure-to-enroll rate, score distribution drift를 측정한다.
  - Proposed acceptance targets, observed가 아님: FAR <= 0.1%, FRR <= 5%, TAR >= 95% at FAR 0.1%, 그리고 threshold rationale 문서화.
- Liveness evaluation:
  - Printed photo, screen, replay video, vendor test plan이 지원하는 경우 mask, bona fide capture를 포함하는 labeled presentation-attack dataset을 만든다.
  - Attack category와 device class별 APCER와 BPCER를 측정한다.
  - Proposed acceptance targets, observed가 아님: APCER <= 1%, BPCER <= 5%. 더 높은 risk attack category는 launch block 또는 compensating control이 필요하다.
- Runtime generation:
  - Virtual model, current FaceMarket real assets, asset rebuild 이후 remediated FaceMarket real assets 각각에 대해 p50/p95 generation time을 측정한다.
  - Enrollment 이후에도 worker가 prebuilt private reference를 계속 소비하므로 generation path에 material change가 없을 것으로 예상된다. 다만 이는 benchmark로 확인해야 한다.

## 한계

- 이 저장소 안의 local public OACX/RAON evidence는 ENT_MID token-to-`trans` 동작과 CI/birth-bearing transaction이 가능하다는 local note만 보여준다. 예를 들어 `.omx/plans/facemarket-final-sprint-plan.md:50-64`는 local ENT_MID/token/trans integration을 기록한다. Official government-ID face photo 또는 liveness schema는 contract/manual detail이 pending이므로 이 보고서에서 주장하지 않는다.
- Government-ID portrait source, liveness payload schema, match threshold, retention rule, vendor audit log는 구현 전에 official RAON/OACX/OpenDID/biometric-provider contract로 확인해야 한다.
- 현재 저장소의 Holder VC verification은 full user-presented VC presentation이 아니라 lifecycle/status를 검증하는 것으로 보인다. 현재 deployment audit은 `docs/research/facemarket-opendid-vc-deployment-audit.md:38-57`에서 FaceMarket FastAPI가 Holder endpoint를 사용하며 Orchestrator runtime을 사용하지 않는다고 기록한다. Product claim상 presentation requirement가 필요하다면 별도 contract review가 필요하다.
- 이 보고서는 production database나 R2 bucket을 inspect하지 않았다. 삭제를 실행하기 전 production metadata에 대한 dry-run migration으로 purge count를 산출해야 한다.

## 우선순위 구현 단계와 go/no-go 기준

### 0A단계 — 외부 계약 spike

코드를 만들기 전에 portrait/liveness/matcher의 실제 공급 계약을 확정한다.

Go/no-go:

- Go: Official payload schema 또는 제공사 서면 답변, sandbox response sample, portrait 사용·보존 조건, liveness/matcher 제공 범위, TTL/rate limit/IP allowlist, 비용과 SLA가 모두 기록되었을 때.
- No-go: “신분증상의 모든 데이터” 같은 포괄 문구만 있고 portrait binary나 liveness/match 결과 필드가 명시되지 않았을 때.

### 0B단계 — 법무·동의·보존 gate

Raw biometric 최소화와 즉시 파기 정책을 제품 요구사항으로 고정한다.

Go/no-go:

- Go: 별도 동의문, 목적·항목·보존기간·파기·철회, 위탁/국외 이전, score 보존 필요성, incident flow가 법무/개인정보 책임자에게 승인되었을 때.
- No-go: Raw government portrait, selfie, video/frame 또는 reusable template를 장기 저장하는 설계가 남았거나, score bucket을 근거 없이 비식별로 간주할 때.

### 1단계 — 비파괴 enrollment/Holder 준비

Disabled flag와 sandbox에서 새 enrollment 및 private/auth Holder 경로를 완성한다. 기존 모델 상태와 object는 건드리지 않는다.

Go/no-go:

- Go: Holder clean build/test, private authenticated deployment, CX/portrait/liveness/match/VC issue E2E, 모든 실패 경로의 fail-closed, raw deletion 증거가 통과할 때.
- No-go: Holder 61-error build failure, portrait 공급 미확정, best-effort VC, raw biometric 잔존 중 하나라도 남을 때.

### 2단계 — 재등록 준비도와 cutover 승인

Dry-run count, re-enrollment UI, 사용자 통지, support/runbook, maintenance window를 준비한다.

Go/no-go:

- Go: Exact model/license/asset/job/VC/object count와 deletion manifest를 product, security/privacy, operations가 명시적으로 승인할 때.
- No-go: 새 등록 경로가 production-ready가 아니거나, 삭제 후 사용자가 복구할 경로가 없거나, 대상 수가 추정치뿐일 때.

### 3단계 — freeze 후 purge

승인된 cutover window 안에서 먼저 status/license/generation을 freeze하고 검증한 다음 object를 삭제한다.

Go/no-go:

- Go: 모든 기존 real model이 server-side에서 blocked되고, pending job이 취소되고, idempotent purge 뒤 DB/R2 reconcile과 “삭제 후 biometric API 200 금지” 테스트가 통과할 때.
- No-go: 기존 model이 선택/생성 가능하거나, thumbnail/object 경로가 살아 있거나, purge manifest를 reconcile할 수 없을 때.

### 4단계 — runtime/policy hardening과 재검증

Allowed/forbidden use, current asset binding, mandatory Holder VC, thumbnail scope, account deletion purge를 enforce하고 기존 모델을 새 enrollment로 재검증한다.

Go/no-go:

- Go: Generation gate가 policy pass, VC valid, assets current, no reverify/delete flag를 증명하며 revoked/expired/dangling/unavailable case가 hard failure일 때.
- No-go: 선택된 real model이 virtual/faceless generation으로 조용히 fallback할 수 있을 때.

### 5단계 — claim과 launch readiness

UI/copy, observed benchmark, 운영 runbook을 갱신한다.

Go/no-go:

- Go: Settlement UI가 audit receipt와 actual payment를 구분하고, 실제 측정치만 accuracy/speed로 표시하며, re-verification/deletion/VC revoke/account transfer 운영이 검증되었을 때.
- No-go: 측정하지 않은 보안 감소율, 생체 정확도, 처리 속도 또는 실제 지급을 제품이 주장할 때.
