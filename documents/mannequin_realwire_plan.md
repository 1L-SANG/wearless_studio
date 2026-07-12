# 마네킹컷 실작동(real-wire) 기획 v2

> 목표: **로컬에서 진짜 백엔드로** 마네킹 페이지 전 과정이 실제 동작 —
> 자동 생성(2크레딧) → 순차 핏 확인 → 조정 → **재생성(2크레딧, 새 버전)** → 버전 선택 → 확정 이동.
> 역할: 기획=Claude · 기획 피드백=Codex · **설계 주도=Codex** · 설계 피드백=Claude · 구현=Claude · 최종 브라우저 QA=사용자.

## 0. 현재 상태 (검증된 사실)

- 배선은 코드상 완결: 프론트 httpAdapter 4메서드 · `:regenerate`(캐시 게이트 없음, 매 호출 새 버전 append) · 크레딧 2 정합 · matchCut 소비 + 레거시 폴백. pytest 238 pass, 프롬프트 dry-run 스모크 PASS, 6월 라이브 E2E(생성→R2→크레딧 차감) PASS.
- **그러나 프론트가 mock 모드로 꺼져 있음** (`.env.local: VITE_API_MODE=mock`, BASE_URL은 prod를 가리킴). 즉 "페이지가 실제로 작동"의 남은 일 = 실행 환경·플로우 연결이지 기능 코드가 아니다.
- 서버 CORS는 `http://localhost:5173` 허용 ✓. 서버 .env(GEMINI/R2/DB/base mannequin asset) 완비 ✓ (dry-run이 증명).

## 1. 갭 (이번 작업 대상)

| # | 갭 | 왜 문제 |
|---|---|---|
| G1 | 모드 전환이 `.env.local` 수동 편집뿐 | mock↔http 왕복이 실수 유발. 재현 가능한 스위치 필요(`.env.http`는 `vite --mode http`로 실행해야 적용됨) |
| G2 | 로컬 백엔드 기동 절차 비문서화 | uvicorn 커맨드·사전조건 구전 상태. dispatcher는 DB+R2+(Gemini **또는** OpenAI)로 기동하지만 **마네킹은 Gemini 필수** — 기동 OK ≠ 마네킹 준비 OK, Gemini 구성을 명시 확인해야 |
| G3 | 실플로우 스모크 부재 | input(브라우저→R2 presigned PUT CORS!)→분석→마네킹 생성→조정→재생성→크레딧이 로컬에서 한 번도 연결 실행 안 됨 |
| G4 | **무세션 = 401 폭탄** *(v2 정정)* | ~~조용한 mock 폴백~~ 은 없음(어댑터 빌드타임 고정, 미구현 함수는 즉시 throw). 실제로는 무세션이면 Authorization 없이 백엔드행 → 전 호출 401. 조치 = `http()`에서 토큰 없으면 요청 전 **명시적 hard-fail**("로그인이 필요해요") |
| G5 | dev 계정 크레딧 잔량 미확인 | 표시 잔액이 아니라 **active `credit_sources` 합계 + `reserved=0` 불변식**으로 확인. 스모크에 최소 4크레딧 |
| G6 | (확정) 매칭 하의 시드 — **이번 비범위** | 사용자 결정: 상의 단독 먼저. 미시드면 매칭 스텝 자동 미노출(게이트 정상) |
| **G7** | **컷 이미지 `<img>` 로드 불가 — P0 blocker** *(v2 신규, 코드 확정)* | `_cut_to_api`의 `src="/v1/assets/{id}/file"`이 ① **상대경로**(브라우저가 Vite 5173으로 요청, 프록시 없음→404) ② 라우트가 **Bearer 필수**(`require_user`)라 `<img>`로는 인증 불가→401. 이중 블로커. 반면 업로드 complete 응답은 이미 `r2.public_url`(공개, 만료 없음) 사용 — 마네킹 컷만 불일치. **유료 생성 전 반드시 해소**(크레딧 쓰고 깨진 이미지 보는 최악 방지) |
| **G8** | **OAuth localhost 미등록 → 세션 불생성** *(v2 신규, 과거 실측 2026-06-16)* | 로그인은 OAuth 전용(구글/카카오 전체 리다이렉트). Supabase Auth Redirect URLs에 `http://localhost:5173` 없으면 복귀해도 세션 없음(TODO.md 기록). **외부 대시보드 설정 = 사용자 액션**. 스크립트 스모크용 토큰은 service-role 경유 확보(설계에서 확정) |

## 2. 단계 (v2 — Codex A1~A6 반영, 유료 호출 전 선행 게이트 강화)

- **P0 준비·선행 게이트 (무과금)**:
  1. 백엔드 로컬 기동 + **Gemini 구성 명시 확인**(dispatcher on만으론 불충분, G2).
  2. 세션 확보: 스크립트 스모크용 dev 토큰(service-role 경유, 설계 확정) → 로컬 `GET /v1/me/account` **200** (A1).
  3. **G7 자산 서빙 해소 + `<img>` 실로드 선행 게이트**: 업로드 1건 + 기존 보호 자산 1건이 실제 이미지 경로로 픽셀 로드되는지 확인 **후에만** 유료 생성 진입 (A2). R2 presigned PUT/complete 포함 (A3).
  4. 크레딧: active `credit_sources` 합계 ≥4 + `reserved=0` 확인 (A3).
  5. 스위치: `.env.http` + `pnpm dev:http`(BASE_URL=`http://localhost:8000`) + `http()` 무세션 hard-fail (G1·G4).
- **P1 코어 스모크 (상의 단독, 유료 2콜)**: 새 프로젝트 ID 확인 · 기존 cuts **0** · 정면사진 업로드 · analysis 저장 확인 (A4) → 마네킹 생성 job → 조정 1축 → **재생성 job** → 확정.
  **결정적 증거 (A5)**: job ID 2건 terminal `done` · 원장 차감 **-2/-2** · `reserved=0` 복귀 · versions **[1,2]** · v1 직접 선택→PATCH·DB 반영→**새로고침 후 유지·추가 job/차감 없음** · 컷 이미지 **픽셀 로드**. ("서버 로그"는 합격 기준에서 제외.)
  **타임아웃 규율 (A6)**: 클라 타임아웃(180/300s)은 서버 실패가 아님 — 재클릭 금지, 동일 job/cuts/ledger 재조회로 판정.
- **P2 발견 버그 수정** + 회귀 테스트.
- **P3 사용자 브라우저 QA** (복귀 후): OAuth 로그인(G8 — Supabase Redirect URLs에 localhost 등록 필요, **사용자 액션**) → P1 시나리오 클릭. 통과 = "실작동" 완료.

## 3. 비범위 (이번에 안 함)

- prod(api.wearless.kr) 스위치/배포 — 로컬 검증 후 별도.
- 콘티 재저장 무료생성 구멍(생성 측 스냅샷) — 상세페이지 트랙, 별도 진행.
- 매칭 하의 시드 — 메타 수령 전(§1 G6, 사용자 결정 대기).

## 4. 리스크

- Gemini 실호출 비용: 스모크에 이미지 2~3콜(소액, 기존 스모크와 동일 수준).
- R2 브라우저 업로드 CORS: localhost 허용 여부 실측 필요(P1 첫 게이트).
- dispatcher 미기동 시 잡이 pending에 머묾 → 프론트 300s 후 실패 토스트(원인 식별 로그 필요).
- 세션 만료 시 mock 폴백(G4) — QA 신뢰성 핵심.

## 5. 로그

- 2026-07-11 **구현 (Claude, 사용자 부재 중 자율 진행 — 사용자 결정 항목은 말미로 유예)**:
  - **G7 픽스 = (B)안 채택**: `/assets/{id}/file`을 **capability URL(무인증 302)**로 전환(`routes.py` + `repo.get_asset_public`) + 프론트 어댑터에서 상대 src **절대경로화**(`httpAdapter.absolutize`, 마네킹 3메서드 적용). (A)안(응답에 R2 공개 URL 직접)이 아닌 이유: `/file` 패턴이 에디터 블록·wardrobe·vary 역파싱(`editor_image_job._ASSET_FILE_RE`)까지 시스템 전반 계약이라, 라우트 공개화가 **전 소비처를 한 번에** 고침 + R2 객체가 public base로 이미 공개라 보안 후퇴 없음(UUID=능력토큰). 테스트 3건 추가(`test_asset_file.py`), **서버 245 passed**.
  - **스위치**: `.env.http`(gitignore됨 — 내용: `VITE_API_MODE=http` + `VITE_API_BASE_URL=http://localhost:8000`) + `pnpm dev:http`(= `vite --mode http`; Vite 우선순위로 .env.local의 mock을 덮음). `http()`에 무세션 사전 hard-fail("로그인이 필요해요…") — 401 폭탄 방지(G4). 빌드 ✓.
  - **G2 함정 실증·해결**: `Settings`는 dataclass(os.environ만) — `.env` 자동 로드 없음 → 맨 uvicorn은 껍데기 부팅(dispatcher off). **정답 기동 = `.venv/bin/uvicorn app.main:app --port 8000 --env-file .env`**.
  - **스모크 인프라**: `scripts/smoke_realwire.py`(P0 무과금 게이트 → `--paid`로 P1; A1~A6 게이트 구현, 스모크 계정 비밀번호는 매 실행 랜덤 재설정·무저장) + `scripts/grant_smoke_credits.py`(repo.grant_subscription 재사용, 스모크 계정 한정 — qa-smoke@wearless.kr, basic 200 지급).
  - **P0 결과: 12/12 PASS** — password grant 세션·account 200·R2 PUT/complete·공개 URL 픽셀 로드·**`/file` 무인증 302 픽셀 로드(G7 실증)**·정면 게이트·분석 job done(dispatcher 생존)·분석 저장.
  - 후속(별도 트랙): 에디터 블록·wardrobe의 `/file` src도 같은 절대경로화 필요(어댑터 적용 지점만 추가하면 됨 — 라우트는 이미 공개).
  - **✅ P1 유료 스모크 전 게이트 PASS (2026-07-11, 3회차 합산)**: S5 생성(done·cuts A/B·**-2** 200→198·reserved=0·**생성 컷 `<img>` 픽셀 로드 396KB**) + S6 재생성(done·**-2** 198→196·reserved=0·**versions [1,1]→[1,1,2,2] append**·**fitProfile 영속 axes.fit=over**·재생성 컷 픽셀 로드 401KB) + S7 선택 영속(A-1, 재조회 유지). 실패 경로도 실증 — prod가 가로챈 job 2건은 error 종결 + **예약 2크레딧 정확히 release**(차감 0). 스모크 총비용: 크레딧 4(196 잔여) + Gemini 이미지 콜(로컬 성공 2잡 = 4장, prod 실패 2잡 = 8장 낭비 — G9 참조).
  - 참고: 현 워커는 **A/B 두 후보**(baseFit regular/slim, 후보별 version) — "단일 후보" 주석은 오독이었음. 재생성 게이트는 max(version)+1 append 로 판정.
  - QC 캘리브레이션 백로그: Pillow QC가 흰 배경 마네킹(연한 다리)을 `missing_lower_body`로 오탐(bboxBottom 0.93인데도) — shadow 유지가 옳고, enforce 전 재캘리브 필수.
  - **⚠️ G9 발견(중대): 공유 DB job 경합 — prod dispatcher가 로컬 job을 가로챔.** P1 1차 실행에서 생성 job이 실패했는데, job_events에 **A/B 두 후보 + QC 게이트**(missing_lower_body×4) 흔적 — 현 코드는 단일 후보·QC shadow이므로 **구버전 워커 = AWS prod(api.wearless.kr)가 같은 Supabase를 폴링하며 클레임**한 것(로컬 좀비 프로세스 없음 확인). 파장: ① 로컬 스모크가 로컬 코드를 검증 못 함 ② prod 구코드는 QC가 켜져 있어 top-only 컷을 전부 탈락시킴(즉 **prod 마네킹 생성은 현재 망가진 상태**일 개연성) ③ 실패 시 예약 release는 정상 동작 실증(reserved=0 복귀, 차감 0). **스모크 대응 = 202 직후 id 지정 셀프-클레임 + 로컬 워커 인프로세스 실행**(`smoke_realwire._run_mannequin_job_inline`). **근본 해결 = 최신 main을 prod에 배포(사용자 결정)** — 배포 후엔 어느 쪽이 클레임해도 동일 코드라 무해. 사용자 브라우저 QA도 배포 전엔 같은 경합 위험.

- 2026-07-11 v1 작성(Claude). Codex 기획 피드백 → 반영 → Codex 설계 → Claude 피드백 → 구현 순.
- 2026-07-11 **재개 + v2 개정**: §6 피드백의 ★ 2건을 코드로 검증 — 둘 다 사실로 확정(G4 정정=무세션 401·mock 폴백 부재 `index.js:24-30`/`httpAdapter.js:17-28` · G7 확정=`_cut_to_api` 상대경로 `routes.py:770` + file 라우트 Bearer 필수 `routes.py:1121`). G7·G8 신설, P0/P1을 A1~A6대로 재구성. 다음=Codex 설계 주도.
- 2026-07-11 **일시정지(사용자 지시)** — 재개 지점:
  1. 사용자 결정 확보됨: ① 스코프=상의 단독(top-only, 매칭 시드는 메타 수령 후 별도 트랙) ② Gemini 실호출 스모크 승인(dev 크레딧 4 차감).
  2. Codex 기획 피드백 **도착·§6에 보존됨** — 재개 시 §6 반영(v2 개정)부터 시작.
  3. 이후 순서: Codex 설계 주도(스위치 구조·기동 절차·스모크 시나리오·mock 폴백 방지) → Claude 피드백 → 구현(P0→P1→P2) → 사용자 브라우저 QA.
  4. 워킹트리 미커밋: 마네킹 UI(우측 예시 패널·태그 제거·안내문, Mannequin.jsx/css) + 방향서 + 이 기획 문서 — 빌드·스크린샷 검증 완료 상태.

## 6-1. Codex 설계 태스크 결말 + reconcile (2026-07-11)

- **결말**: ultra 설계 태스크(task-mrgrh6lo)는 D1 방향 신호까지만 내고 **워커 크래시**(토큰 고갈 추정, pid 소멸·로그 침묵) — D2~D6 없음. 상태 파일이 stale "running"이라 stop 게이트를 막아 **cancelled로 정정**. 이후 Codex effort는 xhigh(~/.codex/config.toml).
- **D1 부분 신호**: Codex는 (a)안 선호 — "마네킹 컷 src에 절대 R2 URL 직접 + 인증 `/file` 유지 + job 완료 후 GET /mannequins로 정본화".
- **reconcile 판정 = 구현한 (B)안 유지.** 근거: ① (a)는 마네킹만 고치고 **에디터 블록·wardrobe의 `/file` src는 여전히 `<img>` 불가**로 남음(그쪽은 DB에 `/file` URL이 영속돼 있어 (a)식이면 마이그레이션/런타임 매핑 별도 필요) — (B)는 클래스 전체를 한 번에 해소 ② `/file` 인증의 실보호는 명목적(R2 객체가 public base로 이미 공개, 잔여 델타=UUID id→key 오라클) ③ (B)는 이미 구현·테스트(245)·**E2E 스모크 픽셀 로드 실증** 완료. — 단, (a)의 "재생성 후 목록 재조회로 정본화"는 이미 어댑터에 동일 취지로 구현돼 있음(regenerate 후 전체 목록 재조회). 재검토 원하면 사용자 판단으로 (a) 전환 가능(전환 비용: 서버 2곳+테스트, 이득: /file 인증 복원).

## 6. Codex 기획 피드백 (2026-07-11 수령 — v2에 반영 완료)

> 판정: **아래 수정(A1~A6) 후 진행 OK.** 일부 주장은 코드 재확인 필요(★표).

핵심 지적:
1. **P0에 인증·준비상태 검증 누락** — OAuth가 `window.location.origin` 복귀라 localhost 허용은 미확인 가설(과거 실패 기록 `documents/TODO.md:62`). dispatcher는 DB+R2+"Gemini **또는** OpenAI"로 기동하나 마네킹은 Gemini 필수(`main.py:91-97`) → health/dry-run만으론 준비 증명 안 됨.
2. **우선순위 뒤집힘** — 스크립트 추가보다 [OAuth 세션→로컬 `/v1/me/account` 200 → R2 PUT/complete → analysis 저장 → 크레딧 원천] 검증이 먼저. `.env.http`는 `vite --mode http`로 실행해야 적용.
3. ★**G4 전제 오류** — 무세션 http는 "조용한 mock 폴백"이 아니라 Authorization 없이 백엔드행→401(`httpAdapter.js:17-28`, 어댑터는 빌드타임 고정 `index.js:12-34`). 경량 조치 = `http()`에서 토큰 없으면 요청 전 **명시적 hard-fail**.
4. **비범위 3건은 유지 OK, 대신 필수 추가 2건** — ① ★**보호 자산 브라우저 서빙**: 컷 src `/v1/assets/{id}/file`이 Bearer 요구(`routes.py:765-770,1120-1134`)인데 Vite proxy 없음(`vite.config.js:14`) → `<img>` 로드 불가 가능성. P2 "발견 버그"가 아니라 **P0 blocker로 승격**. ② OAuth callback(localhost) 설정.
5. **P1 게이트 보강** — 결정적 증거로: R2 PUT/complete 성공 · analysis job done+필드 저장 · paid job 2건 done·각 2차감·**reserved=0** · v1 직접 선택→PATCH/DB 확인→**새로고침 유지·추가 job/차감 없음** · **실제 이미지 픽셀 로드**. "서버 로그"는 합격 기준에서 제거. 클라 timeout(180/300s)은 서버 실패가 아님 → 재클릭 전 동일 job 조회.
6. **최대 시간낭비 리스크** = 크레딧·Gemini 소진 후 자산 URL/인증 문제로 깨진 이미지 발견 → **유료 생성 전** 업로드 1건+기존 보호 자산 1건을 실제 `<img>` 경로로 렌더하는 선행 게이트. 크레딧도 표시 잔액 아닌 active `credit_sources` 합계+reserved=0로 확인.

수정 목록: **A1** P0에 localhost OAuth 왕복+account 200 게이트 / **A2** 보호 자산 `<img>` 서빙을 P0 승격+실로드 확인 / **A3** R2 PUT/complete+크레딧 원천·reserved 불변식 선검증 / **A4** 새 project ID·기존 cuts 0·analysis 저장 확인 후 생성 / **A5** job ID·terminal 상태·원장·버전 선택·서버 선택값을 P1 증거로 / **A6** timeout 재시도 금지→동일 job/cuts/ledger 확인.
