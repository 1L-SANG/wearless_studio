# ADR — Armhole Locality: MVP Freeze

Machine-readable companion: [`armhole_locality_mvp_freeze.json`](armhole_locality_mvp_freeze.json).
Every number in this document is also present in that file.

## 1. Decision

- MVP does **not** wire armhole part-segmentation into production.
- The current hybrid composite runtime path stays exactly as it is.
- v13 (locality merge primitive) and v14 (local QC + fallback selector) are preserved as
  **offline experimental safety modules**, not as production code.
- No production branch, config flag, or dead code is added for them.

## 2. Status

`ACCEPTED_FOR_MVP_FREEZE`

## 3. Context

| version | verdict |
|---|---|
| v7 | `CANONICAL_MASKS_PERSISTED_PARTITION_FEASIBLE` — SAM2 part candidates persisted |
| v8 | `NO_VALID_CANDIDATE_COMBINATION` — all 27 combinations per model invalid |
| v9 | `BLOCKED_RESIDUAL_TORSO_INVALID` — residual partition produced `assigned_once=100%` / `overlap=0%` for free, which proved nothing |
| v10 | `BLOCKED_ARMHOLE_PATH_INVALID` — sleeve prompt coverage collapsed to 0.0 (left) and 0.1667 (right) |
| v11 | `BLOCKED_RECOMPOSITION_INCONCLUSIVE` — locality violation confirmed: 91.72% (left) / 96.90% (right) of removed pixels lay **outside** the armhole ROI |
| v12 | `BLOCKED_SOURCE_PATH_NOT_FOUND` — the v10 generator does not exist in the repo, git history, stashes or any worktree |
| v13 | `OFFLINE_LOCALITY_PRIMITIVE_VALIDATED` — ROI-local merge primitive |
| v14 | `OFFLINE_LOCAL_QC_FALLBACK_VALIDATED` — deterministic selector with pre-stage fallback |
| v15 | `LEFT_LOST_PROMPT_VALID_SLEEVE_EVIDENCE_CONFIRMED` — the lost prompt was real sleeve evidence, not seam ambiguity |
| v16 | `OFFLINE_ADAPTER_READY_UPSTREAM_SEGMENTATION_MISSING` — the integration point exists, the inputs do not |

## 4. Validated Contracts

Validated offline against canonical artifacts:

- **outside-ROI bitwise preservation** — added, removed and changed pixels are all `0` on both sleeves
- **inside-ROI constrained identity** — `final & roi == constrained & roi & garment_mask`, bitwise
- **deterministic fallback** — a plain conjunction of gates; three identical runs produce the same
  decision, the same rejection-reason ordering and the same mask SHA256
- **no arbitrary conflict split** — a left/right overlap stops the partition instead of arbitrating pixels
- **baseline-not-worse safety gates** — the selected result never scores below the pre-stage baseline on
  prompt coverage, lower-sleeve retention, cuff retention, component integrity, opposite-side crossing or
  torso-centre intrusion
- **torso safety checks** — placket coverage, side-chest preservation, sleeve intrusion and
  garment containment all hold

The merge contract itself:

```python
final_sleeve = (
    (pre_stage_sleeve & ~armhole_roi)
    | (constrained_sleeve & armhole_roi)
) & garment_mask
```

## 5. Production Readiness

| 항목 | 상태 |
|---|---|
| Locality primitive | Offline validated |
| Local QC selector | Offline validated |
| Production integration point | Identified |
| Production upstream sleeve segmentation | Missing |
| Constrained sleeve producer | Missing |
| Armhole ROI producer | Missing |
| Canonical prompt producer | Missing |
| Production wiring | Not performed |
| Deployment | Not performed |

## 6. Runtime Input Gap

- required inputs: **14**
- currently available: **1**
- currently missing: **13**

Available: `garment_mask` (from `panel_map.build_panel_map`, reachable at
`mannequin_job._apply_hybrid_composite`).

Missing, all 13:

1. `pre_stage_sleeve_left`
2. `pre_stage_sleeve_right`
3. `constrained_sleeve_left`
4. `constrained_sleeve_right`
5. `armhole_roi_left`
6. `armhole_roi_right`
7. `positive_points_left`
8. `positive_points_right`
9. `shoulder_anchor_left`
10. `shoulder_anchor_right`
11. `underarm_anchor_left`
12. `underarm_anchor_right`
13. `torso_center_band`

`14 = 1 + 13`.

Three of these — `constrained_sleeve_*`, `armhole_roi_*` and `positive_points_*` — have **no producer
anywhere in the repository**, not merely no wiring. The sleeve panels production does have are
landmark-derived approximate band quads, not the SAM-derived masks the v13/v14 thresholds were
calibrated against.

## 7. MVP Behavior

- the existing hybrid composite path remains unchanged
- the armhole locality stage is never called
- no additional provider call
- no additional SAM2 inference
- no new runtime latency
- no DB, API or schema change

## 8. Deferred R&D

Split out as separate work items:

- production-grade per-part sleeve/torso segmentation
- shoulder/underarm anchor detection
- armhole ROI generation
- constrained sleeve producer
- multi-garment validation
- production shadow mode
- production enforce mode

## 9. Unfreeze Criteria

Restart only when one of these holds:

1. armhole part separation becomes a required feature for real product quality, not a nice-to-have
2. the current panel-map approach fails repeatedly on a representative sample
3. the fallback or manual-review rate exceeds the product target
4. at least 10 representative carriers plus an agreed evaluation rubric are available
5. an upstream producer has a named owner and a committed implementation schedule

Not accepted as unfreeze criteria: *"we have spare time"*, *"it would probably look better"*.

## 10. Future Integration Point

Candidate only — **not implemented**:

- file: `server/app/workers/mannequin_job.py`
- function: `_apply_hybrid_composite`
- position: after panel-map success, before `composite_stripe`

Chosen because it is the only point where the garment mask exists, the carrier is still in memory, and
nothing downstream has consumed panel geometry yet.

## 11. Rollback

`rollback_required = false` — no production change was made.

Any future wiring must be reversible by setting its mode flag to OFF, returning to the existing path
without a DB migration and without depending on previously written artifacts.

## 12. Evidence

Experimental source (untracked, local):

| path | SHA256 |
|---|---|
| `server/scripts/armhole_locality_merge_v13.py` | `19a4de2458a900ba4fcdcd163147f7245abe8e1beba010ee1ff0df34e9042444` |
| `server/tests/test_armhole_locality_merge_v13.py` | `0d98a897c0e9cb037fd7fcc7efa0c64cb7f5203781dded66fb02f36d98b22c7c` |

Artifact directories under
`server/ab_out/frame_lock/stripe-projection-protected-v1/artifacts/` (gitignored, local only):

| directory | key JSON | SHA256 |
|---|---|---|
| `diagnostic_armhole_locality_primitive_v13/` | `execution_meta.json` | `96992049f00b783a63184e8aa54d4f211eb1dd0a7cd7effebd08ae6b1b47f385` |
| `diagnostic_armhole_local_qc_fallback_v14/` | `execution_meta.json` | `e42fa2fbc1b1f98b3bd0ae41a4bb35e098152d2d158bfc7f25c96570e198e4a5` |
| `diagnostic_armhole_local_qc_fallback_v14/` | `selector_decisions.json` | `cf0f43bd166bc26fb621695def8cd1170a1902a0a074307da388a4d1534d340c` |
| `diagnostic_left_armhole_lost_prompt_v15/` | `verdict.json` | `44ba72a54b7306fc1c4abd99ed1b7a0783ed619ed1f3223dd5d0533e4979a6e4` |
| `diagnostic_production_armhole_integration_design_v16/` | `readiness_assessment.json` | `47d5c843a728567d30642675d3e5d72ebe423406db303347f54669f7e6b3dd6f` |

Test re-run at freeze time:

```
cd server && pytest tests/test_armhole_locality_merge_v13.py -q
```

return code `0`, passed `33`, failed `0`, skipped `0`, canonical replay executed `true`.

Runtime import check over `server/app/`: `runtime_import_count = 0` for
`armhole_locality_merge_v13`, `select_local_armhole_candidate` and `merge_sleeve_inside_roi`.

## Verdict

`ARMHOLE_BRANCH_FROZEN_FOR_MVP`
