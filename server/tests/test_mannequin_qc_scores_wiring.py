"""QC 점수 4중 배선 대칭성 (플랜 §6.5 — 생성 직후 ↔ 재생성 후 표시 일치).

프론트는 두 경로로 컷을 읽는다:
  생성 직후  → pollJob → jobs.result 봉투(finalize_mannequin_success 가 만든 cuts)
  재생성 후  → GET /projects/{id}/mannequins (봉투를 버리고 재조회, httpAdapter.js 주석 명시)

그래서 컬럼·repo select·_cut_to_api·MannequinCut 모델 넷 중 하나만 빠져도 "생성 직후엔
점수가 보이다 재생성하면 사라지는" 비대칭 버그가 남는다. 여기서 두 경로의 shape 이
같은지를 잠근다.
"""
from app.models import MannequinCut
from app.routes import _cut_to_api

# finalize_mannequin_success 가 봉투에 싣는 cut dict (repo.py) 와 동일 shape.
ENVELOPE_CUT_KEYS = {
    "id", "src", "candidate", "version", "baseFit",
    "fitAdjust", "lengthAdjust", "matchAdjust", "qcScores",
}

_SCORES = {
    "product_fidelity": 82, "physical_naturalness": 91,
    "image_quality": 95, "series_consistency": None,
    "critical_errors": [], "outcome": "needs_review",
}


def _row(qc_scores=_SCORES):
    return {
        "candidate": "A", "version": 3, "base_fit": "regular",
        "fit_adjust": None, "length_adjust": None, "match_adjust": None,
        "asset_id": "a1", "r2_key": "k", "qc_scores": qc_scores,
    }


def test_reread_path_shape_matches_envelope_path():
    """두 경로의 키 집합이 동일해야 한다 — 어긋나면 표시가 비대칭이 된다."""
    assert set(_cut_to_api(_row())) == ENVELOPE_CUT_KEYS


def test_reread_path_carries_scores():
    assert _cut_to_api(_row())["qcScores"] == _SCORES


def test_model_declares_qc_scores_or_response_filters_it():
    """response_model 이 미선언 필드를 걸러내므로 모델에 없으면 라우트가 실어도 안 나간다."""
    assert "qc_scores" in MannequinCut.model_fields
    dumped = MannequinCut(**_cut_to_api(_row())).model_dump(by_alias=True)
    assert dumped["qcScores"] == _SCORES


QC_SCORE_KEYS = {
    "product_fidelity", "physical_naturalness", "image_quality", "series_consistency",
    "series_inconsistencies", "critical_errors", "outcome", "salvaged",
}


def test_merge_qc_scores_stays_within_contract():
    """저장되는 qc_scores 는 QcScores 계약(types.js) 키만 담아야 한다.

    구제 경로에서 `best_reject` 에 image_qc 원본(p2)을 담았다가 그대로 저장하면
    `verdict`·`mismatches`·`correctionPrompt` 가 섞여 들어간다. 프론트가 계약 밖 키를
    보게 되고, 마이그레이션 주석의 키 목록과도 어긋난다.
    """
    from app.workers.mannequin_job import merge_qc_scores

    p2 = {"verdict": "retry", "mismatches": ["색 다름"], "correctionPrompt": "fix",
          "product_fidelity": 70, "physical_naturalness": 80, "image_quality": 90,
          "series_consistency": None, "critical_errors": ["logo altered"]}
    merged = merge_qc_scores(p2, {"consistency": 88, "inconsistencies": ["배경 밝음"]},
                             salvaged=True)
    assert set(merged) <= QC_SCORE_KEYS, f"계약 밖 키: {set(merged) - QC_SCORE_KEYS}"
    for leaked in ("verdict", "mismatches", "correctionPrompt"):
        assert leaked not in merged
    assert merged["series_consistency"] == 88
    assert merged["salvaged"] is True


def test_merge_qc_scores_none_when_no_signal():
    from app.workers.mannequin_job import merge_qc_scores
    assert merge_qc_scores(None, None) is None


def test_missing_scores_stay_none_not_crash():
    """QC off·구 행은 qc_scores 가 null — 표시 경로가 죽으면 안 된다."""
    assert _cut_to_api(_row(qc_scores=None))["qcScores"] is None
    row = _row(qc_scores=None)
    del row["qc_scores"]  # select 에서 컬럼이 빠진 레거시 경로 방어
    assert _cut_to_api(row)["qcScores"] is None
    assert MannequinCut(**_cut_to_api(row)).qc_scores is None
