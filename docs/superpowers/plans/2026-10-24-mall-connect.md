# 쇼핑몰 연결과 다운로드 메뉴(1단계 a) 구현 계획

> **For agentic workers:** 구현은 Codex, 검증은 Claude(`CLAUDE.md`). Codex는 커밋하지 않는다. 각 Task는 테스트 먼저, 실패 확인, 구현, 통과 순서로 한다.

**Goal:** 에디터의 다운로드를 분할 버튼과 메뉴로 바꾸고(몰 로고 포함), 0단계 "몰에 올리기" 이름을 "몰에 붙여넣기"로 통일하며, 설정의 쇼핑몰 연결 화면(지그재그 인증키 저장과 검증, 처음 한 번 기본 설정)을 만든다. 상품 등록 모달과 지그재그 createProduct 호출은 다음 계획(1단계 b)이다.

**Architecture:** 프론트는 기존 분할 없는 버튼 자리에 분할 버튼과 role=menu 드롭다운을 두고, `/settings/malls` 페이지를 추가한다. 서버는 `mall_connections`(암호화한 키)와 `mall_defaults` 테이블, 지그재그 GraphQL 클라이언트(HMAC 인증, shop 조회, 배송지 조회), 연결·변경·해제·기본 설정 라우트를 `MALL_CONNECT_ENABLED` 플래그 아래 둔다. 실제 상품 등록 API는 부르지 않는다.

**Tech Stack:** Vite + React(JSX), CSS Modules, FastAPI, psycopg, httpx, Supabase Postgres(append-only 마이그레이션), node:test, pytest.

**Spec:** 확정 시안 `mockups/export-merge-20261023/plan-b-api-v5.html`(구역 2 메뉴, 구역 3 연결 설정, 구역 4 기본 설정)과 검수 `REVIEW_astra.md`, 리서치 `RESEARCH_refero.md`(D1~D8). 조사 근거 `.scratch/mall-upload-research-20261023/REPORT.md` 2절. 지그재그 인증 문서 https://zigzag.kr/_openapi/docs/authorization/ , 스토어 문서 https://zigzag.kr/_openapi/docs/api-shop/ , 스키마 사본 `.scratch/mall-upload-research-20261023/zigzag_openapi_schema_20261023.graphql`.

## Global Constraints

- Vite + React SPA. 컴포넌트는 `src/lib/api/`를 거쳐서만 서버를 부른다. 목 어댑터(`src/mock/api.js`)에도 같은 계약을 넣는다.
- CSS 변수와 CSS Modules. 인라인 스타일과 하드코딩 hex 금지. `word-break` 덮어쓰기 금지. 나란한 버튼은 `.modal-actions` 알약 규칙. 상단 바 버튼은 기존 `btn-sm` 8px 모서리 유지(2026-08-15 결정).
- 글자 규칙(v3~v5 시안): 모달과 설정 화면 본문 15px, 라벨 14px 600, 보조 글자는 `--fg-2-read`(토큰 없으면 새로 추가: `#55555e`), 13px 미만 금지. 이동 링크는 밑줄+꺾쇠, 같은 자리 수정은 연필 아이콘.
- 마이그레이션은 새 파일 `supabase/migrations/20261024120000_mall_connections.sql` 하나만. 기존 파일 수정 금지.
- 비밀값: 인증키 원문은 DB에 `pgp_sym_encrypt`로만 저장하고 응답에 절대 넣지 않는다(끝자리 8자만). 로그에도 남기지 않는다. KEK는 새 설정 `mall_keys_kek`(env `MALL_KEYS_KEK`), 토스 KEK 재사용 금지.
- 지그재그 요청은 서버에서만 보낸다. 타임아웃 8초. 알파 URL은 설정 `zigzag_openapi_url`(기본 `https://openapi.zigzag.kr/1/graphql`).
- 브라우저 실행 금지, 유료 API 금지(지그재그 호출은 테스트에서 가짜 응답만).
- 에이블리는 공개 API 문서가 없다. 이번 범위에서는 메뉴와 설정 화면에 "준비 중"으로만 보이고 서버 라우트는 만들지 않는다.

## Review Focus

1. 인증키 변경(rotate)이 새 키 검증 실패 시 기존 연결을 그대로 두어야 한다(Task 3 `test_rotate_keeps_existing_connection_on_failure`).
2. 연결 해제 뒤에도 기본 설정은 남고, 다시 연결하면 그대로 쓰인다(Task 3 `test_revoke_keeps_defaults`).
3. 메뉴를 키보드로 열고 Escape로 닫으면 초점이 화살표 버튼으로 돌아와야 한다(Task 5 `menu closes on escape and restores focus`).
4. 플래그가 꺼져 있으면 메뉴에 몰 항목이 없고 프로필 메뉴에도 "쇼핑몰 연결"이 없어야 한다(Task 5 `menu hides mall items when connect api is disabled`).
5. 연결 응답에 키 원문이 섞여 나가면 안 된다(Task 3 `test_connect_response_never_contains_keys`).

---

### Task 1: 마이그레이션, 설정, 계약 문서

**Files:**
- Create: `supabase/migrations/20261024120000_mall_connections.sql`
- Modify: `server/app/config.py`(`mall_connect_enabled: bool`, `mall_keys_kek: str | None`, `zigzag_openapi_url: str`, `zigzag_hmac_algorithm: str = "sha1"`), `documents/common_data_contract.md`("§ 쇼핑몰 연결" 절)
- Test: `server/tests/test_mall_connections_migration.py`

**Interfaces:**
- 테이블 `public.mall_connections`: `id uuid pk`, `user_id uuid not null references auth.users on delete cascade`, `mall text check in ('zigzag','ably','smartstore')`, `status text check in ('connected','revoked') default 'connected'`, `shop_id text`, `shop_name text`, `key_hint text`(끝자리 8자), `access_key_enc bytea`, `secret_key_enc bytea`, `permissions text[]`, `connected_at timestamptz default now()`, `last_registered_at timestamptz`, `revoked_at timestamptz`, `created_at`, `updated_at`. unique index `(user_id, mall) where status = 'connected'`. RLS 켜고 owner select만(쓰기는 서버 service role).
- 테이블 `public.mall_defaults`: `user_id`, `mall`, pk `(user_id, mall)`, `shipping jsonb`, `returns jsonb`, `addresses jsonb`, `disclosure jsonb`, `tax_type text check in ('TAX','FREE') default 'TAX'`, `product_origin text check in ('domestic','imported') default 'domestic'`, `completed_items text[] default '{}'`, `updated_at`. RLS owner select.
- 설정 env: `MALL_CONNECT_ENABLED`(기본 false), `MALL_KEYS_KEK`, `ZIGZAG_OPENAPI_URL`, `ZIGZAG_HMAC_ALGORITHM`.

- [ ] 테스트: SQL 파일에 두 테이블, partial unique index, `enable row level security`, delete 정책 없음, 타임스탬프 유일성.
- [ ] 마이그레이션과 config 구현, 계약 문서 절 추가(필드 표, 상태 전이 connected → revoked, 키는 끝자리만 노출).

### Task 2: 지그재그 클라이언트

**Files:**
- Create: `server/app/services/zigzag_client.py`
- Test: `server/tests/test_zigzag_client.py`

**Interfaces:**
- `build_authorization(access_key: str, secret_key: str, query: str, *, signed_date_ms: int, algorithm: str = "sha1") -> str` → `CEA algorithm=HmacSHA256, access-key=<ak>, signed-date=<ms>, signature=<hex>`. 서명 메시지는 `f"{signed_date_ms}.{re.sub(r'\s+',' ',query)}"`, HMAC은 `algorithm`(sha1 또는 sha256)로 secret_key를 키로 사용. 문서 예제 코드가 sha1이라 기본 sha1, 운영에서 거절되면 설정으로 sha256 전환.
- `class ZigzagClient(base_url, access_key, secret_key, *, algorithm, timeout=8.0, transport=None)`: `async query(graphql: str, variables: dict | None = None) -> dict`(`errors`가 있으면 `ZigzagApiError(code, message)`), `async shop() -> {"shop_id","shop_name","allowed_brands":[...],"sites":[...]}`(Query.shop), `async shipping_addresses() -> list[{"id","name","address","postcode","is_default"}]`(Query.shop_shipping_address_list, limit 50).
- 오류 매핑 `classify_error(exc) -> str`: HTTP 401/403 또는 인증 오류 메시지 → `invalid_key`; 권한 부족 메시지(권한, permission, scope) → `missing_permission`; 타임아웃·연결 실패 → `unreachable`; 그 외 → `unknown`. 셀러 문구 표 `ERROR_COPY = {invalid_key: "인증키가 맞지 않아요. 파트너센터에서 다시 복사해 주세요", missing_permission: "상품 등록 권한이 없는 키예요. 상품과 주문 API 권한을 켜 주세요", unreachable: "지그재그에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요", unknown: "연결을 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요"}`.

- [ ] 테스트: `test_authorization_header_format`(문서 Python3 예제와 같은 입력으로 같은 서명), `test_authorization_sha256_option`, `test_shop_parses_response`, `test_graphql_errors_raise`, `test_classify_error_*` 4건, `test_timeout_maps_to_unreachable`. httpx `MockTransport` 사용.
- [ ] 구현과 통과.

### Task 3: 연결·변경·해제·기본 설정 라우트

**Files:**
- Create: `server/app/mall_connect.py`(router prefix `/v1/malls`)
- Modify: `server/app/repo.py`, `server/app/main.py`(플래그 아래 include)
- Test: `server/tests/test_mall_connect.py`

**Interfaces:**
- repo: `get_mall_connection(conn, *, user_id, mall) -> dict | None`(status connected만, 키 복호 안 함), `get_mall_connection_keys(conn, *, user_id, mall, kek) -> (access_key, secret_key) | None`, `upsert_mall_connection(conn, *, user_id, mall, shop_id, shop_name, key_hint, access_key, secret_key, permissions, kek) -> dict`(기존 connected 행은 `revoked`로 바꾸고 새 행 insert, 한 트랜잭션), `revoke_mall_connection(conn, *, user_id, mall) -> bool`, `get_mall_defaults(conn, *, user_id, mall) -> dict | None`, `upsert_mall_defaults(conn, *, user_id, mall, payload) -> dict`.
- HTTP(모두 `require_user`, 플래그 꺼지면 라우터 미등록):
  - `GET /v1/malls` → `{items:[{mall:'zigzag', status:'connected'|'none', shopName, keyHint, permissions:['상품 조회와 등록'], connectedAt, lastRegisteredAt, defaults:{completedItems:[...], total:4}}, {mall:'ably', status:'unavailable'}, {mall:'smartstore', status:'unavailable'}]}`
  - `POST /v1/malls/zigzag/connect` body `{accessKey, secretKey}` → shop 조회 성공 시 upsert, `{mall, status:'connected', shopName, keyHint, permissions, connectedAt}`; 실패 시 400 `{error:{code:<classify>, message:<ERROR_COPY>}}`. 앞뒤 공백 제거. 키 길이 상한 256.
  - `POST /v1/malls/zigzag/rotate` 같은 body. 새 키 검증 성공 때만 교체, 실패면 기존 연결 유지하고 400.
  - `DELETE /v1/malls/zigzag` → 204, 기본 설정은 유지.
  - `GET /v1/malls/zigzag/addresses` → 연결 없으면 404 `not_connected`, 있으면 배송지 목록.
  - `PUT /v1/malls/zigzag/defaults` body `{shipping:{feeType:'FREE'|'CHARGED'|'CONDITIONAL_FREE', baseFee, jejuFee, isolatedFee, conditionalAmount}, returns:{partialFee, totalFee, exchangeFee}, addresses:{shippingAddressId, returnAddressId}, disclosure:{country, manufacturer, asManager, phone}, taxType, productOrigin}`(부분 저장 허용, 각 묶음이 다 차면 `completedItems`에 `shipping|returns|addresses|disclosure` 추가) → 저장 결과.
  - 사용자당 connect와 rotate 합쳐 시간당 10회 제한(429 `rate_limited`).
- 응답 어디에도 accessKey, secretKey 원문이 없어야 한다.

- [ ] 테스트: `test_connect_success_stores_encrypted_and_returns_hint`, `test_connect_invalid_key_400_with_seller_copy`, `test_connect_response_never_contains_keys`, `test_rotate_keeps_existing_connection_on_failure`, `test_rotate_replaces_on_success`, `test_revoke_keeps_defaults`, `test_addresses_requires_connection`, `test_defaults_partial_save_tracks_completed_items`, `test_rate_limited`, `test_routes_absent_when_flag_off`. FakeConn/FakePool/httpx MockTransport 패턴은 `server/tests/test_detail_publications.py`, `test_facemarket_publications.py` 참고.
- [ ] 구현과 통과. 전체 `pytest -q`(로컬 DB 없는 personalization setup error는 기존 환경 문제로 보고만).

### Task 4: 프론트 API 어댑터와 목

**Files:**
- Create: `src/lib/api/malls.js`
- Modify: `src/lib/api/httpAdapter.js`, `src/mock/api.js`, `src/mock/db.js`(`DB.mallConnections`, `DB.mallDefaults`)
- Test: `tests/frontend/malls-api.test.mjs`

**Interfaces:**
- `listMalls()`(404면 `{items: [], disabled: true}`), `connectZigzag({accessKey, secretKey})`, `rotateZigzag({accessKey, secretKey})`, `disconnectZigzag()`, `listZigzagAddresses()`, `saveZigzagDefaults(payload)`. 목은 `accessKey`가 `bad`로 시작하면 `invalid_key` 오류, `noperm`으로 시작하면 `missing_permission`, 그 외 성공(스토어명 "데일리 니트 스토어").

- [ ] 테스트: 404 → disabled, 목 성공과 실패 분기, 응답에 키 원문 없음.
- [ ] 구현과 통과.

### Task 5: 분할 버튼과 메뉴, 이름 통일

**Files:**
- Create: `src/features/editor/ExportMenu.jsx`, `src/features/editor/ExportMenu.module.css`
- Modify: `src/features/editor/Editor.jsx`(상단 바), `src/features/editor/EditorPublishModal.jsx`(문구), `src/features/library/PublicationsPanel.jsx`(문구가 있으면), 관련 테스트
- Assets: `public/assets/malls/zigzag-48.png`, `zigzag-96.png`, `ably-48.png`, `ably-96.png`(이미 있음. 공식 앱 아이콘)
- Test: `tests/frontend/export-menu.test.mjs`

**Interfaces:**
- `ExportMenu({ open, onClose, anchorRef, items })`: `items = [{id, label, icon?:'download'|'link'|'gif', logo?:'/assets/malls/zigzag-48.png', srcSet?, badge?:'연결 필요'|'준비 중', kind:'action'|'go', disabled?, onSelect}]`. role=menu, 항목 role=menuitem, 방향키 이동, Home/End, Escape와 바깥 클릭으로 닫고 초점을 `anchorRef`로 복귀. 몰 항목은 왼쪽에 24px 로고(`<img src=... srcSet="...-96.png 2x" alt="">`).
- 상단 바: 기존 "다운로드" 기본 버튼 오른쪽에 붙은 화살표 버튼(같은 높이, 왼쪽 모서리 0, `aria-label="다운로드와 쇼핑몰 등록 메뉴"`, `aria-haspopup="menu"`, `aria-expanded`). 본체 클릭은 지금과 같이 다운로드 모달. 기존 "몰에 올리기" 버튼은 제거.
- 메뉴 항목(순서 고정): 이미지로 다운로드 → 다운로드 모달. 몰에 붙여넣기 → 게시 모달(게시 기능 비활성이면 항목 숨김). 지그재그: 연결 없음이면 라벨 "지그재그 연결하기"(kind go, 밑줄+꺾쇠 스타일) → `navigate('/settings/malls', {state:{returnTo: location.pathname}})`; 연결됨이면 "지그재그에 상품 등록"에 배지 "준비 중"으로 disabled(1단계 b에서 연결). 에이블리: 항상 "에이블리에 상품 등록" 배지 "준비 중" disabled. GIF 따로 받기는 `gifCount > 0`일 때만. `listMalls()`가 `disabled: true`면 지그재그·에이블리 항목을 넣지 않는다.
- 이름 통일: 게시 모달 제목 "붙여넣을 코드 만들기", 기본 버튼 "코드 만들기", 결과 머리말 "코드를 만들었어요. 쇼핑몰 HTML 입력칸에 붙여 넣어 주세요". 보관함 패널 제목은 "붙여넣을 코드"로. 기존 테스트의 문구 단언을 갱신한다.

- [ ] 테스트: `split button renders main and caret with aria`, `menu lists fixed order and hides mall items when connect api is disabled`, `zigzag item is connect link when not connected and disabled badge when connected`, `menu closes on escape and restores focus`, `publish modal copy renamed`.
- [ ] 구현과 통과. `Editor.jsx`의 새 훅은 early return 위에.

### Task 6: 설정 페이지 쇼핑몰 연결과 처음 한 번 기본 설정

**Files:**
- Create: `src/features/settings/MallConnections.jsx`, `MallConnections.module.css`, `src/features/settings/MallDefaultsChecklist.jsx`, `src/features/settings/mallDefaults.js`(순수 함수)
- Modify: `src/apps/seller/App.jsx`(`settings/malls` 라우트, RequireAuth 안), `src/features/shell/shell.jsx`(프로필 메뉴 "쇼핑몰 연결" 항목을 요금제 관리 위에, `!IS_FACEMARKET`이고 malls api 활성일 때), `src/features/shell/ProfileMenuIcon.jsx`(아이콘 `store` 추가)
- Test: `tests/frontend/mall-connections.test.mjs`, `tests/frontend/mall-defaults.test.mjs`

**Interfaces:**
- 페이지 머리: "쇼핑몰 연결", 부제 "연결해 두면 에디터에서 상세페이지를 몰에 바로 등록할 수 있어요". `location.state.returnTo`가 있으면 머리 오른쪽에 "에디터로 돌아가기 ›".
- 몰 줄 상태 기계(v5 구역 3): `none`(연결하기 버튼) → `editing`(줄 안 펼침: 발급 경로 한 줄 "로그인 뒤 스토어 정보 관리 › 상품과 주문 API에서 인증키 발급", 새 창 링크 `https://partners.kakaostyle.com/` "파트너센터에서 인증키 발급하기", Access Key, Secret Key(보기 토글, 입력 중에만), 안내 "저장하면 다시 보여 주지 않아요", 버튼 "연결 확인"과 "접기") → `verifying`(버튼 스피너, 비활성) → `connected`(스토어 이름 굵게, "권한 상품 조회와 등록", "인증키 끝자리 ○○○○○○○○", "연결한 날", 마지막 등록, 상태 도트와 "연결됨", 줄 끝 ⋯ 메뉴: 인증정보 변경, 연결 해제) 또는 `failed`(칸 아래 오류 문구, 입력값 유지, "다시 확인"). 입력칸 `spellCheck=false autoComplete="off"`, 붙여 넣을 때 공백 제거.
- 연결 해제 확인 모달 문구: 제목 "지그재그 연결을 해제할까요?", 본문 "스토어와의 연결이 끊겨요. 해제하면 에디터에서 바로 등록을 못 해요. 등록한 상품과 게시한 이미지는 그대로예요. 기본 설정은 남아요", 버튼 취소·연결 해제(글자만 에러색).
- 인증정보 변경 모달: 새 Access Key, 새 Secret Key, "새 인증키로 확인이 끝나야 바뀌어요. 확인이 안 되면 지금 연결은 그대로 남아요", 버튼 취소·확인하고 바꾸기.
- 에이블리 줄: 로고, "셀러스 어드민 API 토큰", 상태 "준비 중", 한 줄 "제휴가 끝나면 열려요". 스마트스토어 줄: "준비 중".
- 아래 한 줄(자물쇠 아이콘): "인증키는 서버에 암호화해 저장하고 끝자리 8자만 보여요".
- 기본 설정 체크리스트(연결됨일 때만, 줄 바로 아래): 머리 "처음 한 번 기본 설정", "4개 중 n개 끝"과 얇은 진행선, "지금 안 채워도 돼요. 첫 등록 전에 한 번 물어요", 항목 4개(배송비: 정책 라디오 무료/고정/조건부 무료, 기본 배송비, 제주와 도서산간 추가비, 조건부면 무료 조건 금액 | 반품비와 교환비: 부분 반품, 전체 반품, 교환 | 출고지와 반품지: `listZigzagAddresses()`로 받은 목록 select 둘, "파트너센터 주소록에서 가져와요" | 고시 공통값: 제조국, 제조자, AS책임자, 전화번호, 상품 출처 라디오 국내 사입/직접 수입, 과세 여부 라디오 과세/면세). 한 번에 하나만 펼침, 끝난 항목은 체크로 접힘, 항목마다 저장 버튼 하나(`saveZigzagDefaults` 부분 저장). 완료 4개면 머리가 "기본 설정 끝"으로. "나중에 할게요" 링크는 체크리스트를 접는다.
- `mallDefaults.js`: `completedItems(defaults) -> string[]`(각 묶음 필수칸이 다 차면 포함), `validateDefaults(section, values) -> {ok, errors}`(금액은 0 이상 정수, 전화번호는 숫자와 하이픈만, 조건부 무료는 조건 금액 필수).

- [ ] 테스트: `renders five row states`, `connect failure keeps typed values and shows seller copy`, `rotate modal copy`, `revoke confirm copy`, `checklist progress counts completed sections`, `validateDefaults rejects negative fee and missing conditional amount`, `profile menu shows 쇼핑몰 연결 above 요금제 관리 only when enabled`.
- [ ] 구현과 통과.

### Task 7: 전체 검증

- [ ] `pnpm build`, `pnpm test:frontend` 전부 통과.
- [ ] `cd server && .venv/bin/pytest -q tests/test_mall_connections_migration.py tests/test_zigzag_client.py tests/test_mall_connect.py tests/test_detail_publications.py tests/test_routes.py` 통과, 전체 `pytest -q` 결과 보고.
- [ ] 변경 파일 목록, 테스트 이름, 계획과 다르게 한 결정, 못 한 것 보고. 커밋 금지.
