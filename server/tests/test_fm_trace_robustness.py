"""추적 층 견고성 — 오너가 지정한 변형 목록(2026-09-26)을 합성 페이지로 돈다. prod 무관.

JPEG q85/q70/q50 · 860/640 폭 축소 · 단독 세로 조각(800~1200px) · WebP q80 · 스크린샷류 PNG 재저장.
전체 수치는 `python -m scripts.fm_trace_robustness --photos` 가 표로 출력한다(PR 본문에 실측 첨부).
여기서는 페이지를 줄이고 변형마다 조각 4개로 **회귀 하한**만 잠근다.
"""
import pytest

from scripts import fm_trace_robustness as H


@pytest.fixture(scope="module")
def report():
    page = H.synthetic_page(2000, seed=8, blocks=6)
    negatives = [H.synthetic_page(2000, seed=31, blocks=6)]
    return H.evaluate(page, negatives=negatives, transforms=H.CORE_TRANSFORMS, strips=4)


def test_invisible_psnr_at_least_40db(report):
    assert report["psnr"] >= 40.0


@pytest.mark.parametrize("name", H.CORE_TRANSFORMS)
def test_every_strip_decodes_on_its_own(report, name):
    row = report["rows"][name]
    assert row["strip_ok"] == row["strips"], row
    assert row["full_ok"], row


@pytest.mark.parametrize("name", H.CORE_TRANSFORMS)
def test_phash_links_strip_to_own_publication_not_the_other(report, name):
    row = report["rows"][name]
    assert row["phash_hits"] == row["strips"], row      # 운영 한도 안에서 자기 배포본이 뜬다
    assert row["phash_own_max"] <= 6, row
    assert row["phash_false"] == 0, row                 # 남의 페이지는 후보로 안 뜬다
