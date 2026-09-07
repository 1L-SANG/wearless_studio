# FaceMarket 3층 출처증명 (사용 원장 · C2PA 서명 · 체인 앵커) — 설계

**작성일:** 2026-09-04
**상태:** 초안 (사용자 리뷰 대기)
**브랜치:** `feat/facemarket-provenance`
**범위:** 실존 인물 얼굴로 만든 생성물의 출처를 세 층으로 증명한다 — ① 생성물 단위 DB 사용 원장, ② 배포 파일에 임베드하는 C2PA Content Credential, ③ 배포본 해시의 온체인 앵커. 생성 파이프라인·과금·화질은 건드리지 않는다.

---

## 1. 목표

지금 FaceMarket 은 "이 얼굴을 쓸 권리가 있는가"는 증명하지만(라이선스 게이트 + 공개 검증 QR), **"이 파일이 그 권리로 만들어진 것인가"** 는 증명하지 못한다.

- 생성 기록이 **작업 단위**로만 남는다. `detail_page` 잡 하나가 컷 N장을 만들어도 `fm_settlements` 행은 1개다.
- 이미지 어디에도 라이선스 흔적이 없다. `assets.metadata` 에 `facemarket_real_derived: bool` 하나뿐이라, 자산만 보고는 **어느 모델·어느 라이선스로 만들었는지 알 수 없다**.
- 파일이 우리 서비스 밖으로 나가는 순간 출처가 끊긴다.

**성공 기준**

1. 실존 모델 얼굴로 만든 컷 1장마다 원장 행 1개가 생긴다 — 모델ID·라이선스ID·셀러ID·시각·이미지 해시.
2. 셀러가 내려받은 상세페이지 파일 안에 C2PA Content Credential 이 들어 있고, 표준 검증 도구가 읽는다.
3. 그 파일의 해시가 OmniOne Chain 에 기록되어 있고, 아무나 로그인 없이 확인할 수 있다.
4. prod 에서 실제로 동작한다(로컬 전용 아님).

---

## 2. 확정된 결정 (사용자)

| # | 결정 | 버린 대안 |
|---|---|---|
| 1 | C2PA 는 **배포본**(셀러가 내려받는 파일)에 박는다 | AI 컷 자산에만 박기 — 브라우저 캔버스 캡처가 메타데이터를 지워 배포본엔 안 남는다 |
| 2 | 배포본 렌더는 **지금처럼 브라우저**가 하고 서버는 **도장만** 찍는다 | 헤드리스 크롬 서버 렌더(인프라 증설), Pillow 재조립(두 렌더러 영구 유지보수) |
| 3 | 원장은 **컷 1장마다 + 배포본 1건마다 둘 다** | 한쪽만 — "만들었지만 안 썼다"와 "실제로 배포했다"가 구분 안 됨 |
| 4 | **과금 불변** — 작업 1건 = 라이선스비 1회 = 온체인 정산 1건 | 다운로드 과금(결제·환불·UI 전면 변경), 장수 과금(체감 가격 급등) |
| 5 | 서명 인감은 **자체서명 X.509** | 공인 CA + C2PA 적합성 프로그램(비용·기간) |
| 6 | 체인 앵커는 **배포본 해시만** | 앵커 없음(층①이 위조 가능해짐), 컷 전부 앵커(TX 폭증·생성 지연) |
| 7 | 목표는 **prod 실동작** | 코드만 + 플래그 off |

---

## 3. 현재 상태 (전수조사 결과)

### 이미 있는 것 — 재사용한다

| 자산 | 위치 | 이 설계에서의 역할 |
|---|---|---|
| 온체인 recorder | `server/app/facemarket_chain.py` | 새 컨트랙트 배선을 **같은 인스턴스**에 얹는다(nonce lock 공유) |
| 크래시 펜스 정산 | `facemarket.py:1554 record_license_settlement()` — advisory lock + `fm_settlement_signer_intents` broadcasting 펜스 | 앵커 워커가 같은 패턴을 복제 |
| 잡 라이선스 스냅샷 | `routes.py:2828` `payload["_facemarket"] = {modelId, licenseId}` | 원장 행이 참조할 라이선스 출처 |
| 종결 시 라이선스 재검증 | `detail_page_job.py:1716`, `editor_image_job.py:783` — `resolve_model_license(for_update=True)` + `verify_license_local` | 원장 insert 를 이 트랜잭션 안에 넣는다 |
| 공개 검증 | `GET /v1/facemarket/verify/{license_id}` + `src/features/verify/PublicVerify.jsx` | 배포본 검증 페이지의 선례이자 하드룰 원본 |
| presigned PUT | `r2.py:131 presigned_put()` | 브라우저 → R2 직행 업로드 |
| SRI 해시 헬퍼 | `r2.py:90 sha256_sri()` | 해시 포맷 단일 소스 |
| 빈 컬럼 | `assets.checksum` (AI 출고 경로가 안 채움) | 컷 해시를 여기도 채운다 |

### 없는 것 — 만든다

- 생성물 단위 원장 테이블. `image_usage_events` 는 비용 관측용이고 주석에 **"결제·정산 원장이 아니며 service role 은 삭제할 수 있다"** 고 못박혀 있다. 원장으로 쓰면 안 된다.
- C2PA 구현 일체. 레포 전체 검색 결과 구현 0(경쟁사 리서치 문서와 `seed_virtual_models.py` 의 마커 문자열뿐).
- 서버 export 경로. `export_assets`(`sha256` 컬럼 포함) + `export_provenance` 스키마는 `20260803020000_phase9_exports.sql` 에 있으나 **dispatcher 에 `export` 핸들러가 없다** = 죽은 스키마. 이 설계는 그 스키마를 되살리지 않고 **새 테이블을 쓴다**(§10 참조).

### 갈림길이 된 사실

`src/features/editor/editorExport.js` 가 최종 배포본을 만든다 — `html-to-image` 의 `toCanvas` → `canvas.toBlob` → `a.download`. **캔버스 재렌더는 모든 파일 메타데이터를 지운다.** 그래서 서버가 AI 컷에 C2PA 를 박아도 셀러가 실제로 내려받는 PNG 엔 한 톨도 안 남는다. 결정 #1·#2 가 여기서 나왔다.

### 사전 검증한 위험

- **c2pa-python 휠**: PyPI `c2pa-python` 0.37.8 에 `py3-none-manylinux_2_28_x86_64.whl` 존재. Dockerfile base 는 `python:3.12-slim`(bookworm, glibc 2.36 ≥ 2.28) → **Rust 툴체인 없이 설치된다**. 이 위험은 해소됨.

---

## 4. 아키텍처 개요

```
[생성]  셀러가 실존 모델로 컷 생성
          worker → put_bytes 직전에 sha256 계산
          finalize_*_success 트랜잭션 안에서
            → fm_output_records 1행/컷        ← 층①-a
            → assets.checksum 채움

[배포]  셀러가 다운로드 클릭
          브라우저 canvas.toBlob (픽셀 지금과 동일)
            → POST /publications/presign      ← {uploadUrl, uploadToken}
            → PUT R2 직행 (ALB 우회)
            → POST /publications/sign  {uploadToken}
                 서버: R2 get → sha256
                     → fm_publication_records 1행  ← 층①-b
                     → C2PA 서명 (to_thread)       ← 층②
                     → R2 put(서명본)
                     → 앵커 큐 insert
            ← {publicationId, downloadUrl, verifyUrl}
            → a.download 저장

[앵커]  워커가 뒤따라 recordPublication(publicationId, imageHash, licenseRef)
          chain_status: pending → confirmed        ← 층③

[검증]  누구나 GET /verify/p/{publicationId} (무인증)
```

핵심 성질: **서명·다운로드는 동기, 앵커는 비동기.** 체인 확정 폴링이 최대 90초(`FaceMarketChain._CONFIRM_TIMEOUT`)라 다운로드 응답에 묶을 수 없다. C2PA 매니페스트에는 TX 해시 대신 `verifyUrl` 을 넣고, 체인 상태는 그 페이지가 조회해 보여준다.

---

## 5. 층① 사용 원장

### 5.1 `fm_output_records` — 만든 컷 1장 = 1행

```sql
create table public.fm_output_records (
  id            uuid primary key default gen_random_uuid(),
  asset_id      uuid unique references public.assets(id) on delete set null,
  job_id        uuid references public.jobs(id) on delete set null,
  license_id    uuid references public.fm_licenses(id) on delete set null,
  model_id      uuid not null,      -- 비정규화(FK 없음): 부모가 지워져도 역추적 가능
  license_ref   uuid not null,      -- 비정규화된 라이선스 id. license_id 가 null 이 돼도 남는다
  seller_id     uuid not null,      -- 생성한 셀러(jobs.user_id 복사)
  image_sha256  text not null,      -- 원본 바이트 hex digest
  byte_size     bigint,
  created_at    timestamptz not null default now()
);
create index fm_output_records_license_idx on public.fm_output_records(license_ref, created_at desc);
create index fm_output_records_seller_idx  on public.fm_output_records(seller_id, created_at desc);
```

- **모든 FK 가 `on delete set null`이고, 진짜 증빙값(`model_id`·`license_ref`·`seller_id`·`image_sha256`)은 FK 없는 비정규화 컬럼이다.** `restrict` 를 걸면 안 되는 이유: `fm_models` → `fm_licenses` 가 `on delete cascade` 라 모델 삭제가 라이선스를 지운다. 2026-08-29 prod 복구 때 실제로 모델·라이선스가 지워졌다. `restrict` 였다면 그 복구가 막혔을 것이고, `cascade` 였다면 원장이 통째로 사라졌을 것이다. 원장은 **부모보다 오래 살아야 한다.**
- REAL 소스일 때만 기록한다. VIRTUAL/NONE 은 소비한 라이선스가 없다 → 행 없음.

**쓰는 지점 (2곳)**

| 파일 | 지금 | 바꿀 것 |
|---|---|---|
| `workers/detail_page_job.py:~583` | `r2.put_bytes(key, img, mime, …)` | 직전에 `sha256(img)` 계산 → `cut_assets` dict 에 실어 보냄 |
| `workers/editor_image_job.py` | 동일 패턴 | 동일 |
| `repo.finalize_detail_page_success` / `finalize_editor_image_success` | `insert into assets …` | 같은 트랜잭션에서 `assets.checksum` 채우고 `fm_output_records` insert |

**왜 finalize 안인가:** 두 워커 다 lease 펜스(`locked_by = lease_token`)를 걸고 종결한다. lease 를 뺏기면 `finalize` 가 `None` 을 돌려주고 워커는 R2 객체를 지운다. 원장 insert 를 이 트랜잭션 밖에서 하면 **버려진 이미지의 원장 행이 남는다** — 정산 근거로 못 쓴다.

### 5.2 `fm_publication_records` — 내려받은 파일 1건 = 1행

```sql
create table public.fm_publication_records (
  id               uuid primary key default gen_random_uuid(),
  project_id       uuid references public.projects(id) on delete set null,
  seller_id        uuid not null,
  license_id       uuid references public.fm_licenses(id) on delete set null,
  license_ref      uuid not null,          -- 비정규화(§5.1 과 같은 이유)
  model_id         uuid not null,
  kind             text not null check (kind in ('long_png', 'block_png', 'zip')),
  image_sha256     text not null,          -- 서명 전 원본
  signed_sha256    text,                   -- 서명 후(임베드로 바이트가 바뀐다)
  byte_size        bigint,
  r2_key           text,                   -- 서명본 보관(분쟁 증빙). 철회 시 삭제 대상
  source_asset_ids uuid[] not null default '{}',
  c2pa_manifest    jsonb not null default '{}'::jsonb,  -- 실제 박은 주장 원문
  c2pa_status      text not null default 'signed'
                     check (c2pa_status in ('signed', 'skipped', 'failed')),
  chain_status     text not null default 'pending'
                     check (chain_status in ('pending', 'confirmed', 'failed')),
  tx_hash          text,
  chain_id         text,
  recorded_block   bigint,
  revoked_at       timestamptz,            -- 모델 철회 시 표시(행은 남긴다)
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (seller_id, image_sha256)         -- 같은 파일 두 번 받아도 1행(멱등)
);
create index fm_publication_records_license_idx on public.fm_publication_records(license_ref, created_at desc);
```

- `unique (seller_id, image_sha256)` = 멱등 키. 셀러가 같은 상세페이지를 세 번 내려받아도 원장은 1행이고 체인 TX 도 1건이다.
- `publicationId` = 이 테이블의 `id`. 체인 키와 검증 URL 이 같은 값을 쓴다.
- ⚠️ **presign 이 주는 id 는 잠정값이다.** 해시는 업로드가 끝나야 알 수 있으므로, presign 단계에서는 행을 만들지 않고 업로드 키만 발급한다. `sign` 이 해시를 계산해 upsert 하고, 같은 (셀러, 해시) 행이 이미 있으면 **기존 행의 id 를 돌려준다** — 즉 `sign` 응답의 `publicationId` 가 정본이고 프론트는 그것만 쓴다. presign 응답에서 `publicationId` 를 빼고 `uploadToken`(잠정 업로드 키)만 주는 것이 안전하다.
- `revoked_at` 이 있으면 검증 페이지가 "철회됨"으로 바뀐다. **행은 지우지 않는다** — 지우면 "이 파일은 우리가 모르는 파일"이 되어 무단 사용과 구별이 안 된다.

### 5.3 RLS · 삭제 정책

두 테이블 모두 `enable row level security` + 셀러 owner-select 만(`seller_id = auth.uid()`), 쓰기는 service-role. 모델 본인은 자기 얼굴 사용 내역을 봐야 하므로 라이선스 경유 select 정책도 추가한다(`fm_settlements_owner_select` 선례와 동일한 조인).

purge(생체 파기)와의 관계는 §9.

---

## 6. 층② C2PA 서명

### 6.1 흐름

```
POST /v1/facemarket/publications/presign
  body  { projectId, kind, byteSize }
  검증  프로젝트 소유 · REAL 소스 · 라이선스 active(verify_license_local) · byteSize 상한
  응답  { uploadToken, uploadUrl }           # presigned PUT, 5분. 행은 아직 안 만든다

PUT {uploadUrl}                              # 브라우저 → R2 직행
  Content-Type: image/png | application/zip

POST /v1/facemarket/publications/sign
  body  { uploadToken }
  서버:
    1. R2 get_bytes(업로드 키)
    2. sha256 → image_sha256
    3. fm_publication_records upsert
         on conflict (seller_id, image_sha256) do nothing → 없으면 기존 행 조회
         → 여기서 확정된 id 가 publicationId 정본
    4. 이미 c2pa_status='signed' 면 4~6 건너뛰고 기존 결과 반환(멱등)
    5. asyncio.to_thread(sign_c2pa, bytes, manifest)     ← 이벤트루프 보호
    6. r2.put_bytes(서명본 키) · signed_sha256 기록 · 임시 업로드 키 삭제
    7. 앵커 큐에 insert (§7.3)
  응답  { publicationId, downloadUrl(presigned GET, 10분), verifyUrl, c2paStatus }
```

`uploadToken` 은 서명된 단명 토큰(셀러 id + 업로드 키 + 만료). 임의의 R2 키를 서명 대상으로 밀어 넣지 못하게 막는다.

- 4번의 `to_thread` 는 선택이 아니다. 2026-08-26 ALB 장애의 원인이 **동기 이미지 작업의 이벤트루프 동결**이었다(`healthz` 37초 공백 실측). 같은 실수를 반복하지 않는다.
- `zip` kind 는 아카이브 자체에 C2PA 를 못 박는다 → 내부 PNG 각각에 박고 zip 을 다시 싼다. 구현 부담이 크면 1차 범위에서 `zip` 은 `c2pa_status='skipped'` 로 원장만 남기고 서명은 생략한다(원장·앵커는 그대로 동작).

### 6.2 실패 정책 — **다운로드를 막지 않는다**

C2PA 서명 실패(라이브러리 오류·인증서 문제·미지원 포맷)는 `c2pa_status='failed'` 로 기록하고 **원본 바이트를 그대로 돌려준다** + 프론트에 경고 문구. 근거: 생성은 이미 끝났고 크레딧도 차감됐다. 도장이 안 찍혔다고 셀러의 결과물을 인질로 잡지 않는다 — 기존 정산 훅의 best-effort 원칙과 같다.

원장 행 insert 는 **막는다**(실패 시 500). 원장은 정산·분쟁 근거라 유실되면 안 된다.

### 6.3 매니페스트 내용

표준 주장:
- `c2pa.actions`: `c2pa.created`, `digitalSourceType: http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia` — **AI 생성 표시 의무를 이 필드가 겸한다.**
- `claim_generator`: `wearless-facemarket/<app version>`

커스텀 주장 `kr.wearless.facemarket`:
```json
{
  "modelId": "<uuid>",
  "licenseId": "<uuid>",
  "vcId": "<vc id or null>",
  "publicationId": "<uuid>",
  "verifyUrl": "https://<host>/verify/p/<publicationId>",
  "allowedUse": ["..."],
  "forbiddenUse": ["..."],
  "licenseValidUntil": "2027-01-01",
  "sourceAssetIds": ["<uuid>", "..."]
}
```

### 6.4 🔴 하드룰 — 임베드 금지 필드

파일은 무제한 배포된다. 한 번 박으면 **회수 불가**다. `/verify/{license_id}` 라우트의 하드룰을 그대로 적용한다:

**절대 임베드 금지** — 얼굴 이미지·`face_image_key`·`face_image_uri`·`face_image_digest`·CI·`ci_hash`·생년월일 원문·실명·`user_id`·내부 R2 키·presigned URL.

방어 3중:
1. 매니페스트를 만드는 함수는 **화이트리스트 dict 를 조립**한다(`**row` 전개 금지).
2. 조립 결과를 dataclass/Pydantic 모델로 통과시켜 선언 밖 필드를 탈락시킨다.
3. 금지 키 집합에 대한 **단언 테스트**를 회귀 방지용으로 둔다 — 필드 추가 PR 이 이 테스트를 먼저 만나게.

`modelId`·`licenseId`·`sourceAssetIds` 는 UUID 로, 그 자체로는 신원을 드러내지 않고 검증 라우트가 화이트리스트로만 풀어준다. `verifyUrl` 로 들어온 사람이 보는 것은 §8 의 필드가 전부다.

### 6.5 인감(서명 키)

- 자체서명 X.509, ES256. 유효기간 길게(파일이 오래 돌아다닌다), 갱신 시 구 파일 검증이 깨지지 않도록 **인증서 체인을 매니페스트에 동봉**(C2PA 기본 동작).
- 보관: prod `FM_C2PA_CERT_PEM` / `FM_C2PA_KEY_PEM` = SSM SecureString, 로컬 `.env`.
- 둘 중 하나라도 없으면 서명기는 **비활성**(`c2pa_status='skipped'`) — `FaceMarketChain.from_settings` 가 None 을 돌려주는 것과 같은 관례. 원장·앵커는 계속 동작한다.
- 검증기에서 "발급자 미확인"으로 뜨는 것은 **알려진 한계**다. 매니페스트 내용은 읽히고 변조 감지도 동작한다. 신뢰의 무게는 `verifyUrl` → 우리 검증 페이지 → 체인 기록이 진다.

---

## 7. 층③ 체인 앵커

### 7.1 새 컨트랙트

기존 `FaceMarketSettlement` 는 **건드리지 않는다**(실 TX 이력이 있고 재배포하면 주소가 바뀐다). 같은 파일 스타일로 신규:

```solidity
contract FaceMarketProvenance {
    struct Publication {
        bytes32 imageHash;    // 서명 전 원본 sha256
        bytes32 licenseRef;   // keccak256(license uuid)
        uint256 blockNumber;
        bool exists;
    }
    address public owner;
    uint256 public count;
    mapping(bytes32 => Publication) public publications;   // key = publicationId

    function recordPublication(bytes32 publicationId, bytes32 imageHash, bytes32 licenseRef)
        external onlyOwner;
    function getPublication(bytes32 publicationId)
        external view returns (bytes32, bytes32, uint256, bool);
}
```

`FaceMarketSettlement` 와 동일한 제약을 계승: record-only(코인 이동 없음), owner-only, 중복 key revert, 이벤트 로그 없이 `eth_call` getter 로 확인(OmniOne 게이트웨이가 receipt 를 노출하지 않음), 단일 파일(콘솔 업로드).

### 7.2 배선 — nonce 를 깨뜨리지 않게

`FaceMarketChain` 은 `self._nonce_lock` 을 **인스턴스 변수**로 들고 있고, 단일 owner 키가 두 컨트랙트를 모두 쓴다. 그래서 새 컨트랙트를 **별도 클래스·별도 인스턴스로 만들면 안 된다** — 두 인스턴스가 각자 `get_transaction_count("latest")` 를 읽어 같은 nonce 로 서명한다.

→ `FaceMarketChain` 에 `record_publication` / `get_publication` / `wait_for_publication` 을 **메서드로 추가**하고, `from_settings` 가 `fm_provenance_address` 가 있을 때만 provenance contract 핸들을 붙인다. lock 은 자동으로 공유된다.

### 7.3 비동기 앵커 — `jobs` 가 아니라 reconciler 큐

`jobs` 테이블을 쓰지 않는다. `jobs` 는 `project_id` 를 요구하고 `jobs_active_unique_idx`(프로젝트×kind 동시 1건) 제약이 붙어 있어, 프로젝트 하나에서 여러 배포본을 연달아 내려받으면 앵커가 서로를 막는다.

대신 **`fm_vc_revocation_jobs` + `fm_vc_revocation_reconciler.py` 패턴**을 복제한다 — 이미 prod 에서 돌고 있고, 재시도 상한 없는 큐가 고아 잡을 880회 재시도한 사고까지 반영돼 있는 구조다.

```sql
create table public.fm_publication_anchor_jobs (
  publication_id uuid primary key references public.fm_publication_records(id) on delete cascade,
  status         text not null default 'pending'
                   check (status in ('pending','processing','retry','anchored','dead')),
  attempts       int  not null default 0,
  lease_until    timestamptz,
  last_error     text,
  attempted_at   timestamptz,
  created_at     timestamptz not null default now()
);
```

루프 1회의 순서는 `record_license_settlement` 의 검증된 화해 구조를 그대로 따른다:

1. DB 선확인 — 이미 `chain_status='confirmed'` 면 no-op
2. `status='processing'` + `attempted_at` **commit** (크래시 펜스가 RPC 전에 durable 해야 한다)
3. `pg_try_advisory_lock` — settlement 와 **다른 lock id**. 단 §7.2 의 nonce lock 은 같은 `FaceMarketChain` 인스턴스에서 공유되므로 서명 직렬화는 자동으로 보장된다
4. `record_publication` → 실패 시 `wait_for_publication` → `get_publication` fail-closed 순서로 화해
5. `chain_status='confirmed'` + tx/block 미러, `status='anchored'`
6. `attempts` 상한(50) 초과 시 `status='dead'` — 상한 없는 재시도가 홀더를 24/7 켜 둔 전례를 반복하지 않는다

체인 미설정이면 큐에 넣되 `chain_status='pending'` 으로 두고 조용히 넘어간다(기존 `settlement_skipped_no_chain` 과 동일).

**앵커 실패는 다운로드에 영향 없다.** 셀러는 이미 파일을 받았고, 검증 페이지가 "체인 기록 대기 중"을 보여준다.

---

## 8. 검증 표면

### 8.1 신규 `GET /v1/facemarket/verify/p/{publication_id}` — 무인증

`verify_license_public` 의 3중 방어를 그대로 복제한다. 응답 화이트리스트가 **전부**:

```
valid            bool          # 라이선스 active AND 미만료 AND revoked_at is null
status           'active' | 'revoked' | 'expired'
publishedAt      datetime
imageHashPrefix  str           # sha256 앞 12자만 (전체는 안 싣는다 — 대조는 사용자가 자기 파일로)
kind             'long_png' | 'block_png' | 'zip'
allowedUse       list[str]
forbiddenUse     list[str]
licenseValidUntil datetime
chain            { status, txHash, chainId, block } | null
model            { nameMasked, age }        # PublicVerifyModel 재사용
```

금지: 얼굴·실명·생년·CI·`user_id`·`model_id`·R2 키·presigned URL·`source_asset_ids`. `Cache-Control: no-store`(철회가 즉시 반영돼야 한다). 잘못된 UUID 는 DB 조회 전 404.

### 8.2 프론트 `/verify/p/:publicationId`

`PublicVerify.jsx` 와 같은 셸·같은 무인증 라우팅 위치(`RequireAuth` 밖, 앱 크롬 밖). 상태 카피 3종(유효/철회/만료) + 체인 기록 줄.

### 8.3 모델 대시보드

모델 본인 화면에 "내 얼굴 사용 내역" — `fm_output_records` + `fm_publication_records` 조인. 지금은 정산 금액만 보인다.

---

## 9. 철회·파기와의 관계 (미해결 아님, 정책 확정)

배포본 PNG 는 **실존 인물의 얼굴을 담는다.** 모델이 철회하면 생체 파기(`services/biometric_purge.py`) 대상이다. 그런데 파일은 이미 셀러 손에 나갔다.

확정 정책:

| 대상 | 처리 |
|---|---|
| R2 의 서명본 사본 | **삭제**. `fm_publication_records.r2_key = null` |
| 원장 행 | **보존**. `revoked_at` 세팅 |
| 체인 기록 | 불변 — 그대로 둔다(해시뿐, 생체정보 아님) |
| 검증 페이지 | `status='revoked'` + "이 파일의 사용 권한은 철회되었습니다" |
| 셀러가 가진 파일 | **회수 불가.** 검증 페이지가 철회를 알리는 것이 유일한 수단 |

근거: `image_sha256` 은 생체정보가 아니라 파일 지문이고, 원장을 지우면 그 파일이 "우리가 모르는 파일"이 되어 **무단 사용과 정당한 과거 사용을 구별할 수 없게 된다** — 파기의 목적(생체정보 제거)은 R2 사본 삭제로 달성되고, 원장 보존은 오히려 모델을 보호한다.

`biometric_purge._cleanup` 의 삭제 목록에 `fm_publication_records.r2_key` 를 추가하고, 두 원장 테이블 자체는 **삭제 목록에 넣지 않는다**(`fm_settlements` 가 이미 그렇게 되어 있다).

---

## 10. 왜 `export_assets` / `export_provenance` 를 안 쓰나

`20260803020000_phase9_exports.sql` 에 `export_assets`(sha256 포함)·`export_provenance`(renderer_version·snapshot_hash·`provider_calls = 0` CHECK)가 있다. 하지만:

- 워커가 없다(`dispatcher.py` 에 `export` 핸들러 부재) = 한 번도 안 돈 스키마다.
- 그 스키마는 **서버가 결정적으로 렌더하는** 모델(`snapshot_hash` + `renderer_version` 으로 재현 가능)을 전제한다. 우리는 결정 #2 로 **브라우저 렌더 + 서버 공증**을 택했다 — 서버는 픽셀을 재현할 수 없으므로 `renderer_version`·`provider_calls = 0` 계약이 성립하지 않는다.
- `export_assets` 는 `exports` 잡 행을 필수로 요구한다. 우리 흐름엔 export 잡이 없다.

죽은 스키마에 억지로 맞추면 의미가 어긋난 컬럼이 남는다. 새 테이블을 쓰고, 이 판단을 여기 남긴다. (phase9 export 를 나중에 진짜로 구현하면 `fm_publication_records` 를 참조하면 된다.)

---

## 11. prod 배선 (목표 = 실동작)

| # | 항목 | 비고 |
|---|---|---|
| 1 | `FM_C2PA_CERT_PEM` / `FM_C2PA_KEY_PEM` | SSM SecureString. 자체서명 인증서 생성 스크립트 동봉 |
| 2 | `FaceMarketProvenance` 배포 → `FM_PROVENANCE_ADDRESS` | OmniOne 콘솔 단일파일 업로드 |
| 3 | **`FM_CHAIN_RPC_URL` / `FM_SETTLEMENT_ADDRESS` / `FM_CHAIN_PRIVATE_KEY`** | ⚠️ **prod 에 지금 없다**(메모리 `facemarket-prod-gaps`). 이게 없으면 층③ 전체가 no-op |
| 4 | R2 버킷 CORS | 브라우저 presigned PUT 을 위해 `PUT` + 우리 origin 허용 |
| 5 | Dockerfile `c2pa-python` | manylinux_2_28 휠 확인됨 — 추가만 하면 됨 |
| 6 | 마이그레이션 | ⚠️ CI 의 `SUPABASE_DB_URL` 이 앱 DB(`ftjxwxuactfjopbokbni`)와 다른 옛 DB 를 가리킨 이력 있음(2026-08-29 prod-down). 배포 전 확인 |

RPC 호스트 함정: OmniOne Chain 은 `test.` 를 뺀 호스트, chainId 201210 (메모리 `facemarket-chain-deploy`).

---

## 12. 테스트 전략

| 층 | 테스트 |
|---|---|
| ① 컷 원장 | 컷 N장 잡 → 행 N개 · 해시가 실제 바이트와 일치 · **lease 상실 시 0행**(finalize 트랜잭션 안에 있다는 증명) · VIRTUAL 잡은 0행 |
| ① 배포 원장 | 같은 파일 3회 sign → 1행 · `source_asset_ids` 가 실제 컷과 일치 |
| ② 서명 | 서명본에서 매니페스트 파싱 → 주장 값 일치 · **금지 필드 부재 단언**(회귀 방지 하드테스트) · 1비트 변조 시 검증 실패 |
| ② 실패 정책 | 서명기 예외 → 200 + 원본 바이트 + `c2paStatus='failed'` · 원장 insert 실패 → 500 |
| ③ 앵커 | 스텁 체인으로 `pending → confirmed` · 중복 publicationId revert 를 이미 기록으로 흡수 · broadcasting 중 크래시 후 재기동 화해 |
| ③ nonce | settlement 와 publication 을 동시 호출 → 같은 lock 직렬화 확인 |
| 검증 라우트 | 응답 필드 화이트리스트 단언 · 잘못된 UUID 404 · `no-store` 헤더 · 철회 후 `status='revoked'` |
| 철회 | purge 실행 → R2 사본 삭제 · 원장 행 생존 · `revoked_at` 세팅 |

---

## 13. 남은 위험

1. **zip 서명.** 아카이브에 C2PA 를 못 박는다. 1차는 `skipped` 로 두고 원장·앵커만 태우는 것을 권한다(§6.1).
2. **긴 PNG 크기.** 상세페이지 긴 PNG 는 2000px 폭 × 수천~2만px 로 수십 MB 가 된다. presigned PUT 으로 ALB 는 우회하지만 **서버가 서명하려면 바이트를 메모리에 올려야 한다** — Fargate 메모리 한도와 동시 요청 수를 계획 단계에서 실측할 것.
3. **인증서 갱신.** 자체서명 인증서가 만료되면 그 뒤 서명분만 영향받고 기존 파일은 동봉된 체인으로 계속 검증된다. 다만 갱신 런북이 없으면 조용히 `skipped` 로 떨어진다 — 만료 알림을 CloudWatch Slack 경보에 얹을 것.
4. **prod 체인 미배선(§11-3).** 이게 안 풀리면 결정 #7(prod 실동작)이 성립하지 않는다. 계획 단계의 첫 작업으로 잡을 것.
