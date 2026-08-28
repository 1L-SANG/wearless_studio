# FaceMarket 실제 모델 체형·키 반영 컷 생성 — 설계

**작성일:** 2026-08-28
**상태:** 초안 (사용자 리뷰 대기)
**범위:** FaceMarket 실제 모델 등록에 체형·키 입력(#2)과 그 값을 반영한 컷 생성(#3). 프로필 사진(#1)은 별도 PR #193으로 이미 처리됨.

---

## 1. 목표

실제 모델은 저마다 키·체형이 다르다. 지금 FaceMarket 모델은 **얼굴만** 앵커되고(등록 사진 3장 → face_front + sedcard) 몸은 컷 생성 시 매번 새로 생성돼, 같은 모델이라도 컷마다 체형이 흔들린다. 모델이 등록 시 자신의 **키 구간·체형**을 고르면, 이후 그 모델로 만드는 착장 컷이 그 체형을 일관되게 반영하도록 한다.

**성공 기준:** 등록 위저드에서 키·체형을 선택 → 모델에 저장 → 셀러가 그 모델로 컷을 생성하면 프롬프트에 체형·키가 주입되어 결과에 반영된다. 얼굴 identity는 그대로 보존된다.

---

## 2. 확정된 결정 (사용자)

1. **입력 방식:** 구간 버킷 선택(슬라이더/핫존 아님). 성별별 키 구간 + 체형 프리셋.
2. **성별:** OACX 신원확인에서 **자동 유도**(수동 선택 없음). 신원확인 시 모델이 생성되므로 그때 gender를 세팅한다. physique 스텝은 키·체형만.
3. **반영 방식:** cut_generator 프롬프트에 텍스트 주입. FaceMarket 모델은 몸이 생성물(얼굴만 앵커)이라 얼굴 보존과 충돌 없음.
4. **적용 시점:** 앞으로 생성되는 컷만. 기존 컷/자산 재생성은 안 함.

---

## 3. 아키텍처 개요

기존 대표 이미지(cover)의 데이터 흐름을 그대로 미러한다:

```
[등록 위저드]  체형·키 버킷 선택
      │  POST /enrollments/{id}/physique
      ▼
[fm_biometric_enrollments]  gender / height_bucket / body_type  (스테이징)
      │  등록 완료(finalization) 시 승격 — cover_image_url 승격과 같은 지점
      ▼
[fm_models]  gender / height_bucket / body_type  (모델 영구 속성)
      │  셀러가 이 모델로 컷 생성
      ▼
[컷 생성 파이프라인]  모델 속성을 프롬프트 컨트랙트에 "body identity" 절로 주입
      ▼
[생성 이미지]  얼굴 = 앵커 보존, 몸 = 선택한 체형·키 반영
```

세 층(입력·저장·반영)이 각각 독립적으로 테스트된다.

---

## 4. 데이터 모델

### 4.1 마이그레이션 (supabase/migrations)

모두 nullable(선택 입력 — 안 골라도 등록 진행).

- `fm_models`: `gender text`, `height_bucket text`, `body_type text` — gender는 identity 단계, 나머지는 physique 승격.
- `fm_biometric_enrollments`: `height_bucket text`, `body_type text` — physique 스테이징(gender는 모델 소유라 여기 없음).

CHECK 제약으로 허용값을 못박는다(§4.2). NULL 허용(미입력). 기존 행은 NULL로 남는다(하위호환).

### 4.2 허용 어휘 (enum via CHECK)

**gender:** `male` | `female` — **OACX 신원확인에서 자동**. `identity_verify`가 CX 응답에서 성별 필드를 추출(`_dig(trans, "gender"/"sex"/"sexCode"/...)` — 정확한 CX 필드명은 구현 시 실응답으로 확정)해 모델 생성 시 `fm_models.gender`에 세팅한다. 사용자 선택 없음. (CX가 성별을 안 주면 gender NULL → 키 버킷은 남녀 통합 목록으로 폴백.)

**height_bucket** (성별 접두사로 구간 인코딩):
- male: `m_lt170` · `m_170_175` · `m_175_180` · `m_180_185` · `m_185_190` · `m_gte190`
- female: `f_lt155` · `f_155_160` · `f_160_165` · `f_165_170` · `f_170_175` · `f_gte175`

**body_type** (7종 프리셋, 사용자 확정):
- `delicate` (여리여리) · `slim` (마름) · `regular` (보통) · `plump` (통통) · `toned` (잔잔한 근육) · `bulk` (벌크업) · `glamorous` (글래머러스)

값→표시 라벨·프롬프트 문구 매핑은 서버 상수 한 곳(§6 참조)에서 소유한다. 코드에 문장을 흩뿌리지 않는다(personalization.py 검증 관례 재사용).

### 4.3 검증

- height_bucket 접두사와 gender 일치 필수(`m_*`는 male에만). 불일치 → 400 `invalid_physique`.
- 셋 다 all-or-nothing 아님: 각 필드 독립 선택 가능(부분 입력 허용). 단 height_bucket을 주면 gender 필수(버킷이 성별 종속).

---

## 5. 등록 흐름 변경 (#2)

### 5.1 프론트 위저드 (ModelRegister.jsx)

- 현재 스텝: identity → photos → **profile(대표 이미지, STEP 4/6)** → liveness → complete.
- **새 스텝 "체형·키"**를 **photos 다음, profile(대표 이미지) 앞**에 삽입한다: identity → photos → **physique** → profile → liveness → complete. 총 스텝 수 6→7, STEP 라벨 재계산. 선택 스텝(건너뛰기 가능, 대표 이미지와 동일 톤).
- UI: 성별은 **이미 신원확인에서 유도됨** → 토글 없음. 모델 gender에 맞는 키 구간 버킷(라디오/칩) + 체형 프리셋 칩. gender NULL이면 남녀 통합 버킷. 모두 큰 터치 타깃.
- 제출: `POST /enrollments/{id}/physique` (프로필 이미지 업로드와 같은 패턴). Body: `{ heightBucket?, bodyType? }` (gender 없음). 성공 시 다음 스텝.
- **테스트 안정성:** 기존 훅 순서(useState/useEffect 위치 인덱스)에 걸린 테스트가 있으므로([[facemarket-biometric-shipped]] 관례), 새 state/effect는 **마지막에** 추가한다.

### 5.2 백엔드 API

- **gender 캡처:** `identity_verify`(facemarket.py ~270 fields, 291 모델 insert)에서 CX 응답의 성별을 추출해 `fm_models.gender`에 세팅(모델이 여기서 생성됨). fields JSON에도 남긴다(감사·복원용).
- **physique 라우트:** 새 라우트 `POST /enrollments/{enrollment_id}/physique` (facemarket_enrollment.py). Body: `{ heightBucket?, bodyType? }`. §4.2/§4.3 검증(gender는 모델에서 읽어 버킷 접두사 일치 확인). `fm_biometric_enrollments`에 저장. 상태 전이 없음(SFace/QC 없음 — profile-image 라우트 미러).
- `EnrollmentView` 응답에 physique 필드 + 모델 gender 추가(위저드가 재로드 시 선택값 복원 + 버킷 목록 필터 — 대표 이미지 유실 방지 관례와 동일).
- **승격:** finalization 지점(facemarket_enrollment.py ~2010, cover 승격 옆)에서 enrollment의 height_bucket·body_type를 `fm_models`로 복사(gender는 이미 identity 단계에서 세팅됨). cover 승격과 같은 트랜잭션.

### 5.3 모델 응답 노출

- 모델 카드/상세 응답(`_MODEL_CARD_COLS` 등)에 physique 노출은 **선택**(셀러 UI에 "이 모델 키/체형" 표시하려면). 우선 범위: 컷 생성이 읽을 수 있게 서버 내부에서 접근 가능하면 충분. 카드 노출은 후속 가능.

---

## 6. 컷 생성 반영 (#3)

### 6.1 주입 지점

컷 생성 프롬프트는 `agents/confirmed_gpt_prompt.py`(compile_confirmed_gpt_prompt) + `agents/cut_plan.py`(compile_cut_plan / render_prompt_contract)에서 조립되고, **명시적 "소유권 계약"** 구조를 이미 가진다: 한 파트가 "모델의 **얼굴** identity를 소유하고 pose·person·**body**·garment identity는 소유하지 않는다"고 선언한다. 즉 **body identity가 현재 무주공산**(생성물)이다 — 여기에 모델의 체형·키를 넣는다.

### 6.2 데이터 전달

- 컷 생성이 FaceMarket 모델로 트리거될 때(셀러 에디터), 모델의 `gender`/`height_bucket`/`body_type`를 로드해 생성 요청 컨텍스트에 실어 프롬프트 컴파일러까지 전달한다.
- 컴파일러는 값이 있으면 **"body identity" 절**을 프롬프트에 추가한다. 예:
  `Subject build (consistent across cuts): approx 180–185cm height, athletic build.`
- 값→문구 매핑은 §4.2 서버 상수와 같은 소스. 얼굴 앵커(identity_source)와 **독립** — 얼굴은 그대로.

### 6.3 게이팅·범위

- **착장 컷(worn cuts)에만** 주입(상품 ghost/flat-lay 등엔 사람 몸 없음 → 미적용).
- 값 없는 모델(구 모델·미입력) → 절 생략 → 현행 동작 유지(무해).
- **앞으로 생성분만.** 기존 자산·컷 재생성 없음. 재렌더 트리거 없음.

### 6.4 일관성 효과

지금은 얼굴만 앵커라 컷마다 몸이 흔들린다. body identity 절이 체형·키를 못박아 **모델 간·컷 간 일관성**을 높이는 부수 효과. QC 파이프라인은 얼굴 기준이라 이 변경과 독립(체형 절은 프롬프트 텍스트일 뿐, QC 게이트 신설 없음).

---

## 7. 에러 처리·엣지 케이스

- **미입력 등록:** 3필드 NULL로 저장·승격. 컷 생성은 절 생략. 완전 하위호환.
- **성별/버킷 불일치:** 400 `invalid_physique` (API 검증). 프론트는 성별 선택에 따라 버킷 목록을 필터링해 애초에 불가능하게.
- **재등록/재검증:** physique는 등록에 실려 승격되므로, 재등록 시 다시 선택(또는 이전 모델 값 프리필 — 후속).
- **기존 모델(이미 verified):** NULL physique → 현행 컷 생성 유지. 소유자가 나중에 수정하는 경로는 이 스펙 범위 밖(후속: 모델 편집).

---

## 8. 테스트 전략

- **마이그레이션:** 컬럼·CHECK 제약 존재, 기존 행 NULL 허용.
- **API(physique 라우트):** 유효 조합 저장, 성별/버킷 불일치 400, 부분 입력 허용, EnrollmentView 복원.
- **승격:** finalization이 enrollment physique를 fm_models로 복사(cover 승격 테스트 미러).
- **프롬프트 주입(단위):** 모델 속성 → compile 결과 프롬프트에 body identity 절 포함/생략(값 유무), 얼굴 앵커 절 불변.
- **프론트:** 위저드 새 스텝 렌더·제출, 훅 순서 회귀 없음(기존 test 그대로 green).
- **회귀:** facemarket 전체 스위트 green 유지.

---

## 9. 범위 밖 (후속)

- OACX 성별 프리필.
- 모델 카드/상세에 physique 표시.
- 등록 후 모델 physique 편집 화면.
- 기존 컷 재생성/재렌더.
- 슬라이더/미세조정 UI(YAGNI — 버킷으로 시작).
- 몸무게·사이즈 등 추가 측정.

---

## 10. 파일 요약 (구현 대상)

- `supabase/migrations/<new>.sql` — fm_models·fm_biometric_enrollments 3컬럼 + CHECK.
- `server/app/facemarket_enrollment.py` — physique 라우트, EnrollmentView 필드, finalization 승격.
- `server/app/facemarket_physique.py` (신규 소량) — 버킷/체형 어휘·라벨·프롬프트 문구 상수 + 검증(단일 소스).
- `server/app/agents/confirmed_gpt_prompt.py` / `cut_plan.py` — body identity 절 주입(값 전달 배선 포함).
- `src/features/model/ModelRegister.jsx` (+ module.css) — 체형·키 스텝.
- `src/lib/api/facemarket.js` — physique 제출 API.
- 테스트: `server/tests/test_facemarket_*` + `tests/frontend/facemarket-*`.
