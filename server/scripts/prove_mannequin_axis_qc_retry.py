"""P1 축 QC + 편집 교정(retry-as-edit) 실증 하네스 — Codex ultra 설계 §7 페어드 리플레이.

프로덕션 경로(workers.mannequin_job._run_candidate)를 그대로 통과시키며:
  [shadow]  4 arm 실생성(1K) → 실판정 → 실패 기록만·편집 미발화 검증
  [enforce] 동일 원본 바이트 리플레이(확률 차 제거) → 실패 arm 만 실편집 1콜 → 재판정 → 채택 검증
가드는 프로세스 내 try/finally 몽키패치만 — env/CLI 우회 없음. prod 자원(DB·잡·크레딧·R2 쓰기) 무접촉.

실행: cd server && .venv/bin/python -m scripts.prove_mannequin_axis_qc_retry
출력: server/ab_out/mannequin_axis_qc_proof/<run-id>/ (이미지·events·results·summary·contact sheet)
"""

import asyncio
import dataclasses
import hashlib
import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

from scripts.smoke_realwire import _load_env  # noqa: E402

_load_env(SERVER / ".env")

from scripts.fit_fidelity_campaign import (  # noqa: E402
    ARMS, BASE_R2, CLOTHING_TYPE, PRODUCT_NAME, SRC, WB_R2, load_local,
)
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.prompts import load_prompt_template  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.workers import mannequin_job  # noqa: E402

PROOF_ARMS = ["O01", "O02", "O05", "O06"]  # 음성 3 + 양성 대조 1 (O06: adjustedAxes=() 검증 겸)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class _Conn:
    async def commit(self):
        return None


class _Pool:
    def connection(self):
        @asynccontextmanager
        async def _cm():
            yield _Conn()
        return _cm()


class _CaptureR2:
    """R2 쓰기를 로컬 캡처로 대체 — 원격 무접촉."""

    def __init__(self):
        self.puts = []

    def put_bytes(self, key, data, mime):
        self.puts.append({"key": key, "data": data, "mime": mime})

    def delete(self, key):
        pass


class _RecordingGemini:
    """[shadow] 실호출 + (모델·프롬프트해시·입력해시·크기·비율·결과) 기록."""

    def __init__(self, real):
        self.real = real
        self.records = []

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        assert size == "1K", f"1K 강제 위반: {size}"  # 사용자 요구 — 모든 외부 콜 1K
        res = await self.real.generate_content_image(model, prompt, images, size,
                                                     aspect_ratio=aspect_ratio, timeout=300.0)
        self.records.append({
            "model": model, "prompt_hash": _sha(prompt.encode()),
            "input_hashes": [_sha(im.data) for im in images],
            "size": size, "aspect_ratio": aspect_ratio, "res": res})
        return res


class _ReplayGemini:
    """[enforce] 1번째 콜=기록된 원본 재생(엄격 대조), 이후 콜=실편집(1K 검증)."""

    def __init__(self, real, record):
        self.real = real
        self.record = record
        self.calls = 0
        self.edit_records = []

    async def generate_content_image(self, model, prompt, images, size, aspect_ratio=None):
        assert size == "1K", f"1K 강제 위반: {size}"
        self.calls += 1
        if self.calls == 1:  # 원본 리플레이 — 생성 조건 완전 일치 확인 후 동일 바이트 반환
            r = self.record
            assert model == r["model"], "리플레이 모델 불일치"
            assert _sha(prompt.encode()) == r["prompt_hash"], "리플레이 프롬프트 불일치"
            assert [_sha(im.data) for im in images] == r["input_hashes"], "리플레이 입력 불일치"
            assert (size, aspect_ratio) == (r["size"], r["aspect_ratio"]), "리플레이 파라미터 불일치"
            return r["res"]
        res = await self.real.generate_content_image(model, prompt, images, size,
                                                     aspect_ratio=aspect_ratio, timeout=300.0)
        self.edit_records.append({"prompt_hash": _sha(prompt.encode()),
                                  "input_hashes": [_sha(im.data) for im in images], "res": res})
        return res


def _arm_inputs(arm, r2c):
    base = InlineImage("image/png", r2c.get_bytes(BASE_R2[arm["gender"]]))
    srcs = [load_local(p) for p in SRC[arm["src"]]]
    wb = InlineImage("image/png", r2c.get_bytes(WB_R2)) if arm["with_bottom"] else None
    return base, srcs, wb


def _manifest(n_src, with_bottom):
    lines = ["1. Base mannequin — the canvas to dress (keep it identical)"]
    for i in range(n_src):
        lines.append(f"{i+2}. {'front view of the garment' if i == 0 else 'back view of the garment'}")
    if with_bottom:
        lines.append(f"{n_src+2}. matching BOTTOM garment — also dress the mannequin in this, coordinated with the top")
    return "\n".join(lines)


async def _run_arm(arm, *, settings, gemini, template, inputs, events_sink):
    base, srcs, wb = inputs
    profile = {"category": arm["category"], "gender": arm["gender"], "source": "seller",
               "axes": arm["axes"], "version": 1}
    r2cap = _CaptureR2()
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        settings=settings, pool=_Pool(), r2=r2cap, gemini=gemini))
    job = {"id": f"proof-{arm['id']}", "user_id": "proof", "project_id": "proof",
           "lease_token": "proof:t"}

    async def sink(pool, job_id, event_type, payload):
        events_sink.append({"armId": arm["id"], "type": event_type, **payload})

    orig_emit = mannequin_job._emit
    mannequin_job._emit = sink
    try:
        result = await mannequin_job._run_candidate(
            app=app, job=job, candidate="A", base_fit="regular", base_gender=arm["gender"],
            base_img=base, prod_imgs=srcs, match_img=wb,
            product_count=len(srcs) + (1 if wb else 0), template=template,
            product={"name": PRODUCT_NAME[arm["category"]],
                     "clothing_type": CLOTHING_TYPE[arm["category"]]},
            analysis={"clothingType": CLOTHING_TYPE[arm["category"]],
                      "targetGenders": [arm["gender"]]},
            clothing_type=CLOTHING_TYPE[arm["category"]],
            image_manifest=_manifest(len(srcs), wb is not None),
            fit_profile=profile,
            adjusted_axes=() if arm["id"] == "O06" else tuple(arm["adjusted"]),
            fit_profile_source="payload_snapshot")
    finally:
        mannequin_job._emit = orig_emit
    assert result is not None, f"{arm['id']}: 후보 드롭 (예상 밖)"
    assert len(r2cap.puts) == 1
    return r2cap.puts[0]  # 채택본 = 저장본


def _arm_events(events, arm_id, status):
    return [e for e in events if e["armId"] == arm_id and e.get("status") == status]


async def main():
    s0 = load_settings()
    settings = dataclasses.replace(
        s0, mannequin_image_size="1K", mannequin_aspect_ratio="2:3",
        mannequin_max_attempts=2, mannequin_tier="image_high", image_qc="off")
    real = GeminiImageClient(settings)
    template = load_prompt_template(settings)
    r2c = R2Client(s0)
    arms = [a for a in ARMS if a["id"] in PROOF_ARMS]
    inputs = {a["id"]: _arm_inputs(a, r2c) for a in arms}  # 입력 read-only 프리로드

    import time as _t
    run_dir = SERVER / "ab_out" / "mannequin_axis_qc_proof" / _t.strftime("run-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    events, results, issues = [], [], []

    # ---------- Phase 1: shadow ----------
    print("[shadow] 4 arm 실생성 + 실판정 (편집 미발화 검증)")
    shadow_settings = dataclasses.replace(settings, mannequin_axis_qc="shadow")
    shadow_records, shadow_selected = {}, {}
    for arm in arms:
        rec_client = _RecordingGemini(real)
        put = await _run_arm(arm, settings=shadow_settings, gemini=rec_client,
                             template=template, inputs=inputs[arm["id"]], events_sink=events)
        assert len(rec_client.records) == 1, "shadow 에서 외부 콜 1회 초과"
        shadow_records[arm["id"]] = rec_client.records[0]
        shadow_selected[arm["id"]] = put
        ext = "png" if put["mime"] == "image/png" else "jpg"
        (run_dir / f"{arm['id']}_shadow_original.{ext}").write_bytes(put["data"])
        qc = _arm_events(events, arm["id"], "axis_qc")
        retry = _arm_events(events, arm["id"], "axis_retry")
        assert len(qc) == 1 and len(retry) == 1, f"{arm['id']}: shadow 이벤트 수 불일치"
        assert retry[0]["fired"] is False
        assert _sha(put["data"]) == qc[0]["image_hash"], "채택본≠판정본"
        outcome = qc[0]["outcome"]
        expected_fail = arm["id"] != "O06"
        if expected_fail and outcome != "fail":
            issues.append(f"INCONCLUSIVE_FIXTURE_NOT_REPRODUCED: {arm['id']} 1차 생성이 통과")
        if not expected_fail and outcome != "pass":
            issues.append(f"양성 대조 {arm['id']} 실패: {qc[0]}")
        if expected_fail and qc[0]["identity_pass"] is not True:
            issues.append(f"INCONCLUSIVE: {arm['id']} identity 실패 — 핏 미스 증거 아님")
        results.append({"phase": "shadow", "armId": arm["id"], "outcome": outcome,
                        "retry_outcome": retry[0]["outcome"],
                        "image_hash": qc[0]["image_hash"]})
        print(f"  {arm['id']}: {outcome} / retry={retry[0]['outcome']}")

    # ---------- Phase 2: enforce (가드 몽키패치, try/finally) ----------
    print("[enforce] 원본 리플레이 + 실패 arm 실편집")
    enforce_settings = dataclasses.replace(settings, mannequin_axis_qc="enforce")
    orig_guard = mannequin_job._MANNEQUIN_AXIS_QC_ENFORCEMENT_READY
    mannequin_job._MANNEQUIN_AXIS_QC_ENFORCEMENT_READY = True
    edits_fired = 0
    try:
        for arm in arms:
            replay = _ReplayGemini(real, shadow_records[arm["id"]])
            put = await _run_arm(arm, settings=enforce_settings, gemini=replay,
                                 template=template, inputs=inputs[arm["id"]],
                                 events_sink=events)
            ext = "png" if put["mime"] == "image/png" else "jpg"
            (run_dir / f"{arm['id']}_enforce_selected.{ext}").write_bytes(put["data"])
            qc = [e for e in _arm_events(events, arm["id"], "axis_qc")
                  if e["effective_mode"] == "enforce"]
            retry = [e for e in _arm_events(events, arm["id"], "axis_retry")
                     if e["effective_mode"] == "enforce"]
            assert len(retry) == 1
            r = retry[0]
            shadow_qc = next(e for e in _arm_events(events, arm["id"], "axis_qc")
                             if e["effective_mode"] == "shadow")
            replay_qc = qc[0]
            same_signature = (
                [(x["axis"], x["pass"], x["visible"]) for x in shadow_qc["axis_pass"]]
                == [(x["axis"], x["pass"], x["visible"]) for x in replay_qc["axis_pass"]])
            if not same_signature:
                issues.append(f"INCONCLUSIVE_JUDGE_NONDETERMINISM: {arm['id']}")
            # 기대치는 고정 역할이 아닌 **이 run 의 셰도우 실측**에서 유도 (스펙: 비재현은
            # INCONCLUSIVE 기록으로 끝, 편집 메커니즘 증명은 실제 실패한 arm 이 담당).
            shadow_failed = next(
                x for x in results
                if x["phase"] == "shadow" and x["armId"] == arm["id"])["outcome"] == "fail"
            if not same_signature:
                pass  # 비결정 arm 은 issues 기록으로 종결 — 엄격 검증에서 제외
            elif not shadow_failed:
                assert replay.calls == 1 and r["outcome"] == "not_needed", r
                assert _sha(put["data"]) == _sha(shadow_records[arm["id"]]["res"].image)
            else:
                assert replay.calls == 2, f"{arm['id']}: 편집 정확히 1회여야 함({replay.calls-1})"
                edits_fired += 1
                edit_rec = replay.edit_records[0]
                assert edit_rec["input_hashes"] == [
                    _sha(shadow_records[arm["id"]]["res"].image)], "편집 입력≠실패 원본 1장"
                edited_ok = r["outcome"] == "edited_selected"
                if edited_ok:
                    (run_dir / f"{arm['id']}_enforce_edit.{ext}").write_bytes(put["data"])
                else:
                    issues.append(f"편집 미채택: {arm['id']} → {r['outcome']}")
                subj = [q["subject"] for q in qc]
                assert subj == ["generated", "edited"], subj
            results.append({"phase": "enforce", "armId": arm["id"],
                            "retry_outcome": r["outcome"],
                            "selected_hash": _sha(put["data"]),
                            "edit_hash": r.get("edit_hash")})
            print(f"  {arm['id']}: retry={r['outcome']}")
    finally:
        mannequin_job._MANNEQUIN_AXIS_QC_ENFORCEMENT_READY = orig_guard
    assert mannequin_job._MANNEQUIN_AXIS_QC_ENFORCEMENT_READY is False, "가드 복원 실패"

    # ---------- 산출물 ----------
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events))
    (run_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results))
    summary = {
        "arms": PROOF_ARMS,
        "external_image_calls": 4 + edits_fired,
        "edits_fired": edits_fired,
        "guard_restored": mannequin_job._MANNEQUIN_AXIS_QC_ENFORCEMENT_READY is False,
        "issues": issues,
        "automated_verdict": "PASS" if not issues else "SEE issues",
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    # 컨택트시트: 소스 | shadow 원본 | enforce 채택본
    try:
        from PIL import Image
        from io import BytesIO
        thumbs = []
        H = 480
        for arm in arms:
            trio = [Image.open(SRC[arm["src"]][0]),
                    Image.open(BytesIO(shadow_records[arm["id"]]["res"].image)),
                    Image.open(BytesIO((run_dir / f"{arm['id']}_enforce_selected.png").read_bytes()
                                       if (run_dir / f"{arm['id']}_enforce_selected.png").exists()
                                       else (run_dir / f"{arm['id']}_enforce_selected.jpg").read_bytes()))]
            trio = [im.convert("RGB").resize((int(im.width * H / im.height), H)) for im in trio]
            thumbs.append(trio)
        w = max(sum(im.width for im in row) + 20 for row in thumbs)
        sheet = Image.new("RGB", (w, H * len(thumbs) + 10 * len(thumbs)), "white")
        y = 0
        for row in thumbs:
            x = 0
            for im in row:
                sheet.paste(im, (x, y))
                x += im.width + 10
            y += H + 10
        sheet.save(run_dir / "contact_sheet.jpg", quality=88)
    except Exception as e:
        print(f"(contact sheet 생략: {e})")

    print(f"\n자동판정: {summary['automated_verdict']} · 외부 이미지 콜 {summary['external_image_calls']}"
          f" (원본 4 + 편집 {edits_fired}) · 산출물 {run_dir}")
    for i in issues:
        print("  ⚠️", i)


if __name__ == "__main__":
    asyncio.run(main())
