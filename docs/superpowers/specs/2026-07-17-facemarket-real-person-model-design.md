# 실존 인물 디지털화 — 가상모델 fork (설계)

- 날짜: 2026-07-17
- 출처: `대표님 handoff (facemarket-handoff.html)` — "실존 인물 디지털화는 가상모델의 fork다"
- 계약 정본: `documents/ai_agent_modules.md §3 AG-06`, 기존 FaceMarket `facemarket.py`/`facemarket_chain.py`
- 참조 구현(완성·병합됨): 가상모델 파이프라인 (`dd30c69`, cut_generator `resolve_virtual_model_assets`)

## 1. 목표 (Definition of Done)

실존 인물 1명이 다음 흐름을 끝까지 통과하면 완료:

```
본인확인 통과 → 실제 사진 업로드 → 그리드 자산 생성
  → 비공개 버킷 + manifest → ArcFace QC 통과
  → 셀러가 모델 선택 → 본인 얼굴로 옷 컷 생성
```

이 중 **그리드 자산 생성 / 비공개 버킷+manifest / ArcFace QC** 3구간이 미배선 = 이번 작업.
앞뒤(본인확인·얼굴 업로드·컷 생성 슬롯)는 이미 존재하며 재사용한다.

## 2. 확정된 설계 결정

| 결정 | 선택 | 근거 |
|---|---|---|
| 방향 | 실존인물을 가상모델의 fork로 = 1급 MODEL | handoff 제목·thesis |
| 그리드 소스 | **업로드 3장 합성(생성 X)** | handoff §03 "얼굴 새로 생성 금지". PIL 크롭·배치. 100% 본인, 크레딧 0 |
| QC 방식 | **실제 ArcFace (insightface/onnxruntime)** | 법적 스토리 강함. 3장 pairwise 동일인 게이트 |
| 빌드 트리거 | **명시적 엔드포인트/버튼** | 제어 명확, 재시도/멱등 단순 |
| 자산 저장 | **새 테이블 `fm_model_assets`** | 정규화·다중뷰·쿼리 깔끔 |
| 스코프 | 로컬 완주 + **배포 반영(manifest 의존성/mem/weights)** | |

## 3. 현재 코드 지형 (조사 결과)

- **가상모델 그리드 resolve**: `cut_generator.resolve_virtual_model_assets(spec)` → `server/app/data/virtual_models.json` → `(face_front, grid_sedcard)` `{key, mime}` 반환. 키는 **공개 seed 버킷**. modelId는 `analysis.selectedModelId`로 주입.
- **바이트 로드**: `detail_page_job._r2_img` / `editor_image_job` 모두 `app.state.r2`(공개 메인 버킷) **하드코딩**. ← 버킷 인지로 바꿔야 하는 지점.
- **라이선스 얼굴(기존 step03)**: `project.facemarket_license_id` → `r2_face`(비공개) 단일 얼굴 → worn 컷 주입. `detail_page_job` L47~98.
- **실존 얼굴 업로드**: `personalization.upload_face_photo` → `personalization_face_photos(profile_id, angle∈{front,side,angle45}, r2_key, mime_type)` 비공개 버킷. 이미 존재.
- **모델 카탈로그**: `fm_models(id, user_id, display_name, status, ci_hash, did, chain_ref, cover_image_url)`. `facemarket.list_models`가 피드. **그리드 자산 컬럼 없음**.
- **링크**: `fm_models` ↔ `personalization_profiles`는 **`user_id`**로 연결(둘 다 `auth.users` 참조). `fm_identity_verifications.model_id → fm_models.id`.

## 4. 아키텍처 — 3 컴포넌트

### C1. 실존 모델 자산 빌드 (그리드 합성 + 비공개 저장 + 등록)

- **API**: `POST /v1/facemarket/models/me/build-assets` (Bearer, 모델 본인).
  - 게이트: 호출자 user_id의 `fm_models(status='verified')` 존재 + `personalization_face_photos` 3각도 완비. 미충족 시 4xx(명확 메시지).
  - 멱등: 진행 중 빌드 잡 있으면 그 jobId 반환(중복 큐잉 금지). 성공 자산 있으면 재빌드는 명시적 재요청으로만.
  - 잡 큐잉: `kind='fm_model_asset_build'`, `payload={modelId, profileId, faceKeys?}` (얼굴 R2 키는 payload 미포함 원칙 — 워커가 DB 재조회). `202 {jobId}`.
- **워커**: `server/app/workers/fm_model_asset_job.py`
  1. user_id로 `fm_models` + `personalization_face_photos`(front/side/angle45) 로드. `r2_face`에서 3장 bytes 로드(로그·이벤트 미포함).
  2. **ArcFace QC (C2)** — 실패 시 잡 error, 자산 미등록.
  3. **그리드 합성** (PIL): 3장을 정사각 크롭 → 2×2 그리드(4번째 칸=front 반복 또는 side 변형)로 배치, `grid_sedcard.png`. `face_front` = front 원본(webp).
  4. **비공개 저장**: `r2_face.put_bytes` → `facemarket/models/{model_id}/grid_sedcard.png`, `.../face_front.webp`. 공개 버킷 폴백 금지.
  5. **등록**: `fm_model_assets`에 view별 upsert + `fm_models.assets_status='ready'`, `qc_score` 기록. 원자 tx.
  - 진행/종결 이벤트는 상태 enum·카운트만(PII 하드룰 §1.4).

### C2. ArcFace QC 게이트

- **모듈**: `server/app/agents/face_qc.py`
  - insightface(`FaceAnalysis`, onnxruntime CPUExecutionProvider). 3장 각각 임베딩 추출 → **pairwise 코사인 유사도**. 모든 쌍 ≥ 임계면 통과(동일인). 하나라도 미달 → `QcFailed(score)`.
  - 얼굴 미검출/다중검출 처리: 검출 실패 시 QC 실패(등록 차단), 다중이면 최대 얼굴 선택.
  - 반환: `qc_score`(최소 pairwise) — `fm_models.qc_score` 기록.
- **config** (`config.py`): `fm_face_qc_enabled`(bool, dev off 허용), `fm_face_qc_threshold`(float, 기본 0.35), `fm_face_qc_model`(pack 이름, 기본 `buffalo_l`).
  - `enabled=false`면 QC 스킵(shadow/dev), 단 로그에 score 남김.
- **모델 weights**: Docker 이미지 빌드 시 번들(런타임 다운로드 금지 — 네트워크 의존 제거). §6 배포 참조.

### C3. 컷 생성 resolve + 버킷 인지 로드 + 라이선스 게이트

- **resolve 확장**: `cut_generator.resolve_virtual_model_assets(spec, conn=None)` (또는 신규 `resolve_model_assets`):
  - modelId가 자산 등록된 `fm_models.id`(= `fm_model_assets` 존재)면 → refs에 `{key, mime, bucket:"face"}` 반환.
  - 아니면 virtual_models.json 폴백 → `{key, mime, bucket:"public"}`.
  - **반환 스키마에 `bucket` 필드 추가** (기존 호출부 3곳 갱신).
  - DB 접근이 필요하므로 async화 또는 워커에서 미리 조회 후 주입. → 워커가 fm_model_assets를 미리 로드해 spec에 실어주는 방식으로 cut_generator 순수성 유지 검토(계획 단계 확정).
- **바이트 로더 버킷 인지**: `detail_page_job._r2_img` / `editor_image_job` 모델 ref 로드가 `ref["bucket"]`에 따라 `app.state.r2_face` vs `app.state.r2` 선택.
- **라이선스 활성 게이트**: 실존 모델(`fm_models`) 주입 전 `fm_licenses` 활성 라이선스 확인 → 없으면 **미주입**(무라이선스 실얼굴 컷 차단). 가상모델은 게이트 없음(기존 유지).
- **카탈로그**: `list_models`가 `fm_models.assets_status='ready'` + verified만 셀러 선택 가능으로 노출(has_assets 플래그). 자산 없는 verified 모델은 "준비 중".

### 통합: 얼굴 이중주입 방지

기존 `facemarket_license_id → 단일 얼굴` 경로와 C3 그리드 경로가 같은 컷에 얼굴을 두 번 넣을 위험.
**해소 원칙**: 셀러 선택(`selectedModelId=fm_models.id`)이 아이덴티티를 몰고 → **그리드+face_front 주입 + 라이선스 게이트**로 통일. 기존 단일주입은 그리드 자산 없는 모델용 폴백으로 강등. 정밀 배선은 계획 단계에서 확정(회귀 테스트로 이중주입 0 검증).

## 5. 데이터 모델 (마이그레이션 1개)

```sql
-- fm_model_assets: 실존 모델의 아이덴티티 자산(비공개 버킷). 얼굴=생체 PII.
create table if not exists public.fm_model_assets (
  model_id   uuid not null references public.fm_models(id) on delete cascade,
  view       text not null check (view in ('face_front','grid_sedcard')),
  r2_key     text not null,          -- 비공개 버킷 키. API 응답 미노출.
  mime       text not null,
  bucket     text not null default 'face' check (bucket in ('face','public')),
  created_at timestamptz not null default now(),
  primary key (model_id, view)
);

alter table public.fm_models
  add column if not exists assets_status text not null default 'none'
    check (assets_status in ('none','building','ready','failed')),
  add column if not exists qc_score numeric(4,3);  -- 최소 pairwise 코사인 유사도
```

## 6. 배포 반영 (스코프에 포함)

- `server/Dockerfile`: `insightface`, `onnxruntime`(CPU) 설치 + 모델팩(`buffalo_l`) **빌드타임 다운로드**로 이미지 번들. 런타임 네트워크 의존 제거.
- `copilot/api/manifest.yml`:
  - `memory` 상향 검토(현 1024 → onnx+모델팩 상주 시 부족 가능. 2048 권장, 계획 단계에서 실측).
  - `variables`: `FM_FACE_QC_ENABLED: "true"`, `FM_FACE_QC_THRESHOLD: "0.35"`, `FM_FACE_QC_MODEL: buffalo_l`.
- **로컬 우선**: 로컬에서 3 컴포넌트 완주 검증 후 배포. push/merge는 사용자 명시 요청 시에만(local-verify-then-deploy).

## 7. PII 하드룰 (기존 준수)

- 얼굴 바이트·임베딩·비공개 R2 키·공개/서명 URL: payload·이벤트·로그·API 응답 **미포함**.
- 그리드/face_front는 **`r2_face` 비공개 전용**, 인증 게이트 서빙만. 공개 도메인 미연결.
- 잡 이벤트엔 상태 enum·지연·카운트·qc_score만.

## 8. 테스트

- 단위: 그리드 합성(PIL 입출력 크기·칸 배치), ArcFace QC(임베딩 mock → 임계 게이트 통과/차단), resolve 버킷 태깅, 라이선스 게이트, list_models has_assets 필터.
- 통합: 얼굴 3장 → build-assets → QC 통과 → fm_model_assets 등록 → 셀러 컷 생성에서 그리드 주입 + 이중주입 0.
- 회귀: 가상모델 경로 무영향(공개 버킷 로드·게이트 없음 유지).

## 9. 아웃 오브 스코프

- Gemini 그리드 재생성(합성으로 대체).
- Apple/Samsung 월렛 카드(인증·파트너 게이트).
- 온체인 정산 변경(기존 `facemarket_chain` 유지).
- 신체 프로필 기반 전신 생성 고도화(경로 α는 별개 유지).

## 10. 리스크

- **insightface/onnx 무게**: 이미지 크기·mem. → 번들 + mem 상향, 실측.
- **얼굴 이중주입**: §4 통합 원칙 + 회귀 테스트로 차단.
- **resolve async화**: cut_generator 순수성. → 워커 선조회 주입으로 회피 검토.
- **로컬 insightface 설치**: onnxruntime 휠·모델팩 캐시(`~/.insightface`). 로컬 검증 시 1회 셋업.
