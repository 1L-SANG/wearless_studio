# 내 매칭 의류 업로드 (Custom Matching Item) — 설계

- 날짜: 2026-08-04
- 상태: D1–D11 확정 · 구현·검증 완료(서버 1375 · 프론트 138 · 빌드 통과) · 마이그레이션 적용 후 실검증 대기
- 관련: `documents/common_data_contract.md §6`, `server/app/services/matching.py`, `src/lib/matchingFit.js`

## 1. 배경

분석 페이지의 **매칭 의류** 그리드는 운영자가 큐레이션한 16벌(성별×상/하의 버킷당)만
보여준다. 셀러가 실제로 함께 팔거나 코디에 쓰고 싶은 **자기 옷**은 넣을 수 없다.

## 2. 목표 / 비목표

**목표**
- 셀러가 매칭 의류 그리드에서 같은 자기 옷 사진 1~4장을 올려 매칭 후보 1벌로 쓸 수 있다.
- 올린 옷도 큐레이션 의류와 **똑같이** 메인/서브로 고를 수 있고 마네킹 컷·상세페이지 컷에 쓰인다.
  다만 **핏 조정 스텝은 열지 않는다**(D11) — 실물 사진이라 있는 그대로 입히는 게 맞다.
- 그리드는 항상 **16칸(8×2)** 을 유지한다.

**비목표(이번 범위 밖)**
- 여러 **벌** 업로드, 계정 단위 옷장(재사용), 이름 직접 수정, 배경 제거·리터칭.
- 큐레이션 의류 자체의 순위/추천 로직 변경.

## 3. 확정 결정 (2026-08-04 오너)

| # | 결정 | 내용 |
|---|---|---|
| D1 | 큐레이션 4벌 **완전 제거** | 텍스트 지시대로 (§4 표) — DB 행·R2 객체·시드 원본까지 전부 삭제. 각 버킷 16→15벌 |
| D2 | ~~메타데이터~~ | ~~Gemini 3.6 flash 이미지 추론~~ — **D10으로 폐기(2026-08-05)** |
| D3 | 저장 범위 | **해당 프로젝트에서만**, 프로젝트당 **1벌** |
| D4 | 그리드 | **16칸 고정** (큐레이션 15 + 추가 버튼 1 → 업로드 후 큐레이션 15 + 내 옷 1) |
| D5 | 내 옷 노출 | **성별과 무관하게 항상** 그리드 맨 앞에 뜬다 (2026-08-05 오너) |
| D6 | 여러 각도 업로드 | **같은 한 벌**을 최대 **4장**까지. 서버가 **2×2 그리드 1장**으로 합성해 저장하고, 마네킹컷·컷(상세페이지) 생성에 그 그리드를 첨부한다 (2026-08-05 오너) |
| D7 | 성별 칩 | **해제 불가** — 항상 정확히 1개 선택 상태 유지(재클릭으로 `null` 되지 않음) (2026-08-05 오너) |
| D8 | 사진 보안 범위 | **RLS 분리만** 한다. 전용 private 버킷·인증 프록시 서빙은 채택하지 않고 기존 상품 사진과 동일하게 취급 (2026-08-05 오너 · 근거 §5.3) |
| D9 | 업로드 API | **단일 POST**. preview→draftToken→confirm 2단계는 채택하지 않고, 모달을 닫으면 요청 abort + 후보 재조회로 화면을 실제 상태에 맞춘다 (2026-08-05 오너 · 근거 §7) |
| D10 | **AI 추론 제거** | 업로드 대기시간 때문에 Gemini 메타데이터 추론을 **뺀다**. 종류는 슬롯이 정본(하의 슬롯 = 하의), 나머지는 중립값. 잘못된 종류 차단도 함께 사라진다 (2026-08-05 오너) |
| D11 | 내 옷 핏 조정 없음 | 마네킹 화면에서 **내 옷은 매칭 조정 스텝을 열지 않는다**. 상의·하의 모두 `fitCategory = null` (2026-08-05 오너) |

D10·D11 보충 — 둘은 한 몸이다. 셀러가 올린 건 **실물 사진**이라 실루엣·기장을 조정하면 올린
옷과 다른 옷이 나온다. 그래서 ① 카테고리·기장을 추론하지 않고(D10) ② 조정 축도 열지 않는다(D11).
`category`에 큐레이션 어휘(`팬츠`·`스커트`…)를 지어 넣으면 `fit_category`가 잘못된 축을 열어 주므로
닫힌 어휘에 없는 `'커스텀'`을 쓴다. 축 차단은 **한 곳**에서 한다 —
`matching.fit_category()`가 `owner_user_id`/`is_custom` 이면 즉시 `None`
(`server/app/services/matching.py`), 프론트 `matchingFitDefinition`·mock
`fitCategoryFromMatchingMetadata`가 같은 규칙을 미러한다.

**D10이 없애는 것**: 상의 상품에 상의를 올려도 막지 못한다. 오너 판단은 "하의를 올려야 하는
자리면 셀러가 당연히 하의를 올린다"(2026-08-05). 남는 게이트는 원피스 상품(매칭 자체 없음)뿐.

D6 보충 — "여러 벌"이 아니라 **한 벌의 앞·뒤·디테일·착용샷**이다. 매칭 후보는 여전히
"내 옷" **1칸**이고 메인/서브 선택도 그대로다. 그리드는 생성 모델에게 옷의 형태를 정확히
전달하기 위한 입력 형식일 뿐, 여러 벌을 동시에 입히라는 뜻이 아니다.

D7 보충 — 공용 `Chips`는 단일 선택 칩을 재클릭하면 현재 `null`을 보낸다
(`src/components/ui.jsx:129-137`). 전역 동작을 바꾸면 세부 카테고리처럼 해제가 의미 있는 기존
호출자(`src/features/analysis/AnalysisForm.jsx:488-505`)까지 바뀌므로, `Chips`에
`allowDeselect=true` 기본 prop을 추가하고 **대상 성별 호출부만** `allowDeselect={false}`로 넘긴다
(`src/features/analysis/AnalysisForm.jsx:506-513`). `normalizeTargetGendersForClothingType`도 현재는 빈
배열을 허용하므로(`src/lib/productGender.js:11-15`), 분석 초기값/레거시 저장분을 읽을 때 dress는
`women`, 그 외는 기존 첫 값 또는 `women`으로 정규화해 항상 정확히 1개를 만든다. 서버
`prefilter`의 "빈 성별 → 전부 통과"는 다른 호출자 호환을 위해 그대로 둔다
(`server/app/services/matching.py:51-67`).

> 승인(2026-08-05 Claude 검토): D7은 공용 칩 전역이 아니라 분석 화면의 **대상 성별 칩에만** 해제 불가를 적용한다.

D2 보충 — `color_brightness`는 큐레이션을 밝은 순으로 줄 세우는 **정렬 전용** 값이다
(`matching.py:72`). 내 옷은 그리드 맨 앞 고정이라 정렬에 끼지 않으므로 추론하지 않고
기본값 50을 쓴다.

## 4. 큐레이션 4벌 완전 제거 (D1)

표시 순서(= `color_brightness` 내림차순, 동률 `sort_order`) 기준으로 지목된 항목의
실제 id·R2 키는 프로덕션 DB 조회로 확정했다.

| 버킷 | 지시 | id | 이름 | R2 키 (본 / 썸네일) |
|---|---|---|---|---|
| 여성 상의 | 5번째 | `match_women_top_12` | 화이트 슬리브리스 | `seed/matching/match_women_top_12.png` / `seed/matching/thumb/match_women_top_12.png` |
| 여성 하의 | 7번째 | `match_women_bottom_01` | 워시드블루 쇼츠 | `seed/matching/match_women_bottom_01.png` / `seed/matching/thumb/match_women_bottom_01.png` |
| 남성 상의 | 7번째 | `match_men_top_09` | 멜란지그레이 헨리넥 티셔츠 | `seed/matching/match_men_top_09.png` / `seed/matching/thumb/match_men_top_09.png` |
| 남성 하의 | 16번째 | `match_men_bottom_16` | 블랙 쇼츠 (15번째와 사실상 중복) | `seed/matching/match_men_bottom_16.png` / `seed/matching/thumb/match_men_bottom_16.png` |

버킷은 R2 `wearless` 하나다. 자산 UUID는 §4.1 스크립트가 id로 재조회하므로 하드코딩하지 않는다.

**흔적을 남기지 않는다** — 되살아날 수 있는 경로가 네 곳이라 전부 같이 지운다.
`seed_matching.py`(로컬 이미지 → R2 → DB upsert)와 `restore_matching_from_r2.py`
(시드 JSON + R2 객체 → DB 행 복구)가 **멱등 upsert**라, 한 곳이라도 남으면 다음 시드/복구
실행 때 4벌이 그대로 돌아온다.

| # | 대상 | 조치 |
|---|---|---|
| 1 | `matching_items` 4행 | delete |
| 2 | `assets` 8행 (본 4 + 썸네일 4) | delete (FK 때문에 1번 뒤에) |
| 3 | R2 객체 8개 | `R2Client.delete(key)` |
| 4 | `server/seed/matching_items.json` | 4개 엔트리 삭제 |
| 5 | `src/mock/seedMatchingItems.js` | 대응 4개 엔트리 삭제 (id 동일) |
| 6 | `public/assets/matching/{top,bottom}/…` + `thumbs/…` 8파일 | 삭제 |

로컬 원본 파일(6번):
`top/women-top-12.png`, `top/men-top-09.png`, `bottom/women-bottom-01.png`,
`bottom/men-bottom-16.png` 와 각 폴더의 `thumbs/` 동명 파일.

카운트 기준은 **전체 카탈로그**다. 성별×상/하의 4버킷이 각 16벌인 64행에서 버킷별 1벌씩
지우므로 서버 JSON과 mock 모두 **총 64→60행**, 각 버킷은 16→15벌이 된다
(`src/mock/seedMatchingItems.js:1-4`).

### 4.1 일회성 제거 스크립트

`server/scripts/purge_matching_items.py` — 인자로 받은 id들을 DB→R2 순서로 지운다.
`--dry-run` 기본, `--apply` 로만 실제 삭제. 기존 운영 스크립트(`seed_matching.py`,
`restore_matching_from_r2.py`)의 규약을 따른다.

스크립트에는 §4 표의 id 4개와 R2 키 8개를 **고정 manifest**로 함께 둔다. DB에서 키를 다시
찾는 것에만 의존하면 DB 삭제 성공/R2 삭제 실패 뒤 재실행할 때 키를 복구할 수 없기 때문이다.

1. 고정 manifest와 DB 재조회 결과가 일치하는지 검증한다. 현재처럼 DB 행이 이미 모두 없으면
   재실행 상태로 인정하고 R2 단계로 진행하며, 일부만 남았거나 남은 row의 키가 다르면 중단한다.
2. `delete from matching_items where id = any(%s)`와 `delete from assets where id = any(%s)`를 한
   DB 트랜잭션으로 commit한다.
3. commit 뒤 고정 manifest의 각 키에 `R2Client.delete(key)`를 멱등 호출한다. 일부 실패면 성공/실패
   키를 모두 출력하고 non-zero로 종료한다.
4. 재실행은 DB 행이 이미 없어도 고정 manifest로 남은 R2 삭제를 계속한다. DB 삭제는 롤백하지
   않으며, R2 전부와 CDN purge가 성공하기 전에는 작업을 완료로 표시하지 않는다.
5. 지운 목록과 단계별 결과를 요약 출력한다.

DB↔R2 부분 실패의 복구 규칙은 **고정 manifest를 이용한 멱등 재실행**이다. 실행 전에 시드 JSON,
mock 시드, 로컬 원본을 먼저 제거·배포해 `restore_matching_from_r2.py`가 중간 상태에서 행을 되살리지
못하게 하고, 실패 시 같은 `--apply`를 재실행한다. R2 삭제 성공/DB commit 실패도 재실행하면 없는
R2 키 삭제는 no-op이고 DB 삭제를 마친다. `R2Client.delete`는 단일 키 삭제 API다
(`server/app/r2.py:120-122`).

> 승인(2026-08-05 Claude 검토): D1의 부분 실패는 자동 보상 복구가 아니라 위 멱등 재실행 규칙으로 운영한다.

**진행 상태 (2026-08-05) — 1~6번 전부 실행 완료.**

| # | 대상 | 결과 |
|---|---|---|
| 1·2 | `matching_items` 4행 · `assets` 8행 | 오너가 Supabase 콘솔 SQL로 삭제 |
| 3 | R2 객체 8개 | 삭제 후 재조회 0개 확인 |
| 4 | `server/seed/matching_items.json` | 64 → 60 |
| 5 | `src/mock/seedMatchingItems.js` | 64 → 60 (모듈 로드 검증 60/잔존 0), 헤더 주석도 60으로 |
| 6 | `public/assets/matching/` 8파일 + `dist/assets/matching/` 8파일 | 삭제, `npx vite build` 통과 |

따라서 §4.1 제거 스크립트는 **이번 건에는 쓰지 않는다** — 아래 규약은 다음에 같은 작업을
할 때의 정본으로만 남긴다.

남은 항목 둘:
- **CDN 캐시** — 8개 public URL의 Cloudflare exact-URL purge. 저장소 밖 작업이라 오너 몫.
- **`.worktrees/canvas` 사본** — 보조 worktree라 제품에 영향 없으나, 그 브랜치를 되살릴 때
  네 id가 함께 돌아온다(`.worktrees/canvas/src/mock/seedMatchingItems.js:236,337,836,1315`).
  구현 PR이 `dist/**`와 함께 잔존 검사에 포함한다.

삭제 전 prod 실측(2026-08-05):
- `assets`를 참조하는 FK 11개 전부에서 대상 8 asset 참조 **0행**
- `projects.storyboard` / `projects.editor_blocks`에 4개 id **0건**
- 4벌을 **메인**으로 고른 프로젝트 **0건**, `matchSelections`·`fitProfile.matchingFit` **0건**
- **서브**로 고른 프로젝트 1건(`5152335f…`, 7/31, `match_women_bottom_01` selOrder=2) —
  생성은 메인만 해석하므로(`mannequin.main_match_item_id`) 이미지로 도달하지 않음
- `analyses` 101건 중 77건이 id를 언급하나 전부 **후보 목록 사본**(다음 조회 시 교체됨)

**prod 실행은 오너 승인 후 사람이 직접**(마이그레이션 자동 적용 대상 아님).
R2 삭제는 되돌릴 수 없다 — `restore_matching_from_r2.py`의 주석이 기록하듯 2026-07-31에
prod DB 행이 통째로 날아갔을 때 R2 객체가 유일한 복구 원본이었다. 이 4벌은 그 안전망을
스스로 포기하는 것이며, 오너가 "R2에서도 빼"라고 명시 결정했다(2026-08-05).

R2 객체의 기존 응답은 1년 `immutable` 캐시를 쓸 수 있다(`server/app/r2.py:22-23`). 따라서 객체
삭제만으로는 CDN 사본이 즉시 사라진다고 간주하지 않는다. 8개 public URL을 Cloudflare의 exact-URL
purge로 지우고, 각 URL이 origin/CDN 모두 404인지 확인해야 D1을 완료로 표시한다.

### 4.2 남는 참조

오너가 이미 삭제한 4행을 메인으로 고른 프로젝트는 **0건**이고, 서브 1건만 있었다는 §4.1의
prod 실측은 그대로 유효하다. 다만 삭제 id의 런타임 처리는 경로마다 다르다.

- **마네킹 워커만 조용히 격하**한다. `get_matching_item_asset`이 `None`이면 `match_asset`을 두지
  않고(`server/app/workers/mannequin_job.py:1150-1155`), 매칭 이미지를 첨부하지 않은 채 계속한다
  (`server/app/workers/mannequin_job.py:1166-1175`).
- **상세페이지 워커는 해당 착용 컷을 fail-closed**한다. 선택 id 중 하나라도 asset을 못 풀면 빈
  슬롯으로 넣고 그 컷 생성을 건너뛴다(`server/app/workers/detail_page_job.py:815-826`). 잡 전체를
  500으로 만들지는 않지만 "매칭 없음"으로 정상 생성하는 것도 아니다.
- **에디터 워커는 잡을 실패**시킨다. 하나라도 없으면 `matching_asset_unavailable`로 종료한다
  (`server/app/workers/editor_image_job.py:231-236`).
- **프론트 `matchingFit.js`는 stale main을 스스로 폐기하지 않는다.** 후보 map에 없어도 레거시
  `matchClothing`의 첫 선택을 반환할 수 있다(`src/lib/matchingFit.js:22-44`). 후보 재조회 시
  `recommendMatchHttp`가 현재 응답에 존재하는 id만 유지하는 별도 동작은 있다
  (`src/lib/api/httpAdapter.js:228-238`). 서버 재생성 검증은 메타데이터/asset이 없으면 핏 조정을
  제거한다(`server/app/routes.py:131-154`).

따라서 회귀 계약도 경로별로 나눈다(§10). "모든 경로가 에러 없이 매칭 없음으로 격하"라는 단일
주장은 사용하지 않는다.

## 5. 데이터 모델

`matching_items`를 확장한다. 후보 테이블은 따로 만들지 않는다. 현재 자산/메타데이터 해석 경로는
API 핏 검증(`server/app/routes.py:131-154`), 마네킹(`server/app/workers/mannequin_job.py:1150-1155`),
상세페이지(`server/app/workers/detail_page_job.py:654-663`), 에디터
(`server/app/workers/editor_image_job.py:194-208`) 네 production 소비처다. 여기에 production 외
실험 스크립트 `spike_volume_secondpass.py`가 같은 자산 조회를 쓰는 **5번째 소비처**로 존재한다
(`server/scripts/spike_volume_secondpass.py:201-207`). 이 다섯 경로 모두 커스텀 row와 grid asset을
해석하도록 회귀 테스트한다.

```sql
-- supabase/migrations/2026xxxx_custom_matching_items.sql (append-only)
alter table public.matching_items
  add column owner_user_id uuid references auth.users (id) on delete cascade,
  add column project_id uuid references public.projects (id) on delete cascade,
  add constraint matching_items_owner_project_pair_chk check (
    (owner_user_id is null and project_id is null)
    or (owner_user_id is not null and project_id is not null)
  );

-- 프로젝트당 커스텀 1벌 (D3). project id 자체가 전역 unique라 이것이 정확한 경계다.
create unique index matching_items_custom_project_uniq
  on public.matching_items (project_id)
  where owner_user_id is not null;

-- Pillow가 만든 파생 grid를 원본 upload와 구별한다.
-- 현재 source CHECK는 upload/ai/export/seed만 허용한다
-- (supabase/migrations/20260612090000_init.sql:93-111).
alter table public.assets drop constraint assets_source_check;
alter table public.assets add constraint assets_source_check
  check (source in ('upload', 'ai', 'export', 'seed', 'derived'));

-- 기존 "모든 active 행" 정책을 반드시 분리한다.
drop policy matching_items_active_select on public.matching_items;
create policy matching_items_curated_select on public.matching_items
  for select to authenticated
  using (is_active and owner_user_id is null and project_id is null);
create policy matching_items_custom_owner_select on public.matching_items
  for select to authenticated
  using (
    is_active
    and owner_user_id = (select auth.uid())
    and exists (
      select 1 from public.projects p
      where p.id = matching_items.project_id
        and p.user_id = (select auth.uid())
        and p.deleted_at is null
    )
  );
```

기존 RLS는 인증 사용자라면 모든 `is_active` 행을 읽게 한다
(`supabase/migrations/20260612090000_init.sql:324-326`). 위 forward migration은 이를 drop한 뒤
큐레이션 active row만 공용, 커스텀 active row는 `owner_user_id = auth.uid()`이면서 본인 프로젝트일
때만 보이게 나눈다. nullable CHECK는 큐레이션 `(null,null)`과 커스텀 `(owner,project)` 외의 반쪽
row를 금지하고, partial unique index는 프로젝트당 커스텀 1벌을 DB에서도 강제한다.

> 승인(2026-08-05 Claude 검토): 파생 grid를 감사·검증에서 원본 upload와 구분하기 위해 `assets.source`에
> `derived`를 추가한다.

큐레이션 4벌 제거(D1)는 **이 마이그레이션에 넣지 않는다** — R2 객체 삭제가 같이 가야
하는데 SQL은 R2를 건드릴 수 없고, 반쪽만 적용되면 DB에는 없고 R2에는 남는 상태가 된다
(그 상태에서 `restore_matching_from_r2.py`를 돌리면 되살아난다). §4.1 스크립트가 DB와 R2를
한 번에 처리한다.

커스텀 행의 컬럼 값:

| 컬럼 | 값 |
|---|---|
| `id` | `'custom_' \|\| gen_random_uuid()` (text PK) |
| `owner_user_id`, `project_id` | 업로더/프로젝트 |
| `name` | AI 추론 (한글 짧은 이름, 예 "블랙 와이드 팬츠") |
| `clothing_type` | AI 추론. **보완 타입과 다르면 업로드 거부**(§7) |
| `gender` | `'unisex'` (D5 — 성별 프리필터를 항상 통과. 아래 참고) |
| `category` | AI 추론, 닫힌 어휘(§6) |
| `length` | AI 추론, 닫힌 어휘(§6) |
| `fit` | `'regular'` 고정 (NOT NULL 채움용 · 소비처 없음) |
| `color_name` / `color_group` | AI 추론 색 이름 / 닫힌 색 그룹, 실패 시 `'커스텀'` / `'gray'` |
| `style_tags` | `'[]'` |
| `color_brightness` | `50` (정렬 미사용 — 라우트가 앞에 고정) |
| `sort_order` | `0` |
| `image_asset_id` | 서버가 합성한 2×2 grid asset id (`source='derived'`) |
| `thumbnail_asset_id` | 순서 1번 원본 asset id (`source='upload'`) |

### 5.1 D6 원본·grid 저장

- 원본 1~4장은 각각 독립 `assets` row로 유지한다. `user_id`/`project_id`는 업로더/현재
  프로젝트, `source='upload'`, `visibility='private'`, metadata는
  `{purpose:'custom_match_source', order:1..4}`다. 순서 변경 시 metadata order도 같이 바꾼다.
- 업로드 자체는 기존 상품 사진 경로를 그대로 쓴다(`server/app/routes.py:815-826`, 공용 버킷).
  `purpose='custom_match_source'`는 presign/complete에 함께 넘겨 metadata로만 남기고, 버킷·클라이언트
  분기는 만들지 않는다(D8).
- 합성본은 별도 `assets` row다. `source='derived'`, `visibility='private'`, metadata는
  `{purpose:'custom_match_grid', sourceAssetIds:[순서대로 1~4개]}`다. `matching_items.image_asset_id`는
  이 grid를 가리키므로 세 생성 경로가 받는 매칭 이미지도 grid 한 장이다.
- `matching_items.thumbnail_asset_id`는 원본 1번을 가리킨다. UI 타일은 전체 grid가 아니라 가장
  대표성이 높은 1번 사진을 보여준다. 원본은 재합성·감사·향후 교체를 위해 보존하며, 커스텀 row
  삭제 시 이 row만 참조하는 grid와 원본 1~4장을 함께 soft-delete 후 비동기 R2 정리한다. 다른
  참조가 확인되면 해당 asset은 남긴다.
- **전용 private 버킷을 만들지 않는다(D8).** 원본·grid 모두 기존 공용 `wearless` 버킷에 들어가고
  썸네일은 다른 후보와 똑같이 `r2.public_url`로 직렬화한다. 근거는 §5.3.

### 5.2 D6 합성 규칙

서버의 Pillow로 결정적으로 합성한다. 기존 `face_grid.compose_sedcard`도 Pillow 기반 2×2 PNG를
만들지만 ① 앞 3장만 받고, ② 중앙 정사각 크롭하며, ③ 640px 셀/무여백 고정이다
(`server/app/agents/face_grid.py:1-6,13-36`). 옷 전체 실루엣을 잘라낼 수 있고 4장을 버리므로
**직접 재사용하지 않는다**. 새 `server/app/services/garment_grid.py`가 EXIF 회전 보정과 contain
배치를 구현하되 Pillow 디코드/캔버스 패턴만 참고한다.

- 출력: RGB JPEG, 1600×1600px, quality 92, 배경 `(245,245,245)`.
- 바깥 여백 40px, 셀 사이 gutter 24px, 각 셀 748×748px. 원본 비율을 보존해 cell 안에 contain하고
  중앙 정렬한다. crop·배경 제거·왜곡은 하지 않는다.
- 1~3장은 입력 순서대로 좌상→우상→좌하에 놓고 남은 칸은 같은 중립 배경으로 비운다. 4장은
  좌상→우상→좌하→우하를 모두 쓴다. 한 장만 있어도 좌상 1칸이 채워진 **완성된 유효 grid**다.
- 합성 전에 각 원본을 완전히 decode하고 최소변 400px QC를 통과시킨다. 한 장이라도 실패하면
  grid/row를 만들지 않는다. 현재 QC는 decode 실패·최소변만 판정한다
  (`server/app/services/input_qc.py:29-42`).
- 동일 입력 순서와 바이트에는 동일 결과가 나오게 하며, grid checksum으로 중복 저장을 피한다.

> 승인(2026-08-05 Claude 검토): 1600px·40px margin·24px gutter·contain·빈칸 유지의 고정 2×2 규격을 채택한다.

#### 5.2.1 업로드 성능 (2026-08-05)

- 브라우저는 공용 `imageTranscode` 유틸로 각 원본을 **긴 변 1600px 이하, JPEG quality 0.85**로
  한 번만 정규화한 뒤 R2에 올린다. 단, 축소 때문에 최소변 400px 게이트를 새로 깨지 않도록
  극단적인 종횡비에서는 최소변 400px를 긴 변 상한보다 우선한다. HEIC도 같은 단계에서 bitmap으로
  한 번 디코드해 목표 크기 JPEG로 바로 인코딩한다.
- 1~4장의 `presign → PUT → complete` 체인은 사진별 `Promise.all`로 병렬 실행한다.
- 서버는 원본 R2 GET과 mandatory QC를 각각 `asyncio.gather` + `to_thread`로 병렬·격리하고,
  결정적 grid 합성도 `to_thread`에서 실행한다. §5.2 출력 규격과 원본/derived 저장 계약은 그대로다.

### repo 변경

**D5 (성별 무관 노출) 구현 주의** — `gender='unisex'` 는 `matching.prefilter`가
`gender in gset or gender == 'unisex'` 로 통과시키는 값이라 성별 칩이 무엇이든 살아남는다.
다만 프리필터에는 `clothing_type == preferred` 조건도 함께 걸려 있고 랭킹도 태우게 되므로,
커스텀 행은 **프리필터/랭킹에 아예 넣지 않고 라우트가 따로 꺼내 맨 앞에 붙인다**(§7).
`unisex` 값은 `matching.py` 순수 함수를 재사용할 때와 앞으로의 조회를 위한 안전값이지,
노출을 그 함수에 맡긴다는 뜻이 아니다.

- `list_active_matching_items(conn, user_id, project_id)`는 아래처럼 **두 보안 경계를 `UNION ALL`로
  분리**한다. 현재 쿼리의 seed/public join은 의도적인 비공개 키 차단선이다
  (`server/app/repo.py:381-400`). 큐레이션 arm을 느슨하게 만들지 않는다.

```sql
select mi.id, mi.name, mi.clothing_type, mi.gender, mi.category,
       mi.color_name, mi.color_group, mi.style_tags, mi.fit, mi.length,
       mi.color_brightness, mi.sort_order, mi.is_active,
       false as is_custom,
       img.id::text as image_asset_id, img.r2_key as image_key,
       thb.id::text as thumbnail_asset_id, thb.r2_key as thumb_key,
       img.r2_bucket as image_bucket, thb.r2_bucket as thumb_bucket
from matching_items mi
join assets thb on thb.id = mi.thumbnail_asset_id
  and thb.source = 'seed' and thb.visibility = 'public' and thb.deleted_at is null
left join assets img on img.id = mi.image_asset_id
  and img.source = 'seed' and img.visibility = 'public' and img.deleted_at is null
where mi.is_active and mi.owner_user_id is null and mi.project_id is null

union all

select mi.id, mi.name, mi.clothing_type, mi.gender, mi.category,
       mi.color_name, mi.color_group, mi.style_tags, mi.fit, mi.length,
       mi.color_brightness, mi.sort_order, mi.is_active,
       true as is_custom,
       img.id::text as image_asset_id, img.r2_key as image_key,
       thb.id::text as thumbnail_asset_id, thb.r2_key as thumb_key,
       img.r2_bucket as image_bucket, thb.r2_bucket as thumb_bucket
from matching_items mi
join projects p on p.id = mi.project_id
  and p.user_id = %s and p.deleted_at is null
join assets thb on thb.id = mi.thumbnail_asset_id
  and thb.user_id = mi.owner_user_id and thb.project_id = mi.project_id
  and thb.source = 'upload' and thb.visibility = 'private' and thb.deleted_at is null
join assets img on img.id = mi.image_asset_id
  and img.user_id = mi.owner_user_id and img.project_id = mi.project_id
  and img.source = 'derived' and img.visibility = 'private' and img.deleted_at is null
where mi.is_active and mi.owner_user_id = %s and mi.project_id = %s;
-- params: (user_id, user_id, project_id)
```

- `get_matching_item_asset(conn,item_id,user_id,project_id)` /
  `get_matching_item_metadata(conn,item_id,user_id,project_id)`로 시그니처를 바꾸고
  `(owner_user_id is null or (owner_user_id=%s and project_id=%s))` 경계를 추가한다. 지금처럼 id+active만
  보면 다른 사용자가 커스텀 id를 알아낸 경우 해석할 수 있다(`server/app/repo.py:358-378`). 다섯
  소비처가 모두 user/project를 전달해야 한다.
- `insert_custom_matching_item`, `delete_custom_matching_item`, `get_custom_matching_item`을
  `(user_id, project_id)` 필수 시그니처로 추가한다.

### 5.3 파일 서빙 (D8 — 2026-08-05 오너 결정)

**RLS 분리만 하고, 저장·서빙은 기존 상품 사진과 동일하게 간다.**

실제 유출 경로는 목록 API였다 — RLS가 인증 사용자에게 모든 `is_active` 행을 열어 줘서
타인의 커스텀 행과 `image_asset_id`가 **그대로 응답에 실렸다**. §5의 정책 분리가 그 경로를 닫는다.
그 뒤 남는 위험은 "asset UUID를 이미 아는 사람이 `/v1/assets/{id}/file`을 연다"뿐인데,
이는 **셀러의 상품 사진이 지금 놓여 있는 것과 정확히 같은 수준**이다(업로드 자산은 DB상
`visibility='private'`지만 업로드 완료 응답부터 공개 R2 URL을 돌려준다 —
`server/app/repo.py:199`, `server/app/routes.py:903`). 매칭 의류 사진에만 더 높은 기준을 적용하면
기준이 엇갈리면서 워커 3곳·스파이크 스크립트·서빙 경로·프론트 썸네일까지 전부 바뀐다.

따라서 이번 범위는 이렇다.

- 커스텀도 `/match-candidates`에서 `r2.public_url`로 직렬화한다 — **큐레이션 arm과 동일**.
  프론트에 인증 Blob 썸네일 컴포넌트를 새로 만들지 않는다.
- 세 worker(`mannequin_job.py:1153-1171`, `detail_page_job.py:654-679`,
  `editor_image_job.py:194-208,355-374`)와 5번째 spike는 **무변경**이다. 호출부에 `source='seed'`
  전제가 없고 `get_asset_for_user`가 본인 asset을 이미 허용한다(`server/app/repo.py:333-341`).
  버킷 분기가 없으므로 client 선택 로직도 필요 없다.
- 소유권 방어선은 두 겹이다. ① §5 RLS 정책, ② `get_matching_item_asset`/`_metadata`에 추가하는
  `(owner_user_id is null or (owner_user_id=%s and project_id=%s))` 조건. 이 둘이 "타인의 커스텀 id를
  알아내는" 경로를 막는다.

**백로그** — `/v1/assets/{id}/file`의 무인증 capability URL을 인증 프록시로 바꾸는 일은
상품 사진까지 함께 다뤄야 하는 별도 과제다. 이 기능이 그 결정을 앞당기지 않는다.

### 5.4 D6 생성 소비와 프롬프트

현재 세 생성 경로는 모두 `matching_items.image_asset_id`에서 **매칭 후보당 이미지 한 장**을
해석한다. 마네킹은 main 1벌(`server/app/workers/mannequin_job.py:1150-1171`), 상세페이지와
에디터는 선택 `matchIds`별 한 장씩이다(`server/app/workers/detail_page_job.py:654-663,815-828`,
`server/app/workers/editor_image_job.py:194-208,366-390`). D6 뒤에도 첨부 장수 계약은 바꾸지 않고,
커스텀 후보의 그 한 장이 §5.2 grid가 된다. 큐레이션 후보는 기존 단일 사진 그대로다.

다만 프롬프트는 grid임을 알아야 한다.

- 마네킹 manifest는 현재 마지막 이미지를 상품 종류에 따라 단일
  `matching TOP/BOTTOM garment`라고만 설명한다(`server/app/workers/mannequin_job.py:80-98`). custom일
  때는 `a 2x2 contact sheet showing 1-4 views of ONE SAME matching ... garment; treat all occupied
  cells as evidence for that single garment; dress one garment only; never reproduce the grid`로 바꾼다.
- 상세페이지와 에디터는 공용 `_MATCH_LABEL`을 각 matching image에 붙인다
  (`server/app/agents/cut_generator.py:920,982-990`). `build_manifest`가 custom 여부 배열을 받아
  custom slot에만 같은 contact-sheet 가드를 붙인다. 큐레이션 label은 그대로다.
- 빈 셀은 사진 부재일 뿐 흰 의류/두 번째 상품이 아니며, 출력은 언제나 사람 한 명의 정상 사진 한
  장이지 collage/grid가 아니라는 음성 지시를 두 prompt 경로 모두에 넣는다.
- `spike_volume_secondpass.py`도 main asset 한 장을 읽으므로 grid를 그대로 받는다
  (`server/scripts/spike_volume_secondpass.py:201-207`). 버킷이 하나뿐이라(D8) client 분기는 없고,
  이 스크립트는 커스텀 row를 만날 수 있다는 점만 회귀 확인한다.

> 승인(2026-08-05 Claude 검토): 생성에는 grid 한 장을 유지하되, custom slot에만 "같은 한 벌의 여러 시점"과
> "한 벌만 입히고 grid를 출력하지 말라"는 manifest 가드를 추가한다.

## 6. 메타데이터 (D10 — AI 추론 없음)

**2026-08-05 폐기** — 원래 이 절은 `custom_match_analyst`(Gemini 3.6 flash)로 종류·카테고리·
기장·이름을 추론하는 설계였다. 업로드 대기시간이 길다는 오너 판단으로 에이전트·프롬프트·
전용 테스트를 **삭제**했다(`server/app/agents/custom_match_analyst.py`,
`server/prompts/custom_match_analyst_v1.txt`, `server/tests/test_custom_match_analyst.py`).

대신 `routes._custom_match_metadata(expected_type)`가 결정적으로 채운다. 아는 것만 쓰고
모르는 것은 중립값이다 — 추측으로 큐레이션 어휘를 채우지 않는다.

| 컬럼 | 값 | 근거 |
|---|---|---|
| `clothing_type` | `expected_type` (상품의 보완 타입) | 슬롯이 정본 — 하의 자리에 올렸으면 하의 |
| `name` | `내 상의` / `내 하의` | 파일명은 `IMG_4821` 류라 타일 라벨로 부적합 |
| `category` | `'커스텀'` | 닫힌 어휘 밖 값 — `fit_category`가 축을 열지 못하게 |
| `length` | `'regular'` | NOT NULL 채움용 중립값 |
| `color_name` / `color_group` | `'커스텀'` / `'gray'` | 정렬·랭킹 미사용(커스텀은 맨 앞 고정) |

### 핏 축 (D11 — 열지 않는다)

`matching.fit_category()`가 **`owner_user_id`/`is_custom`이면 즉시 `None`**을 돌려준다.
따라서 커스텀은 상의·하의 모두 조정 스텝이 뜨지 않는다. 큐레이션 규칙(pants/skirt/top)은 그대로다.

미러 지점 3곳이 같은 규칙을 지킨다.
- 서버 `server/app/services/matching.py` — 단일 정본
- 서버 `repo.get_matching_item_metadata`가 `owner_user_id`를 함께 select (마네킹 재생성 검증 경로)
- 프론트 `src/lib/matchingFit.js` `matchingFitDefinition`, mock `src/mock/matchingRecommendation.js`

남성 커스텀 스커트용으로 두었던 여성 silhouette 중립 어휘 폴백은 커스텀 경로에서는 더 이상
도달하지 않는다. 큐레이션 스커트가 성별 칩 전환 뒤 선택으로 이월된 경우를 위해 **남겨 둔다**
(`src/lib/matchingFit.js`, 프론트 테스트가 두 경우를 각각 고정).

## 7. 서버 API

**단일 POST로 간다(D9 — 2026-08-05 오너 결정).** 2단계(preview → 서명 `draftToken` → confirm)안은
채택하지 않는다. 막으려던 상태가 "모달을 닫았는데 서버가 늦게 끝나 row가 생김"인데, 그건 유령이
아니라 **그냥 추가된 것**이다 — 그리드에 내 옷 타일이 보이고 `×`로 지울 수 있다. 이 정상 상태
하나를 없애려고 서명 토큰·취소 엔드포인트·24시간 정리 잡 세 가지 인프라를 새로 들이지 않는다.
모달을 닫을 때는 요청을 `abort()`하고 후보를 **재조회**해 서버가 이겼든 졌든 화면을 실제 상태에
맞춘다(§8.2).

### 7.1 `POST .../analysis/custom-match-item`

Body: `{ assetIds: [uuid, ...] }` (중복 없는 1~4개). `clothingType`은 받지 않는다.

1. 프로젝트 소유 확인 후 현재 row 존재 여부를 본다. 있으면 409로 합성 전에 중단한다.
2. 각 asset을 **한 쿼리에서 네 조건 모두**로 검증한다:
   `assets.user_id = user_id and assets.project_id = project_id and source='upload' and deleted_at is null`.
   현재 공용 `get_asset_for_user`는 `(본인 소유 or seed)`만 보므로 타 프로젝트 본인 asset과 seed도
   통과시킨다(`server/app/repo.py:333-341`). 이 endpoint는 그 helper를 그대로 쓰지 않고
   `get_uploaded_assets_for_project`를 둔다. 하나라도 불일치면 id 존재를 숨겨 404다.
3. 모든 원본에 mandatory input QC를 실행한다. 전역 `INPUT_QC`는 현재 off/shadow/enforce이고 complete
   경로는 fetch 실패를 fail-open한다(`server/app/routes.py:866-887`). 이 기능은 설정과 무관하게
   `evaluate_input_qc`를 **fail-closed로 강제**한다. 한 장이라도 reject면 400, R2 read 실패면
   503이며 어느 경우도 row/grid가 없다.
4. 서버 DB의 `products.clothing_type`을 읽어 `matching.complementary_type`을 계산한다. 상품 정본은
   `repo.get_product`이고(`server/app/repo.py:230-236`), 상품 GET도 이를 반환한다
   (`server/app/routes.py:508-519`). request body의 종류는 신뢰하지 않는다.
5. `_custom_match_metadata(expected_type)`로 메타데이터를 채운다(§6, D10). AI 호출 없음.
   보완 타입이 `None`(원피스 상품)이면 여기서 400 `wrong_garment_type`.
6. §5.2 규칙으로 grid를 합성해 `assets(source='derived')`로 저장한다.
7. `projects` row를 `FOR UPDATE`로 잠근다. 같은 lock을 DELETE도 먼저 획득해 add↔delete를 직렬화한다.
8. `matching_items`를 insert하고, **같은 트랜잭션에서** 저장된 `analyses.payload.matchClothing`을
   갱신한다. 새 item은 맨 앞 `selected:false`이고 기존 메인/서브의 `selOrder`는 그대로다.
9. commit한 **DB payload 전체**와 새 item을 반환한다. 응답 shape는 `/match-candidates` item에
   `isCustom:true`, `isCompatible:true`를 더한 형태다.

동시 POST 두 개가 1번의 사전 존재 확인을 함께 통과해도 partial unique index 위반을 잡아
`409 custom_match_item_exists`로 매핑한다. DB 예외를 500으로 흘리지 않는다. DELETE와 경합하면
project lock을 먼저 얻은 요청부터 완결되고, 뒤 요청이 그 결과를 다시 확인한다 — delete가 먼저면
add가 새 row를 만들고, add가 먼저면 뒤 delete가 그 row를 지운다.

**클라이언트가 중간에 끊은 경우** — insert까지 갔으면 row는 남는다. 이는 정상 상태이고,
프론트가 모달을 닫으면서 후보를 재조회하므로 다음 화면에 내 옷 타일로 나타난다(§8.2).
insert 전에 끊겼으면 참조 없는 grid asset만 남는데, 다음 add 시도가 §5.2의 checksum 중복
방지로 재사용하거나 그대로 방치된다(고아 asset 정리는 백로그).

타입 불일치 메시지:

- 하의를 기대: "상품이 상의라서 매칭 의류는 **하의**를 올려야 해요. 바지나 치마 사진으로 다시 올려주세요."
- 상의를 기대: "상품이 하의라서 매칭 의류는 **상의**를 올려야 해요. 셔츠나 니트 사진으로 다시 올려주세요."
- 추론 결과가 `outer|dress|other`: "매칭 의류로 쓸 수 있는 건 상의 또는 하의예요."

크레딧은 차감하지 않는다.

### 7.2 `DELETE /v1/projects/{project_id}/analysis/custom-match-item`

project row lock → 본인 custom row 조회 → `matching_items` 삭제와 `analyses.payload` 정리를 한
트랜잭션으로 처리한다. `matchClothing`에서 id를 제거하고, 남은 selected item을 기존
`selOrder` 오름차순으로 정렬한 뒤 최대 2개에 **1,2를 연속 재부여**한다. 예를 들어 main(1)을
지우고 sub(2)가 남으면 sub는 main(1)이 된다. 삭제 item에 묶인 `fitProfile.matchingFit`도 제거한다.
commit 후 204이며 row가 없어도 멱등 204다.

원본/grid asset은 참조 검사를 거쳐 soft-delete하고 R2 cleanup 대상으로 enqueue한다.
DB commit 뒤 R2 정리가 실패해도 row는 되살리지 않으며 cleanup 재시도로 수렴한다.

### 7.3 에러 계약

| HTTP | code | 조건 / 경계 |
|---|---|---|
| 400 | `input_quality` | decode 실패, 최소변 400px 미달. 필수 QC라 fail-closed |
| 400 | `wrong_garment_type` | 원피스 상품 — 맞춰 입힐 반대편이 없음 (`complementary_type` 이 `None`) |
| 401 | `unauthorized` | 로그인 없음/토큰 오류 |
| 404 | `not_found` | 프로젝트 없음/타인 소유 또는 네 asset 조건 중 하나라도 불일치(존재 은닉) |
| 409 | `custom_match_item_exists` | 사전 존재 확인 또는 partial unique violation |
| 422 | FastAPI validation | `assetIds` 0개/5개 이상/중복/UUID 아님 |
| 503 | `custom_match_storage_unavailable` | R2 read 실패 또는 합성본 저장 실패 |

D10으로 AI 호출이 사라져 `analysis_unavailable`(503)과 `different_garments`(400)는 **폐기**됐다.
남은 503은 저장소 계열 하나뿐이다(R2 read 실패 · 합성본 저장 실패). 502는 이 endpoint에서 쓰지 않는다.

### `GET .../analysis/match-candidates` 변경

- `list_active_matching_items(conn, user_id, project_id)`로 커스텀 포함 조회.
- 커스텀 행은 `prefilter`/`recommend()` 랭킹에 **넣지 않고** 결과 **맨 앞에 고정**(prepend).
  - **성별 필터를 적용하지 않는다**(D5).
  - `clothing_type`이 현재 보완 타입과 다르더라도 숨기지 않고 `isCompatible:false`로 반환한다.
    generation용 선택 후보에서는 제외한다. 맞으면 `isCompatible:true`다.
- 응답 아이템에 `isCustom:boolean`(기본 false), `isCompatible:boolean`(기본 true)을 추가한다.
  계약 문서 §6에도 같은 필드를 추가한다.
- 커스텀 image/thumbnail도 큐레이션과 동일하게 `r2.public_url`로 직렬화한다(D8 · §5.3).

### 7.4 analysis payload·캐시 정합성

현재 분석 저장은 JSONB payload 전체 교체이고(`server/app/routes.py:570-593`), 프론트는 별도
module cache를 사용한다(`src/lib/api/httpAdapter.js:181-195,389-425`). `getMatchClothing`은 저장
payload에 목록이 있으면 DB를 그대로 반환한다(`src/lib/api/httpAdapter.js:438-452`). 따라서
add/delete 후 캐시만 고치면 하드 reload에서 옛 DB payload가 돌아온다.

정본 흐름은 다음 하나다.

1. confirm/delete endpoint가 `matching_items`와 `analyses.payload`를 같은 DB 트랜잭션에서 갱신한다.
2. 프론트는 성공 응답의 **전체 analysis payload**로 화면 state와 `analysisCache`를 함께 replace한다.
   낙관적으로 cache만 먼저 patch하지 않는다.
3. 실패하면 화면/cache를 바꾸지 않고 서버 payload를 다시 읽는다.
4. gender/type/style 변경은 candidate를 재조회하고 선택을 reconcile한 뒤 기존 `saveAnalysis` full-payload
   경로로 저장한다. `toMatchItem`이 `isCustom`/`isCompatible`를 보존해야 재조회 후 배지와 삭제 버튼이
   사라지지 않는다(현재 mapper는 이 필드를 복사하지 않음: `src/lib/api/httpAdapter.js:216-226`).

## 8. 프론트엔드

### 8.1 그리드 타일 (`src/features/analysis/AnalysisForm.jsx` §6 매칭 의류)

- 커스텀 없음 → 큐레이션 15 + **"의류 추가하기"** 타일 = 16칸.
- 커스텀 있음 → **내 옷(맨 앞)** + 큐레이션 15 = 16칸. 추가 타일은 사라진다.
- 상품 종류 top↔bottom 변경으로 기존 내 옷이 현재 보완 타입과 안 맞아도 **숨기지 않는다**.
  맨 앞 16번째 자리를 `내 옷 · 현재 상품과 종류가 맞지 않아요` 비활성 타일로 렌더하고 선택 클릭은
  막되 `×` 삭제는 허용한다. 추가 타일로 바꾸지 않으므로 존재하는 row에 POST해 409가 나는 길도
  없다. 종류를 되돌리면 같은 타일이 다시 활성화되며, 안전상 이전 선택은 자동 복구하지 않는다.
- 추가 타일: 회색 배경 + 실루엣 아이콘 + "의류 추가하기".
  아이콘은 **보완 타입에 따라** 상의/하의 모양으로 바뀐다(첨부 목업은 하의).
- 내 옷 타일: 좌상단 `내 옷` 배지, 우상단 `×`(삭제). 썸네일은 원본 1번이고 큐레이션과 똑같이
  `<img src>` 한 줄로 표시한다(D8 · §5.3 — 별도 인증 썸네일 컴포넌트 없음).
  호환 상태의 나머지 선택 동작은 큐레이션과 동일.

> 승인(2026-08-05 Claude 검토): 종류 불일치 때도 16번째 칸은 삭제 가능한 비활성 `내 옷` 타일로 유지한다.

### 8.2 업로드 모달

추가 타일 클릭 → 화면 전체에 **살짝 어두운 오버레이**(첨부 목업 2) + 중앙 카드.
`ModelDetailModal`(같은 파일)의 마크업/닫기 규칙을 따른다.

상태 기계: `idle → picking → uploading → analyzing → (done | error)`

- `idle` — 점선 드롭존, "같은 옷 사진을 최대 4장 올려주세요 · 1장만으로도 추가할 수 있어요 ·
  JPG/PNG · 최소 400px", 파일 선택 버튼. 드래그&드롭과 클릭 선택 둘 다 지원.
- `picking` — 1~4개 미리보기를 순서 번호와 함께 표시한다. 추가 선택은 빈 칸만 채우고 4장이면
  비활성화한다. 드래그로 순서를 바꿀 수 있고 키보드용 `앞으로/뒤로` 버튼도 제공한다. 각 사진에
  `삭제`와 `교체`가 있으며, 교체는 같은 위치를 유지한다. 1장만 남아도 완료 가능하고 0장이 되면
  `idle`로 돌아간다. 순서 1번은 UI 타일 썸네일이 된다.
- `uploading` / `analyzing` — 1~4개 미리보기 + "옷을 확인하는 중이에요…" 스피너.
  (두 단계를 문구로 나누지 않는다 — 셀러에게는 한 동작이다.)
- `done` — 서버가 돌려준 이름·사진 순서를 보여주고 성공 payload로 그리드를 반영한 뒤 닫는다.
  ("이 옷으로 추가하기" 확정 버튼은 없다 — 사진 선택이 곧 추가다. D9로 2단계가 사라졌으므로
  버튼을 남기면 이미 만들어진 row를 다시 확정하는 모양이 되어 거짓말이 된다.)
- `error` — 서버 메시지 그대로 노출 + "다른 사진 고르기". 특히 타입 불일치 메시지는
  §7.1 문구를 그대로 쓴다.
- ESC·바깥 클릭으로 닫기. 모달마다 `AbortController` 하나를 만들고 upload PUT/complete/add 전부에
  같은 `signal`을 넘긴다. 닫기·unmount·사진 재선택은 먼저 `abort()`한다.
- **닫을 때는 반드시 후보를 재조회한다(D9).** abort는 브라우저 쪽만 끊을 뿐 서버 트랜잭션은
  이미 커밋됐을 수 있다. 재조회하면 서버가 이겼으면 내 옷 타일이 보이고, 졌으면 추가 타일이
  그대로다 — 어느 쪽이든 화면이 실제 상태와 맞는다. 낙관적 반영은 하지 않는다.

> 승인(2026-08-05 Claude 검토): 업로드 순서는 드래그와 키보드 버튼으로 바꾸고, 사진별 삭제/제자리 교체를
> 제공하며, 1번 사진을 타일 썸네일로 쓴다.

### 8.3 API 어댑터

- `httpAdapter`: 기존 `uploadPhoto(projectId, {filename, mime, blob})` 재사용 →
  `purpose`와 `signal` 인자를 추가하고, `addCustomMatchItem(projectId, {assetIds}, {signal})`,
  `removeCustomMatchItem(projectId)`를 추가한다.
  add/delete 성공 시 응답의 full analysis로 cache를 replace한다(§7.4).
- `toMatchItem`은 `isCustom`과 `isCompatible`을 복사한다. 현재 필드 목록에는 둘이 없어
  재조회 시 커스텀 판정이 사라진다(`src/lib/api/httpAdapter.js:216-226`).
- `src/mock/api.js`: 같은 시그니처. 업로드는 `URL.createObjectURL`, 추론은 하지 않고
  **파일명 기반 목 판정**(`bottom`/`top` 키워드 없으면 보완 타입으로 가정) →
  mock에서는 항상 성공. mock custom row도 `isCustom:true`를 끝까지 보존한다. 현재 mock mapper도
  이 필드를 복사하지 않는다(`src/mock/matchingRecommendation.js:31-46`).
- mock의 `fitCategoryFromMatchingMetadata`는 현재 bottom만 처리해 매칭 상의가 `top` 축을 잃는다
  (`src/mock/matchingRecommendation.js:22-28`). `clothingType==='top'`이면 `'top'`을 먼저 반환하고,
  bottom만 pants/skirt 규칙을 적용해 서버 `matching.fit_category`와 맞춘다
  (`server/app/services/matching.py:28-48`).
- mock 계약 테스트(`tests/frontend/`)가 두 어댑터의 add/delete shape, `isCustom`,
  `isCompatible`, top fitCategory를 비교한다.

## 9. 엣지 케이스

| 상황 | 동작 |
|---|---|
| 성별 칩 재클릭 / 도중 변경 | 재클릭은 현재 값을 유지해 미선택 상태가 생기지 않는다(D7). 내 옷은 새 성별에서도 맨 앞(D5) |
| 삭제된 4벌을 고른 과거 프로젝트 | 마네킹은 무매칭 진행, 상세페이지는 해당 컷 빈 슬롯, 에디터는 `matching_asset_unavailable` 실패(§4.2) |
| 상품 종류를 top→bottom으로 바꿈 | 내 옷은 비활성 불일치 타일로 맨 앞에 남고 선택에서 제거된다. 되돌리면 활성화되지만 자동 재선택하지 않음 |
| 내 옷이 메인인 상태로 삭제 | row와 선택을 원자 삭제하고 남은 sub의 `selOrder`를 1로 재부여. `fitProfile.matchingFit` 제거 |
| 원피스 상품 | 매칭 자체가 없으므로 그리드·추가 버튼 모두 미노출(현행 `complementary_type=None`) |
| 1장만 업로드 | 유효하고 완전한 업로드. 나머지 grid 셀은 중립 배경 |
| 2~4장에 다른 옷 혼합 | 검사하지 않는다(D10) — 그리드에 그대로 합성된다 |
| 같은 프로젝트 재업로드/동시 POST | 409 → 프론트가 "지우고 다시 올려주세요" 안내. unique violation도 같은 code |
| 업로드 도중 모달 닫기 | 요청 abort 후 **후보 재조회**. 서버가 이미 커밋했으면 내 옷 타일로 보이고, 아니면 추가 타일 유지 (D9) |
| 추론 실패(모델 오류) | 503, row 생성 안 함. 추측값으로 넣지 않는다 |
| 남성 + 커스텀 스커트 | 타일 노출, matching skirt에만 성별 중립 silhouette 어휘로 핏 조정 표시 |

## 10. 테스트

**서버(pytest)**
- migration/RLS — nullable CHECK 네 조합 중 `(null,null)`/`(owner,project)`만 통과, 프로젝트당
  partial unique, 큐레이션은 인증 사용자에게 보이고 custom은 owner에게만 보이는지. `assets` RLS와
  별개로 `matching_items` row 자체가 새지 않는지 확인.
- `test_custom_match_item.py` — 1/2/3/4장 POST 성공, 0/5/중복 422, 네 asset 조건 각각 404,
  mandatory 400 QC, 400 다른 옷/타입 불일치, 503 Vision/storage, 사전 존재 409,
  동시 POST unique-violation 409, POST↔DELETE lock 순서, 멱등 DELETE.
- 합성 단위 — 1600×1600/JPEG, 순서별 셀, 1~3장 빈칸, 4장 전체, contain으로 전신 crop 없음,
  원본 checksum/순서 결정성. `face_grid`가 아니라 garment compositor가 호출되는지.
- `test_matching.py` 보강 — UNION ALL 두 arm, 커스텀 맨 앞/랭킹 제외, 타 사용자·타 프로젝트 비노출,
  타입 불일치는 `isCompatible:false`, women/men 모두 노출(D5).
  기존 `test_matching.py:103`의 `fake_list(conn)` fixture는 새 repo 시그니처
  `fake_list(conn,user_id,project_id)`와 `is_custom`/asset-id 필드를 받게 고친다.
- 소유권 게이트 — `get_matching_item_asset`/`_metadata`가 타 사용자·타 프로젝트의 커스텀 id를
  `None`으로 돌려주는지(D8의 두 번째 방어선). `/v1/assets/{id}/file` 자체는 이번에 바꾸지 않는다.
- 삭제된 id 회귀 — 마네킹은 무매칭 계속, 상세페이지는 해당 착용 컷 빈 슬롯, 에디터는
  `matching_asset_unavailable` 실패를 각각 고정한다(§4.2).
- `purge_matching_items.py` — `--dry-run`이 아무것도 지우지 않고 대상만 출력하는지,
  `--apply`가 DB commit→R2→CDN 순서로 호출하는지, DB/R2 각각의 중간 실패 후 고정 manifest 재실행이
  수렴하는지(R2/CDN은 fake client 호출 기록 검증).
- `_custom_match_metadata` — 상의/하의별 결정적 값(D10). 라우트 성공 테스트가 insert 로 넘어간
  metadata 를 그대로 고정한다.
- `fit_category`가 커스텀(`owner_user_id`/`is_custom`)에는 항상 `None`, 큐레이션에는 기존대로
  `pants/skirt/top`을 내는지(D11 회귀).
- 커스텀은 성별·종류와 무관하게 조정 스텝이 안 뜨고(D11), 큐레이션 스커트의 남성 중립 어휘
  폴백은 그대로 남는지.
- production 세 worker와 `spike_volume_secondpass.py`가 커스텀 row의 grid asset을 정상 해석하는지
  (버킷 분기는 없음 — D8).
- 전체 `cd server && .venv/bin/pytest -q` 통과 유지.

**프론트**
- `pnpm build` 통과(프로젝트의 기존 script 사용).
- mock 계약 테스트: add/remove shape, `isCustom`/`isCompatible` 왕복, mock top fitCategory.
- add/delete 뒤 하드 reload가 DB payload와 같은 후보/선택을 복원하고, gender/type 재조회 뒤에도
  custom 배지·삭제 버튼이 유지되는지.
- 단위/수동 — 성별 칩 재클릭이 값 유지, 다른 `Chips` 호출자는 기존 해제 가능; delete 후 selOrder
  1..N; type 변경 때 16칸과 비활성 custom 타일; 다시 되돌릴 때 자동 재선택 없음.
- Abort 스모크 — uploading/analyzing에서 ESC·바깥 클릭·unmount 시 signal abort, late response가
  state/cache를 직접 바꾸지 않고, 닫기 직후 재조회 결과가 서버 실제 상태와 일치(D9).
- 커스텀 썸네일은 큐레이션과 동일하게 `<img src>` 직접 사용 — 인증 Blob URL 생성/revoke 코드가
  들어오지 않았는지 확인(D8 · §5.3).
- 헤드리스 크롬 스모크: 16칸 → 추가 모달 → 1장 완료, 4장 순서/교체/삭제, custom 활성/비활성
  타일을 각각 스크린샷.

## 11. 백로그 (이번에 안 함)

- 이름 직접 수정, 여러 **벌** 업로드, 계정 단위 옷장.
- 업로드 이미지 배경 제거·리터칭(이번에는 contain grid 합성만 함).
- 별도 썸네일 리사이즈(이번에는 원본 1번을 gated 썸네일로 겸용).
