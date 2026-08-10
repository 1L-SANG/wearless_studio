"""계약 5 — 파이프라인 불확실성을 **사람 검수로 떠넘기지 않는다**.

무엇이 잘못됐었나
-----------------
65~79점은 `needs_review` 로 등급이 매겨지고 곧장 출고됐다. 고칠 예산이 남아 있는데도
"사람이 보라"는 배지를 달고 나가서 과금까지 됐다. 계약 5 는 검수를 복구 수단으로 쓰지
말라고 한다 — 고칠 수 있으면 먼저 고친다.

무엇을 바꾸지 않았나
--------------------
"고쳐라"가 "못 고치면 실패시켜라"는 아니다. 예산이 없으면 그대로 출고한다(계약 3:
후보 실패가 곧 잡 실패는 아니다). 재시도는 여전히 유한하다(계약 11) — 이 판정은
호출자의 예산 검사 **앞에** 있을 뿐 예산을 늘리지 않는다.
"""

import inspect

import pytest

from app.workers import mannequin_job
from tests.conftest import make_settings


def _scores(fidelity: int) -> dict:
    return {"product_fidelity": fidelity, "physical_naturalness": fidelity,
            "image_quality": fidelity, "series_consistency": fidelity,
            "critical_errors": []}


def _enforce():
    return make_settings(image_qc="enforce")


@pytest.mark.parametrize("score", [65, 70, 79])
def test_a_repairable_score_is_repaired_not_shipped_for_review(score):
    s = _enforce()
    assert mannequin_job.score_outcome(s, _scores(score)) == "needs_review"
    assert mannequin_job.final_decision(s, _scores(score)) == "retry"


@pytest.mark.parametrize("score", [80, 90, 100])
def test_a_passing_score_still_ships_immediately(score):
    s = _enforce()
    assert mannequin_job.final_decision(s, _scores(score)) == "ship"


def test_a_regenerate_score_still_retries():
    s = _enforce()
    assert mannequin_job.final_decision(s, _scores(30)) == "retry"


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_observation_modes_are_untouched(mode):
    """off/shadow 는 관측이다 — 무엇이 나와도 출고한다. 이 변경이 거기 새면 안 된다."""
    s = make_settings(image_qc=mode)
    for score in (30, 70, 95):
        assert mannequin_job.final_decision(s, _scores(score)) == "ship", score


def test_missing_scores_still_ship():
    """판정이 없는 것은 나쁨이 아니다 — 기존 관용 그대로."""
    s = _enforce()
    assert mannequin_job.final_decision(s, None) == "ship"
    assert mannequin_job.final_decision(s, {}) == "ship"


def test_only_the_looping_branch_retries_and_it_requires_budget():
    """실제로 **되도는** 분기는 하나뿐이고 그것만 예산을 요구한다.

    이게 없으면 needs_review 가 무한 복구 루프가 된다(계약 11 위반). 나머지 두 분기는
    되돌지 않는다 — 하나는 fail-closed 로 올리고, 하나는 예산 소진 후 **구제 출고**다.
    """
    src = inspect.getsource(mannequin_job)
    lines = src.splitlines()
    looping = []
    for i, line in enumerate(lines):
        if 'final_decision(s, qc_scores) == "retry"' not in line:
            continue
        body = "\n".join(lines[i + 1:i + 14])
        if "continue" in body:
            looping.append(line)
    assert len(looping) == 1, looping
    assert "budget_left" in looping[0], looping[0]


def test_the_budget_exhausted_path_ships_instead_of_failing():
    """계약 3: 후보 실패가 곧 잡 실패는 아니다 — 못 고치면 최선본으로 출고한다."""
    src = inspect.getsource(mannequin_job)
    # `budget_exhausted` 는 두 곳이다(사전 게이트 구제 / 최종 구제). 최종 쪽을 본다 —
    # needs_review 가 새로 도달하는 경로가 그것이다.
    marker = src.rindex('"reason": "budget_exhausted"')
    window = src[marker - 700:marker]
    assert '"salvaged": True' in window, window[-300:]
    assert "_is_better_candidate" in window, window[-300:]


def test_the_decision_stays_pure():
    """예산·시계·DB 를 보지 않는다 — 판정과 실행을 섞으면 둘 다 시험 불가가 된다."""
    src = inspect.getsource(mannequin_job.final_decision)
    code = src.split('"""')[2]          # 산문이 아니라 **코드**를 본다
    for forbidden in ("await", "pool", "conn", "budget"):
        assert forbidden not in code, forbidden
