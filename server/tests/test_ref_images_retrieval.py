"""검색 증강 Phase 3 — 레퍼런스 컷 벡터 랭킹(순수) + flag-off 무동작 테스트.

실 pgvector 검색(repo.search_ref_images)·SigLIP 임베딩은 DB·torch 의존이라 여기서 제외
(E2E 는 scripts.embed_corpus + 로컬 54322 로 검증). 여기서는 결정적 순수 로직만 잠근다."""

import asyncio
from types import SimpleNamespace

from app.services.retrieval import cosine, rank_ref_images_by_vector
from app.workers.mannequin_job import _load_style_refs, _ref_manifest_lines


# ─────────────────────────── cosine ───────────────────────────

def test_cosine_identical_is_one():
    assert cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == 1.0


def test_cosine_orthogonal_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_unnormalized():
    # 방향 같고 크기만 다르면 코사인 1.0.
    assert abs(cosine([2.0, 0.0], [5.0, 0.0]) - 1.0) < 1e-9


def test_cosine_zero_and_mismatch_are_zero():
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 2.0], [1.0]) == 0.0  # 길이 불일치


# ─────────────────────────── rank_ref_images_by_vector ───────────────────────────

def _row(id_, vec):
    return {"id": id_, "embedding": vec}


def test_rank_orders_by_cosine_desc():
    q = [1.0, 0.0]
    rows = [_row("b", [0.0, 1.0]), _row("a", [1.0, 0.0]), _row("c", [0.7, 0.7])]
    ranked = rank_ref_images_by_vector(rows, q)
    assert [r["id"] for r in ranked] == ["a", "c", "b"]


def test_rank_tie_break_by_id_asc():
    q = [1.0, 0.0]
    rows = [_row("z", [1.0, 0.0]), _row("a", [1.0, 0.0])]  # 동일 유사도
    ranked = rank_ref_images_by_vector(rows, q)
    assert [r["id"] for r in ranked] == ["a", "z"]  # id 오름차순


def test_rank_k_limits_result():
    q = [1.0, 0.0]
    rows = [_row("a", [1.0, 0.0]), _row("b", [0.9, 0.1]), _row("c", [0.0, 1.0])]
    assert [r["id"] for r in rank_ref_images_by_vector(rows, q, k=2)] == ["a", "b"]


def test_rank_missing_embedding_sinks_to_bottom():
    q = [1.0, 0.0]
    rows = [{"id": "noemb"}, _row("a", [1.0, 0.0])]
    ranked = rank_ref_images_by_vector(rows, q)
    assert ranked[0]["id"] == "a" and ranked[-1]["id"] == "noemb"


def test_rank_empty_rows():
    assert rank_ref_images_by_vector([], [1.0, 0.0]) == []


# ─────────────────────────── _ref_manifest_lines ───────────────────────────

def test_ref_manifest_lines_numbering():
    out = _ref_manifest_lines(5, 2)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("5. STYLE REFERENCE")
    assert lines[1].startswith("6. STYLE REFERENCE")
    assert "DIFFERENT garment" in lines[0]


# ─────────────────────────── _load_style_refs: flag off = 무동작 ───────────────────────────

def test_load_style_refs_off_returns_empty_without_touching_db():
    # retrieval_refimages != 'on' 이면 임베딩·검색 없이 즉시 ([], []) — 행위 변화 0 게이트.
    s = SimpleNamespace(retrieval_refimages="off", embed_image_model="x", embed_image_dim=768,
                        ref_images_topk=2)
    app = SimpleNamespace(state=SimpleNamespace())  # pool/r2 접근 없어야 정상
    prod = [SimpleNamespace(mime="image/png", image=b"\x89PNG")]
    refs, ids = asyncio.run(_load_style_refs(app, s, prod_imgs=prod, clothing_type="top", gender="women"))
    assert refs == [] and ids == []


def test_load_style_refs_on_but_no_prod_imgs_returns_empty():
    s = SimpleNamespace(retrieval_refimages="on", embed_image_model="x", embed_image_dim=768,
                        ref_images_topk=2)
    app = SimpleNamespace(state=SimpleNamespace())
    refs, ids = asyncio.run(_load_style_refs(app, s, prod_imgs=[], clothing_type="top", gender="women"))
    assert refs == [] and ids == []
