"""Hybrid composite mutation battery — HM1~HM12 를 단일 명령으로 실행한다.

사용법:
    python server/tools/run_hybrid_mutation_battery.py [출력 JSON 경로]

동작:
  · 현재 repo HEAD 로 detached throwaway worktree 를 만들어 그 안에서만 변이·실행한다
    (리뷰 대상 worktree 는 절대 만지지 않고, 전후 HEAD/diff/status 로 무변경을 증명).
  · 각 mutant 는 안전 게이트 하나를 무력화한다. 대응 테스트가 실패(=KILLED)해야 하며,
    하나라도 SURVIVED 면 게이트가 장식이라는 뜻이다.
  · PATCH_MISS 는 앵커 문자열이 리팩터로 유실된 것 — mutant 를 현행화해야 한다.
"""
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(
    subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                   text=True, cwd=pathlib.Path(__file__).resolve().parent).stdout.strip())
MUT = REPO / ".mutation-battery-worktree"
PY = sys.executable
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    REPO / "server" / "tools" / "mutation-battery-report.json")


def sh(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0 and "pytest" not in cmd:
        raise RuntimeError(f"{cmd}: {r.stderr[:400]}")
    return r


def reviewed_state():
    head = sh(f"git -C {REPO} rev-parse HEAD").stdout.strip()
    status = sh(f"git -C {REPO} status --short").stdout.strip()
    return {"head": head, "status": status or "clean"}


SM = "server/app/services/hybrid_composite/stripe_model.py"
PR = "server/app/agents/product_reference.py"
MJ = "server/app/workers/mannequin_job.py"
WC = "server/app/services/hybrid_composite/warp_composite.py"
SV = "server/app/services/hybrid_composite/source_validation.py"
PM = "server/app/services/hybrid_composite/panel_map.py"
UNITS = "tests/test_hybrid_composite_units.py"
INTEG = "tests/test_hybrid_worker_integration.py"
CSR = "tests/test_composite_source_reference.py"
FIXV2 = "tests/test_hybrid_fix_v2.py"

MUTANTS = [
    ("HM1", "색 순서 반전", SM,
     [("colors = tuple(run_color(st, ln) for st, ln in ordered)",
       "colors = tuple(reversed([run_color(st, ln) for st, ln in ordered]))")],
     f"{UNITS}", ["extractor_period_width_and_color_gates", "low_saturation"]),
    ("HM2", "period ×1.2", SM,
     [("    period = float(np.median(strip_periods))",
       "    period = float(np.median(strip_periods)) * 1.2")],
     f"{UNITS}", ["extractor_period_width_and_color_gates"]),
    ("HM3", "Detail authority 제거", PR,
     [('PATTERN_SOURCE_PRIORITY: tuple[str, ...] = ("Detail", "Front", "Back", "Fit")',
       'PATTERN_SOURCE_PRIORITY: tuple[str, ...] = ("Front", "Detail", "Back", "Fit")')],
     f"{CSR} {INTEG}", ["orders_detail_front_back_fit", "detail_first", "carries_detail_slot"]),
    ("HM4", "post-composite Gemini 호출 추가", MJ,
     [("            series = (\n                await _apply_series_qc(",
       "            if hybrid_info and hybrid_info.get(\"applied\"):\n"
       "                await gemini.generate_content_image(model, \"noop\", [InlineImage(res.mime, res.image)], image_size)\n"
       "            series = (\n                await _apply_series_qc(")],
     f"{INTEG}", ["freezes_generation_after_completion"]),
    ("HM5", "deterministic 실패를 LLM pass 로 덮기(_save_cut 강등 제거)", MJ,
     [("            if hc.get(\"needsReview\") and qc_scores[\"outcome\"] == \"auto_pass\":\n"
       "                qc_scores[\"outcome\"] = \"needs_review\"\n", "")],
     f"{INTEG}", ["components_needing_review_finalizes_as_review",
                  "cannot_be_overridden_by_llm_auto_pass"]),
    # HM6 v2 — 구 fallback 주입 지점은 Codex P0 재배선으로 hard failure 가 도달 불가
    # (SURVIVED-neutral 실측). 같은 질병의 현행 표현 = fail-closed 신호 무력화.
    ("HM6", "hard failure fail-closed 신호 무력화(성공 흐름 복원)", MJ,
     [("    if isinstance(summary, dict) and summary.get(\"applied\") is False:\n"
       "        raise _HybridCompositeFailClosed(summary)",
       "    return None")],
     f"{INTEG}", ["fails_closed_before_save_or_success_finalize"]),
    ("HM7", "protected mask 밖 drift", WC,
     [("    alpha = np.zeros((h, w), np.float32)",
       "    alpha = np.full((h, w), 0.12, np.float32)"),
      ("    alpha[painted == 0] = 0.0  # 패턴이 실제로 칠해진 곳만 합성",
       "    # (mutant) outside reset 제거")],
     f"{UNITS}", ["composite_and_qc_gates_hold", "outside", "drift"]),
    ("HM8", "warp flip/stretch 검출 무력화", WC,
     [('        "neg_jacobian": int((det <= 0).sum()),', '        "neg_jacobian": 0,'),
      ('        "stretch_over_frac": float((stretch > MAX_LOCAL_STRETCH).mean()),',
       '        "stretch_over_frac": 0.0,')],
     f"{UNITS}", ["warp_rejects_flipped_quad"]),
    ("HM9", "shading 이 chroma 까지 오염", WC,
     [("    shaded = pattern_lab.copy()",
       "    shaded = pattern_lab.copy()\n"
       "    shaded[..., 1:] = (pattern_lab[..., 1:] + carrier_lab[..., 1:]) / 2.0")],
     f"{UNITS}", ["shading_transfer_keeps_source_chroma"]),
    ("HM10", "reference 입력 gate 무력화", SV,
     [("    if min(crop.shape[:2]) < MIN_ROI_SIDE_PX:", "    if False:"),
      ("    if n_periods < MIN_PERIODS_IN_ROI:", "    if False:")],
     f"{UNITS}", ["source_validation_fails_closed"]),
    ("HM11", "geometry carrier mismatch 차단 제거", PM,
     [("                if abs(s_val - c_val) > 2:", "                if False:"),
      ("                if rel > CONSTRUCTION_RATIO_TOL:", "                if False:")],
     f"{UNITS}", ["blocks_carrier_with_mismatched_construction"]),
    # HM12 v2 — hard summary 의 needsReview 는 fail-closed raise(applied is False 키)
    # 뒤의 장식 필드라 flip 이 semantically dead(SURVIVED 실측). 살아있는 경로 =
    # component 부분실패의 soft needsReview 를 지워 auto_pass 승격시키는 변이.
    ("HM12", "component 부분실패 needsReview 제거(자동통과 승격)", MJ,
     [('    needs_review = bool(art.components_needing_review)',
       '    needs_review = False')],
     f"{INTEG}", ["components_needing_review_finalizes_as_review"]),
    # fix-loop v2 에서 추가된 게이트 — 슬랩 클리핑·커프스 보호도 battery 대상이다.
    ("HM13", "per-pixel 배정을 quad 클리핑으로 회귀(cost 상한 0)", WC,
     [("    MAX_ASSIGN_COST = 0.75", "    MAX_ASSIGN_COST = 0.0")],
     f"{FIXV2}", ["covers_silhouette_beyond_panel_quads"]),
    ("HM14", "커프스 보호 밴드 제거", WC,
     [("    CUFF_BAND_FRAC = 0.78", "    CUFF_BAND_FRAC = 10.0")],
     f"{FIXV2}", ["cuff_band_keeps_carrier_pixels"]),
    # HM15: y_top 클립은 close 전/후 두 곳 — 한 곳만 제거하면 재클립이 커버해
    # 생존한다(1차 실행 실측 = 이중 방어의 증명). 질병 표현엔 양쪽 제거가 필요.
    ("HM16", "repeat-invariant None-skip 신호 무시(vision aspect 게이트 재활성)", PM,
     [('                if ("torso_aspect_mask" in source_inventory',
       '                if False and ("torso_aspect_mask" in source_inventory')],
     f"{FIXV2}", ["repeat_invariant_none_signal_skips_vision_aspect_gate"]),
    ("HM15", "해부학 y-경계(칼라 위) 클립 제거(전/후 양쪽)", PM,
     [("    work[:y_top] = 0\n    work[y_bot:] = 0\n    # 프린지/홀 충전",
       "    work[y_bot:] = 0\n    # 프린지/홀 충전"),
      ("    work = cv2.morphologyEx(work, cv2.MORPH_CLOSE, close_kernel)\n    work[:y_top] = 0",
       "    work = cv2.morphologyEx(work, cv2.MORPH_CLOSE, close_kernel)")],
     f"{FIXV2}", ["no_paint_above_collar_or_below_hem"]),
]

before = reviewed_state()
head = before["head"]
sh(f"git -C {REPO} worktree remove --force {MUT} 2>/dev/null || true", check=False)
sh(f"git -C {REPO} worktree add --detach {MUT} {head}")

results = []
try:
    for mid, desc, path, patches, targets, expect_subs in MUTANTS:
        f = MUT / path
        src = f.read_text()
        for old, new in patches:
            if old not in src:
                results.append({"id": mid, "desc": desc, "result": "PATCH_MISS",
                                "missing": old[:80]})
                src = None
                break
            src = src.replace(old, new, 1)
        if src is None:
            print(mid, "PATCH_MISS")
            continue
        f.write_text(src)
        r = sh(f"cd {MUT}/server && {PY} -m pytest {targets} -q -p no:randomly 2>&1 | tail -25",
               check=False)
        out = r.stdout
        failed = "failed" in out
        matched = [sub for sub in expect_subs if sub in out]
        sh(f"git -C {MUT} checkout -- {path}")
        sh(f"find {MUT}/server -name __pycache__ -type d -exec rm -rf {{}} + 2>/dev/null || true",
           check=False)
        results.append({
            "id": mid, "desc": desc, "file": path,
            "cmd": f"pytest {targets}",
            "result": "KILLED" if (failed and matched) else (
                "FAILED_BUT_UNMATCHED" if failed else "SURVIVED"),
            "matched_expected_nodes": matched,
            "tail": out.strip().splitlines()[-2:],
        })
        print(mid, results[-1]["result"], matched)
finally:
    sh(f"git -C {REPO} worktree remove --force {MUT}", check=False)

after = reviewed_state()
report = {"head": head, "reviewed_before": before, "reviewed_after": after,
          "reviewed_unchanged": before == after, "mutants": results}
OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
print("\nreviewed_unchanged:", before == after)
killed = sum(1 for r in results if r["result"] == "KILLED")
print(f"killed: {killed}/{len(results)}")
sys.exit(0 if killed == len(results) else 1)
