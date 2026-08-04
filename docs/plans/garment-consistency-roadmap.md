# 착용컷 일관성 로드맵 v2.4 (2026-08-04)

목표는 포즈·카메라·의류 정체성·패턴 충실도를 운에 맡기지 않고, 세 개의 Lock 계약과
검증 게이트로 운영하는 것이다.

> 현재 상태: **핵심 코드 계약과 1K 실측은 완료됐지만 fine-stripe 품질과 enforce 승인은 아직이다.**
> 지정 HEIC 실물로 1K smoke, 5×3 Frame 캘리브레이션, 최신 코드 브라우저 E2E를 수행했다.
> 다만 사람의 blinded label이 0건이고 1K fine stripe는 안전 투영 한계보다 작으므로 main 병합과
> enforce 전환은 별도 승인 전까지 금지한다.

상태 표기: ✅ 코드 완료·테스트 확인 / 🟡 코드 완료·실이미지 검증 필요 / ⏳ 미실행·미결정 /
⏸ 명시적 후순위

검증 기준: branch `feat/garment-consistency-check`, implementation HEAD `9111fef`, main `5580961`.

## 3-Lock 계약

| Lock | 정본 | 지배 대상 | 현재 상태 |
|---|---|---|---|
| Frame Lock | canonical Mannequin Profile / base mannequin | 포즈, body yaw, view family, 카메라, 프레이밍, 크롭, 배경, 조명, 그림자 | ✅ 프롬프트·Pre/Final QC·재시도/롤백 배선 완료, 🟡 enforce는 shadow guard |
| Identity Lock | 사용자 승인 front baseline | 컷 간 의류 정체성, 실루엣, 기장, 디자인 계보 | ✅ bounded edit, editor worn new, detail worn, vary lineage 배선 |
| Texture Lock | 원본 사진, 승인 Product Truth, protected assets | 무늬, 색, 로고, repeat/pitch | ✅ Product Truth 승인 계약, 🟡 stripe projection 실이미지 캘리브레이션 필요 |

원칙: smoke는 연결 증명일 뿐 품질 증거가 아니다. 자동 downgrade는 금지하며, fine-pattern
downgrade는 사용자의 명시 선택만 저장한다.

## 완료된 코드 계약

| 영역 | 상태 | 근거 |
|---|---|---|
| Product Truth revision | ✅ | `product_truth_packages/assets/review_events`, draft/patch/approve/reject API, approved source fingerprint 검사 |
| 구조화 pattern/correction 승인 | ✅ | `ProductInput.jsx` Product Truth 수정·승인 UI, `product_truth.py` patternSpec/validation, frontend correction tests |
| Structured QC persistence/policy | ✅ | `qc_results`, `generation_runs.truth_package_id`, `structuredQC` 저장·decision 적용 |
| Frame Lock prompt | ✅ | provider input 0 = base mannequin, prompt의 `IMAGE 1 IS THE IMMUTABLE CANVAS`, style ref provider 입력 제외 |
| Frame QC | ✅ | `_apply_frame_qc`, `mannequin_frame_qc.decide`, Vision은 관찰 필드만, Pre/Final QC, rollback |
| Frame retry reservation | ✅ | `_has_frame_retry_budget`, `_apply_edits(... reserved_frame_retry=...)`, 관련 테스트 |
| Frame shadow collector | ✅ | `frame_shadow_collect.py`, `frame_blinded_label.py`, `frame_shadow_report.py`, append-only provenance/label/report |
| Independent calibration repetitions | ✅ | 같은 프로젝트에서 rep 0은 generate, rep 1+는 regenerate로 각각 실제 provider 호출; 준비 결과 재사용을 금지하는 회귀 테스트 |
| Safe rollout flags | ✅ | prod manifest keeps Frame/structured/edit/hybrid/projection in shadow; Product Truth can enforce approval/fingerprint only |
| Baseline anchor | ✅ | mannequin bounded edit, editor worn `mode:new`, detail worn jobs, editor vary lineage all carry baseline/source lineage |
| Manual downgrade UI | ✅ | pattern-fidelity/hybrid failures block or review; downgrade choice persists to storyboard and hydrates after reload |
| HEIC Front/Back/Detail collector | ✅ | `sourceImages` requires Front/Back/Detail, converts HEIC bundle to JPEG upload, smoke-only one-arm guard |
| Hybrid 2D stripe projection | 🟡 | extraction, panel map, warp, deterministic QC, projection planner, worker metadata wired; activation still needs real-image calibration |
| Product Truth runtime freshness | ✅ | worker가 승인 package와 현재 source evidence fingerprint를 provider 호출 직전에 재검증; 기존 프로젝트 UI도 approved/draft truth를 재수화 |
| QC lower-body detector | ✅ | 하단 12% 자체 대비 레짐으로 판정; 15개 실출력의 `missing_lower_body` 오탐 6→0 |

## Pending Empirical Work

| Ticket | Status | Done when |
|---|---|---|
| Browser E2E on latest code | ✅ | 기존 완료 프로젝트→에디터→마네킹 재진입, 기존 컷/부분수정/통과/단계 guard 확인; 유료 생성 버튼은 누르지 않음 |
| One actual Gemini 1K stripe smoke | ✅ | `stripe-projection-smoke-20260804-v5`, provider image calls = 1; connection proven, quality not proven |
| Frame calibration 5 products x 3 | ✅ | `frame-5x3-1k-20260804-v3`, 15 독립 1K 호출, deterministic/Vision Frame pass 15/15, critical violation 0 |
| Stripe projection spike | 🟡 | Designated HEIC set reaches extraction → panel map → warp → deterministic QC; quality currently fails at 1K fine stripe |
| Threshold calibration | 🟡 | 실출력 분포와 오탐 수정 완료; 사람 blinded label이 0건이라 false-pass 임계 확정은 보류 |
| Enforce decision | ⏳ | `labeledCount > 0`인 blinded review와 QA 승인 후에만 shadow→enforce |

Main remains unmerged until the QA report is complete and approval is explicit.

## Designated Stripe Smoke Set

Use the shirt HEIC bundle as the first smoke/QC set:

- `/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/스트라이프셔츠_앞면.heic`
- `/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/스트라이프셔츠_뒷면.heic`
- `/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/스트라이프셔츠_디테일컷.heic`

Local filesystem inspection found the same files under macOS decomposed Unicode spelling of `상의`; keep the
human-readable path above as the designated set, but verify path resolution before a paid run.

이 번들은 사설 QC 정본으로 고정한다. 원본 바이트는 저장소에 복사하지 않고
`server/tests/fixtures/private_qc/stripe_heic_fingerprints.json`의 파일별 SHA-256과 bundle SHA-256으로
동일성을 검증한다. 이후 stripe QC smoke와 회귀 QA는 우선 이 세 파일을 사용하며, 해시가 달라지면
조용히 다른 표본으로 진행하지 않고 fail-closed 한다.

### 2026-08-04 1K projection smoke result

Dataset: `stripe-projection-smoke-20260804-v5`

- Provider image calls: `1`
- Manifest: `server/ab_out/frame_lock/stripe-projection-smoke-20260804-v5/manifest.json`
- Output sample: `server/ab_out/frame_lock/stripe-projection-smoke-20260804-v5/goldenset-stripe-shirt_rep0.png`
- Frame outcome: pass; generated view stayed in the canonical 3/4 family.
- Projection wiring: pass. Real HEIC data reached source extraction, panel map, warp, and deterministic QC.
- Projection quality: fail. This is expected evidence, not a routing failure.

Observed QC metrics:

```json
{
  "projection": {
    "ok": false,
    "reason": "target_period_too_small",
    "targetAxis": "vertical"
  },
  "deterministicQc": {
    "passed": false,
    "failures": ["pattern_metric_failed"],
    "metrics": {
      "period_rel_err_max": 1.0058,
      "repeat_count_rel_err_max": 0.5015,
      "direction_error_max": 0.0,
      "color_delta_e00_median": 6.83,
      "color_delta_e00_max": 9.61,
      "mask_coverage": 0.8796,
      "outside_drift_frac": 0.0,
      "outside_ssim": 0.9999
    }
  }
}
```

Conclusion: for this shirt at 1K, stripe direction and outside-mask preservation are measurable and good, but
the torso stripe period/repeat fidelity is not acceptable. Do not turn texture projection to enforce from this
sample. Next calibration should compare 1K vs 2K on the same HEIC set, or keep 1K as smoke-only and route fine
stripe quality decisions to review/downgrade.

## 2026-08-04 Frame 5×3 calibration result

Dataset: `server/ab_out/frame_lock/frame-5x3-1k-20260804-v3`

- 실제 Gemini 1K provider image calls: `15` (5개 상품 × 각 3회 독립 호출)
- manifest: `validForCalibration=true`, `purpose=calibration`, `smokeOnly=false`, `sampleCount=15`
- raw sample manifest SHA-256: `a25648d12188715f70bff42118096cb52ec5682029e783b17d54d53b81285a06`
- provenance problems: `[]`; source bundle 5개, output SHA-256 15개 모두 고유
- deterministic Frame QC: pass `15/15`
- Vision observation: status ok `15/15`; combined Frame decision pass `15/15`
- blind visual audit: canonical 3/4 family, full head/body/feet, scale/background/lighting critical violation `0`
- detector correction: 실출력에서 `missing_lower_body` 오탐 `6 → 0`, 신규 오탐 `0`
- report: `criticalFalsePassCount=0`, `labeledCount=0`, `enforceReadyCandidate=false`,
  `status=needs_labels_or_fixes`

결론: Frame Lock의 1K 연결과 현재 표본의 시각적 일관성은 확인됐다. 그러나 자동/도구 관찰은 사람의
blinded label을 대신하지 않는다. `labeledCount=0`인 동안 `MANNEQUIN_FRAME_QC=shadow`를 유지한다.

## 2026-08-04 browser E2E result

- 최신 프론트/백엔드를 `localhost:5173` / `localhost:8000`으로 기동했다.
- 로그인 사용자의 완료된 스트라이프 셔츠 프로젝트를 보관함에서 열고 에디터 20개 블록을 복원했다.
- `/create/mannequin` 재진입 후 기존 컷, AI 부분수정 진입점, 통과 상태, 완료 프로젝트 단계 guard를 확인했다.
- 해당 브라우저 경로에서는 Gemini/provider 호출을 실행하지 않았다.
- Product Truth 기존 프로젝트 복원 누락은 회귀 테스트로 재현 후 수정했다. 404만 legacy/missing으로
  처리하고 500/network 오류는 숨기지 않는다.
- 보고서: `.gstack/qa-reports/qa-report-localhost-2026-08-04.md`

## Runtime Sequence

```
source originals + approved Product Truth + canonical Mannequin Profile
  -> fresh generation with Frame Lock prompt
  -> Pre-Frame QC
      -> hard Frame violation: retry once if reserved budget remains
      -> second hard violation: reject / no save
  -> fit / untuck / bust edits only where allowed
      -> bounded edit never runs global automatic passes
  -> supported fine pattern: deterministic stripe projection
  -> Final Frame QC
      -> regression: rollback to Pre-Frame pass, else reject/review
  -> Fidelity / Structured QC
  -> user approval or explicit downgrade choice
  -> approved baseline + originals + canonical base anchor follow-up cuts
```

Preserve this order. Do not move texture projection before Pre-Frame QC, and do not skip Final Frame QC after
edits or projection.

## Activated Output Contract (2026-08-04)

- Ordinary and non-stripe products generate at `1K`.
- Approved `STRIPE` / `PINSTRIPE` products generate at `4K`; check/plaid and other patterns do not receive the
  4K cost upgrade. They remain protected/QC risks.
- `MANNEQUIN_IMAGE_SIZE_CAP=off`; the old global 1K cap is not a production safeguard.
- Deterministic texture projection is applied only after deterministic QC passes.
- Enforce mode also requires protected collar/placket source components. A periodic/color metric pass cannot
  override missing protected components.
- Failed projection creates no usable candidate. The seller is offered only an original-photo hero or a fresh
  regeneration request.

## Reproducible Commands

Verified module tests:

```bash
cd /Users/nojeong-un/devs/wearless_studio/server
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q \
  -o cache_dir=/tmp/wearless-roadmap-v23-pytest-cache \
  tests/test_product_truth.py \
  tests/test_mannequin_frame_qc.py \
  tests/test_frame_calibration_toolchain.py \
  tests/test_texture_projection.py \
  tests/test_hybrid_composite_units.py \
  tests/test_hybrid_worker_integration.py \
  tests/test_generation_run.py \
  tests/test_baseline_edit_path.py \
  tests/test_editor_vary_wiring.py
```

```bash
cd /Users/nojeong-un/devs/wearless_studio
node --test \
  tests/frontend/mannequin-ai-edit.test.mjs \
  tests/frontend/mannequin-review-state.test.mjs \
  tests/frontend/product-truth-matching-recovery.test.mjs \
  tests/frontend/image-transcode.test.mjs
```

Frame-only smoke and calibration must isolate unrelated QC/features:

```bash
export MANNEQUIN_IMAGE_SIZE=1K
export MANNEQUIN_IMAGE_SIZE_CAP=1K
export MANNEQUIN_FRAME_QC=shadow
export MANNEQUIN_HYBRID_COMPOSITE=off
export MANNEQUIN_TEXTURE_PROJECTION_2D=off
export MANNEQUIN_MAX_ATTEMPTS=1
export MANNEQUIN_UNTUCK_PASS=off
export MANNEQUIN_BUST_PASS=off
export MANNEQUIN_AXIS_QC=shadow
export GARMENT_QC_EXTRA_CANDIDATES=0
export GARMENT_QC_MODE=off
export IMAGE_QC=off
export MANNEQUIN_QC_ENABLED=false
export RETRIEVAL_REFIMAGES=off
export MANNEQUIN_STRUCTURED_QC=off

cd /Users/nojeong-un/devs/wearless_studio/server
uv run python -m scripts.frame_shadow_collect \
  --manifest /tmp/frame_stripe_smoke_manifest.json \
  --dataset-id frame-stripe-1k-smoke-YYYYMMDD \
  --reps 1 \
  --smoke \
  --execute
```

For calibration, use a five-product manifest and remove `--smoke`:

```bash
uv run python -m scripts.frame_shadow_collect \
  --manifest /tmp/frame_5x3_manifest.json \
  --dataset-id frame-5x3-YYYYMMDD \
  --reps 3 \
  --execute
uv run python -m scripts.frame_blinded_label --dataset-dir ab_out/frame_lock/frame-5x3-YYYYMMDD ...
uv run python -m scripts.frame_shadow_report \
  --dataset-dir ab_out/frame_lock/frame-5x3-YYYYMMDD \
  --dataset-id frame-5x3-YYYYMMDD
```

The `--execute` commands require provider credentials and may incur cost. On 2026-08-04 the designated stripe
smoke was run once and the five-product calibration was run 15 times at 1K. No secrets are written into manifests
or docs.

## Deferred

⏸ 3D/UV projection, own model training, Recipe Store, durable workflow orchestrator, failed-region correction,
check/plaid/logo expansion, and VITON-grade wrinkle/curvature handling are deferred until the Frame and stripe
evidence above is complete.

## Verification Snapshot

- Backend full suite: `2435 passed, 1 skipped, 96 deselected, 1 warning`.
- Frontend suite: `220 passed, 0 failed`.
- Build: pass.
- Browser E2E: latest-code persisted-project/editor/mannequin re-entry pass; no paid provider action.
- Actual Gemini 1K stripe smoke: run with designated `스트라이프셔츠_*.heic` set.
- Frame 5×3 calibration: 15 independent 1K outputs; critical Frame violation 0; human labels 0.
- Actual Gemini 4K stripe projection: provider/input wiring and deterministic pattern metrics passed, but visual
  audit rejected the result because collar/placket protection was missing and the torso projection boundary was
  visible. That observation added the `protected_component_missing` fail-closed gate; no accepted AI stripe
  candidate is claimed.
- Local visual report:
  `server/ab_out/frame_lock/stripe-projection-enforce-4k-20260804-v6/report.html`.
