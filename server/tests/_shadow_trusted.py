"""검증을 실제로 통과한 trusted verification 을 테스트에 제공한다.

`trusted` 는 중앙 verifier 만 만들 수 있게 봉인돼 있다(그게 이번 P1 의 핵심이다).
그래서 판정 **로직** 을 보는 테스트도 진짜 파일·manifest 를 세워 verifier 를
통과시켜야 한다 — 느슨한 fixture 로 trust 를 흉내내면 계약을 덮는다.

세션당 한 번만 만들어 재사용한다(파일 I/O 라 매번 세우면 느리다).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import tempfile

SERVER = pathlib.Path(__file__).resolve().parents[1]
_CACHE = {}


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, SERVER / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_trusted(n: int = 2, *, dataset_id: str = "ds"):
    """실제 파일 → manifest → verifier. → (verification, dataset_dir, source_dir, rows)"""
    from app import shadow_cases as scases
    from app import shadow_verification as sv
    from app.config import load_settings

    SC = _load("_sc_trusted", "scripts/shadow_collect.py")
    SM = _load("_sm_trusted", "scripts/shadow_manifest.py")
    root = pathlib.Path(tempfile.mkdtemp())
    src_dir = root / "sources"
    src_dir.mkdir()
    origin = sorted((SERVER.parent / "public" / "assets" / "fit-examples")
                    .glob("*.jpg"))[0]
    shutil.copy(origin, src_dir / origin.name)
    ds = root / dataset_id
    ds.mkdir()
    s = load_settings()
    raw = (src_dir / origin.name).read_bytes()
    rows = []
    for i, (name, ch) in enumerate(scases.VARY_CASES[:n]):
        prep = scases.generation_prepared(s, ch)
        vp = scases.vision_prepared(ch)
        ob = b"PNG-" + bytes([i])
        (ds / f"{name}.png").write_bytes(ob)
        rows.append({"id": name, "output_id": f"o{i}", "case": name,
                     "source": origin.name, "source_kind": "editor_asset",
                     "edit_type": SC.editor_vary.edit_type_for(ch),
                     "image_calls": 1, "vision_calls": 1,
                     "human_label": "fidelity_pass",
                     "edit_qc_result": {"decision": "pass",
                                        "vision": {"meta": {"status": "ok"}}},
                     "provenance": SC._provenance(
                         prep, case_name=name, changes=ch, attempt=1,
                         source_bytes=raw, output_bytes=ob,
                         vision_meta={"promptSha256": vp.prompt_sha256,
                                      "provider": "p", "status": "ok"})})
    (ds / "samples.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    manifest = SM.build(str(ds / "samples.jsonl"), dataset_id=dataset_id,
                        invalid_reasons=[], image_usd=0, vision_usd=0,
                        collected_at="t", command=None, source_dir=src_dir)
    (ds / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
    verification = sv.verify_manifest_for_report(
        manifest=manifest, rows=rows, samples_path=ds / "samples.jsonl",
        source_dir=src_dir)
    return verification, ds, src_dir, rows, manifest


def trusted():
    """판정 로직 테스트용 — 실제 검증을 통과한 verification(세션 캐시)."""
    if "v" not in _CACHE:
        v = build_trusted()[0]
        assert v.trusted, v.problems
        _CACHE["v"] = v
    return _CACHE["v"]
