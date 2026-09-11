"""REAL 등록자에게 MODEL FULL BODY 슬롯 열기 — 자산 해석·매니페스트 라벨·순서 고정.

세 조합이 서로 밀리지 않아야 한다:
  REAL 2장(기존)   MODEL        + MODEL SHEET
  REAL 3장(신규)   MODEL FACE   + MODEL SHEET + MODEL FULL BODY
  VIRTUAL 2장(기존) MODEL FACE   + MODEL FULL BODY
전신 없는 등록자의 프롬프트는 바이트 단위로 기존과 같아야 한다.
"""
import pytest

from app.agents.cut_generator import build_manifest

PROD = [{"slot": "Front"}, {"slot": "Back"}]


def _manifest(**kw):
    return build_manifest(PROD, has_mannequin=False, has_match=False, mood_count=0,
                          has_space_set_plate=True, **kw)


def _roles(text):
    return [line.split(". ", 1)[1].split(" —")[0].split(" (")[0].strip()
            for line in text.splitlines() if line[:1].isdigit()]


def test_real_two_assets_unchanged():
    roles = _roles(_manifest(has_model_face=True, has_model_sheet=True, has_model_full_body=False))
    assert roles[:2] == ["MODEL", "MODEL SHEET"]
    assert "MODEL FULL BODY" not in roles


def test_real_three_assets_face_sheet_fullbody_in_order():
    roles = _roles(_manifest(has_model_face=True, has_model_sheet=True, has_model_full_body=True))
    assert roles[:3] == ["MODEL FACE", "MODEL SHEET", "MODEL FULL BODY"]
    assert roles[3:5] == ["PRODUCT", "PRODUCT"]


def test_virtual_two_assets_unchanged():
    roles = _roles(_manifest(has_model_face=True, has_model_sheet=False, has_model_full_body=True))
    assert roles[:2] == ["MODEL FACE", "MODEL FULL BODY"]
    assert "MODEL SHEET" not in roles


def test_sheet_and_fullbody_without_face_still_rejected():
    """옛 가드의 진짜 위험 — 얼굴 슬롯 없이 시트를 체형 근거로 위장하는 경우는 계속 막는다."""
    with pytest.raises(ValueError, match="conflicting_model_body_authority"):
        _manifest(has_model_face=False, has_model_sheet=True, has_model_full_body=True)


def test_manifest_order_matches_resolver_order():
    """매니페스트 순서 = identity_source 반환 순서(face_front → grid_sedcard → body_front)."""
    import inspect
    from app.agents import identity_source
    src = inspect.getsource(identity_source.resolve_real_model_assets)
    assert 'for view in ("face_front", "grid_sedcard")' in src
    assert 'if "body_front" in by_view' in src
    assert src.index('"grid_sedcard"') < src.index('"body_front"')
    roles = _roles(_manifest(has_model_face=True, has_model_sheet=True, has_model_full_body=True))
    assert roles.index("MODEL FACE") < roles.index("MODEL SHEET") < roles.index("MODEL FULL BODY")


def test_body_front_is_optional_and_pinned_the_same_way():
    """전신은 선택이지만 핀(등록·정책 버전·비공개 버킷)은 얼굴과 똑같이 건다."""
    import inspect
    from app.agents import identity_source
    src = inspect.getsource(identity_source.resolve_real_model_assets)
    assert 'r.get("bucket") != "face"' in src
    assert 'r.get("evidence_version") != policy_version' in src
    # 전신 핀이 어긋나면 전체 거부(반쪽 근거 금지)
    assert src.count("return None") >= 3


def test_editor_worker_branches_on_image_count():
    """editor_image_job 이 len==2 하드코딩이 아니라 장수로 분기한다."""
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[1] / "app" / "workers" / "editor_image_job.py").read_text()
    assert "n_model_images = len(model_images)" in text
    assert "len(model_images) == 2" not in text
    assert "has_model_face=n_model_images >= 2" in text
    assert "has_model_sheet=n_model_images == 3 or (n_model_images == 2 and not model_has_full_body)" in text


def test_migration_adds_body_front_and_pins_bucket():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    sql = (root / "supabase" / "migrations" / "20260910000000_fm_model_assets_body_front.sql").read_text()
    assert "'face_front', 'grid_sedcard', 'body_front'" in sql
    assert "view <> 'body_front' or bucket = 'face'" in sql
    assert "drop constraint if exists" in sql  # PG16-safe · 재실행 가능
