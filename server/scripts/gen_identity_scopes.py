"""프런트가 읽을 컷 범위 규칙(src/data/identityScopes.json)을 **서버 규칙으로** 만든다.

범위는 예시 id 가 아니라 **컷 모양**에 달려 있다 — 같은 예시도 정면·풀/미디움 블록에 놓일
때만 확정 프로필을 요구한다. 그래서 id 표가 아니라 (1) 판정 규칙과 (2) 서버가 실제로 계산한
대조 케이스를 함께 내보낸다. 프런트는 규칙을 **데이터로** 평가하고, 대조 케이스로 자기
평가기가 서버와 같은 답을 내는지 확인한다(tests/frontend/identity-scope.test.mjs).

실행: cd server && .venv/bin/python -m scripts.gen_identity_scopes [--check]
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agents import confirmed_gpt_runtime, identity_scope  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "src/data/genExamples.json"
SPACE_SETS = ROOT / "src/data/storyboardSpaceSets.json"
OUT = ROOT / "src/data/identityScopes.json"

#: 확정 프로필을 요구하는 블록 모양(confirmed_gpt_runtime.profile_requested 의 조건).
_PROFILE_SHAPE = {
    "cutType": "styling",
    "direction": "front",
    "shot": ["full", "medium"],
    "refScope": "all",
    "pose": "auto",
    "noSpaceSet": True,
}


def _example_ids() -> list[str]:
    rows = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    return [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]


def _space_sets() -> dict[str, list[dict]]:
    data = json.loads(SPACE_SETS.read_text(encoding="utf-8")).get("sets") or []
    return {str(s["id"]): (s.get("members") or []) for s in data if isinstance(s, dict) and s.get("id")}


def _confirmed_profile_examples(ids: list[str]) -> list[str]:
    """그 모양에 놓이면 확정 프로필을 요구하는 예시들(= 가상 전용)."""
    out = []
    for example_id in ids:
        block = {"exampleId": example_id, "cutType": "styling", "direction": "front",
                 "shot": "full", "refScope": "all", "pose": "auto"}
        try:
            if confirmed_gpt_runtime.profile_requested(block):
                out.append(example_id)
        except Exception:  # noqa: BLE001 — 레지스트리 오류는 확정 경로로 본다
            out.append(example_id)
    return sorted(out)


def _virtual_space_sets(sets: dict[str, list[dict]]) -> list[str]:
    out = []
    for set_id, members in sets.items():
        scopes = {
            identity_scope.scope_for_block({
                "exampleId": m.get("exampleId"), "cutType": m.get("cutType"),
                "direction": m.get("direction"), "shot": m.get("shot"),
                "refScope": "pose", "pose": "auto", "spaceGroupId": set_id,
            })
            for m in members if isinstance(m, dict)
        }
        if identity_scope.VIRTUAL in scopes:
            out.append(set_id)
    return sorted(out)


def _cases(ids: list[str], sets: dict[str, list[dict]]) -> list[dict]:
    """서버가 실제로 계산한 답 — 프런트 평가기가 이걸 그대로 재현해야 한다."""
    shapes = [
        {"cutType": "styling", "direction": "front", "shot": "full", "refScope": "all", "pose": "auto"},
        {"cutType": "styling", "direction": "front", "shot": "medium", "refScope": "all", "pose": "auto"},
        {"cutType": "styling", "direction": "back", "shot": "full", "refScope": "all", "pose": "auto"},
        {"cutType": "styling", "direction": "front", "shot": "full", "refScope": "pose", "pose": "auto"},
        {"cutType": "horizon", "direction": "front", "shot": "full", "refScope": "all", "pose": "auto"},
        {"cutType": "product", "direction": "front", "shot": "ghost", "refScope": "all", "pose": "auto"},
    ]
    cases = []
    for example_id in ids[:40]:                      # 표본이면 충분하다(전수는 파일만 키운다)
        for shape in shapes:
            block = {"exampleId": example_id, **shape}
            cases.append({"block": block, "scope": identity_scope.scope_for_block(block)})
    for set_id, members in list(sets.items())[:20]:
        member = (members or [{}])[0]
        block = {"exampleId": member.get("exampleId"), "cutType": member.get("cutType"),
                 "direction": member.get("direction"), "shot": member.get("shot"),
                 "refScope": "pose", "pose": "auto", "spaceGroupId": set_id}
        cases.append({"block": block, "scope": identity_scope.scope_for_block(block)})
    return cases


def build() -> dict:
    ids = _example_ids()
    sets = _space_sets()
    confirmed = _confirmed_profile_examples(ids)
    virtual_sets = _virtual_space_sets(sets)
    # 공간세트 멤버 예시도 세트 id 없이 블록에 올 수 있어 함께 싣는다.
    member_examples = sorted({
        str(m.get("exampleId")) for set_id in virtual_sets for m in sets.get(set_id) or []
        if isinstance(m, dict) and m.get("exampleId")
    })
    return {
        "_meta": {
            "generatedBy": "server/scripts/gen_identity_scopes.py",
            "rule": "server/app/agents/identity_scope.py",
            "scopes": list(identity_scope.SCOPES),
        },
        "rules": [
            {"scope": identity_scope.VIRTUAL,
             "why": "확정 GPT 프로필 — 근거가 가상 모델 확정 시트라 실제 등록자에겐 없다",
             "when": {**_PROFILE_SHAPE, "exampleIn": confirmed}},
            {"scope": identity_scope.VIRTUAL,
             "why": "스튜디오 공간세트 — REAL 미검증(hatchingroom_2161 실측 0/9)",
             "when": {"cutType": "horizon", "spaceSetIn": virtual_sets,
                      "exampleIn": member_examples}},
        ],
        "spaceSets": {set_id: (identity_scope.VIRTUAL if set_id in virtual_sets
                               else identity_scope.BOTH) for set_id in sorted(sets)},
        "cases": _cases(ids, sets),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="쓰지 않고 현재 파일과 비교만")
    args = ap.parse_args()
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("identityScopes.json 이 서버 규칙과 다르다 — 다시 생성해라", file=sys.stderr)
            return 1
        print("ok")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: rules={len(data['rules'])} "
          f"confirmed={len(data['rules'][0]['when']['exampleIn'])} "
          f"virtualSets={len(data['rules'][1]['when']['spaceSetIn'])} cases={len(data['cases'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
