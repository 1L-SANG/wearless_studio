# FaceMarket 테스트컷 4장 선택 + 공개 프로필 명세 v1 (2026-09-07)

오너 지시(2026-09-07)를 구현 계약으로 옮긴 문서다. 기존 구현(PR #228, `/model/confirm` 1장 선택)을
확장한다. 새로 만들지 않고 아래 파일을 고친다.

- 서버: `server/app/facemarket_admin_models.py`, `server/tests/test_facemarket_model_test_cuts.py`
- 어드민: `src/features/admin/AdminModels.jsx`
- 모델: `src/features/model/ModelConfirm.jsx`(+ `.module.css`)
- 랜딩: `src/features/facemarket-landing/sections/BrowseSection.jsx`, `ModelDetailDialog.jsx`, `BrowseModels.module.css`
- API 클라이언트: `src/lib/api/facemarket.js`

## 1. 흐름

1. 등록 링크 입력이 끝나고 VC까지 발급되면 모델은 `pending`이다.
2. 어드민이 콘솔 모델 탭에서 테스트컷 **4장**을 올린다. 확대샷(`closeup`) 2장, 전신샷(`fullbody`) 2장.
   - 확대샷: 얼굴이 주로 보이는 스튜디오샷.
   - 전신샷: 전신이 드러나고 약간의 포즈가 있는 스튜디오샷.
3. 어드민이 [모델에게 보내기]를 누른다. 4장이 다 있어야 눌린다. 모델은 `awaiting_confirm`.
4. 모델은 `/model/confirm`에서 확대샷 1장, 전신샷 1장을 고른다. 오른쪽에 "모델 리스트에 이렇게 보여요"
   미리보기가 같이 뜬다. 동의 체크 뒤 [이 모습으로 공개하기].
5. 확정하면 모델은 `verified`가 되고, 고른 2장이 공개 카탈로그 버킷으로 승격되며, 공개 모델 리스트
   (`/models`)에 정식으로 올라간다. 어드민 콘솔에는 "공개 승인됨 · 확정일"과 대표 컷 2장이 표시되고,
   슬랙 알림이 간다(설정돼 있을 때만, best-effort).
6. 다시 만들기는 기존대로 1회.

## 2. 데이터

새 마이그레이션 `supabase/migrations/20260907180000_fm_model_test_cut_kinds.sql`:

```sql
alter table public.fm_model_test_cuts add column if not exists kind text;
update public.fm_model_test_cuts set kind = 'closeup' where kind is null;
alter table public.fm_model_test_cuts alter column kind set not null;
alter table public.fm_model_test_cuts drop constraint if exists fm_model_test_cuts_kind_check;
alter table public.fm_model_test_cuts add constraint fm_model_test_cuts_kind_check
  check (kind in ('closeup', 'fullbody'));

alter table public.fm_models add column if not exists fullbody_image_url text;
```

- `fm_models.cover_image_url` = 확정된 확대샷의 1024px WebP 키(기존 그대로).
- `fm_models.fullbody_image_url` = 확정된 전신샷의 1024px WebP 키. 키 접두는 확대샷과 같은
  `facemarket/catalog/models/{model_id}/covers/` 를 쓴다(`_cover_serving_url` 분기가 그대로 먹는다).
- `sort`는 0..3. 상수는 `MAX_TEST_CUTS = 4`, `MAX_TEST_CUTS_PER_KIND = 2`.
- 확정 시 고른 2장은 `approved = true`, 나머지 2장은 `false`.

## 3. 서버 API 계약

모든 응답은 CamelModel(camelCase)이다.

### 3.1 어드민

- `GET /v1/facemarket/admin/models/{id}/test-cuts` (기존)
  - 각 cut에 `kind` 추가.
  - `closeupCount`, `fullbodyCount`, `cutsComplete`(확대 2 + 전신 2 이면 true), `confirmedAt` 추가.
  - `readyToSend`는 기존 의미(등록 완료 + VC 발급) 유지.
- `POST /v1/facemarket/admin/models/{id}/test-cuts` (기존, multipart)
  - form 필드 `kind` 필수(`closeup` 또는 `fullbody`). 없거나 다른 값이면 400 `invalid_kind`.
  - 종류별 2장 초과면 409 `cut_limit` ("확대샷은 2장까지 올릴 수 있어요." 또는 "전신샷은 2장까지…").
- `DELETE …/test-cuts/{cutId}` 변경 없음(승인 컷은 409 그대로).
- `POST …/send-test-cuts`
  - 확대 2 + 전신 2 가 아니면 409 `test_cuts_incomplete`
    ("확대샷 2장과 전신샷 2장을 모두 올려야 보낼 수 있어요.").
  - 나머지 검증(상태·등록·VC) 기존 그대로.

### 3.2 모델 본인

- `GET /v1/facemarket/model/test-cuts`
  - 각 cut에 `kind` 추가.
  - `profile` 추가(아래 3.4 공통 shape). 미리보기가 공개 리스트와 똑같은 값을 보여 주기 위해서다.
- `POST /v1/facemarket/model/test-cuts/confirm`
  - body `{ "closeupCutId": "...", "fullbodyCutId": "..." }` 둘 다 필수. 기존 `approvedCutId`는 폐기.
  - 검증: 두 컷 모두 본인 모델 소유, `kind`가 각각 맞아야 함(아니면 400 `kind_mismatch`),
    모델 상태 `awaiting_confirm`(아니면 409 `not_awaiting_confirm`).
  - 처리: 두 원본을 각각 1024px WebP로 축소해 공개 버킷에 저장(키는 확정 시도마다 새로),
    잠금 안에서 `approved` 갱신 + `fm_models` 갱신(`status='verified'`, `confirmed_at`,
    `confirm_consent_version`, `cover_image_url`, `fullbody_image_url`) 원자 반영.
    커밋 실패 시 올린 공개 파일 2개 모두 삭제(기존 보상 패턴).
  - 커밋 뒤 슬랙 알림 best-effort: `facemarket_notify`에 `notify_slack_model_confirmed(settings, display_name, admin_link)`
    를 추가해 `notify_slack_new_application`과 같은 웹훅을 쓴다. 실패는 로그만.
  - 응답 `{ status: "verified", confirmedAt, closeupImageUrl, fullbodyImageUrl }`.
- `POST /v1/facemarket/model/test-cuts/redo` 변경 없음.

### 3.3 공개 모델 리스트 (신설, 인증 없음)

- `GET /v1/facemarket/public/models`
  - 응답 `{ "items": [ PublicModelItem, … ] }`, 최대 200, `confirmedAt` 내림차순.
  - `Cache-Control: public, max-age=60`.
  - 노출 조건: `fm_models.status = 'verified'`, `confirmed_at is not null`,
    `cover_image_url like 'facemarket/catalog/models/%'`, `fullbody_image_url like 'facemarket/catalog/models/%'`,
    현재 enrollment의 활성 라이선스(`status='active'`, `vc_id` 있음, `license_valid_until > now()`) 존재.
  - PublicModelItem = 3.4의 `profile` 필드 + `id`, `closeupImageUrl`, `fullbodyImageUrl`, `confirmedAt`.
  - 이미지 URL은 `_cover_serving_url`로 변환한 값. R2 키 원문은 내보내지 않는다.
  - PII 금지: 이메일·실명(applicant_name)·생년월일·ci_hash·did·user_id는 절대 싣지 않는다.

### 3.4 공통 `profile` shape

```json
{
  "displayName": "정일상",
  "gender": "male",
  "heightCm": 178,
  "heightBucket": "m_175_180",
  "bodyType": "toned",
  "license": {
    "allowedUse": ["상의", "아우터"],
    "unitPrice": 10000,
    "validUntil": "2027-09-07T00:00:00Z",
    "validDays": 365
  }
}
```

- `displayName` = `fm_models.display_name`(활동명). 마스킹하지 않는다.
- `heightCm` = 현재 enrollment가 물린 지원서(`fm_model_applications.height_cm`). 없으면 null.
- `heightBucket`, `bodyType` = `fm_models` 값. 없으면 null.
- `license` = 현재 enrollment의 활성 라이선스. `validDays = round((license_valid_until - created_at) / 1 day)`.
- 몸무게 컬럼은 어디에도 없다. 카드·상세는 "키 · 체형"으로 보여 준다(§7 오너 결정 1).

## 4. 어드민 UI (`AdminModels.jsx` TestCuts 섹션)

- 그룹 두 개: "확대샷 n/2", "전신샷 n/2". 그룹마다 [이미지 추가] 입력이 따로 있고 `kind`를 붙여 올린다.
  각 그룹 2장이 차면 그 그룹의 추가 버튼만 잠근다.
- 썸네일 위 배지: 확정 뒤 대표 컷은 "대표"(기존) 그대로.
- [모델에게 보내기]는 `readyToSend && cutsComplete`일 때만 활성. 비활성 사유 title:
  4장이 안 찼으면 "확대샷 2장과 전신샷 2장이 모두 있어야 보낼 수 있어요.", 등록 미완이면 기존 문구.
- 확정 뒤: "공개 승인됨" 배지 + "{날짜}에 모델이 확정했어요. 모델 리스트에 올라갔어요." 한 줄.
- 상태 필터·라벨에 `awaiting_confirm: '확인 대기'`를 추가한다(지금은 빠져 있어 필터에서 못 고른다).

## 5. 모델 확정 UI (`ModelConfirm.jsx`)

두 열. 왼쪽이 선택, 오른쪽이 미리보기(데스크톱에서 sticky). 폰에서는 위아래로 쌓인다.

- 머리: eyebrow "공개 전 마지막 확인", h1 "프로필에 걸 컷을 골라 주세요",
  설명 "확대샷 1장, 전신샷 1장을 고르면 오른쪽처럼 모델 리스트에 올라가요. 확정 전에는 아무것도 공개되지 않아요."
- 왼쪽 선택:
  - 1행 "확대샷 · 1장 선택" 아래 라디오 카드 2장(가로 나란히, 3:4 비율).
  - 2행 "전신샷 · 1장 선택" 아래 라디오 카드 2장.
  - 기본 선택은 각 종류의 첫 장. 라디오 이름은 `closeup-cut`, `fullbody-cut`.
- 오른쪽 미리보기 제목 "모델 리스트에 이렇게 보여요":
  - 카드 미리보기: 확대샷(3:4) + 활동명(주) + "키 178cm · 잔잔한 근육"(보조). 라벨은
    `src/lib/facemarketPhysique.js`의 `HEIGHT_BUCKETS`·`BODY_TYPES`에서 찾는다. `heightCm`이 있으면 cm,
    없으면 구간 라벨, 둘 다 없으면 체형만.
  - 상세 미리보기: 확대샷 | 전신샷 두 장 나란히, 아래 활동명, 아래 두 칸 "신체 사이즈 | 라이선스 조건"
    (키·체형 | 허용 품목·건당 단가·유효기간). §6의 공개 상세 창과 같은 구성이어야 한다.
  - 유효기간 표기: 3650일 이상 "영구", 365의 배수 "n년", 그 외 "n일".
- 동의 체크 문구(정확히): "고른 컷 2장과 아래 프로필(활동명·키·체형·라이선스 조건)이 FaceMarket 모델
  리스트에 공개되는 것에 동의합니다."
- CTA "이 모습으로 공개하기"(둘 다 고르고 동의해야 활성), 보조 "다시 만들어 주세요"(1회, 기존).
- 성공 토스트 "프로필을 확정했어요. 모델 리스트에 올라갔어요." 뒤 `/status`로 이동(기존).
- 도착 컷이 4장이 아니어도 화면은 깨지지 않는다. 종류별로 있는 만큼 보여 주고, 한 종류가 0장이면
  CTA를 잠그고 "전신샷이 아직 없어요. 관리자에게 알려 주세요." 를 그 행에 띄운다.

## 6. 공개 모델 리스트·상세 (랜딩 `/models`)

- 목록은 `GET /v1/facemarket/public/models`를 부르고, 실모델 카드를 앞에, 가상 예시 카드
  (`browseModels.js`, '예시' 배지)를 뒤에 둔다. 실모델 카드에는 '예시' 배지가 없다.
- 실모델 카드 보조 텍스트: "키 178cm · 잔잔한 근육". 예시 카드는 기존 "키 · 몸무게" 유지.
- 고지 문구: 실모델이 하나라도 있으면 "'예시' 표시가 있는 카드는 가상 모델이에요." 로 바뀐다.
- 상세 창(오른쪽 서랍 유지)은 오너 스케치대로:
  - 위: 확대샷 | 전신샷 두 장 나란히(3:4, 같은 높이).
  - 아래 왼쪽 정렬로 활동명(크게).
  - 그 아래 두 칸, 가운데 세로선: "신체 사이즈"(키·체형) | "라이선스 조건"(허용 품목·건당 단가·유효기간).
  - 우상단 X. 기존 '모델 예시 이미지' 그리드는 제거.
  - 예시 모델은 확대샷 = `portrait`, 전신샷 = 본인 전신 예시가 있으면 그 첫 장, 없으면 "전신샷 준비 중"
    빈 칸(점선). 예시 배지는 이름 옆에 그대로.
- 실패(네트워크·서버)해도 예시 카드는 그대로 보인다. 실모델 호출은 조용히 실패한다.

## 7. 오너 결정 대기(구현은 아래 가정으로 진행)

1. 몸무게: 실모델은 입력 항목이 없어 "키 · 체형"으로 표시한다. 몸무게를 쓰려면 지원서에 항목을 추가해야 한다.
2. 공개 이름: 활동명(display_name)을 마스킹 없이 그대로 노출한다.
3. 예시 카드: 실모델이 생겨도 '예시' 배지를 달고 뒤에 남긴다. 내리는 시점은 오너가 정한다.
4. 기존에 1장만 골라 확정한 모델이 있다면 전신샷이 없어 공개 리스트에서 빠진다. 어드민이 다시 보내면 된다.

## 8. 분담·브랜치

| 브랜치 | 담당 | 범위 |
|---|---|---|
| `codex/facemarket-testcuts-server` | Codex A | §2 마이그레이션, §3 서버 전부, pytest |
| `codex/facemarket-testcuts-front` | Codex B | §3 API 클라이언트, §4 어드민, §5 모델 확정, node 테스트 |
| `codex/facemarket-detail` | Claude | 이 문서, §6 랜딩 목록·상세, 어댑터 node 테스트 |

합치는 순서: server → front → detail 을 `codex/facemarket`에 머지한 뒤 빌드·테스트·스크린샷.

## 9. 검증

- 서버: `cd server && .venv/bin/pytest -q tests/test_facemarket_model_test_cuts.py` 전부 통과, 전체 `pytest -q` 회귀 없음.
- 프론트: `npx vite build`, `node --test tests/frontend/facemarket-*.test.mjs`.
- 화면: 헤드리스 크롬으로 `/models` 카드·상세 창, `/model/confirm`(목 데이터), 어드민 모델 탭 캡처.
