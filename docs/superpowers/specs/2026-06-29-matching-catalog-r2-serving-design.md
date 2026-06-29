# 매칭의류 카탈로그 R2 서빙 (1안: 읽기경로 서버화) — 설계

- 날짜: 2026-06-29
- 상태: 개정 (Codex 독립 리뷰 반영) → 사용자 검토 전
- 범위 결정: 사용자 선택 "1안", 공개 R2 서빙 진행(권리 OK)
- 변경이력: v2 = Codex 리뷰 BLOCKER/MAJOR 7건 반영(§아래 11)

## 1. 목표 (Goal)

매칭의류 카탈로그 썸네일이 **배포된 프론트(Vercel)와 모든 신규 클론에서 보이도록** 한다. 현재 썸네일은 `public/assets/matching/`(로컬, `.gitignore` 제외, IP 리스크)을 가리켜 로컬에서만 보인다. 이를 **R2 공개 CDN URL**로 전환한다.

성공 기준:
- `VITE_API_MODE=http`에서 매칭 후보 썸네일이 **R2 공개 URL**(`m.thumb`)로 렌더된다.
- 배포(또는 새 클론)에서 매칭 썸네일이 깨지지 않는다(로컬 파일 의존 제거).
- 백엔드 추천 결과가 기존 mock 규칙과 **동일**하다(보색 타입·성별 필터·밝기순).
- 선택 상태(메인/서브)가 분석→마네킹→스토리보드 **라우트 전환에도 유지**된다(서버 저장 없이 클라 상태로).
- 서버 테스트(pytest) 통과, 기존 31개 회귀 없음. mock 모드 회귀 없음.

## 2. 범위 (Scope)

**포함 (1안 — 읽기경로):**
- `matching_items`/`assets`를 **실데이터로 시드**(R2 업로드 + DB 삽입).
- 추천 로직 **백엔드 포팅** + 후보 조회 **엔드포인트**.
- 프론트: 후보 리스트를 **백엔드에서 fetch**(http) + **선택 상태를 클라 스토어로 이전**해 공개 R2 썸네일로 표시.

**제외 (의도적 — 별도 단계):**
- 매칭 **선택(matchSelections) 서버 저장** → 분석 저장(Phase 3)의 일부. 마네킹 워커가 `repo.get_analysis`로 선택을 DB에서 읽으므로([mannequin_job.py:141](../../../server/app/workers/mannequin_job.py#L141)), 마네킹이 매칭을 실제로 입히려면 "분석 저장"이 필요 — 본 spec은 다루지 않는다. (1안에서 선택은 **클라 상태**로만 유지.)
- 운영자 매칭 항목 편집 UI.
- 썸네일 동적 리사이즈(사전 생성 썸네일 사용; 계획 §3 `metadata.variants`는 추후).

## 3. 배경 — 이미 존재하는 인프라

- **`assets`** ([init.sql:94](../../../supabase/migrations/20260612090000_init.sql#L94)): `source∈(upload,ai,export,seed)`, `visibility∈(private,public)`, `r2_bucket`, `r2_key`(unique), `mime_type`, `byte_size`, `width/height`, `checksum`, `original_filename`, `metadata`. `check (user_id is not null or source='seed')` → seed 무소유 허용.
- **`matching_items`** ([init.sql:152](../../../supabase/migrations/20260612090000_init.sql#L152)): `id`(text PK), 메타필드들, `image_asset_id`, `thumbnail_asset_id`, `is_active`, `sort_order`. **`color_brightness` 없음**(추가 필요).
- **`R2Client`** ([r2.py](../../../server/app/r2.py)): `put_bytes(key,data,mime)`, `head`, `public_url(key)` → `r2_public_base` 있으면 `{base}/{key}`, 없으면 **1h signed GET**(주의). `R2_PUBLIC_BASE`는 `server/.env`에 설정됨(확인).
- **키 규칙** (plan §3): `seed/matching/{matchingItemId}.{ext}`. 서빙: *"seed/public은 CDN 직접"*.
- **엔드포인트 매핑** (plan §4): `getMatchClothing` → `GET /v1/projects/{id}/analysis/match-candidates`(과도기).
- **프론트 어댑터** ([index.js](../../../src/lib/api/index.js)): `VITE_API_MODE=http`면 `{...mockAdapter, ...httpAdapter}` 부분 스왑.
- **레거시 계약(중요)** ([types.js:131](../../../src/lib/types.js#L131)): UI가 소비하는 `MatchClothing`은 **`thumb`** 필드(+ optional imageUrl/thumbnailUrl, selected, selOrder). [toLegacyMatchClothing](../../../src/mock/matchingRecommendation.js)이 `MatchingItem.thumbnailUrl → thumb`로 변환. → **엔드포인트/어댑터는 반드시 `thumb`를 채워야 함.**
- **추천 규칙(mock)** ([matchingRecommendation.js](../../../src/mock/matchingRecommendation.js)): 보색 타입(top/outer/dress→bottom, else top) → `isActive` & `clothingType===preferredType` & 성별(unisex 또는 포함) → `colorBrightness` 내림차순, 동률 `sortOrder` → limit.
- **현재 선택 영속 메커니즘(중요)**: 모든 화면이 `api.getMatchClothing()`로 **mock DB의 `DB.analysis.matchClothing`(후보+선택 병합)** 을 재조회 → 선택이 라우트를 넘어 유지됨. Storyboard는 [Storyboard.jsx:324](../../../src/features/storyboard/Storyboard.jsx#L324)에서 **인자 없이** 호출. Mannequin은 [Mannequin.jsx:234](../../../src/features/mannequin/Mannequin.jsx#L234) `getMatchClothing(pid)`.
- **스토어**: [src/store/useAppStore.js](../../../src/store/useAppStore.js)(Zustand, 전역·라우트 유지), [src/lib/draftStore.js](../../../src/lib/draftStore.js).
- **로컬 소스**: 64항목/128파일(`{gender}-{type}-NN.png` + `thumbs/`), 생성기 [.scratch/gen-matching.mjs](../../../.scratch/gen-matching.mjs). seed id = `match_{gender}_{type}_{nn}`.

## 4. 아키텍처 (구성요소 A–E)

### A. 마이그레이션 (스키마)
신규 append-only `supabase/migrations/20260629HHMMSS_matching_color_brightness.sql`:
```sql
alter table public.matching_items
  add column color_brightness integer not null default 50;
```
- append-only 준수. 64행이라 인덱스 불필요. 적용은 dry-run/apply 분리, prod는 사용자 승인.
- RLS/grant: 기존 `matching_items` authenticated select 정책 유지(새 컬럼은 자동 포함). seed `assets`(public)는 서버(service-role)만 읽어 URL 생성하므로 추가 grant 불필요.

### B. 시드 (R2 업로드 + DB 삽입) — 운영자 데이터, 멱등
스크립트 `server/scripts/seed_matching.py`:
- **입력**: (1) 정본 메타 JSON, (2) 로컬 이미지 디렉터리.
  - [gen-matching.mjs](../../../.scratch/gen-matching.mjs)가 `server/seed/matching_items.json`(데이터만)도 emit하도록 확장(JS↔Python 핸드오프를 JSON 단일화, 파싱 위험 제거). mock JS emit은 유지.
- **처리(항목당, 멱등)**:
  1. 본/썸네일 **checksum(SHA-256)** 계산.
  2. R2 `put_bytes` → 키 `seed/matching/{id}.{ext}`(본), `seed/matching/thumb/{id}.{ext}`(썸네일). **immutable 캐시헤더** 설정 (`put_bytes` 확장 또는 `put_object(CacheControl="public, max-age=31536000, immutable")`).
  3. `head`로 업로드 검증.
  4. `assets` upsert(`r2_key` unique): `source='seed'`, `visibility='public'`, `user_id=null`, `mime_type`, `byte_size`, `width/height`, **`checksum`**.
  5. `matching_items` upsert(`id`): 메타 + `image_asset_id`/`thumbnail_asset_id` + `color_brightness` + `is_active` + `sort_order`.
- **멱등**: checksum 동일하면 재업로드 skip. 같은 키/ID 덮어씀(중복 무생성). 부분 실패 항목만 재시도.
- **R2 공개 접근**: 버킷/도메인 레벨(R2 public bucket 또는 커스텀 도메인). per-object ACL 아님 → `put_bytes`로 충분, `r2_public_base`가 서빙. **검증 단계에서 1개 객체 `curl`로 공개 200 실증.**
- **보너스**: 시드 후 `matching_items`가 채워지면 마네킹 워커의 `get_matching_item_asset`도 실데이터로 동작(현재 빈 테이블 추정).

### C. 추천 서비스 (백엔드)
`server/app/services/matching.py` — 순수 함수, mock 규칙 정확 포팅:
```
recommend(items, clothing_type, genders, limit=None) -> list
  preferred = 'bottom' if clothing_type in {'top','outer','dress'} else 'top'
  pool = [i for i in items if i.is_active and i.clothing_type == preferred
          and (not genders or i.gender == 'unisex' or i.gender in genders)]
  pool.sort(key=lambda i: (-(i.color_brightness if i.color_brightness is not None else 50), i.sort_order))
  return pool[:limit] if limit else pool
```
- 리포 `repo.list_active_matching_items(conn)` → 항목 + 조인된 `assets.r2_key`(본/썸네일).
- 단위 테스트로 mock 동치 검증.

### D. 엔드포인트
`GET /v1/projects/{project_id}/analysis/match-candidates`
- 쿼리: `clothingType`(필수), `gender`(반복/콤마, 선택), `limit`(선택).
- 인증: `require_user` + `get_project` 소유권(없으면 404).
- **fail-fast(§Finding5)**: `r2_public_base` 미설정인데 seed 공개 서빙이 필요한 경우 startup 검증 또는 엔드포인트에서 `500 + 명확 로그`(1h signed URL 무성공 통과 방지).
- **응답 shape(§Finding1)**: **레거시 MatchClothing[]** — `id, name, thumb(공개 R2 URL), imageUrl, thumbnailUrl, gender, selected:false, selOrder:null` + 정렬·필터 메타. **`toLegacyMatchClothing`을 재사용**해 변환(또는 백엔드가 동일 매핑 수행). `thumb = r2.public_url(thumbnail r2_key)`.
- 리스트 응답은 기존 `get_library`처럼 배열 직반환(httpAdapter가 `res.json()` 그대로).

### E. 프론트 통합 (읽기경로 — 1안의 주 작업)
원칙: **후보(이미지)=서버 소유 / 선택=클라 상태**(계약 api.js:124와 일치). 현재 선택이 mock DB로 라우트 유지되던 것을 **클라 스토어로 이전**한다(서버 저장은 제외 범위).

- **선택 상태 이전(§Finding3)**: `useAppStore`에 클라 전용 `matchSelections: [{id, order}]`(또는 동등) 추가 — 라우트 전환에도 유지. 선택 토글이 여기에 기록.
- **후보 소스 통일(§Finding2,4)**:
  - `httpAdapter.getMatchClothing(projectId, ctx)` 구현 → D 호출(`ctx={clothingType,targetGenders}`), 결과를 `toLegacyMatchClothing`으로 변환 후 **스토어 선택 오버레이**.
  - **mock `getMatchClothing(projectId, ctx)`도 ctx 제공 시 재계산**하도록 갱신(인자 무시 금지) → 두 모드 동치, 회귀 방지.
- **소비자 3곳 정비(§Finding4)**:
  - [AnalysisForm](../../../src/features/analysis/AnalysisForm.jsx): `clothingType/targetGenders` 변경 시 `getMatchClothing(ctx)` 재호출(기존 `patchProduct` mock 재계산 의존 제거). 토글 → 스토어 선택.
  - [Mannequin.jsx:234](../../../src/features/mannequin/Mannequin.jsx#L234): `getMatchClothing(pid, ctx)` + 스토어 선택 오버레이.
  - [Storyboard.jsx:324](../../../src/features/storyboard/Storyboard.jsx#L324): 현재 인자 없는 호출 → `pid + ctx` 전달 + 스토어 선택 오버레이.
- **대안(더 가벼움)**: 선택을 스토어로 옮기지 않고, http `getMatchClothing`이 후보만 서버에서 받고 **선택 오버레이는 기존 mock 레이어(`DB.analysis.matchClothing`) 유지**(과도기 하이브리드). 정합성은 낮으나 변경 최소. (검토 시 택1)

## 5. 데이터 흐름
1. **시드(운영자, 1회)**: 생성기 → 메타 JSON + 로컬 128파일 → `seed_matching.py` → R2(`seed/matching/...`) + `assets`(seed/public) + `matching_items` 64행.
2. **런타임(http)**: 컨텍스트 변경 → 프론트 `match-candidates` 호출 → 백엔드 active 조회·추천·공개 URL → 레거시 `MatchClothing[]`(`thumb`=R2) → 스토어 선택 오버레이 → 렌더(공개 CDN, 배포 OK).
3. **선택**: 사용자 최대 2개 선택 → 클라 스토어. 서버 저장은 추후(분석 저장).

## 6. 에러 처리
- **시드**: mime 화이트리스트, `head` 검증 실패 명확 중단·재시도, checksum 멱등.
- **엔드포인트**: 소유권 404; 빈 카탈로그 → 빈 배열; 자산 누락 항목 제외(로그); **R2_PUBLIC_BASE 미설정 fail-fast**.
- **프론트**: `http()` 에러봉투 한국어; 후보 fetch 실패 시 빈 리스트 graceful; 선택 오버레이는 후보와 독립(후보 비어도 선택 상태 보존).

## 7. 테스트 & 검증
- **백엔드 단위**: `services/matching.recommend` — mock 동치(보색/성별/밝기순/limit/경계).
- **백엔드 통합**: `match-candidates` — 인증·소유권 404, 응답 `thumb` 채워짐, 공개 URL, 빈 카탈로그, R2_PUBLIC_BASE 미설정 fail-fast.
- **시드**: 멱등(2회 동일), `head` 검증, 행수 64, checksum 저장.
- **공개 접근 실증**: 시드 객체 1개 `r2_public_base` URL `curl` → 200/이미지.
- **프론트**: `pnpm build` 통과; http 모드 썸네일 R2 렌더 + **라우트 전환 후 선택 유지**; mock 모드 회귀 없음.
- 기존 pytest 31개 회귀 없음.

## 8. 보안 / IP
- 공개 게시 사용자 승인됨. `assets.visibility='public'`, `source='seed'`, 무소유.
- 후보 조회는 인증 필요(로그인 사용자 열람). 이미지 URL 자체는 공개.
- 프롬프트 인젝션/소유권 규약 불변(매칭=운영자 시드).

## 9. 리스크 / 오픈 이슈
1. **프론트(E)가 1안의 핵심 작업/리스크** — 선택상태 클라 스토어 이전 + 소비자 3곳 정비. read-path지만 분석-sync 성격이 일부 들어옴. (가벼운 대안 §4E 하단.)
2. **R2 공개 도메인 실제 동작** 미검증(env만 확인) → 시드 후 `curl` 실증 필수. 비공개면 공개 버킷/커스텀 도메인 설정(인프라, 사용자 영역 가능).
3. **생성기→Python JSON 핸드오프** 신규 산출물 위치(`server/seed/matching_items.json`).
4. **썸네일 키 규칙** `seed/matching/thumb/{id}.{ext}`(계획은 본 이미지 키만 명시 — 본 spec이 썸네일 규칙 확정).

## 10. 비범위 후속 (참고)
- 분석 저장(Phase 3) → matchSelections 서버 영속 → 마네킹이 매칭 실사용.
- 운영자 편집 UI, 썸네일 variants, 카탈로그 캐싱/ETag.

## 11. Codex 독립 리뷰 반영 (v2)
| # | Codex 지적 | 심각도 | 처리 |
|---|---|---|---|
| 1 | UI는 `thumb` 소비, spec은 `thumbnailUrl` | BLOCKER | §3·§4D: `toLegacyMatchClothing` 재사용해 `thumb` 채움 |
| 2 | mock `getMatchClothing`이 ctx 무시 시 회귀 | MAJOR | §4E: mock도 ctx 재계산 |
| 3 | 선택이 라우트 전환에 유실 | MAJOR | §4E: 선택을 클라 스토어로 이전 |
| 4 | Storyboard 소비자 누락 | MAJOR | §4E: 3곳 모두 정비 |
| 5 | R2_PUBLIC_BASE 미설정 시 signed URL 무성공 통과 | MAJOR | §4D·§6: fail-fast |
| 6 | checksum 누락 | MINOR | §4B: SHA-256 저장·멱등 |
| 7 | 캐시헤더 없음 | MINOR | §4B: immutable Cache-Control |
