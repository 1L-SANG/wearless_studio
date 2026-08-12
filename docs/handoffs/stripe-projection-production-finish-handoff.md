# Stripe Projection Production Finish — 인계 (2026-08-05)

## 1. HEAD

- 시작 HEAD: `7a03b838904e935534597b8fe307b988bb5faad7`
- 종료 HEAD: `677af1f`
- main: `5580961889fdc2ac5438a83ac1c60467a9725350` — **불변** (merge/rebase/reset/checkout/push 없음)
- 브랜치: `feat/garment-consistency-check`

## 2. 커밋

| SHA | 내용 |
|---|---|
| `eed870c` | Phase A — offline replay 하네스 + 워커 geometry 캡처 |
| `677af1f` | Phase B — 부위 방향 구제, shaped 소유, seam 기준 정정 |

## 3. 변경 파일

- `server/scripts/replay_stripe_projection.py` (신규)
- `server/tests/test_replay_stripe_projection.py` (신규)
- `server/app/services/hybrid_composite/warp_composite.py`
- `server/app/services/hybrid_composite/deterministic_qc.py`
- `server/app/workers/mannequin_job.py`
- `server/tests/test_hybrid_composite_units.py`
- `docs/plans/garment-consistency-roadmap.md`

보호 dirty(`server/uv.lock`, `src/App.jsx`, `.context/`)는 스테이지·커밋하지 않았다.
`git add` 는 전부 파일명 명시.

## 4. 재현한 결함

**replay 가 유료 호출 0회로 짚어낸 것**: 현재 코드에서 collar 가
`component_pattern_axis_unmeasurable` 로 거절되고, enforce 가 이를
`protected_component_missing` 으로 승격해 **출고를 통째로 막았다**. 캡처 당시(7a03b83 이전)
실행에는 이 review 가 없었으므로, 그대로 뒀다면 다음 유료 호출에서야 드러났을 회귀다.

원인은 기하다 — 칼라 박스에 좌·우 잎과 스탠드가 서로 다른 각도로 들어오고, 전역 구조
텐서가 평균내 일관성 `0.140` (하한 0.16). 모드별로는 `0.747 / 0.830 / 0.744`.

## 5. 알고리즘 변경

- **방향 모드 분할** (`_component_orientation_regions`) — 2θ 공간 국소 구조 텐서로 모드를
  찾아 각자의 축·주기로 warp. **단, 박스 전체로 축이 잡히면 그것을 쓴다**(구제 경로).
- **component fabric mask** — chroma 이상치(브랜드 라벨) 배제 전용. 소유·거절 판단에 미사용.
  불확실하면 박스 전체 반환 → 이전 동작보다 나빠질 수 없음.
- **shaped ownership** — 부위 소유를 박스가 아니라 원단 형태로 제한, 나머지는 carrier 유지.
- **seam 기준 정정** — QC 가 실루엣 밴드(40px) 대신 합성기의 실제 내부 feather(13.3px)로 측정.

## 6. QC 변경

`verify_composite(..., inner_feather_px=...)` 추가. 지표 `seam_band_px` 기록.
기존 게이트·임계는 변경하지 않았다. 패널 strict 게이트도 그대로 둔다.

## 7. offline replay 결과

명령:
```
cd server && uv run python scripts/replay_stripe_projection.py replay \
  ab_out/frame_lock/stripe-projection-protected-v1/artifacts
```

| 항목 | 값 |
|---|---|
| stage | `qc` (합성·QC 완주) |
| components_needing_review | `[]` |
| seam_ramp_excess / seam_grad_norm | `0.90` / `0.086` |
| boundary_chroma_de00 | `2.56` |
| drape_amp_ratio / local_p2 | `0.896` / `0.651` |
| outside_drift_frac | `0.0` |
| period_rel_err_max | `0.0008` |
| 남은 실패 | `pattern_metric_failed` — **기존** 패널 strict 게이트(소매 purity 0.592/0.882) |
| 재현성 | 두 실행의 output/metrics 해시 동일 |

## 8. provider 호출 수

**0회.** 이번 작업 전체에서 Gemini 이미지·Vision 호출 없음.

## 9. 테스트 결과

- backend `2559 passed, 1 skipped, 96 deselected` (Phase B 이전 기준선 2553 + 신규 6, 회귀 0)
- frontend `224 passed`
- `npm run build` PASS
- `git diff --check` clean

회귀 귀속은 추정하지 않고 `git stash` 로 시작 HEAD 와 A/B 대조해 확인했다.

## 10. HTML

`server/ab_out/frame_lock/stripe-projection-protected-v1/artifacts/replay_report.html`
(source·carrier·mask·ownership·alpha·painted·composite + 부위 200% crop + 지표 + 복원 경고)

## 11. 저장·크레딧·baseline

이번 작업은 replay(파일 전용)만 실행했다 — R2·DB·credit·output·baseline 접근 0.
그 사실을 테스트가 import 파싱으로 강제한다.

## 12. 남은 위험

1. **육안 게이트 미통과.** 이 데이터셋은 geometry 캡처 이전 자산이라 landmark 를 mask 에서
   복원해야 하고, 충실도가 painted IoU `0.7136` 에 그친다. mask IoU `0.977` 은 y-클립 때문에
   소매 끝 오차에 둔감한 **틀린 기준**이었다. 이 replay 로 시각 판정을 내리면 안 된다.
2. **패널 strict 게이트**가 이 carrier 의 소매(purity 0.592/0.882)에서 계속 실패한다. 기존
   게이트이며 완화하지 않았다. 복원 landmark 가 purity 를 낮추는지 실 geometry 로 재확인 필요.
3. **임계 캘리브레이션 미완** — blinded label 0건은 그대로다.
4. 방향 분할은 실 자산 1건(칼라)에서만 검증됐다. 다른 부위·상품 분포는 미확인.

## 13. Codex 재검증 명령

```bash
cd /Users/nojeong-un/devs/wearless_studio
git log --oneline 7a03b838904e935534597b8fe307b988bb5faad7..677af1f
git diff --stat 7a03b838904e935534597b8fe307b988bb5faad7..677af1f
shasum -a 256 src/App.jsx .context/codex-session-id   # e11f1b0f… / 553ecad2…
git rev-parse main                                     # 5580961… 이어야 함

cd server
uv run pytest -q --deselect tests/test_personalization.py
uv run pytest -q tests/test_replay_stripe_projection.py

# 무비용 replay 2회 — output/metrics 해시가 같아야 한다
uv run python scripts/replay_stripe_projection.py replay \
  ab_out/frame_lock/stripe-projection-protected-v1/artifacts --out /tmp/r1
uv run python scripts/replay_stripe_projection.py replay \
  ab_out/frame_lock/stripe-projection-protected-v1/artifacts --out /tmp/r2
diff <(jq -S .hashes /tmp/r1/replay_metrics.json) <(jq -S .hashes /tmp/r2/replay_metrics.json)

cd .. && npm run test:frontend && npm run build
open server/ab_out/frame_lock/stripe-projection-protected-v1/artifacts/replay_report.html
```

검증 시 확인할 것:
- replay 가 프로덕션 함수만 부르는가 (import AST 테스트가 실제로 그것을 막는가)
- 방향 분할이 **구제 경로**로만 동작하는가 (박스 전체 측정이 되면 대체하지 않는가)
- fabric mask 가 소유·거절에 관여하지 않는가
- seam 기준 변경이 완화가 아니라 기준 정정인가 (진폭 무관 지표와 일치하는가)
- painted IoU 0.71 을 근거로 시각 판정을 내리지 않았는가

## 14. 금지 사항 (유지)

main merge/rebase/reset/checkout/push·PR, 운영 DB 변경, 기존 migration 수정,
보호 dirty 수정, 실패 이미지를 정상 후보로 노출, 품질 기준 완화, 무제한 provider 호출.
**Phase D 유료 4K 는 별도 승인 전까지 실행 금지.**

## 15. 다음 세션 첫 명령

```
cd /Users/nojeong-un/devs/wearless_studio/server
uv run python scripts/replay_stripe_projection.py replay \
  ab_out/frame_lock/stripe-projection-protected-v1/artifacts
```
