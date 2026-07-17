# 실존 인물 디지털화 — 가상모델 fork (설계 v2)

- 날짜: 2026-07-17
- 출처: `대표님 handoff (facemarket-handoff.html)` — "실존 인물 디지털화는 가상모델의 fork다"
- 계약 정본: `documents/ai_agent_modules.md §3 AG-06`, 기존 FaceMarket `facemarket.py`/`facemarket_chain.py`
- 참조 구현(완성·병합됨): 가상모델 파이프라인 (`dd30c69`, cut_generator `resolve_virtual_model_assets`)
- **v2**: codex 독립 리뷰(2026-07-17, GATE FAIL) 블로커 반영 — 아이덴티티-소스 상태머신, 워커 런타임 컨텍스트, 상업가능 weights, R2/DB staging, 라이선스 in-flight 정책.

## 1. 목표 (Definition of Done)

실존 인물 1명이 다음 흐름을 끝까지 통과하면 완료:

```
본인확인 통과 → 실제 사진 업로드 → 그리드 자산 생성
  → 비공개 버킷 + manifest → 얼굴 대조 QC 통과
  → 셀러가 모델 선택 → 본인 얼굴로 옷 컷 생성
```

미배선 = **그리드 자산 생성 / 비공개 버킷+manifest / 얼굴 대조 QC / 컷 resolve+게이트**.
앞뒤(본인확인·얼굴 업로드·라이선스·컷 슬롯·배지·정산)는 존재하며 재사용.

## 2. 확정된 설계 결정

| 결정 | 선택 | 근거 |
|---|---|---|
| 방향 | 실존인물을 가상모델의 fork = 1급 MODEL | handoff |
| 그리드 소스 | **업로드 3장 합성(생성 X)** | handoff §03 "얼굴 새로 생성 금지" |
| QC 엔진 | **OpenCV SFace ONNX (Apache-2.0)** + YuNet 검출기(Apache-2.0) | codex [P1]: insightface buffalo_l = 비상업 weights. SFace는 상업가능·경량(~37MB)·opencv-contrib 내장 |
| QC 규칙 | 3장 pairwise 코사인 동일인 게이트, 미달 시 등록 차단 | 합성 소스라 핵심=스푸핑 방지(남의 얼굴 섞기) |
| 빌드 트리거 | **명시적 엔드포인트/버튼** | 제어·멱등 단순 |
| 자산 저장 | **새 테이블 `fm_model_assets`** (+ RLS 서비스 전용) | 정규화·다중뷰 |
| 아이덴티티 주입 | **워커 런타임 컨텍스트**(비직렬화) — spec 주입 금지 | codex [P1]: private key가 payload/log/event로 누출 |
| 스코프 | 로컬 완주 + 배포(Dockerfile·**mem 2048**·weights pin) | codex [P1]: 1GB 비현실 |

## 3. 현재 코드 지형

- **가상모델 resolve**: `cut_generator.resolve_virtual_model_assets(spec)` (SYNC+`@lru_cache`) → `virtual_models.json`(공개 seed 키) → `(face_front, grid_sedcard)` `{key,mime}`. modelId=`analysis.selectedModelId`.
- **바이트 로드**: `detail_page_job._r2_img`/`editor_image_job` 모두 `app.state.r2`(공개) 하드코딩.
- **라이선스 얼굴(step03·있음)**: `project.facemarket_license_id` → `r2_face`(비공개) 단일 얼굴 → worn 컷. `wants_face()` true인 컷에만.
- **불변식(codex 확인)**: 컷마다 아이덴티티 소스를 **정확히 하나**만 선택(라이선스 face_ref OR 가상 (face_front,grid)). **절대 결합 안 함.**
- **실존 얼굴 업로드**: `personalization.upload_face_photo` → `personalization_face_photos(profile_id, angle∈{front,side,angle45}, r2_key, mime_type)` 비공개.
- **카탈로그**: `fm_models(id,user_id,display_name,status,ci_hash,did,chain_ref,cover_image_url)`. 자산 컬럼 없음.
- **링크**: `fm_models` ↔ `personalization_profiles`는 **`user_id`**로만. `fm_identity_verifications.model_id→fm_models.id`. (현재 1 user=1 profile이나 스키마상 복수 가능 → §7 가드.)

## 4. 아키텍처 — 컴포넌트

### C1. 실존 모델 자산 빌드 (합성 + 비공개 저장 + 등록)

- **API**: `POST /v1/facemarket/models/me/build-assets` (Bearer, 모델 본인).
  - 게이트: user_id의 `fm_models(status='verified')` + `personalization_face_photos` 3각도 완비. 미충족 4xx.
  - **멱등/동시성**(codex [P1]): `jobs`에 `kind='fm_model_asset_build'` + model_id 부분 유니크 인덱스(status in running/queued) → 동시 빌드 1개. 진행 중이면 그 jobId 반환.
  - **payload에 얼굴 R2 키·바이트 미포함**(codex [P1]) — `{modelId}`만. 워커가 DB 재조회.
- **워커**: `server/app/workers/fm_model_asset_job.py`
  1. user_id로 `fm_models` + `personalization_face_photos` 로드. `r2_face`에서 3장 bytes 로드(로그·이벤트 미포함).
  2. **얼굴 대조 QC (C2)** — 실패 시 잡 error, 자산 미등록.
  3. **그리드 합성**(PIL): 3장 **타이트 얼굴 크롭·중립 배경·라벨 없음** → 2×2. **4번째 칸=중립**(front/side/angle45 + 여백 or 축소 전신, 포즈 복제 유발 회피). `grid_sedcard.png`. `face_front`=front 원본(webp).
  4. **R2/DB staging**(codex [P1] — 오브젝트 스토리지는 tx 밖):
     - (a) staged 키(`.../staging/`)로 `r2_face.put_bytes` → (b) DB tx로 최종 등록(`fm_model_assets` upsert + `fm_models.assets_status='ready'`, `qc_score`) → (c) commit 성공 후 staged→final 확정 or 실패 시 staged 오브젝트 cleanup. lease 펜스·재시도 시 orphan 정리(개인화 워커 선례).
  - 진행/종결 이벤트=상태 enum·카운트만.

### C2. 얼굴 대조 QC 게이트 (OpenCV SFace)

- **모듈**: `server/app/agents/face_qc.py`
  - `cv2.FaceDetectorYN`(YuNet)로 각 사진 얼굴 검출(다중=최대 얼굴, 0개=QC 실패), `cv2.FaceRecognizerSF`(SFace)로 정렬+임베딩 → 3장 **pairwise 코사인**. 모든 쌍 ≥ 임계면 통과. 미달→`QcFailed(score)`.
  - 반환 `qc_score`(최소 pairwise) — 민감 생체 파생 → `fm_models.qc_score`에만, 응답·로그 집계 외 노출 금지.
  - storage/cv2 예외 sanitize(키·경로 누출 차단).
- **config**: `fm_face_qc_enabled`(dev off 허용, off면 score만 로그), `fm_face_qc_threshold`(SFace 코사인 기준, 캘리브 전 잠정 0.363=opencv 권장선), `fm_face_qc_model_path`.
- **weights**: OpenCV Zoo `face_recognition_sface`(Apache-2.0) + `face_detection_yunet`(Apache-2.0). Docker 빌드 시 **pin+checksum**으로 번들(런타임 다운로드 금지). 상업 재배포 가능.

### C3. 컷 생성 — 단일 아이덴티티-소스 상태머신 + 버킷 인지 + 라이선스 게이트

**핵심(codex [P1]): 컷당 아이덴티티 소스 1개 불변식 유지.** 잡 시작 시 **한 번** 선택:

```
선택 우선순위(잡 레벨, 컷 루프 전 1회):
  REAL_ASSETS        ← selectedModelId == license.model_id ∧ fm_model_assets ready ∧ 라이선스 활성(§게이트)
  VIRTUAL_ASSETS     ← selectedModelId가 virtual_models.json 항목 (라이선스 불요)
  LEGACY_LICENSED_FACE ← 실자산 선택 없음 ∧ facemarket_license_id 단일 얼굴만 존재(기존 step03 폴백)
  NONE               ← 위 다 아님 (얼굴 없이 생성)
  REJECTED           ← 실자산 대상인데 라이선스 실패 → 조용한 폴백 금지, 컷 생성 거부
```

- **버킷 인지**: resolve refs에 `bucket`(`face`|`public`) 추가. 로더(`detail_page_job`/`editor_image_job` 모델 ref)가 `bucket` 따라 `r2_face` vs `r2`.
- **resolve seam**(codex [P1]): `@lru_cache` sync resolve는 **불변 가상 JSON 전용 유지**. 실자산은 **워커가 preload**: 잡 시작 시 `fm_model_assets`+라이선스 상태를 **비직렬화 런타임 컨텍스트**(dict, spec 아님)로 1회 로드 → resolved refs를 generator에 전달. private 키는 spec·payload·event 미진입.
- **라이선스 게이트 2회**(codex [P1]): (1) private 자산 로드 전, (2) 외부 추론/저장 직전. in-flight 해지: 추론 전 해지→나머지 컷 중단, publish 전 해지→저장·게시 차단+임시 산출물 정리. 라이선스 id/version·확인 시각을 비-PII 감사기록.

### 공유 계약

detail-page·editor 두 워커가 **같은 아이덴티티-소스 상태머신·resolve 컨텍스트**를 쓰도록 단일 헬퍼로 추출(중복 배선 방지).

## 5. 데이터 모델 (마이그레이션 1개)

```sql
create table if not exists public.fm_model_assets (
  model_id   uuid not null references public.fm_models(id) on delete cascade,
  view       text not null check (view in ('face_front','grid_sedcard')),
  r2_key     text not null,          -- 비공개 버킷 키. API 응답 미노출.
  mime       text not null,
  bucket     text not null default 'face' check (bucket in ('face','public')),
  created_at timestamptz not null default now(),
  primary key (model_id, view)
);
-- RLS: 서비스 롤 전용(생체 파생). 익명/authenticated read 금지.
alter table public.fm_model_assets enable row level security;

alter table public.fm_models
  add column if not exists assets_status text not null default 'none'
    check (assets_status in ('none','building','ready','failed')),
  add column if not exists qc_score numeric(4,3),           -- 민감: 집계만 노출
  add column if not exists assets_source_hash text;         -- 소스 3장 지문 → 각도 변경 시 재빌드 감지

-- 동시 빌드 1개: 진행 중 잡 유니크
create unique index if not exists fm_model_asset_build_singleflight
  on public.jobs (( (payload->>'modelId') ))
  where kind = 'fm_model_asset_build' and status in ('queued','running');
```

## 6. 배포 반영

- `server/Dockerfile`: `opencv-contrib-python-headless`(SFace/YuNet 포함) 설치 + weights(sface·yunet onnx) **pin+checksum 번들**. insightface/onnxruntime 불필요.
- `copilot/api/manifest.yml`: `memory: 2048`(codex [P1] — 1024 비현실). `variables`: `FM_FACE_QC_ENABLED:"true"`, `FM_FACE_QC_THRESHOLD:"0.363"`.
- **로컬 우선**: 3~4 컴포넌트 로컬 완주 검증 후 배포. push/merge는 명시 요청 시만.

## 7. PII 하드룰 (기존 + codex 보강)

- 얼굴 바이트·임베딩·비공개 R2 키·서명 URL: payload·이벤트·로그·API 응답·job result·trace·dead-letter·debug repr **전부 미포함**.
- `fm_model_assets` **RLS 서비스 전용**. `cover_image_url`에 서명URL·private key 금지.
- `qc_score` = 민감 생체 파생 → 집계 지표만.
- storage/cv2 예외 메시지 sanitize(키·로컬 경로 제거).
- 그리드/face_front = `r2_face` 전용, 인증 게이트 서빙만.
- **동의 철회/파기 캐스케이드**: profile purge 시 `fm_model_assets` + 파생 산출물도 삭제(개인화 purge 잡 확장).

## 8. 테스트

- 단위: 그리드 합성(크기·칸·4번째 중립), SFace QC(임베딩 게이트 통과/차단·다중얼굴·0얼굴), resolve 버킷 태깅, **상태머신 선택**(REAL/VIRTUAL/LEGACY/NONE/REJECTED 각각), 라이선스 게이트, staging cleanup.
- 통합: 3장→build-assets→QC→등록→셀러 컷에서 REAL_ASSETS 주입 + **이중주입 0**(회귀), 라이선스 실패→REJECTED(폴백 안 함), 가상모델 경로 무영향.
- in-flight: 추론 전 라이선스 해지→중단 검증.

## 9. 아웃 오브 스코프

Gemini 그리드 재생성 / Apple·Samsung 월렛 / 온체인 정산 변경 / 경로 α 전신생성 고도화.

## 10. 리스크 & codex 미해결 유보(해커톤: 데모 OK, 상용 전 처리)

- SFace 임계 캘리브·오매칭율·인구편향·다중얼굴 정책: 데모 후 골드셋 캘리브.
- EXIF 회전·손상/초대형 이미지·decompression bomb 가드: 업로드단 강화(후속).
- user_id당 복수 profile: 현재 1:1이나 build-assets에서 **명시 profile 선택 or 최신 1개** 규칙 고정(모호 조인 제거).
- 워커 cold-start/타임아웃·데모 결정적 픽스처.
