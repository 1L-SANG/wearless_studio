"""컷 범위(identity_scope) — 실제/가상 모델이 만들 수 있는 컷을 가른다.

판정은 서버 한 곳(app/agents/identity_scope.py)이고, 프런트가 읽는
src/data/identityScopes.json 은 그 규칙의 출력이다. 둘이 갈라지면 셀러 화면과 서버가
다른 말을 하게 되므로 여기서 재생성해 비교한다.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents import identity_scope

ROOT = Path(__file__).resolve().parents[2]
SCOPES_JSON = ROOT / "src/data/identityScopes.json"


def _block(**over):
    base = {"cutType": "styling", "direction": "front", "shot": "full",
            "refScope": "all", "pose": "auto"}
    base.update(over)
    return base


# ── 규칙 ──
def test_confirmed_gpt_example_is_virtual_only():
    """확정 GPT 프로필의 근거는 가상 모델 확정 시트다 — 실제 등록자에게는 존재할 수 없다."""
    spec = _block(exampleId="ex_styling_men_top_full_snapshot_03")
    assert identity_scope.scope_for_block(spec) == identity_scope.VIRTUAL


def test_studio_space_sets_are_virtual_only():
    """hatchingroom_2161 실측 REAL 0/9 · VIRTUAL 3/3. 나머지 스튜디오 세트도 REAL 미검증."""
    spec = _block(cutType="horizon", spaceGroupId="ssg1__set_horizon_men_bottom_x__sg_1",
                  exampleId="ss_set_horizon_men_bottom_hatchingroom_2161_prod01_01",
                  refScope="pose")
    assert identity_scope.scope_for_block(spec) == identity_scope.VIRTUAL


@pytest.mark.parametrize("spec", [
    _block(cutType="horizon", exampleId=None),           # 공간세트 아닌 스튜디오 컷
    # 2026-09-25: styling 섹션이 열렸다 — 스타일링·미러 컷(둘 다 styling 섹션)도 both 다.
    _block(cutType="styling", exampleId=None),
    _block(cutType="mirror", exampleId=None),
])
def test_studio_and_styling_cuts_are_both(spec):
    assert identity_scope.section_of(spec) in identity_scope.REAL_ALLOWED_SECTION_ROLES
    assert identity_scope.scope_for_block(spec) == identity_scope.BOTH


@pytest.mark.parametrize("spec", [
    _block(cutType="product", exampleId="ex_product_top_ghost_01"),
    _block(cutType="product", shot="detail", exampleId=None),
    _block(cutType="styling", contentRole="hero", exampleId=None),      # hooking 섹션
    _block(cutType="styling", sectionRole="hooking", exampleId=None),
])
def test_cuts_outside_the_studio_and_styling_sections_are_virtual_only(spec):
    """실제 모델은 studio·styling 섹션 컷만 만든다(identity_scope.REAL_ALLOWED_SECTION_ROLES).

    2026-09-14 에 studio 만 열었고, 2026-09-25 사용자 결정으로 styling 을 더했다.
    hooking·product 는 여전히 막힌다. 컷 모양만 보던 예전에는 이것들이 전부 both 였다.
    """
    assert identity_scope.section_of(spec) not in identity_scope.REAL_ALLOWED_SECTION_ROLES
    assert identity_scope.scope_for_block(spec) == identity_scope.VIRTUAL
    # 섹션 규칙을 뺀 판정(공간세트 표가 쓰는 것)은 예전 그대로 both 다.
    assert identity_scope.shape_scope_for_block(spec) == identity_scope.BOTH


def test_unknown_input_never_blocks():
    assert identity_scope.scope_for_block(None) == identity_scope.BOTH
    assert identity_scope.scope_for_block({}) == identity_scope.BOTH


@pytest.mark.parametrize("scope,kind,allowed", [
    (identity_scope.VIRTUAL, identity_scope.REAL, False),
    (identity_scope.VIRTUAL, identity_scope.VIRTUAL, True),
    (identity_scope.REAL, identity_scope.REAL, True),
    (identity_scope.REAL, identity_scope.VIRTUAL, False),
    (identity_scope.BOTH, identity_scope.REAL, True),
    (identity_scope.BOTH, identity_scope.VIRTUAL, True),
    (None, identity_scope.REAL, True),          # 모르는 범위는 막지 않는다
    (identity_scope.VIRTUAL, None, True),       # 모르는 모델 종류도 막지 않는다
])
def test_allows(scope, kind, allowed):
    assert identity_scope.allows(scope, kind) is allowed


def test_identity_kind_reads_the_model_id():
    assert identity_scope.identity_kind("11111111-1111-1111-1111-111111111111") == identity_scope.REAL
    assert identity_scope.identity_kind("mA") == identity_scope.VIRTUAL
    assert identity_scope.identity_kind(None) == identity_scope.VIRTUAL


# ── 프런트 표가 규칙과 같은가 ──
def test_front_table_matches_the_server_rule():
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.gen_identity_scopes", "--check"],
        cwd=str(ROOT / "server"), capture_output=True, text=True)
    assert proc.returncode == 0, (
        "src/data/identityScopes.json 이 서버 규칙과 다르다. "
        "cd server && .venv/bin/python -m scripts.gen_identity_scopes 로 다시 만들어라.\n"
        + proc.stdout + proc.stderr)


def test_front_table_shape():
    data = json.loads(SCOPES_JSON.read_text(encoding="utf-8"))
    assert set(data) >= {"rules", "spaceSets", "cases"}
    assert {r["scope"] for r in data["rules"]} <= set(identity_scope.SCOPES)
    # 초기 분류 — 스튜디오 공간세트와 확정 프로필 컷만 가상 전용이다.
    assert data["spaceSets"]["set_horizon_men_bottom_hatchingroom_2161_prod01"] == "virtual"
    assert any(v == "both" for v in data["spaceSets"].values())
    assert identity_scope.REAL not in {r["scope"] for r in data["rules"]}   # real 전용은 아직 없다


def test_cases_are_the_servers_own_answers():
    """프런트 평가기가 대조할 케이스 — 여기 값이 곧 서버 판정이어야 한다."""
    data = json.loads(SCOPES_JSON.read_text(encoding="utf-8"))
    assert len(data["cases"]) > 100
    for case in data["cases"]:
        assert identity_scope.scope_for_block(case["block"]) == case["scope"], case
    assert {c["scope"] for c in data["cases"]} == {"virtual", "both"}
