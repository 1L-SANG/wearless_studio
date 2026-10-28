실사용 모델: `gpt-6.1-sol`. Effort: `max`. 현재 세션의 `turn_context` 기록으로 확인했다.

# 여러 몰에 한 번에 등록, 구현 설계

작성일: 2026-10-28. 기준 브랜치: `feat/multimall`. 기준 HEAD: `6104248f742a8ed9b3ed5002a1bd1bc0cf4baa87`, 카페24 PR #593의 코드다.

2라운드 기준은 출시 전이다. Claude가 10월 28일 확인한 사실에 따르면 #593은 OPEN이며 아직 머지하지 않았고, 운영에는 카페24 연결과 mall_register 잡이 없다. 몰 연결과 게시 플래그도 꺼져 있어 운영 셀러의 기본값 이전을 위한 장치를 만들지 않는다. 오너 지시(10월 28일)로 #593은 단독으로 머지하거나 출시하지 않는다. #593의 이전 등록 창과 연결 줄은 v2 시안 화면으로 교체한 뒤 Phase A와 한 번에 머지한다. 이 현황은 사용자가 제공한 확인 결과이며 이번 작업에서 운영 DB와 GitHub를 조회하지 않았다.

이 작업의 산출물은 이 문서 하나다. 제품 코드, 마이그레이션, 계약 문서를 이번 작업에서 바꾸지 않는다. 커밋, 스테이징, stash, 브라우저 실행, 외부 네트워크 호출, 유료 AI API 호출을 하지 않는다. 아래 파일명, SQL, 함수 시그니처와 테스트는 후속 구현 지시이며 실행 결과가 아니다.

## 1. 목표, 근거와 결정

셀러는 판매 기본 정보를 한 번 저장하고, 상품마다 판매가와 재고를 입력한다. 어느 몰의 등록 메뉴를 눌러도 같은 창을 열며 그 몰을 먼저 선택한다. 서버가 몰마다 점검하고, 막힌 몰을 제외한 준비된 몰부터 접수한다. 결과와 재시도도 몰마다 구분한다.

### 핵심 결정, 12개

1. 몰마다 무과금 `mall_register` 잡 하나를 만든다. 한 확인에서 만든 잡은 같은 `batchId`로 묶는다.
2. 묶음 전용 잡과 결과 표는 만들지 않는다. 접수 영수증을 각 잡에 같은 값으로 보관하고 한 트랜잭션으로 커밋한다.
3. 사용자당 공통 판매 기본 정보 표를 새로 만들고, `mall_defaults`는 주소와 브랜드, 채널 등 몰에 속한 선택을 보관한다.
4. 활성 등록의 유일성은 프로젝트와 몰 단위다. 인덱스와 `create_job`의 충돌 후 조회를 함께 바꾼다.
5. 고시는 에디터 값을 우선하며 빈칸만 공통값으로 채운다. 창에서 저장한 값은 에디터 블록에도 저장하고 새 리비전으로 게시한다.
6. 사전 점검과 접수 재점검은 같은 서버 함수를 쓴다. 클라이언트의 준비됨 표시는 등록 허가가 아니다.
7. 카페24는 처음에 진열과 판매를 모두 끈다. 옵션, 재고, 이미지 설정이 끝나야 요청한 상태로 전환한다.
8. 상품 번호가 확인된 작업은 실패로 낮추지 않는다. 후속 설정 실패는 상품 생성과 설정 미완료를 함께 표시한다.
9. 생성 요청의 전송 기록을 먼저 커밋한다. 전송 후 불명확하면 조회로만 복구하고 자동으로 다시 생성하지 않는다.
10. 카페24 상세 이미지는 카페24에 복사한다. 지그재그 상세 이미지 보관은 13절의 보존안을 추천하며 오너 승인을 기다린다.
11. 카테고리는 무료 규칙으로 추천한다. 유료 AI는 기본 흐름에서 호출하지 않는다.
12. Phase A는 카페24까지 운영 가능하게 만든다. Phase B 지그재그는 기본 플래그 꺼짐이며 발급 키와 알파 검증 전에는 준비 중이다.

### 정본 우선순위

이번 오너 지시와 확정 v2 시안이 제품 방향의 우선 근거다. 현재 코드와 현행 계약으로 저장과 인증의 실제 동작을 확인한다. 예전 시안(v1~v5)은 폐기했다(10월 29일 오너 지시로 파일 삭제). 예전 시안에 있던 제조연월 자동 입력, 고시 12항목 고정, 기본 노출함, 등록 후 상품 갱신을 되살리지 않는다.

| 근거 | 이번 설계에서 사용하는 부분 |
|---|---|
| `/Users/daily/Documents/wearless_studio/.scratch/mall-on-20261028/v2.html` | 판매 기본 정보 카드, 연결, 한 번에 등록, 고시 채우기, 몰별 결과 |
| 같은 폴더 `BRIEF_v2.md` | 21개 요구사항. 17절에서 구현 Task와 대응한다 |
| `/Users/daily/Documents/wearless_studio/.scratch/mall-upload-research-20261023/DESIGN_cafe24.md` v3 | OAuth, 토큰 회전, lease, 선커밋, 조회 복구, 종료 우선순위 |
| 같은 폴더 `zigzag_openapi_schema_20261023.graphql`, `REPORT.md` 2절 | 지그재그 입력 타입, 쿼리, 서명과 x-solution, 이미지 복사 구분 |
| `documents/common_data_contract.md`의 § 게시, § 쇼핑몰 연결 | 게시 수명, 연결 보안, 현재 등록 계약 |
| `documents/PRD.md`, `CONTEXT.md`, `docs/adr/0017-detail-page-partial-publish.md` | 프로젝트와 분석의 의미, 실측 추정 금지, 완성한 상세의 내보내기 정책 |
| `/Users/daily/Documents/wearless_studio/CLAUDE.md`, `DESIGN.md` | append-only, 새 표 ACL, 서비스 레이어, 외과적 변경, 화면 규칙 |
| `git show origin/main:src/features/editor/presets/infoPresets.js` | NOTICE_FIELDS 10항목, `info` 정본, 기본 사이즈 FREE 한 줄 |

공식 API 사실은 사용자가 제공한 10월 27일과 28일 확인 내용 및 로컬 스키마 사본을 사용했다. 이번 작업에서는 외부 문서를 조회하거나 실제 API를 호출하지 않았다. 응답 JSON의 세부 키까지 확인하지 못한 부분은 15절에 별도로 적었다.

### 현재 코드에서 재사용하는 것

`mall_connect.py`의 카페24 설치와 콜백, `cafe24_tokens.py`의 연결 ID 고정, 회전 잠금과 버전 조건 무효화는 유지한다. `cafe24_client.py`의 오류 가림, 가격 기준 조회, 상세 이미지 복사, 자체상품코드 정확 일치 조회를 확장한다. `mall_register_job.py`의 체크포인트, 모든 재시도 직전 재검증, `repo.set_job_metadata_patch`, `set_job_progress_fenced`, `finalize_uncharged_job`의 lease 조건을 재사용한다.

현재 `jobs_active_unique_idx`와 `create_job._active`는 프로젝트와 kind만 구분한다. 그대로 두면 두 번째 몰이 첫 몰의 잡에 합류한다. 현재 `get_project_mall_registration_recovery`는 카페24 전용이며 최신 5건 밖의 미확인 작업도 찾는다. 이 범위는 공통화해도 줄이지 않는다. 현재 `derive_outcome`은 진행 중에도 `failed`를 계산하므로 새 조회 계약에서는 진행 상태와 최종 결과를 분리한다.

### 비교한 대안

| 선택 | 장점 | 비용과 결론 |
|---|---|---|
| 묶음 잡 하나 안에 몰별 실행 기록 | 잡 수가 적음 | 서로 다른 lease, 부분 실패, 몰별 재시도가 한 행에 섞임. 기존 카페24 원장을 크게 바꾸므로 제외 |
| 몰별 잡과 별도 묶음 표 | 접수 영수증이 한 행에 있음 | 실행 대상이 두 몰뿐이고 잡 원장이 이미 있음. 조정용 표와 수명 관리가 추가되므로 현재는 제외 |
| 몰별 잡, 같은 batchId, 복제한 불변 영수증 | 기존 디스패처와 복구를 사용함 | 최대 두 행의 작은 중복 저장을 허용한다. 묶음 접수는 원자적으로 저장하고 개별 실행은 독립시킴. 채택 |
| `mall_defaults`에 `mall='common'` 행 추가 | 새 표가 없음 | 실제 몰과 공통값이 한 enum에 섞이고 shop_id, 주소, 완료 항목의 의미가 달라짐. 제외 |
| `mall_sales_defaults` 신설 | 사용자당 한 행이라는 의미가 명확함 | 새 표 하나가 필요하지만 주소를 다른 몰로 복사하지 않기 쉽다. 출시 전이므로 이전 코드의 호환 저장은 만들지 않음. 채택 |

## 2. 범위와 단계

Phase A는 공통 기본값, 새 창, 카페24 옵션과 재고, 추가 이미지, 이미지 비율, 원산지와 소재, 서버 사전 점검, 몰별 작업과 결과를 포함한다. 지그재그 연결과 주소 선택은 기존처럼 쓸 수 있지만 상품 등록은 준비 중이다.

Phase B는 지그재그 카테고리와 고시 매핑, createProduct 어댑터, 보관 정책, x-solution 주입, 알파 검증과 제한 공개다. 에이블리는 어댑터 자리와 셀러스 안내만 둔다. 스마트스토어는 현재 연결 목록의 준비 중 상태만 유지하고 등록 메뉴를 추가하지 않는다.

몰 사이 재고 동기화, 주문 연동, 등록 완료 후 상품 정보 수정과 상세 교체, 에이블리 구현은 제외한다. 이 문서의 설정 이어 하기는 최초 등록의 미완료 단계만 같은 스냅샷으로 마치는 기능이다. 이미 판매를 시작한 상품의 재고를 다시 덮어쓰는 편집 기능이 아니다.

## 3. 데이터 모델과 전진 마이그레이션

### 3.1 공통 판매 기본 정보

새 표 `public.mall_sales_defaults`를 둔다. 실제 구현 때 `supabase migration new multimall_registration`으로 생성한 새 파일에 공통 기본값과 잡 인덱스 변경을 함께 넣는다. 이 문서에서 `<A_SCHEMA_TS>_multimall_registration.sql`은 그 파일의 자리표시자다. 이미 있는 두 mall 마이그레이션을 수정하지 않는다.

| 열 | 타입과 초기값 | 의미 |
|---|---|---|
| `user_id` | uuid PK, auth.users FK, 삭제 cascade | 소유자당 한 행 |
| `shipping` | jsonb, 기본 `{}` | feeType, baseFee, jejuFee, isolatedFee, conditionalAmount |
| `returns` | jsonb, 기본 `{}` | totalFee, partialFee, exchangeFee |
| `disclosure` | jsonb, 기본 `{}` | country, manufacturer, asManager, phone, warrantyText |
| `tax_type` | text, 기본 TAX, TAX 또는 FREE | 카페24 A 또는 B로 변환 |
| `product_origin` | text, 기본 domestic, domestic 또는 imported | 유통 출처. 제조국이나 병행수입 여부와 같지 않음 |
| `shipping_days` | integer, 기본 2, 1 이상 30 이하 | 앱의 지원 범위. 지그재그 실 허용 범위는 알파 확인 |
| `listing_mode` | text, 기본 HIDDEN, HIDDEN 또는 ON_SALE | 최초 설정이 끝난 뒤 요청할 진열과 판매 상태 |
| `revision` | bigint, 기본 1 | 공통 기본값의 낙관적 잠금 |
| `created_at`, `updated_at` | timestamptz | 서버 시각 |

shipping, returns, disclosure에는 JSON object check를 둔다. 금액은 원 단위 정수이고 0 이상 2,147,483,647 이하다. 조건부 무료 금액은 1 이상이다. FREE는 baseFee를 0으로 정규화하고 conditionalAmount를 제거한다. 다른 정책으로 바꿀 때도 쓰지 않는 조건값을 제거한다. 미입력 금액을 배송비 0으로 추정하지 않는다. 완료 항목은 서버 계산이며 새 표에 중복 저장하지 않는다.

`country`의 기존 한국어 값을 보존한다. 원산지 변환은 정규화한 국가명과 ISO 두 자리 코드의 정적 표를 사용한다. 대한민국, 한국, KR은 KR로 해석하며 모르는 국가는 추정하지 않는다. 국가 선택 UI가 저장하는 값도 이 계약을 따른다.

`warrantyText`는 오너 결정 전에는 빈 값이다. 추천 기본 문구는 `본 제품에 이상이 있을 경우 소비자분쟁해결기준에 따라 보상합니다.`다. 이는 제안 문구이며 법적 적합성을 확인한 사실로 표시하지 않는다. 오너가 승인하면 기본값 공급 함수에서만 이 문구를 제공한다. SQL로 셀러가 쓴 고시를 덮지 않는다.

새 표는 RLS를 켜고 `revoke all ... from anon, authenticated`를 실행한 뒤 service_role에만 필요한 권한을 준다. 프론트는 서버 API로만 조회한다. 운영 기본 ACL이 새 표에 쓰기 권한을 줄 수 있으므로 RLS만 켜고 끝내지 않는다. 새 SECURITY DEFINER 함수는 만들지 않는다.

### 3.2 몰별 기본값

기존 mall_defaults의 PK와 mall enum을 유지하고 revision bigint, 기본 1만 추가한다. Phase A가 읽고 쓰는 값은 shop_id, addresses, revision이다. addresses에는 shippingAddressId와 returnAddressId를 둔다. 브랜드와 채널, 병행수입 여부도 몰별 값이라는 원칙은 유지하되 구체적인 저장 필드와 검증은 지그재그 알파 확인 뒤 별도 설계한다.

카페24에는 주소 ID를 보내지 않는다. 배송비와 반품비도 상품에 넣지 않으며 shipping_fee_by_product는 F로 몰 기본 규칙을 유지한다. 지그재그 CreateProductInput의 address에는 return_id만 있으므로 shipping_id를 만들지 않는다.

몰 재연결로 shop_id가 바뀌면 그 몰의 주소를 비우고 revision을 올린다. 같은 스토어의 인증키 교체는 주소를 유지하되 진행 작업은 고정한 연결 세대가 바뀌었으므로 중단한다. 공통 배송과 고시는 연결 해제나 다른 스토어 연결로 지우지 않는다.

### 3.3 출시 전 기본값 정리

공통값은 처음부터 mall_sales_defaults만 읽고 쓴다. 데이터 백필, 지연 이전, 이중 쓰기, 구 서버용 저장 경로는 만들지 않는다. `PUT /v1/malls/zigzag/defaults`와 기존 응답의 몰별 공통 defaults는 제거하고 5.1절의 새 API로 교체한다. 호출하는 실제 서비스, mock 서비스와 테스트도 한 번에 새 계약으로 바꾼다.

mall_defaults의 shipping, returns, disclosure, tax_type, product_origin, completed_items 열은 이번 마이그레이션에서 지우지 않는다. 코드에서는 읽거나 쓰지 않으며 이후 별도 정리 대상으로 남긴다. addresses, shop_id와 revision만 몰별 기본값의 정본이다. 연결을 저장할 때도 사용하지 않는 completed_items를 계산하거나 갱신하지 않는다.

아직 공통 행이 없으면 GET은 revision 0과 비어 있는 배송 및 고시, 기본 2일과 HIDDEN을 반환한다. 첫 PUT은 baseRevision 0으로 새 행을 만들고, 그 뒤에는 저장된 revision으로 충돌을 검사한다. 조회가 몰별 과거 행을 찾아 복사하지 않는다. 다른 스토어로 바뀌어도 공통값은 보존하고 그 몰의 주소만 비운다.

### 3.4 jobs의 몰별 경합 제어

같은 새 마이그레이션 `<A_SCHEMA_TS>_multimall_registration.sql`에서 일반 활성 인덱스의 대상에서 mall_register만 제외하고 다음 전용 인덱스를 둔다. 기존 제외 kind 여섯 개는 그대로 보존한다.

```sql
create unique index jobs_mall_register_active_unique_idx
  on public.jobs (project_id, (payload->>'mall'))
  where kind = 'mall_register' and status in ('pending', 'running');

alter table public.jobs add constraint jobs_mall_register_payload_check
  check (kind <> 'mall_register' or
    (project_id is not null and coalesce(payload->>'mall', '') in ('cafe24', 'zigzag', 'ably')));

create index jobs_mall_register_batch_idx
  on public.jobs (user_id, (payload->>'batchId'), created_at)
  where kind = 'mall_register' and payload ? 'batchId';
```

일반 인덱스 교체와 전용 인덱스 생성은 같은 트랜잭션에서 수행한다.

실제 일반 인덱스 predicate는 현재 파일의 제외 목록에 mall_register를 더한 것으로 작성한다. 운영 등록 이력은 없다는 전제에서 새 제약과 잡 형식을 함께 배포한다. 잘못된 payload를 보정하는 데이터 이전 코드는 만들지 않는다. 새로운 제약은 잘못된 몰과 프로젝트 없는 등록을 거부해야 한다.

`create_job`의 `_active`는 kind가 mall_register일 때 반드시 `payload->>'mall' = 입력 mall`을 추가한다. `_by_key` 우선순위와 SAVEPOINT, `ON CONFLICT DO NOTHING`, bounded 재조회는 유지한다. 다른 kind의 합류와 크레딧 경로는 바꾸지 않는다. 같은 몰에 다른 입력이 활성 상태이면 그 잡을 성공 접수처럼 반환하지 않고 해당 몰의 blocked 결과로 돌려준다.

### 3.5 잡의 저장 계약

| 위치 | 단일 등록 형식 |
|---|---|
| `idempotency_key` | `mall-register:{user_id}:{batchId}:{mall}` |
| `payload` | mall, batchId, requestHash, connectionId, shopId, publicationId, editorRevision, snapshot |
| `snapshot` | 상품명, 가격과 기준, 옵션 조합, 수량과 추가금액, 이미지 자산과 순서, 고시, 카테고리와 템플릿, 공통값과 몰별 선택의 확정본 |
| `metadata.batchReceipt` | batchId, requestHash, acceptedAt, selectedMalls, items. items에는 몰, accepted 또는 blocked, jobId, 공개 가능한 점검 오류만 포함 |
| `metadata` | externalCode, stage, create, productId, checkpoints, warnings, resumeRequestKeys, leaseRecoveries |
| `result` | outcome, setupStatus, productId, productUrl, adminUrl, stages, retryAction, code, warnings, retention |

requestHash는 정규화한 클라이언트 요청 전체의 SHA-256이다. selectedMalls는 정렬하고 객체 키를 정렬한다. 이미지 순서와 옵션 순서는 의미가 있으므로 보존한다. 클라이언트가 확인해 보낸 confirmedSourcesByMall은 비교에 포함하되 서버가 이번 접수에서 새로 읽은 연결 ID, 현재 기본값 revision, API 조회 시각으로 교체하거나 덧붙이지 않는다. 같은 키 재전송은 자원이 바뀌었어도 원래 영수증을 반환한다. 다른 입력으로 같은 batchId를 쓰면 409다.

서버가 해석한 값은 snapshot에 모두 고정한다. 실행 중 기본값, 분석, 색상, 에디터가 바뀌어도 작업 내용을 다시 만들지 않는다. metadata는 얕은 병합이므로 checkpoints를 쓸 때에는 최신 하위 객체 전체를 만들어 해당 키를 패치한다. 같은 lease 안에서는 단계별 쓰기를 직렬 실행한다. verify와 resume는 running 잡에 쓰지 않는다.

`externalCode`는 기존처럼 `wl`과 최초 잡 ID의 hex 앞 16자리다. 재시도, lease 복구, 미완료 설정 재개에서도 바뀌지 않는다. 지그재그와 카페24의 복구용 코드는 서로 다른 잡에서 나온다.

상품 ID는 몰과 관계없이 metadata.productId에 문자열로 저장한다. 카페24 응답의 product_no 정수는 어댑터에서 정규화하며 카페24 API 호출 때만 양의 정수로 변환한다. 몰에 따라 다른 이력 형식을 두지 않는다.

### 3.6 묶음 영수증의 수명과 제약

접수 트랜잭션 안에서 준비된 몰의 잡을 먼저 만들고, 실제 jobId를 넣은 동일 영수증을 새 잡 모두에 기록한 뒤 한 번 커밋한다. 영수증에는 외부 호출 결과를 쓰지 않는다. GET 묶음은 영수증의 blocked 항목과 각 잡의 현재 뷰를 합친다. 두 사본이 다르면 서버 오류로 보고 접수를 재구성하지 않는다.

준비된 몰이 0개면 잡과 영수증을 저장하지 않고 409 `no_ready_malls` 및 최신 점검을 돌려준다. 이것은 접수되지 않은 요청이므로 수정 후 같은 batchId를 써도 되지만 UI는 새 확인마다 새 키를 만든다. 하나라도 접수된 batchId는 영원히 원래 영수증만 재생한다. 막혔던 몰의 문제를 고친 뒤에는 그 몰만 선택해 새 batchId로 접수한다.

한도는 기존 사용자당 시간당 10개 신규 몰 잡이다. 두 몰이면 두 슬롯을 원자적으로 소비한다. 슬롯 부족 시 한도를 넘길 일부 몰을 임의로 선택하지 않고 묶음 전체를 429로 롤백한다. 재생, verify, 같은 잡 설정 재개는 신규 등록 슬롯을 소비하지 않으며 각각 별도 제한을 적용한다.

## 4. 공통 입력과 고시의 정본

### 4.1 RegisterDraft와 확정 스냅샷

주요 DTO는 `server/app/services/mall_registration_contract.py`에 둔다. 프론트는 JavaScript와 JSDoc를 쓰며 TypeScript 전환은 하지 않는다.

```text
RegisterDraft = {
  projectId, publicationId, editorRevision, selectedMalls: ['cafe24', 'zigzag'],
  productName, basePrice, defaultQuantity,
  priceOverrides: { cafe24?: {amount, basis}, zigzag?: {amount, basis} },
  sizes: ['FREE'], variantOverrides: [{variantKey, quantity, additionalAmount}],
  mainAssetId, additionalAssetIds: [],
  categories: {cafe24?: {categoryId}, zigzag?: {categoryId}},
  confirmedSourcesByMall?: {cafe24?: SourceVersions, zigzag?: SourceVersions},
  salesOverrides?: {listingMode, shippingDays, shipping, returns, taxType, productOrigin}
}
```

SourceVersions는 connectionVersion, salesDefaultsRevision, mallDefaultsRevision, catalogVersion, editorRevision이다. 최종 preflight에서 ready로 확인한 몰의 응답에서 받고 그 몰을 접수하려면 반드시 넣는다. blocked와 preparing 몰은 확인 버전이 없어도 되며 다른 몰의 부분 접수를 막지 않는다. 신뢰 가능한 허가증이 아니라 셀러가 확인한 출처가 바뀌지 않았는지 비교하는 값이다. 서버는 이 값으로 데이터나 권한을 결정하지 않고 현재 사실을 다시 읽는다.

RegistrationSource는 소유자 검사된 project, product, editorBlocks, publication, assets, salesDefaults, mallSpecificDefaults, recoveries를 가진다. MallFacts는 몰, 연결 ID와 공개 세대, 스토어 ID, 준비 상태, 카테고리와 템플릿 및 그 버전, 가격 기준, 이미지 규칙, 허용 채널과 브랜드, 조회 완전성을 가진다. 어느 DTO에도 인증키를 넣지 않는다. freeze_registration은 요청의 sizes와 저장된 사이즈표 라벨이 일치하는지도 검사한다.

색상은 저장된 상품의 색상 그룹 ID와 이름에서 서버가 읽는다. 사이즈는 저장된 size_table 행 라벨에서 시작하고 없으면 FREE 한 줄로 제안한다. FREE가 실제 치수를 의미하지는 않는다. 창에서 사이즈를 고치면 에디터 사이즈표도 고치되 측정값을 새로 지어내지 않는다. 기존 라벨을 삭제하면 그 행과 연결된 입력값의 변경을 확인시킨다.

공통 상품명 지원 범위는 공백을 정리한 1자 이상 100자 이하다. 제어문자는 거부하며 일반 한글과 Unicode를 무조건 삭제하지 않는다. 몰의 문자 제약 때문에 정리가 필요하면 바뀐 값을 확인받고 새 요청 해시로 접수한다.

variantKey는 색상 그룹 ID와 공백을 정리한 사이즈 라벨의 JSON 배열을 직렬화한 값이다. 구분자 문자열을 단순 연결하지 않는다. 색상과 사이즈의 곱을 만들고 중복 라벨과 정규화 충돌을 거부한다. 옵션 이름은 색상, 사이즈로 고정한다. 색상이 없는 상품은 임의의 색상을 만들어 등록하지 않고 옵션 줄에서 확인하게 한다.

기본 재고는 각 조합에 같은 수량을 넣는 값이다. 전체 재고를 옵션 수로 나누지 않는다. 입력하지 않은 재고는 화면에서 0으로 보이고 서버에서도 0으로 정규화한다. 숫자 0은 품절이며 음수, 소수, Boolean은 거부한다. 옵션별 추가금액은 초기 지원 범위에서 0 이상 정수다. 기본 판매가와 합한 최종가의 overflow도 검사한다. 몰마다 추가금액과 수량을 다르게 받는 기능은 이번 범위에 넣지 않는다.

품목 200개 초과는 지그재그만 차단한다. 카페24는 자체 API 한도 확인 전 앱의 보수적인 200개 제한을 동일하게 적용하되 이를 카페24 공식 상한이라고 쓰지 않는다. UI에서는 큰 옵션표를 한 번에 전부 펼치지 않고 스크롤 안에서 행을 보여준다.

### 4.2 고시 10항목 병합

에디터 `required_notice` 블록의 `info.fields`가 정본이다. 표시용 text 요소를 OCR하거나 문자열 순서로 파싱하지 않는다. 같은 프리셋 블록이 둘 이상이고 값이 다르면 `notice_conflict`로 지그재그를 막고 블록을 고르게 한다. 카페24의 원산지와 소재에도 충돌하는 필드는 보내지 않는다.

| NOTICE_FIELDS key | 에디터 빈칸일 때의 보충 | 없는 경우 |
|---|---|---|
| name | 상품명 | 상품명 확인 |
| materials | 기존 프리셋이 사용한 저장 분석 소재 | 카페24 소재 경고, 지그재그 차단 |
| colors | 저장된 색상 이름 | 지그재그 차단 |
| sizes | 에디터에 실제 입력된 실측 문구 | FREE만으로 치수를 채우지 않음. 지그재그 차단 |
| maker | 공통 manufacturer | 지그재그 차단. 직접 수입은 수입자 표기도 확인 |
| origin | 공통 country | 지그재그 차단, 카페24 원산지 경고 |
| care | 기존 프리셋의 소재 규칙과 셀러 입력 | 지그재그 차단 |
| madeAt | 자동 보충 없음 | 셀러 입력을 추천, 오너 결정 대기 |
| warranty | 승인된 공통 warrantyText | 승인 전 빈칸. 지그재그 차단 |
| contact | 공통 asManager와 phone을 모두 합침 | 어느 하나라도 없으면 지그재그 차단 |

`정보 입력 필요`, 공백, NBSP만 있는 문자열은 빈칸이다. 이미 있는 셀러 입력은 기본값이나 분석이 바뀌었다고 덮지 않는다. 자동 제안한 값은 출처와 함께 창에서 보여주며, 저장 시 실제 에디터 `info`와 렌더 요소를 함께 갱신한다. 사용자가 고시의 품명과 등록 상품명을 다르게 썼다면 경고하고 임의로 통일하지 않는다.

지그재그의 필수 고시는 화면의 10개와 같다고 가정하지 않는다. 말단 카테고리 템플릿과 10항목의 연결, 추가 필수 항목의 입력과 저장 필드는 알파 확인 뒤 별도 설계한다. Phase A는 에디터의 고시 10항목을 채우고 저장하는 범위다. 기존 10개 표에 근거 없는 항목을 미리 합치지 않는다.

### 4.3 저장, 게시와 리비전

창을 열 때 미완료 작업 복원을 먼저 한다. 신규 입력일 때만 저장을 기다리고 현재 리비전을 확인한다. 고시와 사이즈 저장은 Editor가 기존 `setBlocks`와 `buildInfoBlock`, `carrySlotImages` 경로로 처리하고 `api.saveEditorBlocks`를 통해 저장한다. 별도 서버 고시 쓰기 API나 직접 Supabase 쓰기를 만들지 않는다.

순서는 편집 블록 반영, 저장 완료, 새 리비전 확인, 같은 리비전 ready 게시 재사용 또는 새 게시, preflight다. 고시 저장 전 게시를 그대로 전송하지 않는다. 저장 충돌, 편집 중 추가 변경, 게시 실패가 있으면 새 잡을 만들지 않는다. 서버도 접수 트랜잭션에서 `publication.editor_revision == request.editorRevision == projects.editor_revision`을 확인한다.

이미지 선택은 같은 프로젝트의 소유자 자산 ID만 받는다. 서버가 R2 키와 공개 URL을 결정하며 외부 URL 입력은 받지 않는다. 기본 대표는 편집본의 첫 유효 이미지 자산이고 추가 이미지는 다음의 서로 다른 자산 최대 4개다. 부족하면 있는 만큼 사용한다. 순서는 셀러가 바꿀 수 있으며 대표와 중복한 추가 이미지는 제거한다. GIF는 정지 자산이 준비된 경우만 사용하고 변환 안내를 해당 상품에만 표시한다.

## 5. API 계약

새 라우터 `server/app/mall_registration.py`를 `main.py`에 등록한다. 기존 연결 OAuth 라우터는 그대로 둔다. 모든 새 경로는 로그인, 소유자 SQL 조건, `Cache-Control: no-store`, 기존 오류 봉투 `{error:{code,message,...}}`를 사용한다. 알 수 없는 필드와 Boolean 금액은 거부한다. upstream 오류 본문은 반환하지 않는다.

### 5.1 기본값과 카탈로그

| 메서드와 경로 | 입력과 응답 |
|---|---|
| `GET /v1/malls/sales-defaults` | `{revision,shipping,returns,disclosure,taxType,productOrigin,shippingDays,listingMode,missingItems}`. 연결 전에도 저장 가능 |
| `PUT /v1/malls/sales-defaults` | `{baseRevision,...변경할 구역}`. 구역 내부는 명시한 필드만 병합, 빈 문자열은 지움, null 의미는 스키마에서 필드별 고정. 새 revision 반환. 충돌은 409 `defaults_conflict` |
| `GET /v1/malls/{mall}/registration-defaults` | 몰별 revision과 addresses. 키와 shop_id 원문은 제외 |
| `PUT /v1/malls/{mall}/registration-defaults` | `{baseRevision,addresses?}`. 현재 연결 소유 스토어의 주소 선택만 허용. 알 수 없는 몰은 404 |
| `GET /v1/malls/{mall}/registration-catalog` | Phase A는 카페24의 `{catalogVersion,complete,categories,priceBasis,imageRatio,capabilities}`. 지그재그와 에이블리는 준비 중이며 템플릿과 채널 응답은 알파 뒤 설계. 원문 API body를 그대로 전달하지 않음 |
| 기존 `GET /v1/malls/zigzag/addresses` | 주소 목록을 끝까지 조회해 반환. 50개씩 페이지 처리, 중복 ID 제거. 주소록 추가는 파트너센터에서 함 |

`GET /v1/malls`에는 items와 각 몰의 addressesRevision을 제공하고 각 몰에 `registration:{status:'available'|'preparing'|'reconnect_required', reasonCode?}`와 공개용 connectionVersion을 추가한다. 지그재그 연결됨과 등록 가능은 독립이다. 기능 플래그나 x-solution이 없으면 연결이 있어도 등록은 preparing이다. preparing의 원인에 비밀값 이름이나 존재 여부를 셀러에게 노출하지 않는다.

카탈로그 캐시는 최대 10분이며 사용자, mall, 연결 세대, 카탈로그 계약 버전을 키로 쓴다. 연결 교체와 해제 때 무효화한다. 실패와 잘린 응답은 ready 캐시로 저장하지 않는다. 입력마다 preflight를 다시 해도 외부 카탈로그를 매번 재호출하지 않으며, UI는 이전 응답이 최신 입력을 덮지 않도록 요청 세대를 사용한다.

### 5.2 preflight

`POST /v1/malls/registrations/preflight`는 RegisterDraft를 받는다. 외부 상품 쓰기와 잡 생성, 등록 한도 소비는 없다. 카탈로그 조회 캐시는 가능하며 오류도 몰별 결과로 반환한다. 로그인과 프로젝트 소유자 위반은 전체 401 또는 404다.

```json
{
  "editorRevision": 18,
  "inputHash": "normalized-draft-sha256",
  "readyMalls": ["cafe24"],
  "blockedMalls": ["zigzag"],
  "items": [
    {"mall":"cafe24","status":"ready","issues":[],"warnings":[],
     "sourceVersions":{"connectionVersion":"public-version","salesDefaultsRevision":3,"mallDefaultsRevision":2,"catalogVersion":"catalog-hash","editorRevision":18},
     "summary":{"variantCount":2,"price":39000,"shippingSource":"mall"}},
    {"mall":"zigzag","status":"blocked",
     "issues":[{"code":"notice_missing","path":"notice.madeAt","message":"제조연월을 채워 주세요","action":"fill_notice"}],
     "warnings":[]}
  ]
}
```

status는 ready, blocked, preparing 중 하나다. issue는 code, path, message, action, 필요하면 templateFieldKey를 가진다. action은 fill_notice, edit_options, choose_category, choose_image, edit_price, open_defaults, reconnect, verify_existing, resume_existing 중 하나다. 경고는 ready를 막지 않는다. pending 또는 running인 기존 작업은 ready가 아니라 기존 상태 확인 행동으로 반환한다.

items의 sourceVersions는 각 몰이 확인한 출처다. 첫 preflight에서는 confirmedSourcesByMall이 없어도 되며 ready 항목에는 최신 사실과 함께 반환한다. blocked와 preparing 항목은 버전을 생략할 수 있다. 입력을 고친 뒤 다시 받은 최신 응답에서 ready를 확인한 몰의 값만 draft에 붙인다. 접수에서 연결 세대가 다르면 connection_changed, 기본값 revision이 다르면 defaults_changed, 카탈로그 내용이 다르면 catalog_changed로 그 몰을 막고 다시 점검하게 한다. 현재 ready 후보인데 확인 버전이 없는 몰은 그 몰만 preflight_required로 막는다. 다른 스토어, 새로운 배송비나 ON_SALE 기본값을 조용히 채택하지 않는다. 실제 사용 필드의 차이를 따로 판정하지 않는 보수적인 revision 비교를 초기 구현으로 택한다.

preflight는 등록할 준비가 된 저장과 게시가 있어야 최종 ready를 준다. UI의 게시 준비 중에는 `publication_required` 점검을 표시하고 기존 onPublish를 호출한다. 서버 API가 에디터 화면을 캡처하거나 게시를 대신 만들지 않는다.

### 5.3 묶음 접수

`POST /v1/malls/registration-batches` 입력은 `{batchId:UUID, draft:RegisterDraft}`다. preflight inputHash를 허가 토큰으로 쓰지 않는다. 기존 batchId 조회와 요청 해시 대조를 자원 재검사보다 먼저 수행한다.

신규 접수 절차는 다음과 같다.

1. 로그인, 계정 폐쇄, 요청 형식을 검사한다. 사용자와 batchId로 영수증을 찾으면 같은 입력은 200으로 재생하고 다른 입력은 409 `idempotency_key_reused`다.
2. DB 잠금 밖에서 몰별 카탈로그와 권한 조회 사실을 준비한다. 준비 안 된 몰은 preparing으로 둔다.
3. 짧은 접수 트랜잭션에서 8절의 잠금 순서를 지키고 소유 프로젝트, 현재 연결과 기본값, ready 게시, 에디터 revision, 저장된 사이즈 라벨, 자산, 미확인 과거 작업을 다시 확인한다.
4. `evaluate_preflight`를 같은 입력과 현재 사실에 실행한다. ready 후보에 대해서만 confirmedSourcesByMall을 필수 비교한다. 누락은 그 몰만 preflight_required, 변경은 그 몰만 연결 또는 기본값 변경 오류다. blocked와 preparing 몰에 버전이 없다는 이유로 다른 몰을 막지 않는다. 막힌 몰은 영수증에만 기록하고, 통과한 몰의 snapshot을 고정해 `create_job`으로 한 개씩 만든다.
5. 다른 키의 같은 몰 활성 작업 충돌은 그 몰을 blocked로 기록하고 existingJobId를 준다. 다른 몰은 접수할 수 있다. 미확인 과거 생성 작업도 새 생성보다 기존 확인을 우선한다.
6. 신규 잡이 없으면 트랜잭션을 롤백하고 409 `no_ready_malls`를 반환한다. 있으면 신규 잡 수만큼 한도를 소비하고 동일 영수증을 모두 패치한 뒤 커밋한다. 커밋 후 한 번만 dispatcher를 깨운다.

새 접수는 202다. 응답은 `{batchId,acceptedAt,items:[{mall,status:'accepted',jobId}, {mall,status:'blocked',issues}],readyCount}`다. 실행 결과가 일부 실패해도 접수 응답을 HTTP 500으로 바꾸지 않는다. DB 오류는 전부 롤백한다. 한 번 커밋한 두 잡의 실제 외부 실행을 한 트랜잭션으로 묶지 않는다.

버튼을 본 뒤 연결이나 고시가 바뀌어 ready 몰 수가 줄 수 있다. 서버의 실제 accepted 항목을 즉시 화면에 반영한다. 이전 점검에서 막혔다가 접수 때 새로 준비된 몰은 선택되어 있어도 ready 확인 버전이 없으면 접수하지 않는다. 해당 몰만 다시 점검한 뒤 새 확인으로 올린다. 선택하지 않은 몰을 추가하지 않는다.

### 5.4 조회, 확인, 재개

| 경로 | 계약 |
|---|---|
| `GET /v1/malls/registration-batches/{batchId}` | 소유자의 묶음 영수증과 현재 몰별 등록 뷰. 없으면 404 |
| `GET /v1/malls/registrations/{jobId}` | 소유자, kind, payload.mall을 확인. `status,progress,stage,outcome,setupStatus,productId,productUrl,adminUrl,stages,retryAction,warnings,retention` 반환 |
| `POST /v1/malls/registrations/{jobId}/verify` | 종료된 unknown 또는 duplicate_suspected만 조회 복구. 사용자당 분당 6회. 원격 생성이나 재고 쓰기를 하지 않음 |
| `POST /v1/malls/registrations/{jobId}/resume` | `{requestKey:UUID}`. 종료된 작업 중 서버가 허용한 설정 이어 하기만 같은 잡을 pending으로 전환. 입력 변경 불가, 사용자당 분당 6회 |
| `GET /v1/projects/{projectId}/mall-registrations` | `{items,recoveries:{cafe24:[],zigzag:[]}}`. 현재 스토어의 전체 역사에서 활성, unknown, duplicate_suspected, 설정 미완료를 찾음. Phase A의 zigzag 복구 목록은 빈 배열 |

실행 중 `outcome`은 null이다. 최종 outcome은 created, failed, unknown, duplicate_suspected다. productId가 확인되면 아직 설정 중이어도 productId를 보여주되 완료로 끝내지 않는다. `setupStatus`는 pending, complete, needs_attention, not_applicable 중 하나다.

retryAction은 none, verify, resume, new_request, reconnect, open_admin 중 하나다. 상품 생성 전 확정 실패는 값을 수정한 새 확인으로 새 batchId를 만든다. 접수 응답 유실은 같은 batchId와 같은 요청을 재전송한다. unknown은 verify만 제공한다. created와 needs_attention은 안전한 단계에 한해 resume를 제공한다. 성공한 몰은 실패 몰 재시도 선택에서 제외한다.

resume는 원본 jobId와 payload를 보존하고 metadata의 완료 단계도 지우지 않는다. 동일 requestKey는 재생한다. job 행 잠금과 활성 유일성 조건 아래 status가 종료 상태이고 해당 몰의 다른 활성 작업이 없는지 확인한 뒤 pending으로 바꾼다. locked_by, locked_at, finished_at을 비우고 error_message를 정리한다. `leaseRecoveries`는 새 실행 회차에서 0으로 초기화하되 이전 값은 resume 이력에 남긴다. result의 이전 오류는 기록으로 남기고 진행 중 조회에서는 최종 결과로 쓰지 않는다.

#593의 카페24 전용 접수, 조회, verify 경로는 새 공통 경로로 교체한다. 이전 요청 모델과 접두사, 단일 recovery 응답, 업그레이드 전용 오류는 남기지 않는다. 프론트 서비스와 mock 및 테스트도 같은 계약으로 바꾼다. 운영 등록 형식은 이 문서의 한 가지다.

## 6. 몰마다 사전 점검

| 검사 | 공통 또는 카페24 | 지그재그 |
|---|---|---|
| 기능 준비 | 연결, 게시, MULTIMALL 스위치, 몰별 스위치와 허용 스토어 | 추가로 ZIGZAG_REGISTER_ENABLED와 x-solution, 알파 검증 완료 |
| 소유자와 편집본 | 살아 있는 본인 프로젝트, 실존 모델 차단, ready 게시와 일치하는 리비전, 정규화된 파일 | 동일 |
| 연결 | 접수한 연결 ID와 현재 스토어, 카페24 refresh 유효와 4개 scope | 해당 연결 세대와 상품 조회, 등록 권한. shop 성공만으로 쓰기 권한이 확인됐다고 하지 않음 |
| 판매가 | 1 이상 정수. 카페24 가격 기준 조회 필수 | 지그재그 조사 기준 10원 단위, 최종 옵션가도 검사. 실제 하한은 알파 확인 |
| 옵션과 재고 | 색상과 사이즈 조합, 0 이상 정수, 앱 상한 200개 | 최대 200개, attribute_list와 option_list의 완전한 대응 |
| 카테고리 | 셀러 분류 목록. 선택은 권장, 미선택은 경고. 고른 ID는 현재 목록에 있어야 함 | 말단 필수. 카테고리와 essential_code 매핑 및 템플릿 해시 일치 |
| 고시 | 10항목 전체가 비었다고 차단하지 않음. 소재와 원산지는 있으면 보내고 없으면 경고 | 10항목의 빈칸과 해당 템플릿 추가 필수값을 모두 검사 |
| 대표와 추가 이미지 | 대표 필수, 추가 선택. 본인 프로젝트 자산, 실제 포맷과 바이트, 이미지 설정 조회 | MAIN 정확히 1장, SUB 순서, 공개 가능한 서버 소유 URL |
| 배송과 반품 | 카페24 몰 기본을 사용하므로 공통 배송 미완료가 카페24를 막지 않음 | 정책별 필수 금액, 제주와 도서산간, 전체와 부분 반품비, 교환비 |
| 주소와 발송 | 카페24 해당 없음 | 현재 스토어 주소록에 있는 두 ID, return_id 전송, shipping_days |
| 브랜드와 채널 | 카페24 해당 없음 | 허용 조합이 하나면 자동 선택, 복수면 기본 설정에서 선택. 브랜드 미필수이고 허용 브랜드가 없으면 생략 |
| 이전 등록 | 같은 몰과 현재 스토어의 활성 작업, 미확인 생성, 설정 미완료 검사 | 동일. 조회 복구 수단 미확인이면 관리자 확인만 허용 |
| 보관 | 카페24 복사 전 원본 게시가 삭제됐으면 차단 | 13절의 보존 모드가 준비되지 않으면 Phase B 운영 공개 차단 |

오류는 몰별 path를 포함한다. 공통 데이터 오류라도 그 몰이 실제 요구하는 항목만 차단한다. 예를 들어 madeAt이 비면 지그재그만 막고 카페24는 계속 준비됨이다. 모든 품목 재고가 0인 것은 경고이며 등록 가능하다. 판매 상태는 품절로 맞춘다. 카탈로그를 끝까지 읽지 못한 것은 빈 목록으로 간주하지 않고 그 몰의 점검 실패로 둔다.

### 카페24 B 가격 기준

현재 `price_fields`는 B일 때 숫자를 price_excluding_tax에 넣는다. 공통 판매가를 소비자가로 해석한 채 그대로 보내면 세금 의미가 달라질 수 있다. 이번 설계에서는 확인되지 않은 세금 변환이나 반올림 공식을 추가하지 않는다.

카페24가 B인 경우 `카페24 상품가, 세금 제외`를 명시하고 `priceOverrides.cafe24={amount,basis:'tax_excluded'}` 확인을 필수로 한다. 카페24 한 곳만 선택했으면 기본 판매가 칸 자체를 이 의미로 표시할 수 있다. 두 몰 선택 시 몰마다 다르게를 자동으로 열어 카페24 값만 확인받는다. B가 아닌 카페24와 지그재그는 basis가 selling_price다. 설정이 바뀌면 해당 몰만 `price_basis_changed`로 막고 다시 확인한다. 셀러가 치는 정보는 여전히 가격과 재고이며 숨은 세금 계산은 없다.

## 7. 카테고리와 몰별 어댑터

### 7.1 파일과 공통 인터페이스

MallFacts, RegistrationSnapshot, LookupResult, CreatedProduct 등 불변 DTO의 정본은 `server/app/services/mall_registration_contract.py`다. `server/app/services/mall_adapters/base.py`는 이를 import하고 MallAdapter Protocol, JobContext Protocol과 registry만 정의한다. `cafe24.py`, `zigzag.py`, `ably.py`가 이를 구현한다. 범용 단계 DSL이나 플러그인 로더는 만들지 않고 명시적인 registry를 사용한다.

```python
def get_adapter(mall: str) -> MallAdapter

class MallAdapter(Protocol):
    def evaluate(self, snapshot: RegistrationSnapshot, facts: MallFacts) -> list[PreflightIssue]: ...
    async def prepare(self, ctx: JobContext) -> None: ...
    async def create(self, ctx: JobContext) -> CreatedProduct: ...
    async def lookup_created(self, ctx: JobContext) -> LookupResult: ...
    async def complete_setup(self, ctx: JobContext) -> None: ...

class JobContext(Protocol):
    job: dict
    snapshot: RegistrationSnapshot
    client: Any
    storage: Any
    async def recheck(self) -> None: ...
    async def checkpoint(self, patch: dict) -> None: ...
    async def call(self, operation: Callable[..., Awaitable[Any]], *, effect: str) -> Any: ...
    async def mark_create_sent(self) -> None: ...
```

LookupResult는 kind가 found, none, multiple, inconclusive 중 하나이며 productIds 배열을 가진다.

Phase A의 zigzag registry 항목은 PreparingAdapter이며 import 시 Phase B 파일의 존재를 요구하지 않는다. Phase B에서 ZigzagAdapter로 교체한다. 에이블리는 계속 준비 중 어댑터다.

effect는 read, image_copy, create, absolute_setup, activate 중 하나다. 호출 래퍼는 모든 재시도 직전 recheck를 실행한다. adapter가 직접 DB 커밋, HTTP 클라이언트 생성, lease 없는 repo 쓰기를 하지 않도록 client와 context를 주입한다. prepare는 생성 전 이미지 준비와 안전한 조회이며 create만 생성 전송 기록을 남길 수 있다.

### 7.2 무료 카테고리 추천

카페24는 연결한 셀러의 categories를 100개씩 끝까지 읽고 parent_category_no를 보존한다. 현재 300개 절단은 제거한다. 운영 보호 상한 5,000개 또는 50페이지에 닿아 다음 페이지가 남으면 complete=false로 반환해 임의의 말단 판정을 하지 않는다. 지그재그 category는 children을 재귀 조회해 말단 경로를 만든다. GraphQL 조회 깊이도 실제 최대 깊이와 대조해 잘린 트리를 ready로 만들지 않는다.

서버에서 저장 분석의 clothingType, subCategory, customCategory, 상품명과 카테고리 이름을 정규화해 비교한다. 완전 일치, 의류 종류 사전, 상위 경로 포함 순으로 점수를 매긴다. 단일 명확 후보만 기본 제안하고 동률이면 직접 고르게 한다. 몰 A의 카테고리 ID를 몰 B에 재사용하지 않는다. 기본 시안의 AI 추천 표기는 구현에서 `추천`으로 바꾼다.

추가 AI 추천은 기본 호출 0건, 비용 0달러다. 선택 확장 시 카테고리 개정마다 두 몰의 후보를 한 요청으로 좁혀 추천하는 1회 호출안을 검토할 수 있다. 모델과 최신 단가는 이번에 조회하지 않았다. 예산 산식은 `상품 수 × (입력 토큰 × 입력 단가 + 출력 토큰 × 출력 단가)`이며, 예를 들어 상품당 입력 3,000토큰과 출력 300토큰의 100건이면 입력 300,000토큰과 출력 30,000토큰이다. 달러 추정이 확정되기 전 승인 요청이나 호출을 하지 않는다. 필요하면 Claude에게 모델, 횟수와 비용 산정을 넘긴다.

## 8. 공통 작업 실행, 잠금과 실패 처리

### 8.1 잠금 순서

같은 경로에서 둘 이상 필요할 때 순서는 계정 `mall-account:{user}`, 몰 연결 `mall:{user}:{mall}`의 cafe24 다음 zigzag 순서, 공통 기본값 `mall-sales-defaults:{user}`, 몰별 기본값 `mall-defaults:{user}:{mall}`, 묶음 `mall-batch:{user}:{batchId}`, 프로젝트 등록 `mall-register:{user}:{project}`, 게시 행, 프로젝트 행, 잡 행 ID 정렬 순이다. 모든 잠금을 매 호출마다 잡는다는 뜻은 아니다. 필요한 잠금의 부분 순서만 지킨다.

접수는 게시 행을 잠근 뒤 프로젝트 행의 editor_revision을 잠그고 다시 읽는다. 기존 게시 삭제 경로의 게시와 프로필 잠금 순서를 역전하지 않는다. verify 저장은 몰 잠금 후 종료된 잡 행을 잠근다. 외부 조회는 그 전에 잠금 밖에서 끝낸다. 토큰 refresh만 기존 예외대로 몰 잠금 안에서 최대 8초 외부 요청을 유지한다. 이 구간에서 계정 잠금을 뒤늦게 잡지 않는다.

잡 잠금을 잡은 채 새 몰 잠금이나 게시 잠금을 요구하지 않는다. 마지막 연결 시각 갱신은 lease를 확인한 짧은 종결 트랜잭션에서 수행하며 그 안에서 토큰 refresh를 호출하지 않는다.

기본값 저장에서 연결 상태도 확인할 때는 계정과 몰을 먼저 잡고 공통 및 몰별 기본값 순서를 따른다. 현재 connection 교체와 탈퇴도 같은 연결 잠금을 사용한다. PostgreSQL 경합 테스트에서 이 순서를 검증한다. R2 읽기, 이미지 변환, 외부 등록을 접수 트랜잭션 안에서 하지 않는다.

### 8.2 실행과 복구

`run_mall_register_job(app, job)`는 payload.mall로 adapter를 고른다. 잡 형식에 따른 실행 분기는 없다. JobContext가 고정한 연결 ID, 스토어, 사용자와 lease를 관리한다.

외부 호출 직전마다 lease 갱신, 기능 상태와 허용 스토어, 계정 폐쇄, 같은 연결 세대와 토큰 유효, 취소, 필요한 게시 및 자산을 확인한다. 토큰 획득에서 기다린 뒤에도 한 번 더 검사한다. lease를 잃으면 DB 쓰기와 새 외부 호출을 모두 중단한다. 이미 날아간 HTTP 요청을 회수할 수 있다고 보장하지 않는다.

생성 직전 `create={sentAt,attempt,leaseToken}`을 펜스로 패치하고 커밋한 뒤 호출한다. 이 기록 이후 프로세스가 죽었으면 실제 전송 전이었을 수도 있지만 조회 복구만 한다. 생성 API가 문서로 보장한 미실행 거절일 때만 rejectedAt을 기록한다. 카페24의 기존 401 회전 재전송은 같은 guarded call 안에서 유지하며 각 전송 전에 새 attempt를 커밋한다. 지그재그는 서명 오류의 미실행 보장이 확인되기 전 같은 특례를 적용하지 않는다.

재개 우선순위는 확인된 productId, 명확한 rejectedAt, create.sentAt, 전송 전 준비 순이다. productId가 있으면 생성 호출을 생략하고 허용된 미완료 설정만 처리한다. sentAt만 있으면 lookup_created만 실행한다. 조회 0건은 미생성의 증거가 아니며 재생성하지 않는다. 여러 건이면 duplicate_suspected, 조회 실패나 지원 불명은 unknown이다.

### 8.3 종료 우선순위와 표시

| 조건 | jobs.status | outcome와 setupStatus | 허용 행동 |
|---|---|---|---|
| 실행 중 | pending 또는 running | outcome null, setup pending | 상태 확인, 창 닫기 |
| productId 확인, 모든 초기 단계 완료 | done | created, complete | 관리자 보기 |
| productId 확인, 재고 또는 이미지 등 초기 단계 실패 | done | created, needs_attention | 안전한 설정 이어 하기 또는 관리자 보기 |
| 명확한 생성 거절 | error | failed, not_applicable | 입력 수정 후 해당 몰 새 요청 |
| 생성 전송 후 결과 불명 | done | unknown, pending | 다시 확인, 관리자 보기 |
| 같은 코드 여러 상품 | done | duplicate_suspected, pending | 관리자 확인 |
| 생성 전 실패 | error | failed, not_applicable | 문제 해결 후 해당 몰 새 요청 |

공용 recover_stale_leases는 유지한다. 첫 만료는 재큐, 두 번째는 error가 될 수 있다. 그 경우에도 metadata의 상품 번호가 최우선이며 created를 failed로 낮추지 않는다. 후속 단계 완료 여부는 checkpoints로 계산한다. 불명확한 sentAt을 공용 서버 오류 문구로 덮지 않는다.

verify는 종료된 잡만 수정한다. 원격 조회 후 몰 잠금과 잡 행 잠금을 다시 잡고 이미 created가 되었으면 그 결과를 반환한다. 1건을 찾으면 productId를 확정하고 setup 미완료로 둔다. 필요하면 별도 resume 버튼으로 재고와 이미지를 이어 간다. verify 자체가 설정 호출을 하지 않는다.

### 8.4 단계별 재시도 분류

조회는 429의 Retry-After를 최대 60초로 제한하고, 전송 실패 및 5xx는 1초, 2초, 4초의 최대 3회 추가 시도를 허용한다. 상세 이미지 복사도 기존 규칙을 유지하며 중복 파일이 생길 가능성은 있으나 중복 상품 생성과 분리한다. 생성은 애매한 실패에서 재전송하지 않는다.

재고 절대값 PUT은 상품이 아직 숨김이고 판매 중이 아니며 activation이 시작되지 않았을 때만 같은 snapshot으로 재시도한다. 추가 이미지 POST의 응답 유실은 곧바로 다시 호출하지 않는다. 원격 목록으로 동일 이미지 집합을 확인할 수 있을 때만 완료 또는 안전한 재시도를 판정하고, 그 조회 계약이 확인 안 됐으면 관리자 확인으로 남긴다.

activation.sentAt 이후에는 주문이 생겼을 수 있으므로 재고 단계를 다시 실행하지 않는다. 활성화 응답 유실은 상품 상태 조회로만 확인한다. 사용자가 몰 관리자에서 옵션이나 판매 상태를 바꿨으면 `remote_product_changed`로 설정 이어 하기를 막는다. 등록 후 임의의 원격 변경을 되돌리는 기능을 만들지 않는다.

## 9. 카페24 어댑터

### 9.1 전송 계약

상품 생성은 `shop_no=1`의 기존 범위를 유지한다. 멀티 쇼핑몰 번호 지원은 추가하지 않는다. `has_option='T'`, `option_type='S'`, options에 색상과 사이즈의 `{name,value:[]}`를 보낸다. S를 택하는 것은 화면의 색상과 사이즈를 각각 선택하게 하기 위해서다. E, F 등 다른 옵션 형식은 초기 범위에서 받지 않는다.

생성 본문은 기존 product_name, supply_price, 가격 기준별 price 또는 price_excluding_tax, description, mobile_description, tax_type, product_condition, custom_product_code를 유지한다. 공급가는 별도 입력 없이 해당 몰의 기준가와 같게 보낸다. 공급가는 참조용이라는 기존 근거를 따른다. 초기 display와 selling은 언제나 F다. 최종 상태는 snapshot.listingMode를 나중에 적용한다.

원산지는 고시 origin을 국가 코드로 바꾼 made_in_code, 소재는 고시 materials를 product_material로 보낸다. 공통 제조국은 고시가 비었을 때만 보충한다. 고시 10항목 전체를 넣는 카페24 필드가 있다고 가정하지 않는다. 배송과 반품지, 상품별 배송비 본문은 만들지 않는다.

### 9.2 체크포인트

| 순서와 stage | 완료 체크포인트 | 실패 후 상태 |
|---|---|---|
| 1 uploading_images | 카페24에 복사한 상세 URL 배열과 원본 files 해시 | 생성 전 failed. 저장된 수만큼 건너뛰어 재개 |
| 2 preparing_images | 이미지 설정 비율, 처리한 대표와 추가 이미지의 자산 ID 및 해시 | 생성 전 failed |
| 3 price_setting | 가격 기준과 요청 본문 해시 | 기준 불일치면 생성 전 중단 |
| 4 creating_product | create.sentAt 선커밋, 이후 productId 즉시 커밋 | 응답 불명이면 조회 복구, 재생성 금지 |
| 5 mapping_variants | variantKey와 원격 variant_code의 일대일 매핑 | created, needs_attention, 진열과 판매 F 유지 |
| 6 setting_inventory | 모든 품목의 재고 및 추가금액 설정 확인 | created, needs_attention, 진열과 판매 F 유지 |
| 7 uploading_thumbnail | 대표 이미지 처리 해시와 완료 상태 | created, needs_attention |
| 8 uploading_additional_images | 각 요청의 이미지 해시, sentAt, 완료 또는 unknown | created, needs_attention. 불명확한 POST 반복 금지 |
| 9 activating | activation.sentAt, 확인한 최종 진열과 판매 상태 | 상태 조회로 복구. 재고를 다시 쓰지 않음 |
| 10 complete | 최종 설정 확인과 last_registered_at | created, complete |

상세 이미지 복사 배치는 기존 8장과 base64 합계 20MB 제한을 유지한다. 대표와 추가 이미지는 원본을 메모리에서 처리하고 자격 증명이나 base64를 잡 JSON에 저장하지 않는다. 처리 bytes는 재실행 때 자산 ID, crop 규칙과 해시로 다시 만들 수 있다. 입력 자산이 삭제됐으면 새 이미지를 임의로 선택하지 않는다.

### 9.3 옵션과 재고

생성 후 품목 목록을 읽고 옵션 이름과 값으로 매핑한다. 반환 순서나 variant_code 숫자 패턴으로 조합을 추정하지 않는다. 예상한 조합 수와 일치하고 중복, 누락이 없어야 한다. 단일 색상과 FREE도 같은 매핑 규칙을 사용한다.

각 품목에는 `additional_amount`, `display='T'`, `selling`을 수량에 맞춰 설정하고 `quantity`, `use_inventory='T'`, `display_soldout='T'`, `safety_inventory=0`을 보낸다. 수량 0이면 품목 selling은 F다. `PUT /products/{no}/variants`를 기본으로 하고, 재고 전용 경로가 필요한 문서 조건이 확인되면 `PUT /products/{no}/variants/{code}/inventories`로 같은 절대 수량을 보낸다. 두 경로를 이유 없이 모두 호출하지 않는다.

API가 성공 HTTP를 줘도 품목별 실패가 있으면 모두 성공한 것으로 저장하지 않는다. 완료 읽기에서 모든 variant가 재고 관리 T이며 수량과 추가금액이 snapshot과 같은지 확인해야 activation을 허용한다. 품목 조회와 응답별 오류 envelope는 실제 fixture를 확인해야 할 항목이다.

HIDDEN은 마지막에도 display F, selling F다. ON_SALE은 모든 필수 단계가 끝나고 수량이 하나 이상 있으면 display T, selling T다. 전부 0이면 display T, selling F로 품절 상태를 보여주며 결과에 이를 안내한다. 상품 생성 직후부터 수량 제한 없이 팔리는 구간을 만들지 않는다.

### 9.4 이미지

`GET /images/setting`의 대표 이미지 폭과 높이를 정규화해 비율을 얻는다. 응답 세부 키를 못 읽거나 크기가 0이면 카페24만 `image_setting_unavailable`로 막는다. 정사각형이라고 조용히 가정하지 않는다. 설정 조회는 현재 4개 scope 중 mall.read_store로 가능하므로 추가 설치가 필요 없다.

EXIF 방향을 반영하고 RGB로 바꾼 뒤 중심 자르기를 기본으로 한다. UI는 같은 비율의 최종 잘림을 보여준다. 얼굴이나 의류를 찾는 AI는 호출하지 않는다. 긴 변 2,000px 이하의 JPEG로 만들고 업로드 제한에 맞게 압축한다. 장당 5,000,000 bytes 이하, 한 요청 20장 이하, JSON과 base64를 포함해 보수적으로 30,000,000 bytes 이하로 배치한다. 마지막 두 제한은 additionalimages 호출에 적용하며 상세설명 이미지 배치와 섞지 않는다.

`POST /products/{no}/additionalimages`는 기본 최대 4장에 적용하되 API 배치 구현은 20장 경계를 검사한다. 추가 이미지의 정확한 요청 필드와 응답 구조, 목록 재조회 및 중복 비교 가능성은 구현 전 fixture로 고정한다. 응답 유실 후 이를 확인할 수 없으면 단계 unknown으로 남기고 관리자에서 확인하게 한다.

최종 이미지는 카페24에 저장되므로 Wearless 게시 만료가 카페24 상세를 깨뜨리지 않는다. 관리자 링크는 검증한 mall_id로 만든 고정 도메인만 허용한다. store.mall_url을 쓸 때에도 HTTPS와 허용 도메인을 검사하며 임의 URL을 그대로 UI 링크로 내리지 않는다.

## 10. 지그재그 어댑터, Phase B 설계 범위

Phase A는 지그재그 등록을 준비 중으로 표시한다. 키와 알파 접근을 받기 전에는 아래 스키마 매핑과 확인 목록만 확정한다. 환경별 연결 열, 이미지 staging 경로, 별도 표와 잠금, 삭제 경합, 복구 구현은 알파 응답을 확인한 뒤 별도 설계한다. 이 문서의 B Task는 지금 구현할 코드 지시가 아니다.

### 10.1 스키마로 확인한 전송 매핑

상품 API는 HMAC 인증과 카카오스타일이 발급한 x-solution이 필요하다. ZIGZAG_X_SOLUTION은 서버 비밀이며 SSM으로 주입한다. ZIGZAG_REGISTER_ENABLED는 false가 기본이고 키 또는 알파 검증이 없으면 준비 중이다. mutation은 `mutation CreateProduct($input: CreateProductInput!) { createProduct(input: $input) }`이며 반환형은 ID!다. 후속 구현은 MockTransport로 query, variables와 헤더를 고정하고 실제 수용 여부는 알파에서 확인한다.

상품 출처인 domestic과 imported를 제조국 또는 병행수입 여부로 해석하지 않는다. 시중가는 몰의 기준 판매가와 같게 보내고 품목 판매가에는 추가금액만 반영한다. 아래 매핑은 스키마의 필드와 입력 출처를 정한 것이며 상태 조합과 스토어별 필수 조건까지 실증한 뜻은 아니다.

| 입력 | 확정 매핑 |
|---|---|
| name | snapshot.productName. 허용 문자 규칙을 검증하며 삭제가 필요하면 셀러에게 정리 결과를 확인받음 |
| description | 불변 게시 조각의 서버 생성 HTML. 10자 이상, 공개 이미지 URL만 포함 |
| fulfillment_type | MERCHANT. 폐기 예정 type은 보내지 않음 |
| essential_code | 선택한 말단 카테고리에 연결된 실제 템플릿 code |
| essentials | `[{key,name,value}]`. 템플릿의 필수 key와 10항목 매핑 및 추가 입력. name과 value는 필수 문자열 |
| sales_status | HIDDEN이면 PREPARING, ON_SALE이면 ON_SALE. 전부 품절이면 SOLD_OUT |
| display_status | HIDDEN이면 HIDDEN, ON_SALE이면 VISIBLE. 노출이나 검수 완료를 보장하는 표현은 하지 않음 |
| option_list | 색상과 사이즈, value_list의 value 및 안정된 external_code. option_type BASIC, format TEXT |
| item_list | 각 조합의 name, external_code, sales_status, attribute_list, inventory, site_list, option_type BASIC |
| item_list.attribute_list | `[{name:'색상',value:색상명},{name:'사이즈',value:사이즈}]`. option_list와 정확히 대응 |
| item_list.inventory | `{quantity}`. 0은 SOLD_OUT, 양수는 목표 판매 상태와 일치 |
| item_list.site_list | `[{site,country,original_price:몰 기준가+추가금액}]`. 이 original_price는 스키마상 품목 판매가 |
| image_list | MAIN 정확히 1장, 그 뒤 SUB. `{image_type,origin_url}`만 기본 전송. deprecated width와 height 생략 |
| category | `{category_id:말단 ID}`. 임의의 부모 카테고리나 카페24 ID 금지 |
| site_list | 허용된 채널과 국가 하나. original_price는 몰 기준 판매가, shipping_fee는 아래 구조 |
| shipping_fee | fee_type, base_fee, area_fee `{jeju,isolated}`, conditional_amount, return_fee `{total,partial}`, exchange_fee |
| address | `{return_id}`. 현재 연결 스토어 주소록에서 재검증한 ID |
| shipping_days | 공통값 또는 해당 등록의 override, 기본 2 |
| tax_type | TAX 또는 FREE |
| parallel_imported | 명시한 값이 있으면 PARALLEL_IMPORTED 또는 NOT_PARALLEL_IMPORTED. 상품 출처만으로 추정하지 않음 |
| brand_id | 허용 브랜드가 하나면 자동 제안, 여러 개면 기본 설정에서 반드시 고른 값. 허용 브랜드가 없고 스토어의 필수 조건도 없을 때만 생략 |
| size_detail_list | 검증한 사이즈 템플릿과 실측이 있을 때만 SELLER, size_category_id, value_list 전송. 불일치면 생략하고 경고 |
| search_keyword_list | 규칙으로 뽑은 상품명과 의류 종류 최대 3개, `{value}`. 없으면 생략 |
| external_code | 최초 잡에서 정한 externalCode. 재시도에도 유지 |

### 10.2 알파에서 확인할 목록

1. 발급 키와 x-solution, 상품 조회와 등록 권한, production과 alpha endpoint의 구분, HMAC 알고리즘과 실제 signed bytes를 확인한다. shop 성공을 상품 쓰기 권한의 증거로 보지 않으며 mutation에 알고리즘 자동 재시도를 하지 않는다.
2. category의 children과 asset_list.values, getAllEssentialTemplate의 values를 받아 말단과 필수 고시의 연결을 확인한다. values는 CrJson이므로 내부 key를 추정하지 않는다. 10개 고시 외 추가 필수 항목과 저장 방식도 이때 설계한다.
3. 허용 채널과 국가, 브랜드 필수 조건, 병행수입 값의 생략 가능 여부, 배송 주소록의 실제 ID와 발송 소요일 범위를 확인한다.
4. PREPARING, ON_SALE, SOLD_OUT과 HIDDEN, VISIBLE의 상품 및 품목 조합, 품목 200개 경계, 가격 하한과 10원 단위, 추가금액 반영을 확인한다.
5. MAIN 1장과 SUB의 origin_url 복사, 대표 이미지 권장 비율, description의 외부 이미지 복사 여부와 등록 검수를 확인한다. private 자산의 인증 주소는 origin_url로 보낼 수 없으므로 공개 전달 방법과 수명은 알파 뒤 설계한다.
6. getProductId의 external_product_id는 스키마상 호스팅사의 product_no다. external_code 검색이라고 가정하지 않는다. product_list의 완전성과 정확 일치 조회가 복구에 쓸 수 있는지 확인한다. 불명확한 생성은 확인 전까지 자동 재전송하지 않는다.
7. 유효한 생성 ID와 GraphQL 오류가 함께 올 때의 처리, 응답 유실, 조회 지연을 확인한다. 처음부터 몰 공통의 선커밋과 생성 사실 우선 원칙을 따른다.
8. size_detail_list는 실제 실측과 맞는 템플릿이 있을 때만 보낸다. updateItemAvailableStockQuantity의 product_id, item_id와 실제 수량 반영을 확인하되 주문 연동과 등록 후 재고 편집 UI는 이번 범위에 넣지 않는다.

확인한 응답 fixture와 수동 인수 증거를 바탕으로 어댑터, 인증키의 환경 구분, 재시도 및 이미지 보관의 후속 설계를 작성한다. 그 설계와 오너의 보관 결정이 완료되기 전에는 운영 등록 플래그를 켜지 않는다.

## 11. 화면 구성

### 11.1 판매 기본 정보와 연결

설정 화면 위에는 공통 판매 기본 정보 카드 하나를 둔다. 배송 정책 세 가지, 제주와 도서산간 추가비, 발송 소요일, 전체와 부분 반품비, 교환비, 제조국과 제조자, AS 책임자와 전화번호, 과세와 출처, 등록 직후 상태를 저장한다. 품질보증 문구는 승인한 기본 문구와 수정 기능을 둔다.

줄마다 사용하는 몰을 표시한다. 배송과 반품은 지그재그, 카페24는 몰 설정 사용으로 보인다. 원산지와 소재는 카페24 요약에도 쓰인다. 몰별 주소는 연결 줄 아래에 남긴다. 브랜드와 채널 선택은 지그재그 알파 뒤 설계한다. 기존 ZigzagConnection 안의 공통 체크리스트를 카드로 올리고 해당 파일의 export와 호출부를 함께 바꾼다. 이름만 유지하려는 wrapper는 만들지 않는다.

카페24 관리자에서 앱을 열면 개발자센터의 App URL `https://ai.wearless.kr/settings/malls`로 들어온다. 카페24가 붙여 보내는 것으로 알려진 `mall_id` 쿼리가 있으면 카페24 몰 아이디 칸을 미리 채운다(공식 문서 미확인). 이 값은 입력 보조일 뿐 연결 근거가 아니며, 설치는 기존 install, state 쿠키, OAuth 콜백 검증을 그대로 거친다. 서버에서 hmac을 검증하지 않고, 쿼리의 다른 값은 읽거나 저장하지 않는다. 채운 뒤에는 주소창에서 카페24 쿼리를 지운다. 카페24 심사가 App URL에서 바로 OAuth를 시작하라고 요구하면 hmac 검증 설계를 따로 추가한다.

에이블리는 준비 중과 `셀러스 입점만 연결할 수 있어요. 파트너스는 연결할 수 없어요`를 표시한다. 제휴 문의 때 AI 이미지 정책을 확인할 항목도 운영 체크리스트에 남긴다. 미확인 정책을 공식 금지라고 단정하지 않는다.

### 11.2 한 번에 등록 창

상단은 몰 선택과 몰마다 점검이다. 그 아래 확인 구역은 상품명, 상세 이미지 N장과 지금 편집본, 대표 이미지, 순서가 보이는 추가 이미지, 옵션과 사이즈 고치기, 몰별 카테고리, 고시 10항목과 빈칸 개수다. 입력 구역은 판매가와 옵션마다 같은 재고 한 칸이다. 몰마다 다른 가격, 옵션별 수량과 추가금액, 배송 및 진열 변경은 기본 닫힌 펼침이다.

카테고리는 몰별 선택으로 표시한다. 고시 채우기는 에디터와 기본값으로 채운 값, 빈 값, 템플릿 추가 항목을 보여준다. 저장은 에디터에 반영된 뒤 재게시와 점검을 다시 한다. 저장 중, 충돌, 게시 실패는 다른 단계와 구별해 알린다.

하단에는 지그재그 실제 배송비와 조건, 발송일, 반품지 이름 및 `카페24는 몰 기본 배송 설정`을 표시한다. `재고는 몰끼리 자동으로 맞춰지지 않아요`를 한 번만 보여준다. 선택한 ready 몰이 두 곳이면 `2곳에 등록`, 한 곳이면 `카페24에만 등록` 또는 `지그재그에만 등록`, 0곳이면 비활성 `확인 후 등록`이다. preparing 몰은 선택할 수 없다.

연결되지 않은 몰의 메뉴는 기존처럼 연결하기로 설정에 간다. 연결되어 등록 가능한 몰의 메뉴는 `onRegisterMall(mall)` 한 핸들러로 같은 창을 연다. 선택 초기값은 누른 몰 하나이며 준비된 다른 몰은 셀러가 추가 선택한다. 자격이 없는 몰을 클릭했다는 이유로 다른 몰에 자동 등록하지 않는다.

### 11.3 결과와 복원

결과 카드마다 몰 이름, 생성 상품 번호, 진열과 판매 상태, 실패한 단계, 다음 행동을 표시한다. 상품 생성 후 재고 실패는 `상품은 만들어졌어요. 재고 설정을 마치지 못해 진열과 판매를 꺼 두었어요`로 표시한다. 활성화 응답이 불명확하면 꺼 두었다고 단정하지 않고 `판매 상태를 확인해 주세요`로 표시한다.

완료한 몰은 완료로 유지한다. 실패한 지그재그만 다시 시도할 때 카페24 생성 요청을 보내지 않는다. 폴링 제한 10분은 유지하며 시간 초과는 실패가 아니다. 창을 닫거나 새로 열어도 서버의 recoveries와 batchId로 이어 간다. 내역 API 실패를 신규 접수 허가로 취급하지 않는다. 계정과 프로젝트가 바뀌면 이전 비동기 응답은 버린다.

### 11.4 화면과 접근성 규칙

Vite, React, JavaScript, CSS Modules와 기존 토큰을 사용한다. 공용 화면의 orb, 글래스, 기존 사진 타일 비율과 콘티 서체를 임의로 바꾸지 않는다. 컴포넌트는 UI와 상태만 소유하고 모든 통신은 `src/lib/api`를 거친다.

한국어는 `word-break: keep-all`, 제목과 짧은 문구는 `text-wrap: balance`를 유지한다. 새 CSS가 body의 줄바꿈 규칙을 덮지 않는다. 나란한 버튼은 `.modal-actions`의 같은 알약 모양을 따른다. 모바일 입력은 16px 이상, 본문 15px, 라벨 14px, 최소 글자 13px이다. 문구는 짧게 쓰며 줄표와 불필요한 반복을 쓰지 않는다.

모달은 role dialog, aria-modal, 제목 연결, Tab 순환, Escape 닫기, 닫은 뒤 원래 메뉴 버튼 초점 복원을 지원한다. 접힌 구역에는 aria-expanded와 controls를 연결한다. 제출 오류는 초점을 받을 수 있는 상단 요약과 해당 필드 오류를 함께 표시하며 오류 링크가 펼침을 연다. 고정 푸터가 초점 필드를 가리지 않게 스크롤 여백을 둔다. 몰별 결과와 진행은 aria-live로 전달하고 색만으로 상태를 표시하지 않는다.

최대 높이 90vh, 본문 스크롤, 머리와 하단 고정으로 한다. 상품 입력 후 PC에서 이어 하는 모바일 정책을 확대하지 않는다. 화면 구현 뒤 PC 1280px와 320, 360, 375, 390, 414px, Chromium과 WebKit에서 화면과 단어 줄바꿈, 가로 넘침을 검사한다. Codex에서는 브라우저를 실행하지 않고, 화면 검사는 Claude가 허용된 환경에서 별도 수행한다.

## 12. 보안과 운영 오류

인증키는 기존 MALL_KEYS_KEK로 암호화하며 카페24 access와 refresh 회전 규칙을 바꾸지 않는다. 새 지그재그 호출에서도 키, x-solution, Authorization, 서명, 원문 오류, httpx 예외 객체를 로그와 jobs, job_events, 결과에 보관하지 않는다. 각 클라이언트가 안전한 상태 코드와 내부 분류만 남기는 예외로 바꾼다.

등록 접수, 조회, verify, resume, 목록은 소유자와 kind, mall을 SQL에서 제한한다. 타인의 프로젝트, 게시, 대표 이미지, 옵션 입력을 참조할 수 없어야 한다. 스냅샷의 주소는 ID만 보관하고 상세 주소나 개인정보를 불필요하게 복제하지 않는다. 일반 `/v1/jobs` 조회에도 비밀이 없는지 검사한다.

mall_id는 현재 정규화와 호스트 검증을 유지한다. R2 접근은 서버가 소유권을 확인한 키만 사용한다. 임의 URL로 이미지를 가져오거나 오류 메시지 안의 링크를 열지 않는다. 이미지 디코딩에는 프로젝트 업로드 용량 제한과 픽셀 상한을 적용하고 이미지 변환은 이벤트 루프 밖에서 처리한다. 외부 description HTML은 고정 builder와 escape만 사용한다.

플래그, 해제, 계정 폐쇄는 다음 외부 쓰기 직전 검사한다. 이미 생성한 상품을 자동 삭제하거나 되돌리지 않는다. 몰 연결이 바뀌면 이전 스토어의 상품을 새 연결로 조회하거나 갱신하지 않는다. account purge는 공통 기본값, 몰별 기본값과 연결 암호문을 함께 정리하고 기존 게시 삭제 규칙을 따른다. 지그재그 보존 확장과 탈퇴의 관계는 알파 뒤 별도 설계한다. 원격 상품 자체는 셀러 몰에 남는다.

서버 지표는 몰, stage, 내부 code, 외부 HTTP 상태, 소요 시간, 생성 불명확 수, 설정 미완료 수, lease 회수 수, blocked 이유별 수만 둔다. payload 전체나 고시 내용은 지표 label로 쓰지 않는다. x-solution 누락은 운영 로그에는 내부 코드, 셀러 화면에는 준비 중만 보인다.

## 13. 지그재그 게시 보관 정책 선택지

| 선택지 | 동작 | 평가 |
|---|---|---|
| 기존 보관 만료 유지 | 무료와 해지 후 6개월에 삭제. 등록 전과 결과에 실제 expiresAt 안내 | 가장 단순하지만 몰 상세가 깨질 수 있음 |
| 등록한 게시만 자동 만료에서 제외 | 원격 등록이 확인됐거나 결과가 미확인인 게시를 보존 | 추천. 비용과 보존 약속은 오너 결정 |
| 상세 이미지 복사를 확인할 때까지 공개 보류 | 알파 검증만 수행 | 알파와 보관 결정 전 운영 기본값 |

추천은 두 번째이며 결정 전에는 세 번째를 따른다. MAIN과 SUB의 origin_url 복사와 description 이미지 복사를 구별한다. 알파에서 상세 이미지도 안전하게 복사한다고 확인되면 별도 장기 보존이 필요한지 다시 판단한다. Phase A는 기존 게시 수명을 바꾸지 않는다.

보존안을 채택한 뒤에 필요한 저장 구조, 프로젝트 및 잡 삭제와의 관계, 이미지 전달용 공개 파일, 만료 정리와 명시 삭제, 탈퇴, 잠금과 경합 테스트는 알파 확인 뒤 별도 설계한다. 지금은 참조 표 DDL이나 삭제 API 확장을 미리 정하지 않는다.

비용은 알파에서 측정한 이미지 수와 크기로 산정한다. 조사 보고의 월 GB당 약 0.015달러를 가정하면 게시당 10MB, 1,000개에서 저장비 약 0.15달러라는 참고치가 있다. 현재 요금의 확인된 견적은 아니며 서비스 종료 시 약속과 보존 기간은 오너 결정이다.

## 14. 출시 전 배포 순서와 되돌리기

1. #593과 Phase A의 최종 코드를 합친 상태에서 A07 검증을 마친다. 기존 마이그레이션은 유지하고 공통값과 잡 인덱스용 새 전진 마이그레이션 하나를 준비한다.
2. 플래그를 모두 끈 상태에서 로컬 테스트 DB로 SQL과 단일 잡 계약을 검증한다. 운영 dry-run 결과를 보고 오너 승인 후 적용하며 표, 제약, RLS, ACL과 인덱스를 물리 확인한다.
3. 서버와 프론트, mock을 같은 새 API로 배포한다. mall_register는 api의 JOB_KINDS=-detail_page 범위에서 실행되며 JOB_CONCURRENCY=1은 유지한다. detail-worker는 detail_page 전용이다.
4. 카페24 앱(10월 28일 오너 생성, 개발자센터 권한은 상품분류 읽기, 상품 읽기와 쓰기, 상점 읽기로 `CAFE24_SCOPES`와 같다. Redirect URI는 `https://api.wearless.kr/v1/malls/cafe24/callback`), SSM `CAFE24_CLIENT_SECRET`(10월 28일 저장, 태그 확인), 매니페스트 variables의 `CAFE24_CLIENT_ID`와 `CAFE24_ALLOWED_MALL_IDS`(테스트몰), 매니페스트 secrets의 `CAFE24_CLIENT_SECRET` 참조를 확인하고 오너 승인 후 MALL_CONNECT_ENABLED, DETAIL_PUBLISH_ENABLED, CAFE24_ENABLED, MULTIMALL_REGISTER_ENABLED를 켠다. 테스트몰에서 옵션, 재고와 추가금액, 이미지, 가격 기준 B, 숨김과 판매 전환, 설정 이어 하기를 인수한 뒤 공개한다.
5. 지그재그는 플래그를 끈 채 키와 알파 접근을 기다린다. 10절 확인과 13절 보관 선택, 별도 구현 설계가 끝난 뒤 출시 순서를 정한다. 값이 없는 SSM secret 참조를 먼저 배포하지 않는다.

#593을 단독으로 머지하지 않으므로 이전 형식의 운영 잡은 생기지 않는다. drain과 전환 절차는 두지 않는다. (`server/app/main.py:692`와 `server/app/config.py:674` 기준으로 #593 단독 머지 후 플래그를 켜면 등록이 가능하다는 Codex 지적은 맞지만, 그 경로 자체를 오너 지시로 막는다.)

되돌릴 때는 등록 플래그를 끄고 다음 외부 쓰기 직전 중단 규칙을 적용한다. 이미 보낸 생성은 unknown 또는 created로 보존하며 다시 만들지 않는다. 새 전진 마이그레이션을 역으로 삭제하지 않고, 수정한 코드는 플래그를 끈 상태에서 재배포한다.

## 15. 미확인과 오너 결정

### 외부 확인이 필요한 사실

1. 카페24 images/setting의 실제 폭과 높이 필드, variants 조회의 옵션 매핑 응답, bulk PUT의 품목별 실패 구조, additionalimages의 실제 request envelope와 조회 복구 방법. 사용자가 확인한 endpoint와 기능 사실은 확정이며 이 세부 JSON은 fixture로 고정해야 한다.
2. 카페24 B 가격의 세금과 반올림을 자동 변환하는 공식 계약. 확인 전 이번 설계의 명시적 세전 입력을 사용한다.
3. 지그재그 HMAC 알고리즘과 signed bytes, 실제 상품 권한, x-solution 발급 값, alpha 사용 자격.
4. 카테고리 asset_list.values와 essential template values의 실제 JSON, 필수 고시 추가 필드, size_category 매핑, 브랜드가 필수인 스토어 유형.
5. 지그재그 PREPARING 품목 상태와 판매, 노출의 실제 조합, 10원 단위와 가격 하한, shipping_days 지원 범위, 등록 검수와 실제 노출 시점.
6. 지그재그 상세 HTML 이미지의 복사 여부, 대표 이미지 권장 비율, product_list의 완전성, getProductId의 외부 번호 대응. image_list.origin_url 복사와 구별한다.
7. 에이블리의 API 계약, 솔루션 제휴와 AI 이미지 정책. 이번 구현에서는 호출하지 않는다.

MockTransport는 위 계약에 맞는 코드가 작성됐다는 증거이며 외부 서비스가 그 계약을 받는다는 증거는 아니다. 미확인 항목은 알파 또는 테스트몰 검증 gate로 유지한다.

### 오너 결정, 추천안 포함

| 결정 | 추천 | 결정 전 처리 |
|---|---|---|
| 제조연월과 품질보증기준 | 제조연월은 셀러 입력. 품질보증기준만 승인한 표준 문구를 기본값으로 제공 | madeAt을 현재 월로 만들지 않음. warranty는 빈칸. 카페24 Phase A는 고시 빈칸 때문에 막지 않음 |
| 지그재그 상세 이미지 보존 | 원격 등록 참조가 있는 게시를 자동 만료에서 제외하고 명시 삭제와 탈퇴는 허용 | Phase B 운영 플래그를 끔. 알파에서 복사 여부부터 확인 |
| 카테고리 AI 추가 | 무료 이름 매칭과 의류 종류 사전으로 먼저 출시 | 호출 0건. 필요 시 모델, 횟수, 달러 비용을 산정해 별도 승인 |

몰별 잡, 공통 기본값 새 표, 숨김 상태로 초기 설정, B 가격 의미의 명시 입력, Phase 구분은 이 문서의 구현 제안이다. 오너가 이미 정한 방향과 구별해 검토한다.

## 16. Codex 작업 분할과 검증

설계 작성은 gpt-6.1-sol, max다. 구현은 같은 모델의 xhigh를 기본으로 하고 문구와 CSS만 다루는 A06은 high, DB와 권한 및 운영 전환의 머지 전 최종 검증 A07만 max로 둔다. 두 번 되돌린 재작업 등 CLAUDE.md의 예외가 실제 발생하면 그때 다시 배정한다.

Phase A는 구현 6개와 최종 검증 1개다. repo.py의 SQL과 마이그레이션은 A01로 모으고 mall_connect.py와 config.py의 서버 계약은 바로 다음 A02에서 끝낸다. Editor.jsx와 모달의 입력, 결과 및 복원은 A05로 합친다. 공유 파일을 서로 다른 작업자가 동시에 편집하지 않는다. 화면 마감은 기능 검토와 구분할 필요가 있어 인접한 A06으로만 나눈다.

예정 테스트 수는 아래에서 이름을 고정한 핵심 인수 시나리오 수다. 기존 회귀 테스트나 매개변수화 실행 건수는 합산하지 않는다. 삭제하는 이전 API의 테스트는 새 계약의 같은 검증으로 바꾸며, 사라진 형식을 유지하기 위한 테스트는 남기지 않는다. 문구와 CSS 작업을 위해 구현을 그대로 확인하는 새 테스트를 만들지 않는다.

실행자는 이 문서, CLAUDE.md와 현행 계약을 읽는다. 유료 API 호출 금지, 필요하면 Claude에게 넘김. 브라우저 실행 금지, 화면 검사는 Claude가 한다. 외부 네트워크, 커밋, git add, stash는 별도 허용 전까지 금지다. HTTP 테스트는 MockTransport 또는 ASGITransport만 사용한다. 아래 마이그레이션 파일은 supabase/migrations 아래에 migration new로 생성하며 실제 타임스탬프를 작업 보고에 적는다.

### A01. 데이터 모델과 등록 저장소, 예정 테스트 11개

Effort: xhigh. 이유: 새 RLS 표와 활성 잡 인덱스, 몰별 경합과 원자적 저장을 함께 바꾸는 구현이다.

파일: 생성 `supabase/migrations/<A_SCHEMA_TS>_multimall_registration.sql`, `server/app/services/mall_sales_defaults.py`, `server/tests/test_multimall_storage.py`, `server/tests/test_multimall_storage_postgres.py`. 수정 `server/app/repo.py`의 mall 기본값, 연결 변경, create_job와 등록 검색 함수. 기존 PG fixture는 `server/tests/test_mall_connect_postgres.py`의 로컬 DSN 제한과 스키마 격리를 재사용한다.

산출물은 공통 기본값 표, mall_defaults의 주소 revision, 새 잡 인덱스, 공개 키가 없는 저장 함수다. 이전 공통값 열은 남기고 코드에서는 읽거나 쓰지 않는다. SQL helper는 커밋하지 않으며 접수 라우트가 트랜잭션을 소유한다.

```python
def normalize_sales_defaults(previous: dict, patch: dict) -> dict
def sales_defaults_view(row: dict | None) -> dict
async def get_sales_defaults(conn, *, user_id: str) -> dict | None
async def save_sales_defaults(conn, *, user_id: str, base_revision: int, patch: dict) -> dict
async def save_mall_specific_defaults(conn, *, user_id: str, mall: str, base_revision: int, patch: dict) -> dict
async def load_registration_source(conn, *, user_id: str, project_id: str, publication_id: str, asset_ids: list[str], lock: bool = False) -> dict
async def get_mall_batch_jobs(conn, *, user_id: str, batch_id: str) -> list[dict]
async def find_mall_registration_recoveries(conn, *, user_id: str, project_id: str, mall: str, shop_id: str) -> list[dict]
async def enqueue_mall_registration_batch(conn, *, user_id: str, project_id: str, batch_id: str, request_hash: str, payloads: dict[str, dict], blocked: list[dict]) -> dict
async def requeue_mall_registration(conn, *, user_id: str, job_id: str, request_key: str) -> tuple[dict, bool]
```

payloads는 서버 점검을 통과한 몰별 불변 입력이다. enqueue는 프로젝트 등록 잠금 아래 같은 batchId 재생, 몰별 활성 충돌, 신규 잡 수만큼 한도 소비, 영수증 저장을 처리한다. 기존 create_job의 SAVEPOINT와 다른 kind의 합류 규칙은 보존한다. source 잠금 순서는 8.1절을 따른다.

테스트와 경계:

1. `test_sales_defaults_rls_acl_and_migration_shape`, 새 표와 제약, anon 및 authenticated 쓰기 회수의 실제 PG 확인.
2. `test_sales_defaults_first_save_and_fee_validation`, revision 0 최초 저장, 조건부 금액과 Boolean 및 소수 거부.
3. `test_concurrent_defaults_revision_has_one_winner`, 실 PG 두 연결의 낙관적 잠금.
4. `test_store_change_clears_only_own_addresses`, 공통 기본값과 타 몰 주소 보존.
5. `test_sales_defaults_never_read_deprecated_mall_columns`, 이전 열이 채워져 있어도 지연 이전이나 fallback 없음.
6. `test_two_malls_create_distinct_active_jobs`, 실 PG에서 같은 프로젝트의 두 몰이 두 행을 만듦.
7. `test_competing_batches_same_mall_have_one_active_winner`, 실 PG unique 대기와 한 승자.
8. `test_receipt_jobs_and_quota_commit_or_rollback_together`, 실 PG에서 두 번째 insert 또는 한도 실패 시 신규 잡과 영수증 0건.
9. `test_unresolved_job_beyond_latest_five_is_recovered`, 오래된 미확인 생성이 새 등록을 막음.
10. `test_concurrent_resume_preserves_snapshot_and_checkpoints`, 실 PG에서 한 번만 재큐하며 완료 단계 유지.
11. `test_non_mall_active_join_behavior_is_unchanged`, 기존 일반 잡 합류 회귀.

경합 테스트는 연결 A의 미커밋 insert 뒤 Event로 연결 B를 시작하고 pg_blocking_pids로 실제 대기를 확인한 다음 A를 커밋한다. DB 잠금은 mock repo나 SQLite로 대신하지 않는다.

### A02. 단일 등록 계약과 서버 API, 예정 테스트 13개

Effort: xhigh. 이유: 소유권 검사, 편집 리비전과 연결 세대, 부분 접수 및 멱등성을 여러 서버 경로에서 일치시킨다.

파일: 생성 `server/app/services/mall_registration_contract.py`, `server/app/services/mall_catalog.py`, `server/app/services/mall_preflight.py`, `server/app/mall_registration.py`, `server/tests/test_multimall_routes.py`, `server/tests/test_multimall_routes_postgres.py`. 수정 `server/app/mall_connect.py`, `server/app/config.py`, `server/app/main.py`, `server/app/services/cafe24_client.py`의 읽기 메서드, `copilot/api/manifest.yml`의 false 기본 플래그와 주석. `documents/common_data_contract.md`, `CONTEXT.md`와 필요한 `documents/PRD.md`도 새 API 계약에 맞춘다. 기존 연결과 등록 API 테스트는 새 기본값 및 등록 경로를 호출하도록 갱신한다.

A01의 SQL 함수를 소비한다. 3.5절 잡 형식 하나와 5절 API만 제공한다. 카페24 전용 등록 라우트, zigzag/defaults PUT, 중복 응답 변환을 제거하며 OAuth와 토큰 회전은 유지한다. Phase A의 지그재그와 에이블리 등록 capability는 preparing이다.

```python
def normalize_register_draft(value: dict) -> RegisterDraft
def registration_request_hash(draft: RegisterDraft) -> str
def derive_registration_outcome(job: dict) -> str | None
def registration_view(job: dict) -> dict
def resolve_notice(blocks: list[dict], product: dict, defaults: dict) -> NoticeResolution
def build_variants(product: dict, sizes: list[str], default_quantity: int, overrides: list[dict]) -> list[VariantSnapshot]
def freeze_registration(draft: RegisterDraft, *, source: RegistrationSource, facts: MallFacts) -> RegistrationSnapshot
async def load_mall_facts(app, *, user_id: str, mall: str) -> MallFacts
def evaluate_preflight(draft: RegisterDraft, *, source: RegistrationSource, facts_by_mall: dict[str, MallFacts]) -> PreflightResult
def recommend_categories(product: dict, categories: list[CategoryNode]) -> list[CategorySuggestion]
async def preflight_registration(request: Request, body: PreflightRequest, user_id: str) -> dict
async def create_registration_batch(request: Request, response: Response, body: BatchRegistrationRequest, user_id: str) -> dict
async def get_registration_batch(request: Request, batch_id: str, user_id: str) -> dict
async def get_registration(request: Request, job_id: str, user_id: str) -> dict
async def verify_registration(request: Request, job_id: str, user_id: str) -> dict
async def resume_registration(request: Request, job_id: str, body: ResumeRegistrationRequest, user_id: str) -> dict
```

Cafe24Client의 읽기 계약은 `async def image_setting(self, access_token: str) -> Cafe24ImageSetting`, `async def product_state(self, access_token: str, product_no: int) -> Cafe24ProductState`, `async def variants(self, access_token: str, product_no: int) -> list[Cafe24Variant]`다. categories는 기존 메서드를 사용하되 300개 절단을 없앤다. client 응답 DTO는 해당 client 모듈에서 정의한다. 함수 시그니처의 user_id는 실제 라우터에서 require_user 의존성으로 주입한다.

테스트와 경계:

1. `test_common_notice_preserves_editor_values_and_requires_real_measurements`, 기존 입력 우선, 안내문과 FREE를 실측으로 간주하지 않음.
2. `test_variant_keys_quantities_and_prices_are_unambiguous`, 중복 사이즈, 0 수량, 추가금액 overflow와 안정된 조합 키.
3. `test_batch_hash_preserves_semantic_order_and_frozen_input`, 몰 순서 정규화와 이미지 순서 보존, 접수 뒤 기본값 수정의 영향 차단.
4. `test_preflight_is_read_only_and_blocks_only_affected_mall`, 잡과 한도 소비 없이 고시 누락 및 준비 중을 몰별로 반환.
5. `test_ready_mall_is_accepted_when_blocked_mall_has_no_source_versions`, 부분 접수를 전체 409로 막지 않음.
6. `test_admission_rechecks_connection_defaults_and_catalog_versions`, 바뀐 스토어와 배송비 및 진열 상태를 조용히 채택하지 않음.
7. `test_replay_returns_original_receipt_before_current_resource_checks`, 응답 유실 후 같은 키 재생, 다른 내용은 409.
8. `test_no_ready_or_quota_failure_creates_no_partial_receipt`, 미접수와 한도 롤백.
9. `test_registration_routes_enforce_project_publication_asset_and_job_ownership`, 타인 자원, 다른 kind와 몰의 접근 거부.
10. `test_category_pagination_price_basis_and_image_settings_fail_closed`, 잘린 목록, 미확인 비율, B 가격의 세전 확인.
11. `test_verify_and_resume_never_recreate_unknown_product`, 조회 전용 verify와 허용된 초기 설정 재개만 지원.
12. `test_editor_save_and_admission_revision_race`, 실 PG에서 게시와 프로젝트 revision을 잠그고 오래된 편집본 접수 거부.
13. `test_registration_responses_and_events_redact_secrets`, 키, Authorization과 upstream 오류 본문 비노출.

### A03. 카페24 어댑터와 작업 실행, 예정 테스트 15개

Effort: xhigh. 이유: 외부 상품 생성과 품목 재고의 비동기 단계, lease와 응답 유실 복구를 함께 구현한다.

파일: 생성 `server/app/services/mall_adapters/base.py`, `server/app/services/mall_adapters/cafe24.py`, `server/app/services/mall_adapters/ably.py`, `server/app/services/mall_images.py`, `server/app/workers/mall_register_context.py`, `server/tests/test_cafe24_registration_setup.py`, `server/tests/test_multimall_lease_postgres.py`. 수정 `server/app/services/cafe24_client.py`, `server/app/workers/mall_register_job.py`. #593의 관련 client와 worker 테스트는 단일 잡 입력과 새 단계로 갱신한다.

A01 저장 함수와 A02 DTO를 소비한다. base.py는 DTO를 import하고 7절의 Protocol과 registry만 정의한다. 카페24만 실제 등록 어댑터이며 두 전문몰은 preparing이다. 현재 상세 이미지 복사와 토큰 guarded call을 재사용하고 형식에 따른 워커 분기는 만들지 않는다.

```python
async def run_mall_register_job(app, job: dict) -> None
async def recheck_registration(app, job: dict) -> None
async def finish_registration(app, job: dict, *, code: str | None = None) -> None
def build_cafe24_product_request(snapshot: RegistrationSnapshot, *, description: str, price_basis: str, external_code: str) -> dict
def match_cafe24_variants(expected: list[VariantSnapshot], actual: list[Cafe24Variant]) -> dict[str, str]
def prepare_mall_jpeg(raw: bytes, *, ratio: tuple[int, int], max_side: int = 2000, max_bytes: int = 5000000) -> bytes
```

derive_registration_outcome와 registration_view는 A02의 mall_registration_contract.py에서 완성한 함수를 그대로 소비한다. JobContext의 recheck, checkpoint, call, mark_create_sent와 Cafe24Adapter의 다섯 메서드는 7절 시그니처를 그대로 구현한다.

```python
# Cafe24Client에 추가할 쓰기 메서드
async def update_variants(self, access_token: str, product_no: int, variants: list[dict]) -> VariantUpdateResult
async def set_variant_inventory(self, access_token: str, product_no: int, variant_code: str, inventory: dict) -> None
async def upload_additional_images(self, access_token: str, product_no: int, images: list[bytes]) -> AdditionalImageResult
async def set_product_state(self, access_token: str, product_no: int, *, display: str, selling: str) -> None
```

요청 envelope와 응답의 세부 JSON은 15절의 fixture 확인을 거친다. 임의로 만든 mock이 통과했다는 이유로 외부 계약이 검증됐다고 하지 않는다.

테스트와 경계:

1. `test_create_sends_options_material_origin_and_hidden_flags`, 옵션 S, 배송 기본 유지와 생성 시 판매 F.
2. `test_price_basis_b_sends_only_confirmed_price_excluding_tax`, 필드와 금액 의미 보존.
3. `test_variant_mapping_uses_names_values_and_rejects_missing_or_duplicate_rows`, 응답 순서 의존 금지.
4. `test_inventory_management_zero_stock_and_surcharges_are_applied_before_activation`, 무제한 판매 구간 방지.
5. `test_partial_inventory_failure_keeps_created_hidden_and_resumes_same_product`, 상품 생성과 설정 실패 구분.
6. `test_image_crop_and_additional_upload_limits`, EXIF와 비율, 20장, 장당 5MB와 요청 30MB 경계.
7. `test_lost_additional_image_response_never_blindly_appends`, 불명확한 POST 재호출 금지.
8. `test_completed_image_checkpoints_survive_worker_restart`, 이미 복사한 파일 건너뛰기.
9. `test_activation_response_loss_only_reads_state_and_never_resets_stock`, 활성화 뒤 수량 재설정 금지.
10. `test_hidden_on_sale_and_all_sold_out_final_states`, 모든 필수 단계 완료 후 목표 상태 적용.
11. `test_resume_stops_after_manual_remote_product_change`, 관리자 변경을 덮지 않음.
12. `test_create_sent_commit_precedes_call_and_recovery_only_looks_up`, 선커밋과 조회 복구, 조회 0건 뒤 재생성 금지.
13. `test_outcome_priority_product_id_rejection_and_unknown`, 공용 error 종료 뒤에도 생성 사실 우선.
14. `test_recheck_after_token_wait_enforces_flag_disconnect_purge_and_cancel`, 기다린 뒤 상태 재검증.
15. `test_stale_lease_cannot_patch_or_start_next_remote_call`, 실 PG에서 lease 교체와 늦은 응답을 재현.

### A04. 프론트 서비스와 판매 기본 정보, 예정 테스트 8개

Effort: xhigh. 이유: 실제와 mock 서비스 계약, 저장 revision과 몰별 주소를 여러 프론트 파일에서 함께 바꾼다.

파일: 수정 `src/lib/api/malls.js`, `src/lib/api/httpAdapter.js`, `src/lib/api/mockAdapter.js`, `src/mock/api.js`, `src/lib/mallDefaults.js`, `src/features/settings/MallConnections.jsx`, `src/features/settings/MallDefaultsChecklist.jsx`. 생성 `tests/frontend/multimall-api.test.mjs`, `tests/frontend/mall-sales-defaults.test.mjs`.

A02 API를 소비한다. 카페24 전용 등록 함수와 saveZigzagDefaults는 제거하고 호출부와 mock을 모두 새 계약으로 바꾼다. 이전 export를 남기는 wrapper는 만들지 않는다. MallDefaultsChecklist.jsx는 SalesDefaultsCard를 export하도록 고치고 몰별 주소 입력은 MallConnections.jsx가 소유한다.

```javascript
export function getSalesDefaults()
export function saveSalesDefaults({ baseRevision, ...patch })
export function getMallDefaults(mall)
export function saveMallDefaults(mall, { baseRevision, addresses })
export function getMallRegistrationCatalog(mall)
export function preflightMallRegistration(draft)
export function createMallRegistrationBatch({ batchId, draft })
export function getMallRegistrationBatch(batchId)
export function getMallRegistration(jobId)
export function verifyMallRegistration(jobId)
export function resumeMallRegistration(jobId, { requestKey })
export function SalesDefaultsCard({ defaults, onSaved })
export function MallAddressDefaults({ mall, defaults, onSaved })
```

테스트와 경계:

1. `services use one batch contract and preserve request identity`, 새 경로와 같은 키 재전송.
2. `http mode never falls back to mock for missing registration functions`, 가짜 성공 방지.
3. `mock reproduces blocked mall and mixed result contracts`, 실제 DTO와 오류 일치.
4. `sales defaults card shows split fees shipping days and hidden default`, 세 배송 정책과 전체 및 부분 반품비 유지.
5. `addresses stay under their own mall connection`, 공통값에 주소 ID를 쓰지 않음.
6. `defaults conflict keeps entered values and reloads current revision`, 저장 충돌 처리.
7. `preparing malls never expose a working registration or ably connect form`, 준비 중과 셀러스 안내.
8. `cafe24 app url mall_id only prefills the mall id field`, App URL의 mall_id는 몰 아이디 칸만 채우고 자동으로 설치를 시작하지 않으며, 잘못된 형식은 무시하고 쿼리는 주소창에서 지운다.

### A05. 한 번에 등록, 에디터 저장과 결과 복원, 예정 테스트 10개

Effort: xhigh. 이유: Editor와 모달의 저장, 게시, 부분 접수 및 복원을 한 상태 흐름으로 바꾼다.

파일: 수정 `src/features/editor/Editor.jsx`, `src/features/editor/ExportMenu.jsx`, `src/features/editor/MallRegisterModal.jsx`, `src/features/editor/presets/infoPresets.js`, `src/features/library/PublicationsPanel.jsx`. 생성 `src/features/editor/MallRegisterFields.jsx`, `src/features/editor/MallNoticeFields.jsx`, `src/features/editor/MallRegisterResults.jsx`, `src/features/editor/mallRegisterDraft.js`, `src/features/editor/mallNotice.js`, `src/features/editor/mallRegisterSession.js`, `tests/frontend/multimall-register.test.mjs`.

A04 서비스와 4절의 저장 계약을 소비한다. 등록 메뉴는 onRegisterMall 하나로 통일한다. 에디터 save guard와 pubScope 및 계정 세대 검사는 유지한다. 새로 선택하지 않은 몰을 자동 접수하지 않으며 history 조회 실패가 새 생성으로 이어지지 않는다.

```javascript
export function buildMallRegisterDraft({ projectId, product, blocks, assets, defaults, selectedMall })
export function patchNoticeBlocks(blocks, { fields }, context)
export function patchRegistrationSizes(blocks, sizes, context)
export function MallRegisterModal({ projectId, product, blocks, assets, malls, initialMall,
  waitForSave, onSaveNotice, onSaveSizes, onPublish, onConflict, onClose, onRegistered, anchorRef })
export function MallRegisterFields({ draft, preflight, onChange, disabled })
export function MallNoticeFields({ resolution, onSave, busy, error })
export function MallRegisterResults({ batch, onVerify, onResume, onRetryFailed })
export function createMallRegisterSession({ api, createId, isCurrent, onChange })
// Editor 내부 callback
async function saveMallNotice({ fields })
async function saveMallSizes(sizes)
```

session은 submit(draft), restore(projectId), verify(jobId), resume(jobId), retryFailed(mall, draft)를 제공하며 각각 Promise<RegistrationBatchView>를 반환한다. 고시 저장은 info와 렌더 요소를 함께 바꾸고 블록 ID 및 기존 실측을 보존한다. Phase B의 추가 고시 저장 필드는 알파 뒤 설계한다.

테스트와 경계:

1. `mall menus open one modal with clicked mall selected`, 메뉴와 선택 초기값.
2. `preflight drives partial registration labels and excludes preparing malls`, 준비된 몰 수와 버튼 문구 일치.
3. `notice and size edits save then republish before admission`, info와 렌더 일치, FREE에서 치수를 추정하지 않음.
4. `editor conflict or stale account response prevents submission`, 계정과 프로젝트 세대 및 저장 충돌.
5. `price stock and image overrides preserve their confirmed meaning`, 몰별 세전 가격, 수량 0, 추가금액과 이미지 순서.
6. `lost batch response retries the identical key and frozen draft`, 입력 변경 없이 접수 재생.
7. `mixed results retry only failed mall and resume setup on the same job`, 성공 몰 재생성 방지.
8. `unknown outcome only verifies and never creates again`, 불명확한 결과의 안전한 행동.
9. `timeout reopen and history failure never fall through to a new create`, 10분 제한과 서버 복원.
10. `keyboard focus returns to menu and error summary opens the invalid field`, 모달 초점과 펼침, 오류 접근성.

### A06. 문구와 화면 규칙 마감, 신규 테스트 0개

Effort: high. 이유: A04와 A05에서 정한 동작을 바꾸지 않고 문구와 CSS만 맞춘다.

파일: 수정 `src/features/editor/MallRegisterModal.module.css`, `src/features/editor/ExportMenu.module.css`, `src/features/settings/MallConnections.module.css`, `src/features/library/PublicationsPanel.module.css`와 A04, A05 컴포넌트의 표시 문구만.

함수 추가나 시그니처 변경은 없다. SalesDefaultsCard, MallRegisterModal, MallRegisterFields, MallNoticeFields, MallRegisterResults는 앞선 시그니처를 유지한다. 동작 수정이 필요하면 해당 소유 Task로 되돌리고 xhigh로 다룬다.

11.4절의 토큰, 알약 버튼, 16px 입력, 한국어 줄바꿈, 고정 푸터와 초점 가림을 확인한다. A05의 `keyboard focus returns to menu and error summary opens the invalid field`를 포함한 관련 테스트와 pnpm build를 재실행한다. 새 CSS 검사를 위해 구현을 그대로 복제한 테스트는 추가하지 않는다. 화면 검증은 Claude가 PC 1280px와 폰 5개 폭에서 Chromium과 WebKit으로 수행하고 증거를 제공한다. Codex는 브라우저를 실행하지 않는다.

### A07. 머지 전 최종 검증, 신규 테스트 0개

Effort: max. 이유: DB 마이그레이션과 RLS, 인증키 비노출, 중복 생성 방지 및 운영 배포 순서를 머지 전에 최종 검증한다.

파일: 읽기 검증 범위는 A01부터 A06의 변경 전체와 `documents/common_data_contract.md`, `CONTEXT.md`, `documents/PRD.md`, `copilot/api/manifest.yml`이다. 이 Task에서는 제품 코드와 배포 설정을 고치지 않고 검증 결과를 보고한다. 수정이 필요하면 소유 Task에 돌려준다.

검증 대상 함수는 run_mall_register_job(app, job), create_registration_batch(request, response, body, user_id), enqueue_mall_registration_batch(conn, *, user_id, project_id, batch_id, request_hash, payloads, blocked)다. 시그니처를 바꾸지 않고 각 Task의 구현과 계약을 대조한다. 문제를 발견하면 소유 Task로 되돌려 고친 뒤 영향 있는 검증만 다시 한다.

핵심 검증은 A01의 `test_receipt_jobs_and_quota_commit_or_rollback_together`, A02의 `test_editor_save_and_admission_revision_race`, A03의 `test_stale_lease_cannot_patch_or_start_next_remote_call` 및 unknown 재전송 금지다. 새 제약과 인덱스 정의, RLS, ACL은 실제 로컬 PostgreSQL에서 물리 확인한다. MALL_CONNECTION_TEST_DATABASE_URL이 없어서 skip된 것은 경합 검증 통과로 보고하지 않는다.

서버는 `cd server && .venv/bin/pytest -q`, 프론트는 package.json의 `pnpm test:frontend` 또는 `pnpm test:frontend:files`, 빌드는 `pnpm build`를 사용한다. #593의 OAuth와 토큰 회전, 게시 재구독 및 CDN tombstone 등 유지하는 기존 계약도 회귀 실행한다. 별도 테스트몰 및 화면 인수는 Claude의 증거를 확인하고 미실행은 미확인으로 보고한다.

A02의 계약 갱신 위치는 § 쇼핑몰 연결의 공통 기본값과 주소, 새 API 및 capability, 새 § 여러 몰 등록의 단일 payload와 batchReceipt, 활성 유일성 및 결과 우선순위다. A07은 이 계약이 최종 코드와 일치하는지 검증한다. § 게시에는 카페24 복사 사실만 반영하며 지그재그 보존 정책을 미리 적용하지 않는다. 기존 계약의 카페24 전용 경로와 기본 T 진열, 옵션 미지원 설명은 새 동작으로 교체한다.

### Phase B 착수 대기 목록

아래는 이름과 목적만 남긴 작업 묶음이다. 알파 확인 뒤 별도 설계에서 파일, 함수, 테스트와 배포 세부 순서를 정한다. 현재 구현에 새 표나 삭제 정책을 넣지 않는다.

- B01. 알파 계약 확인. 발급 키로 10.2절의 서명, 권한, 템플릿과 이미지 응답을 확인한다. 예상 effort xhigh, 이유는 인증과 외부 API 계약 검증이다.
- B02. 지그재그 등록 어댑터. 확인한 매핑을 createProduct와 MockTransport 테스트 및 공통 작업에 연결한다. 예상 effort xhigh, 이유는 여러 파일의 외부 API와 비동기 등록 구현이다.
- B03. 선택한 보관 정책과 제한 공개. 오너 선택과 별도 설계에 따라 보관, 삭제, 키 환경 구분 및 공개 설정을 구현한다. 예상 effort xhigh, 이유는 저장 정책과 권한 및 배포 설정을 함께 바꾸기 때문이다.

### 작업 순서와 테스트 규모

Phase A는 A01부터 A07까지 순서대로 진행한다. 새로 고정한 핵심 테스트 이름은 A01 11개, A02 13개, A03 15개, A04 8개, A05 10개로 합계 57개다. A06과 A07은 관련 기존 검증을 실행하며 이름을 중복 계산하지 않는다. Phase B 테스트 수는 알파 뒤 확정한다. 실제 전체 회귀 스위트의 테스트 수를 57개라고 보고하지 않는다.

미확인 카페24 envelope를 해소하기 전에는 mock 통과만으로 운영 인수를 완료하지 않는다. 미래의 등록 데이터는 동일한 멱등키와 lease 및 조회 복구로 보호하며, 출시 전 형식을 지원하려는 별도 분기는 없다.

## 17. BRIEF_v2의 21개 항목 대응

| 번호 | 반영 위치와 담당 Task |
|---|---|
| 1 | 발송 소요일 2일. 3.1절, A01, A04 |
| 2 | 제주와 도서산간 분리. 3.1절, A01, A04, B02 |
| 3 | 몰별 주소와 카페24 몰 설정. 3.2절, A01, A04 |
| 4 | 고시 10항목, 필요한 몰만 차단. 4.2절, A02, A05. 지그재그 템플릿 추가 항목은 B01, B02 |
| 5 | 지그재그 말단 카테고리와 템플릿. 10.2절, B01, B02 |
| 6 | 배송비 세 정책. 3.1절, A01, A04, B02 |
| 7 | 전체 및 부분 반품비와 교환비. A01, A04, B02 |
| 8 | 몰별 점검과 부분 접수. 5.2절과 5.3절, A01, A02, A05 |
| 9 | 현재 편집본과 상세 이미지 수, 보관 선택지. 4.3절과 13절, A05, B01, B03 |
| 10 | 추가 이미지 4장과 순서. 4.3절과 9.4절, A03, A05, B02 |
| 11 | 옵션별 재고와 추가금액. 4.1절과 9.3절, A02, A03, A05 |
| 12 | 몰끼리 재고가 맞춰지지 않는다는 안내. 11.2절, A05, A06 |
| 13 | 몰별 가격과 카페24 B 기준. 6절, A02, A03, A05 |
| 14 | FREE 사이즈 수정과 실측 추정 금지. 4.1절, A02, A05 |
| 15 | 몰별 실제 배송 요약. 11.2절, A04, A05 |
| 16 | 카페24 이미지 설정 비율과 지그재그 미확인. 9.4절과 10.2절, A03, B01 |
| 17 | 카페24 원산지와 소재. 9.1절, A02, A03 |
| 18 | 공통 카드, 사용하는 몰 표시와 진열 안 함. 3.1절과 11.1절, A04, A06 |
| 19 | 에이블리 셀러스 안내와 준비 중. 11.1절, A04, A06 |
| 20 | 에이블리 AI 이미지 정책 확인. 15절, A07 |
| 21 | 요구사항과 구현 근거 연결. 이 대응표, A07 |

이번 작업은 설계 문서만 수정한다. 검증은 요청한 6개 수정사항, 함수 및 DTO, 테스트 수와 Task 대응, 변경 파일 범위의 확인이다. 56개 핵심 테스트와 전체 회귀, 실 PostgreSQL 경합, 실 몰 호출, 화면 검증은 후속 구현에서 수행할 예정이며 이번 작업의 실행 결과가 아니다.
