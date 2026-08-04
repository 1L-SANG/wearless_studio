# 착용컷 일관성 로드맵 v2.3 (2026-08-04)

목표는 포즈·카메라·의류 정체성·패턴 충실도를 운에 맡기지 않고, 세 개의 Lock 계약과
검증 게이트로 운영하는 것이다.

> 현재 상태: **코드 계약은 상당 부분 완료됐지만 품질 완료는 아니다.** 실 Gemini 1K 출력,
> 브라우저 E2E, Frame 캘리브레이션, stripe projection 실측이 끝나기 전에는 main 병합 금지.

상태 표기: ✅ 코드 완료·테스트 확인 / 🟡 코드 완료·실이미지 검증 필요 / ⏳ 미실행·미결정 /
⏸ 명시적 후순위

검증 기준: branch `feat/garment-consistency-check`, HEAD `5ba9b19`, main `5580961`.

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
| Safe rollout flags | ✅ | prod manifest keeps Frame/structured/edit/hybrid/projection in shadow; Product Truth can enforce approval/fingerprint only |
| Baseline anchor | ✅ | mannequin bounded edit, editor worn `mode:new`, detail worn jobs, editor vary lineage all carry baseline/source lineage |
| Manual downgrade UI | ✅ | pattern-fidelity/hybrid failures block or review; downgrade choice persists to storyboard and hydrates after reload |
| HEIC Front/Back/Detail collector | ✅ | `sourceImages` requires Front/Back/Detail, converts HEIC bundle to JPEG upload, smoke-only one-arm guard |
| Hybrid 2D stripe projection | 🟡 | extraction, panel map, warp, deterministic QC, projection planner, worker metadata wired; activation still needs real-image calibration |

## Pending Empirical Work

| Ticket | Status | Done when |
|---|---|---|
| Browser E2E on latest code | ⏳ | Full UI flow run against current branch; report attached |
| One actual Gemini 1K stripe smoke | ✅ | `stripe-projection-smoke-20260804-v5`, provider image calls = 1; connection proven, quality not proven |
| Frame calibration 5 products x 3 | ⏳ | 15 blinded samples, shadow-only, critical Frame false-pass = 0 |
| Stripe projection spike | 🟡 | Designated HEIC set reaches extraction → panel map → warp → deterministic QC; quality currently fails at 1K fine stripe |
| Threshold calibration | ⏳ | Deterministic QC thresholds set from real stripe spike, not synthetic fixtures |
| Enforce decision | ⏳ | QA report + calibration evidence reviewed; explicit approval before raising enforcement |

Main remains unmerged until the QA report is complete and approval is explicit.

## Designated Stripe Smoke Set

Use the shirt HEIC bundle as the first smoke/QC set:

- `/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/스트라이프셔츠_앞면.heic`
- `/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/스트라이프셔츠_뒷면.heic`
- `/Users/nojeong-un/Downloads/노션에 있는 의상들/상의/스트라이프셔츠_디테일컷.heic`

Local filesystem inspection found the same files under macOS decomposed Unicode spelling of `상의`; keep the
human-readable path above as the designated set, but verify path resolution before a paid run.

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

The `--execute` commands require provider credentials and may incur cost; they were not run for this document.
No secrets should be written into manifests or docs.

## Deferred

⏸ 3D/UV projection, own model training, Recipe Store, durable workflow orchestrator, failed-region correction,
check/plaid/logo expansion, and VITON-grade wrinkle/curvature handling are deferred until the Frame and stripe
evidence above is complete.

## Verification Snapshot

- Backend targeted suite: `354 passed, 1 warning`.
- Backend full suite: `2418 passed, 1 skipped, 96 deselected, 1 warning`.
- Frontend suite: `216 passed`.
- Build: pass.
- Browser E2E: not run.
- Actual Gemini 1K stripe smoke: run with designated `스트라이프셔츠_*.heic` set.
- Stripe projection quality evidence: wiring proven, quality failed at 1K fine stripe; synthetic fixtures remain insufficient for enforce.
