> 2026-09-11 가격 개정(단발 14,900원, 월 이용권 49,900원, 초과 7,900원) 미반영 이력 문서. 정본은 documents/legal/00_facemarket_legal_notice_map_v1.md.

# FaceMarket 모델 지원 플로우 구현 지시서 (2026-09-10)

구현 담당(Codex)이 읽는 문서다. 화면 정본은 오너가 확정한 시안 `mockups/facemarket_flows_20260909/apply/screens/*.html`(같은 폴더 `*_390.png`, `*_1280.png` 캡처)과 `apply/flow.md`, FAQ 문구와 검사 기준은 저장소에 포함된 `documents/facemarket_apply_faq.md` 다. 이 문서와 시안이 어긋나면 **이 문서가 우선**한다. 브랜치는 `codex/facemarket-apply-flow`(origin/main `c4adc121` 기준), 작업 디렉터리는 이 워크트리 루트다.

## 0. 반드시 지킬 것
- 저장소 규칙 `CLAUDE.md` 를 따른다. React 훅은 로딩 early-return 위에(훅 개수 불변). 인라인 스타일·CSS-in-JS 금지, CSS Modules 와 `tokens.css` 토큰 사용. Next.js 관례 도입 금지.
- **git 커밋·푸시·add 를 하지 않는다.** 작업이 끝나면 파일만 남긴다. 커밋은 지시한 쪽이 한다.
- **`supabase/migrations/*.sql` 은 append-only.** 기존 파일을 고치거나 지우지 않는다. 새 파일 하나만 추가한다(§4).
- `mockups/**` 는 읽기만 한다. 수정·삭제·복사 금지.
- 문체: 화면 문구는 시안 글자 그대로. 줄표(—) 금지, 슬래시(/) 나열 금지, "A → B" 압축 금지, 이모지 금지.
- 검증 기준(§8)을 전부 통과할 때까지 끝내지 않는다. 통과하지 못한 항목은 마지막 보고에 사유와 함께 적는다.
- 도달 불가한 화면·죽은 라우트를 만들지 않는다. 각 화면에 "어느 화면의 어느 버튼으로 도달하는지" 를 보고에 적는다.

## 1. 라우트와 상단바
1. **새 공개 라우트 `/apply`** 를 facemarket 앱(`src/apps/facemarket/App.jsx`)에 추가한다. 로그인 여부와 상관없이 열린다. 랜딩 셸(`LandingShell`) 안에서 그린다. 내용은 §2 의 지원 시작 페이지다. 컴포넌트 파일은 `src/features/facemarket-landing/pages/ApplyStartPage.jsx`(+ `.module.css`).
2. **상단바에 `모델 지원` 항목을 맨 앞에 추가**한다. 정본은 `src/features/facemarket-landing/facemarketRootTarget.js` 의 `LANDING_NAV` 다. `{ to: '/apply', label: '모델 지원', protected: false }` 를 첫 항목으로 넣는다. `ALLOWED_ROOTS` 에 `/apply` 를 더한다. 상단바 컴포넌트(`LandingHeader.jsx`)는 배열을 그대로 그리므로 항목이 넷이 되어도 깨지지 않는지 768px 근처 폭에서 확인한다(시안은 항목 간격을 `clamp(20px,2.8vw,36px)` 로 줄였다).
3. **랜딩 히어로의 `얼리버드 지원하기` 버튼도 `/apply` 로 간다.** `src/features/facemarket-landing/registerCta.js` 에서 신규 방문자(landing scope)의 목적지 `/model/apply` 를 `/apply` 로 바꾼다. 다른 scope(under_review 등)의 목적지는 그대로 둔다. 관련 테스트 `tests/frontend/facemarket-register-cta.test.mjs` 를 새 목적지에 맞게 고친다.
4. 지원서 자체(`/model/apply`)는 지금처럼 `RequireAuth` 아래에 둔다. `/apply` 의 `지원서 쓰기` 버튼은 `/model/apply` 로 간다. 비로그인이면 기존 가드가 로그인 모달을 열고 로그인 뒤 `/model/apply` 로 돌아온다(가드 코드 수정 없음).
5. `ModelApply` 는 이미 `under_review`·`approved` 지원서가 있으면 `/status` 로 보낸다. 그 동작은 유지한다.

## 2. 지원 시작 페이지 `/apply` (시안 `01_start.html`)
시안을 그대로 옮긴다. 데스크톱 1280 은 두 칸(왼쪽 넓게, 오른쪽 좁게), 폰 390 은 한 칸.
- 왼쪽: 눈썹 `FaceMarket에서 모델로 시작해요`, 제목 `모델 지원`, 두 줄 `내가 등록해 둔 얼굴을 이용해 셀러가 AI로 의류컷을 만들 수 있어요` / `셀러 기준 1번 이용 시 9,900원, 1개월 이용 시 29,900원 (10회 제한)`(금액 두 개 굵게. 값은 `src/lib/facemarketPricing.js` 에서 읽는다), 버튼 `지원서 쓰기`, 가상 모델 얼굴 4장(`/models/women/w1.webp`, `/models/men/m1.webp`, `/models/women/w2.webp`, `/models/men/m3.webp`), 캡션 `가상 모델 사진이에요`, 작은 두 줄 `사진 한 장이면 시작해요` / `경력 없이도 지원해요`.
- 오른쪽 `지금 시작하면`: `01 증서 발급료, 무료예요`(`무료` 만 굵게 + `#2d63ff`), `02 3분이면 제출 가능해요`(`3분` 굵게), `03 24시간 안에 승인 여부를 알려드려요`.
- 오른쪽 `등록하면 이런 게 달라져요`: 세 줄 목록. ① `10명의 셀러가 1번씩만 사용해도 약 70,000원이 자동입금돼요.` 줄바꿈 `셀러가 결제한 금액의 70%를 정산해드려요` + 끝에 작은 `i` 아이콘(호버·포커스 시 말풍선 `1건 기준 9,900원의 70%, 6,930원`, CSS 로만, 키보드 포커스에서도 뜨게). `70,000원` 만 굵게 파란색. ② `허용, 금지 카테고리 설정이 가능해요` ③ `내 얼굴이 어디 쓰였는지 전부 추적이 가능해요`. 아래 작은 글씨 `쌓인 몫이 10,000원을 넘으면 매월 10일에 보내드려요`(값은 `facemarketTerms.js` 의 `MIN_PAYOUT_KRW`, `SETTLEMENT_DAY`).
- 오른쪽 `지원 자격`: `만 19세 이상이어야 해요`, `소속 에이전시가 없어야 해요`, `본인이 직접 지원해야 하며, 이미지 권리를 갖고 있어야 해요`(체크 아이콘, 파란색 아님, 보조색).
- 맨 아래 `자주 묻는 질문`: 큰 글씨 가운데 정렬. 그 아래 **질문마다 흰 카드 하나씩**(옅은 그림자, 얇은 테두리, 라운드 14px, 카드 간격 12px), 접기 형태. 내용은 `documents/facemarket_apply_faq.md`의 공개 FAQ를 따른다. **단, `제 얼굴이 확실히 지켜지는 건가요?` 항목은 넣지 않는다**(실서버 검증 전이라 오너가 보류했다). 따라서 10개다.
- 상단바 오른쪽은 로그인 상태에 따라 기존대로(로그아웃 버튼 또는 로그인).

## 3. 지원서 `/model/apply` 를 3단계 위저드로 (시안 `02_basic`, `03_profile`, `05_confirm`, `06_done`)
`src/features/model/ModelApply.jsx` 를 다시 짠다. 파일을 쪼개도 된다(`ModelApplyBasic.jsx`, `ModelApplyProfile.jsx`, `ModelApplyConfirm.jsx`, `ModelApplyDone.jsx` 등). 단계 상태는 URL 쿼리(`?step=1`)가 아니라 컴포넌트 state 로 두되, `고치기` 로 돌아갔다가 다시 확인으로 올 수 있어야 한다. 진행 표시는 시안처럼 3단 트랙(`1 / 3`, 끝난 단계 체크, 현재 단계 굵게). `ModelRegister.jsx` 의 레일은 export 되지 않으므로 지원서용을 새로 만들되 모양은 시안을 따른다.

### 3-1. 1단계 기본 정보 (`02_basic`)
- 제목 `기본적인 정보들을 알려주세요.`, 설명 `*표시는 필수 입력 항목입니다.`(별표 `#2d63ff`).
- 칸 순서: `이름`(한 칸, 필수), `성별`(여성·남성 알약, 필수), `생년월일`(필수), `전화번호`(필수, 회색 예시 `010-0000-0000`), `이메일`(필수, 로그인 계정 값으로 채우되 직접 수정 가능. 계정 이메일이 없으면 직접 입력하고, 재지원 시 보존된 연락 이메일을 우선 복원).
- 필수는 라벨 뒤 파란 별표만. `선택`·`필수` 글자를 쓰지 않는다. 설명 줄도 없다(`검토 중에 연락해요` 같은 문장 삭제).
- 데스크톱에서 칸 폭을 내용에 맞춘다(이름 260px, 전화번호·생년월일 230px 안팎, 이메일 380px). 폰은 전체 폭.
- **생년월일은 세 칸 입력이다.** `년`(4자리) `.` `월`(2자리) `.` `일`(2자리). 규칙:
  - 숫자만 받는다(`inputMode="numeric"`, 비숫자는 무시).
  - 년에 4자리를 치면 월로, 월에 2자리를 치면 일로 **자동으로 포커스가 넘어간다.** 데스크톱과 폰 모두.
  - 빈 칸에서 백스페이스를 누르면 앞 칸으로 돌아가 마지막 글자를 지운다.
  - 안 쓴 칸은 회색 예시(`2000`, `01`, `01`)가 보이고, 그 칸에 포커스가 오면 사라진다. 점은 칸 사이 고정 글자이고 **동그라미 같은 장식이 없다.**
  - 월 1~12, 일 1~31, 년은 오늘 기준 만 19세 이상이 되는 해까지만 유효. 세 칸이 다 차면 `YYYY-MM-DD` 로 합쳐 상태에 둔다. 붙여넣기로 `19990412` 같은 8자리가 오면 세 칸에 나눠 넣는다.
  - 이 로직은 순수 함수(`nextBirthdateSegments(segments, index, input)` 같은)로 분리해 `src/features/model/birthdateInput.js` 에 두고 테스트를 붙인다(§8).
- 아래 안내 `만 19세 이상만 지원할 수 있어요.` 한 줄.

### 3-2. 2단계 프로필 (`03_profile`)
- 제목 `추가 정보들까지 알려주세요.`, 설명 `*표시는 필수 입력 항목입니다.`
- 맨 위 **프로필 이미지 업로드**(필수). 왼쪽 3:4 업로드 칸(어깨선 실루엣 흐리게, `사진 올리기`), 오른쪽 제목 `프로필 이미지 업로드` 와 초록 체크(`#12775a`) 세 줄: `얼굴이 잘 보이는 사진으로 업로드해주세요.` / `정면 가까이서 찍고, 필터없는 이미지를 업로드해주세요.` / `지원서 확인용으로만 사용되며, 공개 프로필에 올라가지 않습니다.` 업로드는 기존 `stageApplicationPhoto({kind:'profile'})` 그대로. 올린 뒤에는 그 칸에 사진이 보이고 `다른 사진으로 바꾸기`.
- `키`(필수, cm, 세 자리 폭)와 그 오른쪽 `몸무게`(선택, kg, 세 자리 폭). 아래 설명 줄 없음.
- `경력`(선택): `경력 없음`, `1년 미만`, `1년에서 3년`, `3년 이상` 알약. 서버 값은 기존 `EXPERIENCE_LEVELS`(none, beginner, intermediate, professional)에 순서대로 대응한다.
- `모델 에이전시에 속해본 경험이 있나요?`(필수): `예`·`아니오` 알약. 서버 필드 `agencyContracted`(bool).
- `포트폴리오 링크`(선택, 예시 `www.portfolio.com`), `SNS 링크`(선택, 예시 `www.instagram.com/example`). 묻는 문장·설명·접기 없이 칸만.
- 지역, 관심 카테고리, 자기소개 칸은 **없다.**

### 3-3. 3단계 확인 (`05_confirm`)
- 제목 `보내기 전에 확인해주세요.`, 설명 `이름과 생년월일이 신분증과 같은지 한 번 더 봐주세요.`
- 묶음 `기본 정보`(이름, 성별, 생년월일, 전화번호, 이메일)와 `프로필`(사진 썸네일, 키, 몸무게, 경력, 에이전시 경험, 포트폴리오, SNS). 각 묶음 오른쪽 `고치기` 는 해당 단계로 돌아간다. 빈 값은 `적지 않음`.
- 묶음 `체크사항`: 체크박스 5개, 각 항목 앞에 회색 `(필수)`, **사전 체크 없음**, 글자는 아래 그대로.
  1. `만 19세 이상이며, 본인이 직접 적은 내용임을 확인합니다.`
  2. `올린 사진은 본인의 사진이고, 보정이나 필터, AI 생성이 아니며, 제출할 권리가 있음을 확인합니다.`
  3. `현재 어떤 모델 에이전시와도 계약, 소속되어 있지 않은 상태임을 확인합니다.` 줄바꿈 `기존 계약이 존재할 경우 facemarket 모델 리스트에서 제외될 수 있습니다.`
  4. `지원자를 facemarket에 등록할 지 판단하는 여부로만 제출한 내용들이 사용됩니다.` 줄바꿈 `이외의 목적으로는 일체 사용하지 않습니다.`
  5. `개인정보 처리 약관에 대해 동의합니다.` 에서 `개인정보 처리 약관` 은 `/privacy` 로 가는 링크(밑줄, `#2d63ff`, 새 탭).
- 다섯을 다 켜야 주 버튼 `지원 완료` 가 켜진다. 덜 켜졌으면 버튼 위에 `아직 표시하지 않은 체크사항이 N개 있어요.` 한 줄. 기존의 개인정보 고지 스크롤 게이트(`noticeRead`, `PRIVACY_NOTICE` 블록)는 **없앤다.** 대신 체크 5번의 링크가 전문으로 간다.
- 주 버튼 글자는 `지원 완료`. 하단 고정 바는 유지(이전 | 지원 완료).

### 3-4. 접수 완료 (`06_done`, A안)
- 상단바 아래 남은 높이의 세로 가운데. 파란(`#2d63ff`) 선 체크 64px, 제목 `지원서가 접수 완료됐어요`, 본문 두 줄 `24시간 이내 결과를 이메일로 알려드려요.` / `상단의 'Digital DNA 관리' 탭에서도 실시간 상태를 확인 가능해요`, 그 아래 가운데에 검정 사각 버튼(모서리 8px) `지원 상태 보기` → `/status`. 하단 고정 바 없음. `승인되면 필요한 것` 같은 목록 없음.
- `24시간` 문구는 `src/features/facemarket-landing/facemarketTerms.js` 의 `REVIEW_SLA_LABEL` 을 `'24시간 이내'` 로 바꾸고 그 상수를 쓴다. 지금 하드코딩된 `보통 1시간 안` 을 전부 지운다(grep 으로 확인).

### 3-5. 제출 payload 와 클라이언트 검증
- 보내는 키: `contactEmail`, `applicantName`(한 칸 그대로), `gender`(`female`|`male`), `birthdate`(`YYYY-MM-DD`), `phone`, `heightCm`(정수), `weightKg`(정수 또는 null), `experienceLevel`(없으면 null), `agencyContracted`(bool), `portfolioUrl`, `snsUrl`, `attestations`(§4 의 5개 키), `privacyConsent {accepted:true, documentVersion}`.
- `region`, `categories`, `bio` 는 보내지 않는다.
- 1단계 `다음` 은 이름·성별·생년월일(유효)·전화번호가 있어야 켜진다. 2단계 `다음` 은 사진·키·에이전시 경험이 있어야 켜진다.
- 전화번호는 숫자와 하이픈만 받고 `010-0000-0000` 형태로 자동 하이픈을 넣는다.

## 4. 서버 (`server/app/facemarket_applications.py`) 와 마이그레이션
1. **새 마이그레이션 `supabase/migrations/20260910180000_facemarket_application_form_v3.sql`** 하나만 추가한다.
   ```sql
   alter table public.fm_model_applications alter column region drop not null;
   alter table public.fm_model_applications add column if not exists weight_kg integer
     check (weight_kg is null or (weight_kg between 30 and 200));
   ```
   기존 마이그레이션은 손대지 않는다. `region` 컬럼은 남긴다(옛 행 보존).
2. `ApplicationSubmitBody`: `region: str | None = None`, `weight_kg: int | None = None` 추가, `phone` 은 필수(`str`), `height_cm` 필수(`int`), `agency_contracted: bool` 필수. `categories` 는 남기되 기본 `[]` 이고 **`categories_required` 검증을 없앤다**(`invalid_category` 검사는 유지). `bio` 는 그대로 선택.
3. 검증: `phone` 은 숫자·하이픈만, 9~13자리 숫자. `height_cm` 100~250(기존). `weight_kg` 30~200. `attestations` 는 아래 5개 키가 모두 `true` 여야 한다. 하나라도 빠지면 400 `attestation_required`.
   - `adultAndTruthful`, `photosAreMine`, `noAgencyContract`, `reviewOnlyUse`, `privacyPolicy`
4. INSERT·`_APPLICATION_COLUMNS`·`admin_list_applications` 의 SELECT·`ApplicationView`·`AdminApplicationCard`·`_admin_card` 에 `weight_kg` 를 더한다. 컬럼 목록이 세 곳에 따로 있으니 셋 다 고친다(조사 결과: 382-388, 653-667, 756-762 근처).
5. 익명화 스윕(`sweep_terminal_application_pii`)과 그 테스트(`test_facemarket_application_hardening.py` 의 센티널 검사)는 `region` 이 nullable 이 돼도 그대로 통과해야 한다. 센티널 `'-'` 를 계속 써도 된다.
6. 서버의 `REVIEW` 관련 문구에 `1시간` 은 없다(조사 확인). 바꿀 것 없음.

## 5. 어드민 (`src/features/admin/AdminApplications.jsx`)
- 상세 `<dl>` 에서 `지역`·`관심 카테고리` 줄을 지우고 `몸무게`(`weightKg` 있으면 `NNkg`, 없으면 `-`)와 `에이전시 경험`(`agencyContracted` 이 true 면 `있음`, false 면 `없음`) 줄을 더한다. `CATEGORY_LABEL` 은 더 안 쓰이면 지운다.
- `tests/frontend/admin-applications-parity.test.mjs` 는 소스 문자열 검사라 위 변경으로 깨지지 않아야 한다. 깨지면 그 테스트의 의도(shadcn 이관 규칙)를 지키는 쪽으로 고친다.

## 6. Digital DNA 관리 `/status` 여정 (시안 `07_status_review`, `08_status_approved`, `09_status_rejected`)
`src/features/model/modelHubState.js` 의 `HUB_STEPS` 를 **5단계** 로 바꾼다.

| key | label | description(진행 중일 때만 표시) |
|---|---|---|
| application | 지원 접수 | (없음) |
| review | 내부 검토 | 지원서를 검토중이에요. 24시간 이내 결과를 전달해드릴게요. |
| registration | 모델 등록 | 얼굴 구현을 위한 이미지들과, 라이선스 증서에 대한 설정이 필요해요. |
| final_review | 최종 검토 | 등록서를 최종 검토중이에요. 24시간 이내 결과를 전달해드릴게요. |
| confirm | 프로필 이미지 확정 | 저희가 만들어드리는 프로필 이미지 중 사용될 이미지를 선택해주시면 모델 등록이 끝나요. |

`resolveHubJourney` 의 index 를 새 배열에 맞춘다: 지원 없음→0(CTA `얼리버드 지원하기` → `/apply`), under_review→1(설명 표시, 행동은 `지원 취소`), rejected→1(현 시안 09 처럼 사유 표시 + `다시 지원하기` → `/model/apply`), approved→2(**행동 카드**: 이름·설명 왼쪽, 오른쪽 버튼 `등록 시작하기` → `/model/register`, 테두리 그라데이션 `linear-gradient(135deg,#2d63ff 0%,#4f9dff 55%,#8ecbff 100%)`), enrollment 진행 중(identity·photos·license·terms·vc_pending 등)→2(같은 카드, 버튼은 이어서 할 단계로), review_pending·processing·asset_building→3(설명 표시, 새로고침), confirm_pending·awaiting_confirm→4(**행동 카드**, 보통 선, 버튼 `이미지 선택하기` → `/model/confirm`), verified→모든 단계 완료 + 기존 활동 중 대시보드.

`ModelHub.jsx` 의 Timeline:
- 설명은 현재 단계에만. 끝난 단계는 이름 + 작은 시각(있으면), 아직 안 온 단계는 이름만.
- 체크·완료 배지·현재 단계 강조색은 전부 `#2d63ff` 계열(초록 없음).
- 단계 사이 선이 끊기지 않게(시안에서 클래스 충돌로 끊겼던 사고가 있었다. 여정용 클래스는 다른 곳과 겹치지 않는 이름을 쓴다).
- 「지금 필요한 것」 목록과 승인됨 상태의 하단 고정 CTA 는 없앤다. CTA 는 행동 카드 안에만 있다.
- `지원 취소` 는 검토 중 화면에서 여정 아래 **오른쪽 끝**에 밑줄 글자 버튼. 동작은 기존 `cancelApplication`.
- 제목·설명은 시안대로: 검토 중 `지원서를 검토하고 있어요` + `{접수 시각}에 받았어요. 24시간 이내에 {이메일}으로 결과를 알려드려요.`, 승인됨 `승인됐어요. 이제 등록을 시작해요` + `본인확인, 사진, 조건, 증서 4단계예요. 10분이면 끝나요. 승인 메일의 링크를 눌러도 이 화면으로 와요.`
- 관련 테스트 `tests/frontend/facemarket-model-hub.test.mjs`(HUB_STEPS 7개 고정, index 매핑)를 새 5단계에 맞게 고친다.

## 7. 값의 단일 출처
- 가격 `src/lib/facemarketPricing.js`. 정산 `facemarketTerms.js`(`MODEL_SHARE`, `MIN_PAYOUT_KRW`, `SETTLEMENT_DAY`, `LICENSE_ISSUE_FEE_KRW`, `REVIEW_SLA_LABEL`).
- 색·라운드는 `src/styles/tokens.css` 와 기존 `--fm-*` 토큰. 새 hex 를 CSS 에 직접 쓰지 않는다. 그라데이션 세 색만 예외로 CSS 변수(`--fm-journey-grad`)로 한 번 정의해 쓴다.

## 8. 검증 기준 (전부 통과해야 끝)
1. `npx vite build` 통과.
2. `node --test tests/frontend/*.test.mjs` 전부 통과. 고쳐야 하는 기존 테스트: `facemarket-model-hub.test.mjs`, `facemarket-register-cta.test.mjs`, `facemarket-biometric-enrollment.test.mjs`(ModelApply 완료 문구·버튼 라벨 `지원 완료`·payload 키). 새 테스트: `tests/frontend/facemarket-birthdate-input.test.mjs`(자동 넘김·백스페이스·8자리 붙여넣기·유효성 6개 이상), `tests/frontend/facemarket-apply-start.test.mjs`(`/apply` 가 LANDING_NAV 첫 항목이고 `ALLOWED_ROOTS` 에 있으며 FAQ 10개 제목이 정본과 같다).
3. `cd server && .venv/bin/pytest -q` 전부 통과. 새 테스트 `server/tests/test_facemarket_application_form_v3.py`: region 없이 제출 성공, weight_kg 저장·조회, categories 빈 배열 허용, phone 없으면 400, attestations 5개 중 하나 빠지면 400 `attestation_required`, 마이그레이션 파일이 `drop not null` 과 `weight_kg` 를 담는다.
4. 헤드리스 크롬 스모크는 지시한 쪽이 찍는다. 다만 `/apply`, `/model/apply` 3단계, `/status` 세 상태가 mock 모드(`pnpm dev:mock`)에서 렌더되도록 `src/mock/` 의 목 데이터가 새 필드(`weightKg`, `agencyContracted`, 5단계 여정)를 갖추게 한다.

## 9. 마지막 보고 형식
1. 바꾼 파일 목록과 각 파일 한 줄.
2. §8 항목별 통과 여부와 실행 명령·요약 출력(숫자).
3. 각 화면의 도달 경로(어느 화면의 어느 버튼).
4. 지시와 다르게 푼 것과 이유.
5. 지시한 쪽이 결정해야 할 것(있으면).

## 리뷰 수정 복원

- 사용자가 입력한 연락 이메일을 지원서 제출, 확인 화면, 승인 안내 및 테스트컷 안내에 사용한다. 로그인 계정 이메일은 바꾸지 않는다.
- 입력 오류와 연결·서버 오류를 구분하고 작성 내용, 사진, 동의 체크를 보존한다. 이메일·링크 등의 입력 오류는 해당 단계에서 고치고 확인 단계로 돌아온다.
- 링크는 https://가 추가된 최종 주소에 서버의 기존 500자 제한을 적용해 입력 단계에서 안내한다. 붙여넣은 주소를 잘라내지 않는다.
- 일반 의류, 액티브웨어, 홈웨어·잠옷 중 허용할 품목을 선택하며 속옷·수영복에는 사용할 수 없다고 안내한다. 실제 선택 목록 제거는 PR 254가 담당한다.
- FAQ 문구 기준과 회귀 검사를 저장소에 함께 포함한다. 로컬 mockups 파일 없이 검사할 수 있어야 한다.
- 최신 main의 정기결제·로그인 변경과 마이그레이션 20260910180000은 그대로 유지한다.
