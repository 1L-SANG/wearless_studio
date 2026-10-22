# 설계서: 상세페이지 게시(0단계) — 몰에 붙여넣을 영구 이미지 주소와 HTML 만들기

작성 2026-10-23. 근거 조사는 로컬 `.scratch/mall-upload-research-20261023/REPORT.md`. 코드 기준은 origin/main `eae46bb2`.
오너 확정(10/23): 호스팅한다. 보관은 구독 중 무제한, 해지 뒤 6개월, 언제든 ZIP 내보내기.

## 1. 의도

셀러가 Wearless 에디터에서 만든 상세페이지를 지그재그, 에이블리, 스마트스토어, 카페24 상품 등록 화면에 붙여 넣기만 하면 되게 한다. 몰들은 상세 이미지를 자기 서버로 복사하지 않고 우리가 준 주소를 그대로 읽어 가므로, 이미지를 우리 R2에 두고 영구 주소와 `<img>` HTML을 준다. 이 기능은 1단계(지그재그 API 등록)와 2단계(에이블리)의 전제이기도 하다. 두 단계 모두 몰에 넘길 수 있는 것은 이미지 주소뿐이다.

키위스냅(2026-10-11 종료)은 같은 구조였지만 종료와 함께 주소가 끊겨 셀러들의 몰 상세페이지가 깨졌다. 그래서 이 기능은 "게시"와 함께 "보관 약속"과 "내보내기"를 한 묶음으로 만든다.

## 2. 목표, 비목표, 반목표

목표
- 에디터에서 버튼 한 번으로 상세페이지 조각 이미지를 R2 공개 도메인(`https://images.wearless.kr`)에 올리고, HTML 한 덩어리와 이미지 주소 목록을 복사할 수 있다.
- 게시본은 불변 버전이다. 다시 게시하면 새 버전이 생기고 옛 버전 주소는 셀러가 지우기 전까지 살아 있다(몰에 이미 붙인 페이지를 조용히 깨뜨리지 않기 위해).
- 보관 규칙이 화면에 보인다. 만료 예정일, 남은 기간, ZIP 내보내기 버튼.
- 보관함에서 프로젝트별 게시 버전을 보고 HTML 복사, ZIP 받기, 삭제를 할 수 있다.

비목표(이번 범위 밖)
- 몰 API 호출(지그재그 createProduct 등)은 1단계.
- 대표 이미지(썸네일) 몰별 비율 변환은 1단계.
- GIF, 동영상 게시는 2차. 이번에는 정지 이미지 조각만.
- 공개 미리보기 웹페이지(`ai.wearless.kr/p/...`)는 만들지 않는다.
- FaceMarket 출처 공증(C2PA)은 게시본에 적용하지 않는다. 기존 다운로드 경로의 공증은 건드리지 않는다.
- FaceMarket 실존 모델(REAL) 컷이 들어간 프로젝트는 1차에서 "몰에 올리기"를 막는다. 운영 코드는 REAL 파생물을 공개 CDN에 올리지 않고 워터마크와 지문을 심은 다운로드로만 내보내며, 라이선스 취소 시 7일 안에 내리는 의무가 있어 "주소를 끊지 않는다"는 이 기능의 약속과 충돌한다. 프론트는 기존 `isRealModelSelection` 판정으로 버튼을 비활성화하고 "실존 모델 컷은 다운로드로 받아 몰에 직접 올려 주세요"를 보여 준다. 서버는 presign에서 `fm_output_records`로 같은 조건을 다시 확인해 거부한다. REAL 허용은 추적 체계(워터마크, 지문, 취소와 파기 훅)를 잇는 별도 설계로 한다.

반목표(이렇게 되면 실패)
- 셀러가 몰에 붙인 뒤 우리 쪽 조작(다시 게시, 프로젝트 삭제, 정리 작업)으로 주소가 예고 없이 끊기는 것.
- 게시가 느리거나 실패해서 기존 다운로드까지 막히는 것. 게시는 다운로드와 독립된 경로다.
- 다른 사람의 게시 주소를 추측하거나 목록으로 긁을 수 있는 것.

## 3. 제약

- Vite + React SPA, Zustand, CSS 변수. 컴포넌트는 `src/lib/api/` 어댑터만 호출한다.
- 서버는 FastAPI, DB는 Supabase Postgres, 저장은 R2. 마이그레이션은 append-only 새 파일.
- 렌더는 지금처럼 브라우저가 한다(`renderStorefrontFiles`, `captureBlockCanvasSlices`). 서버 렌더를 새로 만들지 않는다.
- 업로드는 브라우저가 presigned PUT으로 R2에 직접 올린다. FaceMarket 공증(`facemarket_provenance.py`의 presign, 업로드 토큰 HMAC)과 같은 패턴을 따르되 코드는 분리한다. 공증 라우트는 FaceMarket 플래그 뒤에 있어서 섞으면 안 된다.
- 한국어 문구는 단어 중간에서 줄이 바뀌지 않게 하고, 나란한 버튼은 같은 알약 모양, 모바일 입력칸 16px 이상(`CLAUDE.md` 디자인 절).
- 유료 AI API 호출 없음.

## 4. 용어

- 게시(publication): 한 프로젝트의 에디터 블록을 특정 폭 프리셋으로 렌더한 조각 이미지 묶음과 그 공개 주소. 불변.
- 버전: 같은 프로젝트의 게시가 여러 개면 만든 시각 순으로 번호를 붙인다(1, 2, 3).
- 조각(slice): 세로로 나눈 JPEG 한 장. 기존 다운로드의 "나눠서 받기" 단위와 같다(최대 3000px, 3MB 이하).
- 보관 만료일(expires_at): 이 날이 지나면 게시가 삭제된다. 구독 중이면 없음.

## 5. 셀러 흐름

1. 에디터 상단 "다운로드" 버튼 옆에 "몰에 올리기" 버튼을 둔다(같은 알약 모양, 둘 다 `genActive`면 비활성).
2. 누르면 "몰에 올리기" 모달. 내용은 다음 순서다.
   - 폭 선택: 기존 `EXPORT_PRESETS` 중 원본 PNG를 뺀 셋(860 스마트스토어·통용, 780 쿠팡, 1290 고화질). 기본 860. 형식은 JPEG 고정, 나눠 올리기 고정. 설명 한 줄: "지그재그, 에이블리, 스마트스토어 모두 860이면 충분해요."
   - 요약: 조각 수, 예상 용량(기존 `buildExportSummary` 재사용).
   - 보관 안내 한 줄: "구독 중에는 계속 보관해요. 구독이 없으면 게시일부터 6개월 뒤 지워져요. 언제든 ZIP으로 받을 수 있어요."
   - 버튼: 취소, 올리기.
3. 올리기를 누르면 진행 표시(이미지 만들기 n/m, 올리는 중 n/m). 끝나면 같은 모달이 결과 화면으로 바뀐다.
   - 조각 썸네일 가로 스크롤.
   - 버튼 묶음(알약 통일): "HTML 복사", "이미지 주소 복사", "ZIP 받기". 복사 성공은 토스트로.
   - 몰별 붙여넣기 안내 접이식 3줄: 지그재그(상품 등록의 상세 설명에서 HTML 편집으로 전환해 붙여넣기), 에이블리(상품 등록의 상세페이지에서 HTML 모드로 붙여넣기), 스마트스토어(상세설명 SmartEditor ONE의 HTML 모드). 메뉴 이름은 구현 때 오너가 실제 화면으로 확인한다.
4. 보관함(`Library.jsx`)의 프로젝트 카드에 "게시 n개" 표시. 누르면 게시 목록 패널: 버전, 게시일, 폭, 조각 수, 만료 예정일(없으면 "구독 중 보관"), 버튼 HTML 복사·ZIP 받기·삭제. 삭제는 확인 모달에 "몰에 붙여 둔 상세페이지가 깨져요. 몰에서 먼저 바꾼 뒤 지우세요."를 넣는다.
5. 다시 게시하면 새 버전이 생기고 옛 버전은 그대로 남는다. 결과 화면에 "이전 버전 n개가 남아 있어요. 몰을 새 주소로 바꾼 뒤 보관함에서 지울 수 있어요."를 보여 준다.

## 6. HTML 조각 형식

- 조각마다 한 줄: `<img src="https://images.wearless.kr/pub/<user_id>/<publication_id>/01.jpg" alt="<상품명> 상세 1" style="display:block;width:100%;max-width:<폭>px;margin:0 auto;">`
- 전체를 `<div style="text-align:center;">`로 감싼다. `script`, `style` 태그, 외부 폰트는 넣지 않는다(스마트스토어가 걸러낸다).
- "이미지 주소 복사"는 줄바꿈으로 구분한 URL 목록.
- 생성은 프론트의 순수 함수(`buildPublicationHtml(files, {productName, width})`)로 두고 테스트한다.

## 7. 데이터 모델 (새 마이그레이션 1개)

```sql
create table public.detail_publications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  project_id uuid references public.projects(id) on delete set null,
  version integer not null,                      -- 프로젝트 안 순번. (project_id, version) unique
  status text not null default 'pending'
    check (status in ('pending','ready','deleted')),
  preset text not null,                          -- 'smartstore' | 'coupang' | 'high'
  width integer not null,
  slice_count integer not null default 0,
  total_bytes bigint not null default 0,
  product_name text not null default '',
  editor_revision integer,                       -- 게시 시점의 projects.editor_revision
  files jsonb not null default '[]'::jsonb,      -- [{index, r2_key, bytes, width, height, asset_id}]
  expires_at timestamptz,                        -- null = 구독 중 보관
  created_at timestamptz not null default now(),
  ready_at timestamptz,
  deleted_at timestamptz
);
```

- `project_id`가 `set null`인 이유: 프로젝트를 지워도 게시는 살아 있어야 한다(반목표 1).
- 계정 삭제: 이 코드베이스의 `DELETE /v1/me/account`는 `auth.users` 행을 지우지 않고 파기 잡만 띄우므로 DB cascade에 기대지 않는다. 파기 잡(`biometric_purge`)에 "게시 삭제" 단계를 추가해 R2 삭제, CDN purge, `assets.deleted_at`, `status='deleted'`를 즉시 처리한다. 탈퇴한 셀러의 상품 사진이 공개 주소에 남지 않게 하기 위해서다. 탈퇴 확인 화면에 "몰에 올린 상세페이지가 깨져요"를 한 줄 넣는다.
- 조각 파일은 `assets`에도 한 줄씩 넣는다(`source='export'`, `visibility='public'`, `metadata.publication_id`). `assets`가 "R2 키의 단일 레지스트리"라는 기존 원칙을 지키기 위해서다.
- R2 키: `pub/<user_id>/<publication_id>/<index 2자리>.jpg`. uuid 두 개라 추측 불가. `Cache-Control: public, max-age=31536000, immutable`로 올린다(presign 때 ContentType처럼 고정하거나 complete 때 서버가 copy로 헤더를 다시 쓴다. 방법은 빌더가 고른다).
- RLS: 소유자만 select, insert, update. 삭제는 서버만. 기존 테이블의 RLS 문장을 그대로 따른다.
- 인덱스: `(user_id, created_at desc)`, `(expires_at) where status='ready'`.
- `documents/common_data_contract.md`에 "게시" 절을 추가한다(필드와 상태 전이).

## 8. 서버 API (v1 라우터, 로그인 필요)

- `POST /v1/projects/{project_id}/detail-publications`
  입력: `preset`, `width`, `files: [{index, byteSize, width, height}]`(최대 40개, 각 3MB 이하, 합 60MB 이하), `productName`.
  처리: 프로젝트 소유 확인, 사용자당 시간당 20회 제한, `version = max+1`, 행을 `pending`으로 만들고 조각마다 presigned PUT(5분)과 업로드 토큰(HMAC, key·publication_id·index·byteSize 포함)을 돌려준다.
- `POST /v1/detail-publications/{id}/complete`
  입력: 업로드 토큰 목록. 처리: 토큰 검증, R2 `head`로 존재와 크기와 MIME 확인(불일치면 그 조각만 실패로 돌려주고 행은 `pending` 유지), 전부 맞으면 `assets` 행 생성, `files` 채움, `status='ready'`, `expires_at` 계산, 결과(`files[].url`, `html`, `expiresAt`, `version`) 반환.
- `GET /v1/projects/{project_id}/detail-publications` 목록(ready만, 최신순).
- `DELETE /v1/detail-publications/{id}` 소프트 삭제 후 R2 삭제와 CDN purge(`purge_public_cache`)를 바로 시도하고, 실패하면 정리 워커가 재시도한다.
- `pending`인 채 30분이 지나면 정리 워커가 올라온 조각을 지우고 행을 `deleted`로 바꾼다.
- 프론트 어댑터: `src/lib/api/detailPublications.js`에 위 네 함수. 목 어댑터(`src/mock/api.js`)에도 같은 계약으로 가짜 응답을 넣는다.

## 9. 보관 규칙과 정리 워커

- 구독 중의 정의: `profiles.plan`이 `'free'`가 아닌 사용자. 토스 자동결제(`subscriptions`)와 계좌이체 수동 이용권(`manual_plan_grants`) 두 경로가 모두 이 컬럼을 갱신하므로, `subscriptions` 테이블만 보면 계좌이체 셀러가 구독자에서 빠진다.
- 만료일 계산(게시 완료 때와 매일 1회 워커). 구독 종료 시각이 따로 기록되지 않으므로 워커가 처음 관찰한 시점을 기준으로 한다.
  - `plan`이 free가 아니면 `expires_at = null`.
  - `plan`이 free이고 `expires_at`이 null이면 `expires_at = now() + 6개월`(게시 완료 때 free면 `created_at + 6개월`).
  - `plan`이 free가 아니게 되면 다시 `null`로 되돌린다.
  - 하루 1회 계산이라 "해지 뒤 6개월"은 최대 하루 오차가 있다.
- 삭제 14일 전부터 보관함 목록에 "n일 뒤 지워져요"를 빨간 글씨로 보여 준다. 메일 알림은 이번 범위 밖이지만 워커가 `expires_at`를 계산하는 자리에 훅만 남긴다.
- 만료일이 지난 게시는 워커가 R2 삭제, CDN purge, `assets.deleted_at`, `status='deleted'` 순으로 처리한다. R2 삭제가 실패하면 상태를 바꾸지 않고 다음 주기에 다시 한다.
- 워커는 `draft_asset_reclaimer.py`와 같은 구조로 `detail_publication_reaper.py`를 새로 만든다. 주기 1시간, 만료 계산은 하루 1회.

## 10. 실패 처리

- 렌더 실패: 기존 다운로드와 같은 문구와 재시도 버튼. 서버 행은 만들기 전이므로 남는 것이 없다.
- 업로드 일부 실패: 실패한 조각만 다시 PUT. 3번 실패하면 "지금은 올리지 못했어요. 대신 ZIP으로 받아 두세요" 버튼을 보여 준다.
- complete 검증 실패(크기 불일치 등): 조각 번호를 돌려주고 프론트가 그 조각만 다시 올린다.
- 네트워크 무응답: `publicationNotarize.js`처럼 모든 왕복에 타이머를 건다(presign과 complete 8초, PUT 60초).
- 어떤 실패도 다운로드 기능에 영향을 주지 않는다.

## 11. 보안

- 키에 사용자 uuid와 게시 uuid가 들어가 추측할 수 없다. 목록 조회 API는 소유자만.
- presign은 MIME을 `image/jpeg`로 고정하고 크기 상한을 검증한다. 업로드 토큰 없이는 complete를 못 한다.
- 공개 도메인은 이미 생성 이미지에 쓰고 있는 것과 같다. 새로 여는 것이 없다.
- 삭제된 게시 주소로 들어오는 요청은 R2 404다. 별도 안내 페이지는 만들지 않는다.

## 12. 비용

- R2 저장 GB당 월 0.015달러, 나가는 전송 무료, 읽기 요청 100만 건당 0.36달러. 셀러 1,000명이 상품 50개를 평균 10MB로 게시하면 500GB, 월 7.5달러 수준.
- 다시 게시할 때 옛 버전이 쌓이므로 보관함에서 지우기 쉽게 만든다. 1차에서는 버전 수 상한을 두지 않는다.

## 13. 배포 순서

1. 마이그레이션(append-only, CI `supabase db push`). 테이블만 추가라 옛 코드와 호환된다.
2. 서버: 라우터와 워커. 환경 플래그 `DETAIL_PUBLISH_ENABLED`(기본 off)로 라우터 등록을 감싼다.
3. 프론트: 모달과 보관함 패널. 플래그가 꺼져 있으면 서버가 404를 주므로 프론트는 기능 감지로 버튼을 숨긴다(`GET` 목록이 404면 숨김).
4. 운영에서 플래그 on. Claude가 실제 게시 1건으로 헤더와 주소를 확인한 뒤 오너에게 보고.
5. 플래그 on의 선행 조건(오너 결정 대기): 이용약관에 게시물 보관 조항(구독 중 보관, 구독 없으면 6개월, 탈퇴 시 즉시 삭제, 서비스 종료 시 유예와 내보내기)을 넣을지. 화면에 약속을 박는 기능이라 약관과 어긋나면 안 된다. 코드 구현은 이 결정과 무관하게 진행할 수 있다.

참고: `images.wearless.kr`은 지그재그 Referer로도 200을 주고 `access-control-allow-origin`과 객체별 `cache-control: immutable`이 반영되는 것을 curl로 확인했다(2026-10-23 검토). 몰에서 그대로 읽는 데 문제가 없다.

## 14. 검증 계획

- 서버 pytest: presign 상한과 횟수 제한, complete의 head 검증 분기, 소유자 아닌 접근 403, 삭제 후 목록 제외, 만료 계산 세 경우(구독 중, 구독 없음, 해지 뒤), 워커의 R2 실패 재시도.
- 프론트 테스트(`tests/frontend`): `buildPublicationHtml` 출력, 설정 정규화, 모달 상태 전이(진행 중 취소 불가, 실패 시 재시도), 목 어댑터로 전체 흐름.
- 화면 검사(Claude가 샌드박스 밖에서): 폰 폭 320, 360, 375, 390, 414에서 모달과 보관함 패널 스크린샷, 단어 중간 줄바꿈과 가로 넘침 자동 검사, WebKit 포함.
- 운영 확인: 게시 1건 뒤 `curl -I`로 `content-type: image/jpeg`, `cache-control: immutable` 확인. 오너가 테스트 스토어가 있으면 지그재그와 에이블리 에디터에 붙여 넣어 모바일에서 확인.
- `pnpm build`, `pnpm test:frontend`, `cd server && .venv/bin/pytest -q` 전부 통과.

## 15. 빌더에게 맡기는 것

- 모달 컴포넌트를 `EditorDownloadModal`과 나눌지 합칠지. 공유할 수 있는 요약과 프리셋 UI는 재사용한다.
- 조각 업로드 동시성(권장 3개씩).
- `Cache-Control` 고정 방법(presign 헤더 고정 또는 complete 때 copy).
- 업로드 토큰 서명 비밀값을 어디서 가져올지. 기존 `_token_secret` 방식과 같은 등급의 설정을 새로 둔다.
- 워커 파일 구조와 로그 문구.

## 16. 내가 정한 것 (오너가 바꿀 수 있음)

- 게시는 모든 로그인 사용자에게 무료다. 크레딧을 깎지 않는다.
- 구독 여부는 `profiles.plan`으로 본다. 구독이 없는 사용자는 게시일부터 6개월, 해지한 사용자는 해지가 관찰된 날부터 6개월 보관한다.
- 다시 게시해도 옛 버전은 자동으로 지우지 않는다.
- 프로젝트를 삭제해도 게시는 남는다. 탈퇴 때는 파기 잡이 즉시 지운다.
- FaceMarket 실존 모델 컷이 든 프로젝트는 1차에서 게시를 막는다.
- 1차는 정지 이미지 JPEG만. GIF와 동영상은 2차.
- 기본 폭 860, 형식 JPEG 고정.
