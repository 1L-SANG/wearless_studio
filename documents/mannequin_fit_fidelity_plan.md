# 마네킹 핏 조정 반영(fidelity) 기획 — 이중 기획 트랙

> 문제(2026-07-13 사용자 QA): 핏 조정 → 재생성 결과가 조정안을 반영하지 않음.
> 실측: 프로젝트 f082cb9e, fit=slim+length=long 재생성(A-1→A-2)에서 **기장 long 완전 미반영**(밑단 허리선 그대로), slim은 판별 애매, 의류 정체성은 완벽 보존.
> 프로세스: Claude·Codex **독립 기획** → 상호 피드백 → **설계 Codex 주도(ultra)** + Claude 중간 점검 → 구현.

## A. Claude안 (독립 작성 — Codex안 미열람)

### P1. 원인 가설 (우선순위)

1. **H1 지시 충돌 (주원인 유력)** — 템플릿 L14 "Faithfully reproduce … **exact** … **silhouette**"가 FIT PROFILE의 "overrides any impression from the photos"와 정면 모순. 범주적 재현 명령 + 사진 프라이어가 2줄 프로필을 압도. 근거: 기장 미반영과 동시에 디테일(랩·리본·컬러)은 완벽 재현 — "사진 충실이 이긴" 전형 패턴.
2. **H2 델타 신호 부재** — 재생성이 이전 컷 이미지를 첨부하지 않는 제로베이스 재생성이라 "무엇에서 무엇으로"의 편집 신호가 없음. 이미지 모델에는 텍스트 선언보다 [기준 이미지 + 이 부분만 바꿔] 편집형 지시가 훨씬 강함(컷 파이프라인·Weavy 인사이트와 일치).
3. **H3 기하 앵커 부족 (부차)** — fit 문구("close to the body")가 판정 불가능하게 관찰적. length는 "below the hips" 앵커가 있으나 H1에 눌림.
4. **H4 검증 부재** — 반영 여부를 아무도 판정하지 않음(Pillow QC 하드 shadow, AG-P2 off) → 실패가 조용히 통과되어 사용자에게 도달.

### P2. 전략 옵션

| 옵션 | 내용 | 기대효과 | 비용 | 리스크 |
|---|---|---|---|---|
| O1 프롬프트 수술 | L14를 "정체성(색·패턴·소재·디테일) 재현"과 "조형(실루엣·기장)은 FIT PROFILE 우선"으로 분해 + 조정된 축만 **CHANGES 섹션**으로 강조("MUST differ from the photos: hem below hips") | 중~상 | 0 (텍스트만) | 개선 폭 불확실, 과교정 시 정체성 흔들림 |
| O2 델타 재생성 | regenerate 시 **이전 선택 컷을 1번 참조로 첨부** + "이 이미지를 기준으로 지정 축만 변경" 편집형 지시 | 상 | 첨부 1장·워커 소수정 | 이전 컷의 결함 상속(정체성 안정성은 오히려 ↑) |
| O3 반영 검증 루프 | 생성 후 vision 판정("요청 축이 반영됐나") → 미반영 시 correctionPrompt 재시도 — **AG-P2 인프라·max_attempts=2 재사용** | 상 (안전망) | 판정 콜 + 재시도 시 생성 1콜 | 판정 신뢰도, 지연 +수십 초 |
| O4 축 문구 기하화 | fit_axes 문구에 측정형 앵커(예: 밑단=골반 아래 한 뼘) — 몰 8곳 검증된 축 정의(fit-axis-design) 주입 | 중 | 소 | 문구 과밀 |

### P3. 추천 조합

- **P0 (즉효)**: O1 + O2 — 텍스트 모순 제거와 이미지 편집 신호는 상보적. O4는 O1에 포함해 조정 축만 기하 앵커.
- **P1 (안전망)**: O3 — '조정 반영' 판정을 shadow로 계측 → 신뢰도 확인 후 enforce. (QC 오탐 사고 재발 방지: 게이트 승격은 실측 후)

### P4. 성공 판정

- **오프라인 하네스**: 동일 입력으로 축 조합 6케이스(fit slim/over · length crop/long · 교차) 생성 → 그리드 비교 + vision 블라인드 판정("두 이미지 중 어느 쪽이 더 긴 기장인가" 강제선택). 수용 기준: 방향 일치 ≥5/6, 정체성 훼손 0.
- **회귀 테스트**: 조정 축 CHANGES 섹션 렌더 스냅샷, 델타 첨부 매니페스트(이전 컷이 1번 슬롯) 단언.

### P5. 비범위 / 주의

- 의류 정체성(색·패턴·로고·소재) 보존은 절대 후퇴 금지 — fidelity 개선과 트레이드오프 상시 감시.
- 컷 생성(AG-06)·매칭 하의 축은 이번 스코프 밖(동일 패턴 후속 적용).
- 크레딧 정책 불변(재시도는 기존 max_attempts 내), 프롬프트 인젝션 방어(셀러 자유텍스트 직접 삽입 금지) 유지.

## B. Codex안 (독립 작성 — ultra, task-mril4xx1)

## P1. 원인 가설들 — 우선순위·근거·확인 방법 (repo 근거 라인 인용).

- 증거 한계: 실제 운영 잡 행·생성 원본은 저장소에 없으므로 시간선과 이미지 판정은 제공된 사건 증거로 취급했다. 저장소에서는 실행 메커니즘만 독립 검증했다.
- **H1 — 최우선: 프롬프트의 핏 우선순위와 정체성 규칙이 충돌한다.** FIT PROFILE 우선이라고 하지만([L15](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:15)), 동시에 사진의 “exact … silhouette”([L14](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:14))와 “shape”를 정본으로 선언한다([L34](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:34)). 원본 크롭 실루엣이 `long`보다 강한 이미지 앵커가 됐을 가능성이 가장 높다.
- **H1 보조:** 매칭 하의는 `tuck`을 허용한다([L21](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:21)). 따라서 긴 상의가 생성돼도 밑단을 숨길 수 있다. 확인: 현행/축 예외 명문화/축 예외+“untucked, hem visible” 3-arm 생성 비교.
- **H2 — 높음: 실패를 잡는 핏 준수 게이트가 없다.** 기본 `image_qc='off'`([config L55](/Users/daily/Documents/wearless_studio/server/app/config.py:55))이고 Pillow는 강제 shadow다([worker L74](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:74)). 현 AG-P2는 `fitProfile` 없이 상품사진+결과만 받으며([image_qc.py L53](/Users/daily/Documents/wearless_studio/server/app/agents/image_qc.py:53)), 오히려 원본과 “same … length”인지 판정한다([QC prompt L5](/Users/daily/Documents/wearless_studio/server/prompts/image_qc_v1.txt:5)). 즉 미반영은 통과하고, 그대로 enforce하면 의도된 길이 변경을 오판할 수 있다.
- **H3 — 낮음·부분 반박: enum/전달 누락은 주원인 같지 않다.** `top/women`에 `slim`과 `long`이 모두 존재하고([fit_axes.py L10](/Users/daily/Documents/wearless_studio/server/app/agents/fit_axes.py:10), [L24](/Users/daily/Documents/wearless_studio/server/app/agents/fit_axes.py:24)), 유효값은 고정 문구로 렌더된다([L137](/Users/daily/Documents/wearless_studio/server/app/agents/fit_axes.py:137)). 프론트도 전체 프로필을 보낸다([Mannequin.jsx L579](/Users/daily/Documents/wearless_studio/src/features/mannequin/Mannequin.jsx:579)).
- 다만 잡 payload 자체는 워커가 사용하지 않고, 라우트가 analysis에 저장한 뒤([routes.py L949](/Users/daily/Documents/wearless_studio/server/app/routes.py:949)) 워커가 최신 analysis를 다시 읽는다([worker L170](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:170)). 따라서 “payload에 값이 있음”은 실제 프롬프트 사용의 증명이 아니다. 확인: 잡 생성 시 effective profile 스냅샷·해시를 기록해 렌더 프롬프트와 대조.
- **H4 — 확정된 구조 드리프트:** 계약은 단일컷이다([PRD L336](/Users/daily/Documents/wearless_studio/documents/PRD.md:336)), 실제 워커·테스트는 A/B 두 후보를 강제한다([worker L210](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:210), [test L245](/Users/daily/Documents/wearless_studio/server/tests/test_mannequin_fit_profile.py:245)). 이 사건처럼 A가 이미 `slim`이면 B도 `slim`으로 덮여([mannequin.py L42](/Users/daily/Documents/wearless_studio/server/app/agents/mannequin.py:42)) 사실상 동일 목표 두 번이다.
- 따라서 원시 증거의 “single-shot”은 **후보별**로만 맞다. QC가 off이면 각 후보 첫 결과가 즉시 채택되지만([worker L100](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:100), [L129](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:129)), 잡 전체는 두 번 생성한다([L271](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:271)).
- **H5 — 보조 요인:** 이전 버전은 입력에 없고 `[base, product…, match?]`만 사용한다([worker L87](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:87)). 상대적 조정 연속성은 약하지만, `long`은 절대 목표이므로 이것만으로 완전 미반영을 설명하진 못한다.

## P2. 해결 전략 옵션 3개 이상 (예: 프롬프트만 / 파이프라인 변경 / 검증 루프 등) — 각각 효과 기대, 비용(지연·크레딧·복잡도), 리스크.

- **옵션 A — 프롬프트만:** 선언 축은 사진의 shape/silhouette보다 우선하고, 나머지 속성만 사진을 따르게 분리한다. `long`에는 “below hips·untucked·hem visible·하의로 가리지 말 것”을 추가한다. 기대효과 높음, 추가 지연·크레딧 0, 리스크는 모델 비결정성과 과도한 디자인 변형.
- **옵션 B — 축 인지 QC+조건부 재시도:** 한 vision 판정에서 `identityPass`와 `axisPass`를 분리하고, 선언 축은 정체성 비교에서 면제한다. 실패 시 축별 correctionPrompt로 최대 1회 재생성. 효과 가장 높음, vision 호출과 실패 시 이미지 호출·직렬 지연 증가, 판정기 오탐 교정 복잡도 존재.
- **옵션 C — 잡 스냅샷·관측성:** regenerate payload의 검증된 profile을 불변 입력으로 쓰고 profile/prompt hash·첨부 슬롯·판정 결과를 기록한다. 품질 직접효과는 낮지만 무음 유실과 재현 불가를 제거한다. 비용·지연 미미, 로그 개인정보·보관 정책 검토 필요.
- **옵션 D — 이전 컷 기반 이미지 편집:** 이전 결과를 앵커로 첨부해 “지정 축만 변경”한다. 상대 변화와 구도 보존은 강하지만 세대 누적에 따른 의류 디테일 열화·오류 고착 위험이 크고 새 파이프라인이 필요하다.
- **옵션 E — 현행 A/B를 단일 목표+재시도 예산으로 전환:** 첫 호출 1회, QC 실패 때만 두 번째 호출한다. 현행 최대 이미지 호출 수 2를 넘지 않으면서 실효성을 높인다. UI/DB의 legacy `candidate='A'` 호환 작업이 필요하다.

## P3. 추천 조합 1개 — 왜 이것인가. 단계(P0/P1) 구분.

- **추천: A+C를 P0, B+E를 P1로 결합한다.** 확인된 프롬프트 충돌을 즉시 제거하면서, 품질 게이트 도입 전에 입력 정합성과 측정 기반을 확보한다.
- **P0:** 선언 축만 사진 정본의 예외로 명시하고, 축별 신체 랜드마크·비가림 규칙을 추가한다. 실제 실패를 베이스 프롬프트 회귀 규칙으로 축적하는 방식은 저장소의 권고와도 맞는다([weavy insights L121](/Users/daily/Documents/wearless_studio/documents/weavy_fashion_proto_insights.md:121)).
- **P0:** job 시점 effective profile을 불변 스냅샷으로 저장·사용하고 prompt/profile hash를 이벤트에 남긴다. `top/women/slim+long` 경로의 라우트→워커 계약 테스트도 추가한다.
- **P1:** 단일 후보로 정리하고, 핏 인지 QC를 shadow로 보정한 뒤 enforce한다. 현재 두 이미지 호출을 “동일 목표 A/B”가 아니라 “첫 결과+필요 시 교정”에 사용한다.
- 옵션 D는 A/B 개선 후에도 상대 조정 실패가 반복될 때만 실험한다. 초기 적용 시 정체성 열화 위험이 해결 이득보다 크다.

## P4. 성공 판정 방법 — 자동화 가능한 검증(예: 축별 A/B 생성 비교, vision 판정) 설계 스케치, 수용 기준.

- **회귀 픽스처:** 보고 사례를 포함해 category×gender별 5개 이상 상품을 고정하고, `null→target`과 극단축 쌍(`crop↔long`, `tight↔over`, `skinny↔wide`)을 각 3회 생성한다.
- **Vision 출력:** `{identityPass, axisPass:{axis, target, observedLandmark, visible}, undeclaredAxesPreserved, mismatches, correctionPrompt}`. `slim`은 단순 픽셀 차이가 아니라 절대 핏 등급으로, `long`은 “밑단이 엉덩이 아래에 보임”으로 판정한다.
- **수용 기준:** 보고 사례 `long` 3/3 통과, 전체 선언 축 target pass ≥90%, 방향성이 있는 쌍의 단조 변화 ≥90%, 의류 정체성 ≥95%, 미선언 축 보존 ≥95%.
- **판정기 게이트:** 사람 라벨 gold set 30쌍에서 vision 일치율 ≥90%·false-pass ≤5%를 달성한 뒤 shadow 50건을 운영 관찰하고 enforce 전환한다.
- **단위/통합 검증:** 충돌 문구 제거, 축별 예외·비가림 문구, job snapshot 사용, QC에 effective profile 전달, 재시도 상한 2를 자동 검증한다. 기존 테스트는 블록 존재·순서만 검사해([test L103](/Users/daily/Documents/wearless_studio/server/tests/test_mannequin_fit_profile.py:103)) 이미지 의미 준수는 보장하지 못한다.

## P5. 비범위/주의 — 건드리면 안 되는 것(의류 정체성 보존, 기존 계약).

- 선언 축 외의 색·패턴·원단·봉제·넥라인·랩타이·로고, 마네킹 몸·포즈·구도·배경은 동결한다([template L25](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:25), [L35](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:35)).
- `fitProfile`의 category×gender enum, `null=사진 그대로`, seller override 계약은 유지한다([fit spec L8](/Users/daily/Documents/wearless_studio/documents/fit_profile_spec.md:8), [L14](/Users/daily/Documents/wearless_studio/documents/fit_profile_spec.md:14)). 자유 텍스트 축을 추가하지 않는다.
- 현 AG-P2를 그대로 enforce하거나 Pillow 게이트를 되살리지 않는다. 전자는 의도된 길이 변경을 정체성 위반으로 볼 수 있고, 후자는 알려진 오탐 때문에 코드상 강제 shadow다([worker L66](/Users/daily/Documents/wearless_studio/server/app/workers/mannequin_job.py:66)).
- 폐기된 `:adjust`를 부활시키지 않는다([routes.py L875](/Users/daily/Documents/wearless_studio/server/app/routes.py:875)). 이전 컷 편집 실험도 기존 `regenerate({fitProfile})` 계약 안에서만 한다.
- 이번 작업은 읽기 전용 기획·진단이며 파일 변경, 테스트 실행, 배포는 수행하지 않았다.

## C. 상호 피드백 & 수렴

### C-1. Claude → Codex안 비평

**동의·수렴**: H1(프롬프트 충돌)=양측 최우선 일치. AG-P2를 그대로 enforce하면 '의도된 변경'을 불량 오판한다는 지적(H2)은 내 O3의 함정을 정확히 보완 — 축 인지(axis-aware) 판정으로 반드시 재설계. 관측성(옵션 C, effective profile 스냅샷·해시)은 내 안에 없던 순수 이득 — 채택.

**실측 보강(+1)**: tuck 가림 가설 검증 — 문제 잡 2건 모두 `withBottom=True`(흰 스커트=실제 매칭 하의, L21 tuck 지시 활성 경로). 가설 성립 조건 실측 확인. P0 프롬프트에 "선언된 length 축이 있으면 untucked·hem visible" 예외 필수.

**반박·조정**:
1. 옵션 E의 "legacy candidate 호환 작업" — 이미 fix/mannequin-single-cut PR(푸시됨)이 단일 후보 전환 완료. E는 "남은 재시도 예산을 교정에 사용" 절반만 유효 → P1의 B와 사실상 동일 항목으로 흡수.
2. 옵션 C 스냅샷 — regenerate만이 아니라 최초 generate(프로필 부재→auto)도 동일 스냅샷 경로로 통일해야 관측이 완전.
3. P4 gold set 30쌍 사람 라벨은 P0 검증으로는 과중 — P0는 소규모 그리드(6~10케이스, 사람 눈+vision 강제선택)로 시작, gold set은 P1 enforce 승격 게이트로 이연.

**Claude안에서 유지할 것**: 조정 축만 CHANGES로 분리 강조(전체 프로필 나열과 별개로 '이번에 바뀌어야 하는 것' 신호), 오프라인 그리드 하네스.

**내가 양보**: O2(이전 컷 첨부 델타 재생성)를 P0에서 제외 — Codex 논거(세대 열화·오류 고착 + 가상모델 스파이크의 '생성물 재주입 금지' 결론과 일치, 그리고 long은 절대 목표라 델타 불필요)가 더 강함. 상대 조정 UX가 생기는 미래에 재검토(P2 실험).

### C-2. Codex → Claude안 비평 (요지)

- **H1·H4·O3 방향 수렴.** O1 정체성/조형 분리, O4 랜드마크화, shadow→enforce 순차 승격 채택 가치 높음.
- **반박 1 — H2(델타 부재) 주원인급은 과함**: fitProfile은 상대 편집이 아니라 **절대 목표**(null만 사진 그대로, fit_profile_spec L14) — 이전 컷 없이도 long을 충족해야 함. → 수용.
- **반박 2 — "MUST differ from the photos" 무조건 적용은 오류**: 사진이 이미 slim이면 차이를 강제할 이유 없음. 계약은 "**사진과 충돌할 때만 선언 축이 이긴다**". → 수용(CHANGES 문구 조건부화).
- **반박 3 — H3은 축별 분리**: long은 이미 "below the hips" 앵커 보유 — 부족했던 건 측정어가 아니라 **untucked·hem visible + 사진-shape 예외**. → 수용.
- **반박 4 — O2(이전 컷 첨부) P0 반대**: 입력 계약(1번=원본 베이스) 위반 + 생성물 재주입 열화 전파(AG-06 스파이크 결론). → 수용(보류, 격리 실험으로만).
- **반박 5 — O3는 "AG-P2 재사용"만으론 부족**: 현 판정기는 fitProfile을 안 받고 "same length"를 요구(image_qc_v1.txt L5) — 그대로 enforce하면 성공한 long을 되돌림. 재시도 배관만 재사용, 판정기는 identityPass/axisPass 분리 신설. → 수용.
- **반박 6 — 6케이스·5/6 기준 과소** + tuck 조건 테스트 포함 필수. → P0 사람눈 그리드는 소규모 유지하되, 정식 픽스처(카테고리×성별 5상품·축쌍 3반복·tuck 포함)는 P1 게이트로. 관측성 갭(워커가 payload가 아닌 최신 analysis 재독 — 경합 창) 명시.

### C-3. 최종 수렴안 (양측 합의)

**P0 (즉효·무과금 변경)**
1. **프롬프트 수술**: L14/L34의 "exact silhouette/shape"를 "정체성 속성(색·패턴·소재·디테일·구조) 재현"으로 한정하고, **선언된 FIT PROFILE 축만 사진 정본의 예외**로 명문화(충돌 시 선언 축 우선 — 무조건 differ 아님).
2. **축별 랜드마크·가시성 규칙**: length 선언 시 "untucked, hem fully visible, 하의로 가리지 말 것" 포함(tuck 가림 실측 대응). 조정 축은 CHANGES 섹션으로 분리 강조(조건부 문구).
3. **관측성**: 잡 생성 시점 effective profile **스냅샷을 워커의 불변 입력**으로(라우트 저장→워커 재독 경합 제거), profile/prompt hash를 job_events에 기록. generate(auto 프로필)도 동일 경로.
**P1 (안전망·게이트)**
4. **축 인지 QC**: identityPass/axisPass 분리 판정(선언 축은 정체성 비교 면제), shadow로 보정 → 기준 충족 시 max_attempts=2 내 조건부 correction retry enforce. (단일컷 PR 전제 — 2번째 이미지 호출 예산을 '교정'에 사용)
5. **정식 검증 픽스처**: 카테고리×성별 5상품 × 축쌍(crop↔long 등) × 3반복 + tuck 조건, 수용 기준(선언 축 pass ≥90%·정체성 ≥95%·미선언 축 보존 ≥95%), gold set 30쌍은 enforce 승격 게이트.
**보류**: 이전 컷 첨부 델타 재생성(상대 조정 UX 등장 시 격리 실험).

## D. 설계 (Codex 주도, ultra — task-mrilt6vm)

## D1. Prompt template rewrite

대상: [mannequin_generate_v1.txt](/Users/daily/Documents/wearless_studio/server/prompts/mannequin_generate_v1.txt:14)

- L14  
  old: `- Faithfully reproduce the garment from the attached views together with the PRODUCT CONTEXT at the very bottom: exact color, pattern, fabric, seams, neckline, silhouette, and any logo or print.`  
  new: `- Faithfully reproduce the garment's identity from the attached views together with the PRODUCT CONTEXT at the very bottom: exact color, pattern, fabric, construction, seams, neckline, trims, hardware, and any logo or print. Fit, length, cut, and silhouette are governed separately by the precedence rule below.`

- L15  
  old: `- Fit and proportion precedence: declared FIT PROFILE (if present) > FIT view reference photo (if attached) > default impression from the product photos.`  
  new: `- Fit and proportion precedence, axis by axis: each declared FIT PROFILE axis overrides conflicting visual evidence for that axis; for every undeclared axis, use the FIT view reference photo (if attached), then the default impression from the other product photos.`

- L19  
  old: `- FIT view (if attached): this shows the same garment worn on a real body. For any FIT PROFILE axis not declared, treat it as the priority reference for fit and proportion — match the garment's length (where the hem falls), cut or silhouette, how loose or fitted it sits, how it drapes, and which body areas it emphasizes or skims.`  
  new: `- FIT view (if attached): this shows the same garment worn on a real body. Use it as the priority reference only for FIT PROFILE axes that are not declared — including where the hem falls, cut or silhouette, ease, drape, and which body areas the garment emphasizes or skims. Do not use it to override a declared axis.`

- L21  
  old: `- MATCHING BOTTOM (if attached, the last image): also dress the mannequin in this bottom garment, coordinated naturally with the top (appropriate layering, tuck, and proportion).`  
  new: `- MATCHING BOTTOM (if attached, the last image): also dress the mannequin in this bottom garment, coordinated naturally with the top. If the main product is a top or outerwear and its length axis is declared in FIT PROFILE, keep the main product untucked with its entire hem visible and do not let the matching bottom cover it; otherwise use appropriate layering, tuck, and proportion.`

- L34  
  old: `- The attached product photos are the ground truth for color, pattern, and shape; the PRODUCT CONTEXT text supports them but must NEVER override what the photos clearly show.`  
  new: `- The attached product photos are the ground truth for garment identity — color, pattern, fabric, construction, seams, neckline, trims, hardware, logos, and prints. The only exceptions to their fit, length, cut, or silhouette are declared FIT PROFILE axes, and only where visual evidence conflicts with the declaration; PRODUCT CONTEXT must NEVER override what the photos clearly show.`

현재 실제 템플릿 토큰은 `${imageManifest}`, `${baseGender}`, `${clothingType}`뿐이다. `${baseFit}`·`${candidate}`는 이미 제거됐고 테스트도 재도입을 거부한다([test_mannequin_fit_profile.py](/Users/daily/Documents/wearless_studio/server/tests/test_mannequin_fit_profile.py:139)). 위 변경은 토큰을 추가·삭제하지 않는다. FIT PROFILE은 토큰이 아니라 렌더러가 템플릿 뒤, PRODUCT CONTEXT 앞에 주입한다.

## D2. `fit_axes.py` 렌더 변경

`FIT_AXES`의 프론트 미러 구조는 건드리지 않고, 백엔드 전용 `AXIS_OBSERVABLES[(category, axis, value)]` 고정 맵을 추가한다. 실제 카탈로그에는 `dress.fit`이 없으며 dress는 `length+silhouette`다.

렌더 형식은 `- {axis}: {promptEn}. Observable target: {고정 문구}.`로 통일한다.

- `top.fit`: tight=`continuous contact at chest, waist, and upper arms, with no visible ease`; slim=`follows chest and waist closely with only slight visible ease and does not read as bodycon`; regular=`light, even ease at chest and waist, without clinging or oversized volume`; semi_over=`extra room at shoulder, chest, and sleeves, with a mildly dropped shoulder point`; over=`shoulder seam below the shoulder point and clear air around chest, waist, and sleeves`.
- `top.length`: ultra_crop=`entire untucked hem well above the navel and visible above any matching bottom`; crop=`entire untucked hem at the high waist and visible above any matching bottom`; basic=`entire untucked hem at the hip line and not covered by any matching bottom`; long=`entire untucked hem below the hips and neither tucked into nor covered by any matching bottom`.
- `outer.fit`: slim=`shoulder seam near the shoulder point with minimal layering ease in body and sleeves`; regular=`natural shoulder line with moderate layering room`; semi_over=`mildly dropped shoulder with extra room through body and sleeves`; over=`shoulder seam visibly below the shoulder point with broad air volume around body and sleeves`.
- `outer.length`: crop_short=`entire untucked hem at waist or high hip and unobscured`; basic=`entire untucked hem at the hip and unobscured`; long=`entire untucked hem at mid-thigh or lower and fully visible`.
- `pants.cut`: skinny=`outline hugs hip, thigh, knee, calf, and ankle continuously`; slim=`narrow at thigh, knee, and ankle with slight ease rather than continuous skin contact`; straight=`inner and outer leg lines nearly parallel from thigh to hem`; bootcut=`close through hip and knee, then visibly wider from knee over the foot`; wide=`leg outlines clear of thighs and calves from hip to hem, with hems covering most of the shoes`; tapered=`ample thigh width narrowing visibly from knee to hem`; relaxed=`clear room at seat and thigh, then a soft near-straight fall with slight taper and hem at shoe top`; semi_wide=`moderate straight column below the knee, wider than straight but narrower than wide, with most of each shoe visible`.
- `pants.length`: above_ankle=`both hems just above the ankle bones with a visible ankle gap and unobscured`; ankle=`both hems at the ankle bones with no break and unobscured`; below_ankle=`both hems past the ankle bones with one soft break on the shoe tops and visible`.
- `skirt.length`: mini=`entire hem above mid-thigh and fully visible`; midi=`entire hem between knee and mid-calf and fully visible`; long=`entire hem from lower calf to ankle and fully visible`.
- `skirt.silhouette`: h_line=`side seams nearly parallel from hip to hem with no flare and full outline visible`; a_line=`fitted waist with both side seams widening continuously to the hem and full outline visible`; mermaid=`outline hugs hip and thigh, then flares sharply near the lower leg with full flare visible`.
- `dress.length`: mini=`entire hem above mid-thigh and fully visible`; midi=`entire hem between knee and mid-calf and fully visible`; long=`entire hem from lower calf to ankle and fully visible`.
- `dress.silhouette`: h_line=`outer lines nearly parallel from shoulder to hem with no flare and full outline visible`; a_line=`fitted upper body with outer lines widening steadily to the hem and full outline visible`; fit_and_flare=`bodice fitted through the natural waist, skirt volume beginning clearly at the waist, full flare visible`; mermaid=`outline hugs hip and thigh, then flares sharply near the lower leg with full flare visible`.

`build_fit_profile_block(profile, adjusted_axes=())`의 최종 블록:

```text
FIT PROFILE (declared target axes; preserve garment identity and every undeclared axis):
- ...
Where the photos conflict with a declared axis, the declared axis wins; otherwise preserve the photographed shape for that axis.

CHANGES FOR THIS GENERATION (seller-adjusted declared axes):
- ...
Apply these targets where the photos conflict; do not force a difference when the photos already satisfy them.
```

CHANGES는 `profile.source == "seller"`이고 `adjusted_axes`에 들어 있는 유효한 주상품 축만 반복 렌더한다. `adjustedAxes`는 regenerate 라우트가 analysis 저장 전에 “직전 정규화 effective profile”과 “새 정규화 effective profile”을 비교해 계산한다. 새 값이 non-null이고 달라진 축만 카탈로그 순서로 저장하며, category/gender가 바뀌면 새로 선언된 축 전체가 대상이다. generate·body 없는 regenerate·null reset은 `[]`이다. `matchCut`은 별도 의류이므로 정상 FIT PROFILE에는 pants.cut observable을 재사용하되 P0 CHANGES에서는 제외한다.

## D3. Snapshot & observability

새 잡 payload 계약:

```json
{"mode":"generate|regenerate","fitProfileSnapshot":{"version":1,"profile":{},"adjustedAxes":["fit","length"]}}
```

`profile`은 카탈로그로 정규화하고 실제 매칭 이미지가 없으면 `matchCut`을 제거한 dict 또는 `null`이다. generate는 저장된 `analysis.fitProfile`—`source:auto` 포함—을 snapshot하며, 현재처럼 프로필 자체가 없으면 새 auto 값을 발명하지 않고 명시적 `null`을 저장한다.

라우트는 이전 analysis와 실제 match asset 존재를 읽어 snapshot/diff를 만든 뒤 `create_job`에 넣는다. `created=False`면 기존 잡 payload가 정본이며 재계산값으로 덮어쓰지 않는다. `created=True` regenerate만 최신 analysis를 다시 읽어 요청 프로필을 merge-save해 UI 연속성을 유지한다.

워커는 `fitProfileSnapshot` 키가 있으면 `profile:null`도 권위 있는 입력으로 사용하고, 프로필 결정을 위해 최신 `analysis.fitProfile`을 재독하지 않는다. 키가 아예 없는 실행 중 legacy 잡만 현재 analysis 기반 `effective_fit_profile`로 fallback한다. 키는 있는데 version/shape가 잘못됐으면 `invalid_fit_profile_snapshot`으로 실패시켜 무음 fallback을 금지한다. analysis는 상품 문맥·성별·매칭 선택을 위해 계속 읽으므로 불변 보장은 fitProfile에 한정한다.

job_events는 새 타입이 아니라 기존 `step`을 선택한다. DB CHECK가 네 타입만 허용하므로([init.sql](/Users/daily/Documents/wearless_studio/supabase/migrations/20260612090000_init.sql:207)) 새 타입은 불필요한 forward migration과 소비자 변경을 만든다.

각 실제 Gemini 호출 직전에 약 250B의 이벤트를 남긴다:

```json
{"status":"prompt_rendered","candidate":"A","attempt":1,"profile_hash":"sha256…","prompt_hash":"sha256…","prompt_version":"…","input_source":"payload_snapshot|legacy_analysis_fallback"}
```

`profile_hash`는 실제 renderer 입력을 `sort_keys=True`, compact separators, UTF-8로 canonical JSON SHA-256(`null` 포함), `prompt_hash`는 해당 attempt에 실제 전송하는 prompt UTF-8 SHA-256이다. 원문 profile/prompt는 이벤트에 넣지 않는다. 기존 `step`과 동일한 best-effort 관측 이벤트이며 생성 실패 원인이 되지 않는다.

## D4. Injection safety & contracts

현재 HTTP body는 단순 `dict`라 route-level Pydantic enum 검증이 없다([routes.py](/Users/daily/Documents/wearless_studio/server/app/routes.py:915)). 실제 방어선은 category/gender/value exact lookup 후 고정 `promptEn`만 출력하는 [fit_axes.py](/Users/daily/Documents/wearless_studio/server/app/agents/fit_axes.py:144)와 `matchCut` lookup([L156](/Users/daily/Documents/wearless_studio/server/app/agents/fit_axes.py:156))이다.

P0에서 같은 allowlist를 쓰는 `normalize_fit_profile`을 추가해 snapshot·diff·renderer가 공유한다. 알 수 없는 축·값·source와 raw `adjustedAxes`는 버리고, 새 FIT PROFILE/CHANGES에는 `promptEn`, `AXIS_OBSERVABLES`, 고정 축명 외 seller 문자열을 절대 보간하지 않는다. 기존 PRODUCT CONTEXT 자유텍스트는 별도 `_sanitize` 경로([prompts.py](/Users/daily/Documents/wearless_studio/server/app/agents/prompts.py:26)) 그대로다.

문서 갱신:

- `fit_profile_spec.md` §1에 `source=seller`만으로 변경 축을 복원할 수 없고 `adjustedAxes`는 job-local임을 명시; §3에 새 block/observable/CHANGES; §4에 snapshot·legacy fallback.
- `ai_agent_modules.md` AG-04 입력을 job-time snapshot으로, 프롬프트 제약을 identity/axis 분리로, 관측을 두 digest로 갱신.
- `mannequin_ui_direction.md` 데이터 연결의 “analysis를 worker가 읽음”을 “analysis는 UI 연속성, worker는 snapshot 소비”로 교체. UI 상태머신 변경은 없다.

## D5. Test plan

- `server/tests/test_mannequin_fit_profile.py`: `test_renderer_has_observable_phrase_for_every_catalog_entry`를 category/axis/gender/value 전체에 parameterize.
- 같은 파일: `test_changes_only_renders_different_seller_axes`, `test_changes_omitted_for_auto_unchanged_and_generate`, `test_malicious_adjusted_axes_are_not_interpolated`.
- tuck: top/outer length는 `untucked`, `entire hem`, `not covered`를 단언하고 pants/skirt/dress length에는 `untucked`가 없음을 음성 단언; 템플릿 L21도 golden으로 고정.
- `server/tests/golden/mannequin_generate_top_women_slim_long.txt`와 `test_prompt_golden_top_women_slim_long`: D1 다섯 줄, block 순서, CHANGES, PRODUCT CONTEXT까지 전체 비교.
- `server/tests/test_mannequin_snapshot.py`: `test_regenerate_snapshots_top_women_slim_long_and_adjusted_axes`—직전 regular/basic, 요청 seller slim/long, snapshot과 `["fit","length"]`, analysis UI 저장을 단언.
- 같은 파일: generate auto/null snapshot, idempotency join 비덮어쓰기, worker가 payload slim/long을 최신 analysis over/crop보다 우선, null authoritative, legacy fallback, malformed snapshot 실패.
- hash 테스트: canonical profile hash와 실제 prompt hash 일치, raw profile/prompt 미포함, retry attempt별 prompt hash 변경.
- P0 육안 하네스는 `server/scripts/smoke_mannequin_fit_grid.py`로 추가하되 기존 `smoke_mannequin.py`의 production 렌더 경로, `--dry-run`, `server/ab_out` 패턴을 재사용한다.
- 8케이스: slim, over, crop/no-bottom, long/no-bottom, crop/with-bottom, long/with-bottom, slim+long/with-bottom(보고 사례), over+crop/with-bottom. PNG·contact sheet·ratings.csv를 만들고 사람 눈 평가가 정본, vision 강제선택은 보조 기록만 한다. 자동 pass/fail·배포 게이트로 쓰지 않는다.

## D6. Rollout

1. renderer/prompt + snapshot 소비/legacy fallback + 테스트를 먼저 배포하고 구 worker를 drain한다.
2. 이어 route snapshot 생산을 배포한다. rolling 중 구 worker가 새 잡을 잡아도 analysis write 덕분에 기존 동작은 유지되지만, drain 전에는 불변성만 보장되지 않는다.
3. DB migration·신규 env flag는 없다. `cd server && .venv/bin/pytest -q` 전체 통과 후 AWS에 배포한다.

프로덕션 sanity-check는 유료 생성·프로젝트 쓰기이므로 승인 후 시행한다. `f082cb9e`의 기존 A-2를 baseline으로 보존하고, 동일 `women/top`, `seller slim+long`, 동일 상품·흰 스커트 입력으로 새 A-3 한 장을 생성한다. 이미 저장값이 slim+long이면 `adjustedAxes=[]`가 정상이며, 강화된 일반 FIT PROFILE만으로도 다음을 만족해야 한다: 밑단 전체가 엉덩이 아래, untucked, 스커트에 가려지지 않음; 몸통은 slim이되 bodycon 아님; 랩·리본·색·패턴·마네킹·배경 보존. 새 step 이벤트의 두 hash와 `input_source=payload_snapshot`도 확인한다.

회귀 시 prompt/observable만 우선 revert하고 snapshot 관측성은 유지한다. 필요하면 route+worker까지 되돌려도 새 payload는 analysis fallback 가능한 형태이고 스키마 변경이 없어 DB rollback은 없다.

## 부록 (P1 skeleton)

전용 `mannequin_fit_qc.py`와 `mannequin_fit_qc_v1.txt`를 만든다. 입력은 `{productImages, generatedImage, effectiveFitProfile, adjustedAxes, hasMatchImage}`이며 profile 문구는 서버 카탈로그로만 렌더한다.

출력:

```json
{"identityPass":true,"axisPass":[{"axis":"length","target":"long","pass":true,"observedLandmark":"below hips","visible":true}],"undeclaredAxesPreserved":true,"mismatches":[],"correctionPrompt":null}
```

현재 `_run_candidate`의 AG-P2 위치—생성 직후, R2 저장 전—에 연결하고 `step/status=fit_qc`로 기록한다. 선언 축은 identity 비교에서 면제한다. `image_qc=shadow`는 로그만 남기고 첫 결과를 채택하며, `enforce`에서만 identity/axis/미선언축 실패를 기존 `mannequin_max_attempts=2` 안의 1회 correction retry로 연결한다. judge 오류는 fail-open, Pillow는 hard-shadow 유지한다.

Enforce 승격 조건은 카테고리×성별 5상품×극단축쌍×3반복+tuck 픽스처에서 선언 축 ≥90%, 정체성 ≥95%, 미선언 축 보존 ≥95%, 보고 long 3/3, 방향 단조성 ≥90%; 사람 gold 30쌍 대비 judge 일치 ≥90%·false-pass ≤5%와 prod shadow 50건 관찰까지 모두 충족하는 것이다.

## E. Claude 중간 점검 (2026-07-13)

**판정: 승인 + 조정 2건.**
1. **배포는 1 PR** — 설계 D6의 2단계(소비 먼저→생산 나중)는 롤링 혼재 논리로는 맞지만, 스냅샷 계약이 양방향 호환(신 워커+구 라우트=키 없음→legacy fallback / 구 워커+신 라우트=키 무시+analysis write 유지)이라 단일 PR·단일 배포로 안전. 커밋은 renderer/route/tests로 분리.
2. **adjustedAxes는 서버 산출 전용** — 프론트가 보낸 값이 있어도 무시하고 라우트가 "이전 정규화 프로필 vs 요청 정규화 프로필" diff로만 계산(D4 함의를 명시 규칙으로 승격).
그 외 D1~D5 그대로 구현. P1(축 인지 QC)은 부록 스키마대로 백로그.

## F. 실검증 (2026-07-13 — 머지 전 실이미지 확인, 사용자 요구)

실패 사례 프로젝트(f082cb9e)의 **실제 입력**(상품 정면 사진 + 매칭 하의 match_women_bottom_14 + 베이스 여성 마네킹)으로 새 프롬프트 실생성. Gemini 직접 3콜(잡·크레딧 경유 없음).

| arm | 지시 | 결과 (육안) |
|---|---|---|
| slim+long ×2 | 기장 롱 + 슬림 | **밑단 힙 아래 롱 실루엣 ✓** · untucked·스커트에 안 가려짐 ✓ · slim 몸판 ✓ · 정체성(로고·컬러·립) ✓ — 2회 일관 |
| slim+crop ×1 | 기장 크롭 (반대 방향) | **하이웨이스트 크롭 ✓** — 같은 입력에서 반대로 정확히 제어 |

**판정: 문제 축(기장) 미반영 → 양방향 제어로 전환 실증. tuck 가림도 해소(밑단 항상 노출).**
비고: 이 프로젝트의 정면 사진이 실패 당시(그레이 랩탑)와 다른 상품(레드 니트)으로 교체돼 있어 픽셀 동일 A/B는 아니나, 동일 프로젝트·매칭·프로필 조건에서 축 반영 여부는 명확. n=3 소표본 — 정식 픽스처(5상품×축쌍×3반복)는 P1 게이트.

## G. 전 카테고리 실생성 캠페인 (2026-07-13 — 36 arms, Codex ultra 설계 M1~M4 실행)

러너: `server/scripts/fit_fidelity_campaign.py` (--batch 1|2|3, --arms 재실행, --rejudge 판정격리). 생성 Gemini 3 Pro 1K 2:3 1콜/arm, 자동판정 `analyze_with_fallback`(M2 스키마), 실패 전수 + 통과 20% 사람 스팟체크. 원장: `server/ab_out/fit_campaign/results.jsonl` (프롬프트·출력 해시 + raw judge JSON).

### 결과 (사람 검증 반영, 44축)

| 카테고리 | 축 통과 | M3 기준 | 판정 |
|---|---|---|---|
| 상의 (T01–T08) | 8/8 | ≥7/8 | ✅ |
| 팬츠 (P01–P10) | 12/12 | ≥11/12 | ✅ (맨발 수정 후) |
| 스커트 (S01–S06) | 7/8 | ≥7/8 | ✅ |
| 드레스 (D01–D06, 프록시) | 8/8 | ≥7/8 | ✅ |
| 아우터 (O01–O06, 프록시) | 5/8 | ≥7/8 | ❌ |
| **합계** | **40/44 (91%)** | ≥40/44 | 전체 기준 충족·아우터만 미달 |

정체성 보존: 실상품 arm 24/24 통과. withBottom 비가림(T07·T08·O05·O06) 전부 통과 — L21 tuck 규칙 실증.

### 반복 이력 (M3 트리거 2회)

1. **맨발 트리거** (P04·P08 쌍실패): 바지 관측문구 4개+promptEn 3개가 신발을 랜드마크로 사용 ↔ 베이스 마네킹은 맨발 → 생성·판정 모두 만족 불가. Codex ultra 설계로 맨발 호환 문구 교체(발등 포개짐·발 대비 통 크기) + 템플릿 "barefoot 유지" 1줄 + 판정 "신발 요구 금지" 1줄. **재실행 P04·P08 2/2 통과, P10(wide+below_ankle 복합)도 신규 통과.**
2. **아우터 축소방향 트리거** (O03·O05 crop_short 쌍실패 + O01 slim): 확대방향(long·over)은 전부 통과, 축소방향만 실패 — 테일러드 재단 비례를 정체성으로 붙드는 실패 모드. Codex ultra 설계 (a)+조건부(c): outer slim/crop_short promptEn·관측문구에 "같은 옷의 재단 비례만 재봉제(이미 만족하면 보존)" 명시. **재실행: O03 통과 전환, O05 슬림 통과·크롭 경계선(하이힙 vs 미드힙), O01 방향 개선되나 미달.**

### 잔존 실패 4축 (전부 아우터·경계 포함)

- O01 fit=slim(오버 소스): 2회 생성 모두 릴랙스드 수준까지만 슬림화.
- O02 fit=over: 세미오버로 생성(판정 2회 일관, 사람 최초 정정 철회) — 극단 평균회귀.
- O05 length=crop_short: 하이힙 경계선(판정 fail, 육안 borderline).
- S05 silhouette=h_line(랩 플리츠 소재): 주름 구조가 미세 A라인 유발 — 의류 구조 vs 선언 충돌 사례.

**핵심 발견: 아우터(무거운 테일러드)는 핏·기장 극단이 양방향 모두 평균으로 회귀**하는 모델 한계. 프롬프트 2차 반복으로 크롭은 회복, 슬림·오버 극단은 잔존. 아우터 arm은 착용샷 프록시 입력(실상품 플랫 아님) — 실상품 재검은 P1 픽스처에서.

### 판정기 정확도 (n=41 판정)

오탐 1건 확정(P06 — 신발 조항, 수정 후 해소), 분쟁 1건은 판정기 승(O02). 강화 후 양성대조(O06 재판정) 유지. 자동판정을 P1 enforce 근거로 쓰지 않는다는 M4 원칙 유지.

## H. 편집 재시도 스파이크 (2026-07-14 — 아우터 잔존 실패 해소 실증)

가설: 실패 이미지를 입력으로 주고 **축 지시만 담은 편집 호출**을 하면, 정체성은 입력 이미지가 지고 지시 예산 전부가 형태 변경에 쓰인다. 스크립트 `server/scripts/spike_outer_edit_retry.py`.

| arm | 편집 지시 | 자동판정 | 육안 |
|---|---|---|---|
| O01 fit=slim | 어깨선 자연 어깨점 + 몸판·소매 여유 제거 | ✅ | 허리 셰이프 잡힌 슬림 ✓ |
| O02 fit=over | 어깨선 드롭 + 몸판·소매 볼륨 추가 | ✅ | 손 덮는 확실한 오버 ✓ |
| O05 length=crop_short | 스커트 허리선 위 자연 허리에서 밑단 종료 | ✅ | 크롭 재킷 ✓ |

3/3 통과·정체성(원단·단추·라펠) 보존·맨발/포즈/배경 유지. **결론: P1 축 인지 QC 재시도는 신규 생성이 아닌 실패 이미지 편집(retry-as-edit)으로 구현** — 편집형은 3차 프롬프트 반복(수확체감 확인)과 달리 극단 축을 직접 해소한다. 이 방식이면 캠페인 기준 44/44 도달. 재시도는 QC 미달 시에만 발화(+1콜).

부기: 신발 신은 베이스로 교체 검토 → 불채택. 맨발 문구 수정으로 바지 12/12 이미 회복, 신발은 스타일링 주장·브랜드 리스크·구매자 오인(판매상품으로 오해) + 전 카테고리 재검증 비용만 추가.

## I. P1 축 QC + 편집 교정 구현·실증 (2026-07-14 — Codex ultra 설계 → Claude 구현, 사용자 승인 트랙)

구현(브랜치 fix/mannequin-fit-fidelity, 테스트 354 전부 그린):
- `app/agents/mannequin_fit_qc.py` + `prompts/mannequin_fit_qc_v1.txt` — 선언 축 전용 판정(카탈로그 허용목록·정확 커버리지 강제·correctionPrompt 없음), 편집 지시 조립(고정 템플릿 10종 + §H 불변 꼬리).
- `MANNEQUIN_AXIS_QC=off|shadow|enforce` (기본 off) + **코드 가드 `_MANNEQUIN_AXIS_QC_ENFORCEMENT_READY=False`** — enforce 설정돼도 가드 전까지 shadow 강등, env/요청/CLI 우회 불가(G9 규율). 정식 게이트(픽스처·골드30·prod shadow 50) 통과 후 리뷰된 코드 변경으로만 해제.
- 워커 통합: 게이트 통과 채택본에 판정 → 실패+enforce 시 실패 이미지 1장 편집 1회(생성+편집 ≤ max_attempts 공유 예산) → 재판정 → **전 선언 축 통과+정체성 유지일 때만 편집본 채택**, 아니면 원본. 모든 인프라 실패 fail-open. 이벤트 `axis_qc`/`axis_retry`(해시만, 원문 미포함). 크레딧 불변(charge=reserved).
- 테스트 28개 신규(`test_mannequin_fit_qc.py`·`test_mannequin_axis_qc.py`): 모드·가드·예산·선점·fail-open·이벤트 위생·채택 규칙.

실증(`scripts/prove_mannequin_axis_qc_retry.py`, run-20260713-195759, 전 콜 1K 강제·페어드 리플레이·prod 자원 무접촉):
| arm | shadow | enforce | 증명된 경로 |
|---|---|---|---|
| O01 slim | fail(오버 잔존) | **편집 1회 → 전 축 통과 → 편집본 채택** | 핵심 메커니즘 완주(육안: 슬림 교정+정체성 보존) |
| O05 slim+crop | pass(1차 재현 안 됨) | 편집 미발화(not_needed) | 통과 시 무개입 |
| O06 over+long | pass→리플레이 fail(경계선) | 편집 발화→개선 못함→**원본 유지** | 보수적 선택(출력 무손상) |
| O02 over | fail→리플레이 pass(경계선) | 미발화 | 판정 비결정 기록 |

외부 이미지 콜 5(원본 4+편집 1), 편집 입력=실패 원본 1장 검증, 가드 복원 확인. **관찰**: ① §G 2차 반복 문구 이후 outer slim/crop 1차 통과율이 실제로 올라감(O01 직전 2연속 통과, O05 이번 통과), ② 경계선 아우터 핏의 판정 비결정 실존 — enforce 승격 전 골드셋 캘리브레이션 게이트가 필수인 실증 근거. 이 포커스드 실증은 재시도 메커니즘 증명이며 정식 승격 게이트를 대체하지 않는다(가드 유지).
