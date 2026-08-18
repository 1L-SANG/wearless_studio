"""생성 직후 관측 판정 병렬화 계약 (2026-08-19 오너 승인).

AG-P2 동일성(image_qc)과 베이스 충실도(base_fidelity)는 같은 생성본을 놓고 서로 독립으로
판정한다 — 입력도 다르고(상품사진 vs 베이스컷) 어느 쪽도 상대 결과를 읽지 않으며 둘 다
fail-open 이다. 직렬로 기다릴 이유가 없어 `_observe_generation_qc` 한 지점에서 동시에
띄운다. 잡당 판정 3~4초 절약이 목적이고, **판정 결과·이벤트 계약은 직렬 시절과 동일**해야
한다(아래 결과/실패 테스트가 그 계약을 고정한다).
"""

import asyncio
import types

from app.workers import mannequin_job as mj


def _call(s, *, prod_imgs, eff="shadow"):
    return dict(
        pool=None, s=s, job_id="j1", candidate="A", attempt=1,
        res=types.SimpleNamespace(image=b"cut", mime="image/png"),
        prod_imgs=prod_imgs, match_img=None, clothing_type="top",
        fit_profile=None, eff_image_qc=eff,
        base_img=types.SimpleNamespace(mime="image/png", data=b"base"),
        product={}, analysis={})


def _settings():
    return types.SimpleNamespace(mannequin_base_fidelity_qc="shadow")


def test_identity_and_base_fidelity_run_concurrently(monkeypatch):
    """동시성 증명 — image_qc 가 base_fidelity 의 시작 신호를 기다린다.
    직렬(image_qc 를 다 기다린 뒤 base_fidelity)이면 데드락 → 타임아웃으로 실패한다."""
    events = []

    async def fake_emit(pool, job_id, et, payload):
        events.append(payload)

    monkeypatch.setattr(mj, "_emit", fake_emit)

    async def main():
        base_started = asyncio.Event()

        async def fake_verdict(s, prod, gen, **kw):
            await asyncio.wait_for(base_started.wait(), timeout=2)
            return {"verdict": "pass", "mismatches": []}

        async def fake_base(**kw):
            base_started.set()
            return {"axes": ["poseFrameMatch"]}

        monkeypatch.setattr(mj.image_qc, "verdict", fake_verdict)
        monkeypatch.setattr(mj, "_apply_base_fidelity_qc", fake_base)
        prod = [mj.InlineImage("image/png", b"p1")]
        return await asyncio.wait_for(
            mj._observe_generation_qc(**_call(_settings(), prod_imgs=prod)), timeout=3)

    p2, base_fidelity = asyncio.run(main())
    assert p2 == {"verdict": "pass", "mismatches": []}
    assert base_fidelity == {"axes": ["poseFrameMatch"]}
    assert [e["status"] for e in events] == ["image_qc"], "판정 성공 이벤트 계약 유지"
    assert events[0]["imageQc"] == p2


def test_image_qc_failure_is_isolated_and_emitted(monkeypatch):
    """동일성 판정이 죽어도 base_fidelity 는 살아 돌아온다 — p2 는 None(게이트 미적용),
    실패 이벤트는 직렬 시절과 같은 shape(image_qc_failed)로 남는다."""
    events = []

    async def fake_emit(pool, job_id, et, payload):
        events.append(payload)

    async def fake_verdict(s, prod, gen, **kw):
        raise RuntimeError("vision down")

    async def fake_base(**kw):
        return {"axes": []}

    monkeypatch.setattr(mj, "_emit", fake_emit)
    monkeypatch.setattr(mj.image_qc, "verdict", fake_verdict)
    monkeypatch.setattr(mj, "_apply_base_fidelity_qc", fake_base)

    p2, base_fidelity = asyncio.run(mj._observe_generation_qc(
        **_call(_settings(), prod_imgs=[mj.InlineImage("image/png", b"p1")])))
    assert p2 is None
    assert base_fidelity == {"axes": []}
    fails = [e for e in events if e.get("status") == "image_qc_failed"]
    assert len(fails) == 1 and fails[0]["error"] == "RuntimeError"


def test_base_fidelity_raise_does_not_kill_candidate_or_identity_qc(monkeypatch):
    """리뷰 지적(8/19): base_fidelity 의 '예외를 안 올린다' 계약은 문서 약속일 뿐이라
    (성공 경로 emit 이 try 밖에서 result shape 을 읽는다), 여기서 한 번 더 잡는다 —
    깨지면 gather 가 후보 전체를 죽이고 동일성 판정 태스크를 고아로 만든다."""
    async def fake_verdict(s, prod, gen, **kw):
        return {"verdict": "pass", "mismatches": []}

    async def fake_base(**kw):
        raise KeyError("poseFrameMatch")  # 미래의 shape 변경 시나리오

    async def fake_emit(pool, job_id, et, payload):
        pass

    monkeypatch.setattr(mj, "_emit", fake_emit)
    monkeypatch.setattr(mj.image_qc, "verdict", fake_verdict)
    monkeypatch.setattr(mj, "_apply_base_fidelity_qc", fake_base)

    p2, base_fidelity = asyncio.run(mj._observe_generation_qc(
        **_call(_settings(), prod_imgs=[mj.InlineImage("image/png", b"p1")])))
    assert p2 == {"verdict": "pass", "mismatches": []}, "동일성 판정은 살아야 한다"
    assert base_fidelity is None, "깨진 관측은 None — 관측 실패가 생성을 못 죽인다"


def test_image_qc_skipped_when_off_or_no_product_images(monkeypatch):
    """스킵 조건(off 모드·상품사진 없음)에서는 판정 콜 자체가 없다 — 직렬 시절과 동일."""
    called = []

    async def fake_verdict(s, prod, gen, **kw):
        called.append(1)
        return {"verdict": "pass"}

    async def fake_base(**kw):
        return None

    async def fake_emit(pool, job_id, et, payload):
        pass

    monkeypatch.setattr(mj, "_emit", fake_emit)
    monkeypatch.setattr(mj.image_qc, "verdict", fake_verdict)
    monkeypatch.setattr(mj, "_apply_base_fidelity_qc", fake_base)

    p2, _ = asyncio.run(mj._observe_generation_qc(
        **_call(_settings(), prod_imgs=[], eff="shadow")))
    assert p2 is None and not called, "상품사진이 없으면 판정 불가"

    p2, _ = asyncio.run(mj._observe_generation_qc(
        **_call(_settings(), prod_imgs=[mj.InlineImage("image/png", b"p1")], eff="off")))
    assert p2 is None and not called, "off 면 판정하지 않는다"
