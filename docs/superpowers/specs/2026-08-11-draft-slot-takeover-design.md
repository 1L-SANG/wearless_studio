# 확정 시점 보관함 등록 + 숨은 임시저장(작업권) 설계

2026-08-11 · 오너 확정 결정 기반. 선행: PR #104 (feat/qa-input-analysis — 공개 분석·flowSession·draft 상시 저장).

## 결정 (오너)

1. **보관함 등록은 '의류정보 확정' 시점부터.** 로그인 사용자도 분석 시작 시 프로젝트를 만들지 않는다. 분석만 하다 만 초안이 보관함에 쌓이지 않는다.
2. **로그인 사용자는 숨은 임시저장 1슬롯(계정당 1개).** 확정 전 작업을 서버에 조용히 백업하되 보관함 목록에는 보이지 않는다. 다른 기기에서 이어하기 가능.
3. **작업권(takeover) 방식.** '이어서'를 누른 탭이 작업권을 가져가고, 다른 탭·기기는 다음 저장/동작에서 서버가 거부 → 잠금 화면("다른 탭 또는 기기에서 이어서 작업 중이에요 · [이 탭에서 계속하기]"). 같은 브라우저의 탭도 서로 다른 입력 사본이므로 토큰만 자동 공유하지 않는다. 항상 한 탭만 편집하며, 미저장 로컬 변경이 있는 채 이어받으면 양쪽 저장 시각을 보여주고 기준을 선택하게 한다.
4. 비로그인은 현행 유지(브라우저 로컬 draft만). 확정 시 로그인 게이트 → draftSync 승격.

## 흐름 변화

### 분석 (로그인 여부 무관)
- 분석은 프로젝트 없이 공개 분석 경로(`POST /v1/public/analyze`)로 통일한다.
- 로그인 사용자가 호출하면 Bearer 를 함께 보내고, 서버는 **유효 토큰이면 IP 레이트리밋을 건너뛴다**(익명만 밸브 적용).
- ProductInput 의 submit 에서 ensureProject 호출 제거(확정 시점으로 이동).

### 확정 (승격)
- 로그인: 확정 클릭 → 프로젝트 생성 + 사진 R2 업로드 + product/analysis/composeMode 저장(기존 draftSync 승격 로직 재사용·single-flight 유지) → 마네킹 발사 → 슬롯 DELETE → 잠금 기록(flowSession) → 콘티 이동. 실패 시 슬롯·draft 유지, 재시도 가능.
- 비로그인: 현행(로그인 게이트 → RootRedirect draftSync) 유지 — 같은 승격 함수를 공유.
- 사진 양(composeMode)은 확정 전에는 로컬+draft 값이고, 승격 때 프로젝트에 저장된다(확정 전 서버 PATCH 제거 — #17 롤백 로직은 확정 후 콘티 '변경' 경로에만 남는다).

### 숨은 슬롯 동기화 (로그인 + 확정 전)
- 텍스트·선택값: 기존 draft debounce(500ms)에 얹어 서버 슬롯 PUT.
- 사진: 추가 즉시 백그라운드 업로드(변환된 JPEG). 실패는 조용히 재시도하고 슬롯 메타에 `photosPending` 표시 — 원격 슬롯을 고르는 화면에서 "사진 저장이 끝나지 않아 일부 사진이 빠질 수 있어요"를 보여준다. 입력 중에는 텍스트 PUT까지 포함한 내부 pending 상태를 일반 경고로 노출하지 않는다. **로컬 IndexedDB draft 가 항상 1차 진실**이며 서버 슬롯은 백업/이동용.
- 진입 시: 서버 슬롯 GET → 존재하면 기존 '이어서/새로' 모달(4순위)에 통합해 저장 시각·기기·사진 수 표시. [이어서]=takeover+payload 로드, [새로 만들기]=슬롯 DELETE.
- 선택 모달의 Esc·배경 클릭은 어떤 초안도 자동 선택하거나 작업권을 가져오지 않는다. 슬롯이 이미 삭제된 409는 자동 재생성하지 않고, 이 탭의 내용을 새 슬롯으로 저장할지 버릴지 명시적으로 고르게 한다.
- window focus 시 슬롯 메타 GET 으로 작업권 상실 조기 감지(선택적 경량 폴링, 60s 이상 간격).

## 서버 설계

### 테이블 (마이그레이션 파일만 작성 — 적용은 오너/배포 절차)
`supabase/migrations/20260811000000_draft_slots.sql`

```sql
create table if not exists public.draft_slots (
  user_id uuid primary key references auth.users(id) on delete cascade,
  payload jsonb not null,             -- product/analysis/composeMode 스냅샷 (사진은 asset 참조)
  active_token uuid not null,         -- 작업권. PUT 은 일치 시에만 허용
  device_label text,                  -- "iPhone" / "Mac Chrome" 등 표시용
  photos_pending boolean not null default false,
  updated_at timestamptz not null default now(),
  expires_at timestamptz not null     -- now() + 7일, PUT 마다 연장
);
```
- draft 사진 asset: 기존 assets 테이블에 `purpose='draft_slot'` 로 저장, 슬롯 교체·삭제·만료 시 soft-delete(기존 soft_delete_unreferenced 패턴).
- 만료는 lazy: GET/PUT 시 expires_at 지난 슬롯은 삭제 취급.

### API (모두 Bearer 필수)
- `GET /v1/draft-slot` → 204(없음) | 200 { meta: {updatedAt, deviceLabel, photoCount, photosPending}, holdsToken: bool } — payload 는 메타만(가볍게). `?full=1` 이면 payload 포함(이어받기 직후 로드용이지만 takeover 응답에 포함되므로 보통 불필요).
- `PUT /v1/draft-slot` body { payload, token, deviceLabel, photosPending } → 200 | **409 token_mismatch**(다른 기기가 이어받음 — 메타 포함 반환) . 슬롯이 없으면 token 새로 발급해 생성(201, token 반환).
- `POST /v1/draft-slot:takeover` → 항상 성공. 새 token 발급·기존 무효화, { token, payload, meta } 반환.
- `DELETE /v1/draft-slot` → 승격 완료·'새로 만들기' 시. 슬롯+draft asset 정리.
- 사진 업로드는 기존 presigned/asset 업로드 경로 재사용(purpose 만 draft_slot).

## 프론트 설계

- 새 모듈 `src/lib/draftSlot.js`: 서버 token(localStorage)과 탭 편집자 식별자를 분리하고, PUT 큐(직렬)·409 핸들러·takeover·deviceLabel 추정을 담당한다. storage 이벤트로 다른 탭의 token을 자동 채택하지 않는다.
- 잠금 화면: ProductInput 위 전면 오버레이. "다른 탭 또는 기기에서 이어서 작업 중이에요 — 내용이 섞이지 않도록 이 화면의 저장을 멈췄어요" + [이 탭에서 계속하기]. 이어받기 → takeover → 원격 payload 로드. **미저장 로컬 변경이 있으면** "이 기기 내용(HH:MM) / 다른 기기 내용(HH:MM)" 선택 모달을 먼저. 슬롯이 사라진 경우에는 로컬 내용을 다시 저장하거나 버리고 새로 시작하는 두 선택만 제공한다.
- 409 감지 지점: 슬롯 PUT 응답 + window focus 메타 GET. 감지 즉시 편집 잠금.
- mock 모드: mockAdapter 에 인메모리 슬롯 구현(데모·테스트).

## 비범위 (이번 트랙에서 안 함)
- 보관함 기존 '초안' 행 정리/마이그레이션.
- 다중 슬롯(계정당 1개 고정).
- 실시간 푸시(폴링·저장 시 감지로 충분).

## 테스트 요구
- 서버: 슬롯 CRUD·409·takeover 무효화·만료 lazy 삭제·익명 401·draft asset 정리. 공개 분석 Bearer 시 레이트리밋 우회.
- 프론트: 승격 시점 이동(분석 시 프로젝트 미생성)·확정 승격 성공/실패 재시도·409→잠금→이어받기·이어서/새로 모달 통합·mock 슬롯.
