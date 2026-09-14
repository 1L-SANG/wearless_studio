"""등록 사진 → 학습셋 내보내기. 사진은 생체정보라 **새 나갈 길**부터 막는다.

이 스크립트가 지켜야 하는 것은 세 가지다.
  1. --apply 없이는 아무것도 쓰지 않는다.
  2. 레포 안으로는 못 쓴다(커밋될 수 있는 곳에 생체정보를 두지 않는다).
  3. 출력에 R2 키가 그대로 찍히지 않는다(지문 12자만).
그리고 파일 이름은 서버와 같은 상수에서 나와야 한다 — 학습 캡션이 붙는 자리다.
"""

import io
import pathlib
import types

import pytest

from app import facemarket_photos as fp
from scripts import fm_export_training_set as ex


def _row(angle, key=None, qc="passed", state="quarantine", size=1234):
    return {"angle": angle, "r2_key": key or f"facemarket/enrollments/e/{angle}.jpg",
            "mime_type": "image/jpeg", "qc_status": qc, "storage_state": state, "byte_size": size}


# ── 레이아웃 ────────────────────────────────────────────────────────────────
def test_the_layout_is_twelve_training_and_four_reference():
    groups = [group for group, _slot in ex.EXPORTS]
    assert groups.count("train") == 12 and groups.count("refset") == 4
    # 측면은 학습에 안 쓴다 — 공개 자산용 한 장이다
    assert "sh_side" not in [slot for _group, slot in ex.EXPORTS]


def test_file_names_come_from_the_server_constant():
    names = {slot: fp.export_name(slot) for _group, slot in ex.EXPORTS}
    assert names["sl_34"] == "해가왼쪽__3:4_무표정"
    assert names["bl_front"] == "해등지고__정면_무표정"
    assert names["sh_chin_down"] == "그늘__턱_살짝_내리기"
    assert len(set(names.values())) == len(names), "이름이 겹치면 파일이 덮인다"


# ── 계획 ────────────────────────────────────────────────────────────────────
def test_the_plan_keeps_a_slot_for_every_missing_photo():
    """무엇이 비었는지가 이 스크립트의 주 출력이다 — 빈 칸을 조용히 빼면 안 된다."""
    items = ex.plan([_row("sh_front"), _row("sl_34")])
    assert len(items) == len(ex.EXPORTS)
    ready = [item["slot"] for item in items if item["row"]]
    assert ready == ["sh_front", "sl_34"]


@pytest.mark.parametrize("bad", [
    {"qc": "failed"},              # QC 미통과
    {"state": "delete_pending"},   # 파기 예정
    {"key": "   "},                # 키가 비었다
])
def test_unusable_rows_are_not_exported(bad):
    assert [item for item in ex.plan([_row("sh_front", **bad)]) if item["row"]] == []


def test_a_legacy_row_still_fills_its_slot():
    """옛 등록(front·face01)도 sh_front 자리를 채운다 — 후보 사슬은 서버와 같은 함수다."""
    item = next(item for item in ex.plan([_row("front")]) if item["slot"] == "sh_front")
    assert item["row"] is not None and item["angle"] == "front"


# ── 안전 규약 ───────────────────────────────────────────────────────────────
def test_writing_inside_the_repo_is_refused():
    assert ex._refuse_inside_repo(ex.REPO_ROOT / "tmp/ds") is not None
    assert ex._refuse_inside_repo(ex.REPO_ROOT / "server") is not None
    assert ex._refuse_inside_repo(pathlib.Path("/tmp/somewhere-else")) is None


def test_a_dry_run_writes_nothing_and_never_prints_a_key(monkeypatch, tmp_path, capsys):
    key = "facemarket/enrollments/secret-one/quarantine/sh_front.jpg"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/x")
    monkeypatch.setattr(ex, "load_settings", lambda: types.SimpleNamespace(
        database_url="", r2_face_bucket="face", r2_bucket="main"))
    monkeypatch.setattr(ex, "fetch_photos", lambda dsn, eid: [_row("sh_front", key=key)])
    monkeypatch.setattr(ex, "R2Client", lambda *a, **kw: pytest.fail("dry-run 은 R2 를 안 만진다"))
    monkeypatch.setattr("sys.argv", ["x", "6f1d0c2e-0000-4000-8000-000000000001", str(tmp_path / "ds")])

    assert ex.main() == 0

    out = capsys.readouterr().out
    assert key not in out and "db.example" not in out and "postgresql://" not in out
    assert ex._digest(key) in out, "지문으로는 어느 객체였는지 되짚을 수 있어야 한다"
    assert list(tmp_path.iterdir()) == [], "--apply 없이는 아무것도 쓰지 않는다"


def test_a_partial_set_is_refused_unless_asked(monkeypatch, tmp_path, capsys):
    """반쪽 학습셋은 조용한 품질 손실이다 — 기본은 거부."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/x")
    monkeypatch.setattr(ex, "load_settings", lambda: types.SimpleNamespace(
        database_url="", r2_face_bucket="face", r2_bucket="main"))
    monkeypatch.setattr(ex, "fetch_photos", lambda dsn, eid: [_row("sh_front")])
    monkeypatch.setattr(ex, "R2Client", lambda *a, **kw: pytest.fail("쓰기 전에 멈춰야 한다"))
    monkeypatch.setattr("sys.argv",
                        ["x", "6f1d0c2e-0000-4000-8000-000000000001", str(tmp_path / "ds"), "--apply"])

    assert ex.main() == 1
    assert "--allow-partial" in capsys.readouterr().out


def test_apply_writes_the_v7_layout_and_nothing_else(monkeypatch, tmp_path, capsys):
    """--apply 경로 전체 — 디렉터리 두 개, 파일 16장, 이름은 <조명>__<컷>.png."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.example:5432/x")
    monkeypatch.setattr(ex, "load_settings", lambda: types.SimpleNamespace(
        database_url="", r2_face_bucket="face", r2_bucket="main"))
    monkeypatch.setattr(ex, "fetch_photos",
                        lambda dsn, eid: [_row(slot) for _group, slot in ex.EXPORTS])

    class _R2:
        def __init__(self, *a, **kw):
            self.reads = []

        def get_bytes(self, key):
            self.reads.append(key)
            return _jpeg(320, 240)

    r2 = _R2()
    monkeypatch.setattr(ex, "R2Client", lambda *a, **kw: r2)
    out = tmp_path / "ds"
    monkeypatch.setattr("sys.argv",
                        ["x", "6f1d0c2e-0000-4000-8000-000000000001", str(out), "--apply"])

    assert ex.main() == 0

    train = sorted(path.name for path in (out / "train").iterdir())
    refset = sorted(path.name for path in (out / "refset").iterdir())
    assert len(train) == 12 and len(refset) == 4
    assert "해가왼쪽__3:4_무표정.png" in train
    assert refset == sorted(f"{fp.export_name(slot)}.png" for slot in fp.REFSET_SLOTS)
    assert sorted(path.name for path in out.iterdir()) == ["refset", "train"]
    assert len(r2.reads) == 16
    # 키는 여전히 출력에 안 나온다
    assert "facemarket/enrollments/" not in capsys.readouterr().out


# ── 진입점 정규화 ───────────────────────────────────────────────────────────
def _jpeg(width, height, orientation=None):
    from PIL import Image

    buf = io.BytesIO()
    image = Image.new("RGB", (width, height), (90, 120, 200))
    if orientation is None:
        image.save(buf, "JPEG")
    else:
        exif = image.getexif()
        exif[274] = orientation
        image.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def test_normalize_applies_the_rotation_and_drops_the_tag(tmp_path):
    """cv2 는 태그를 적용하고 PIL 은 안 한다 — 그대로 두면 검출과 크롭이 다른 그림을 본다."""
    from PIL import Image

    destination = tmp_path / "그늘__정면_무표정.png"
    size = ex.normalize_png(_jpeg(600, 400, orientation=6), destination)

    assert size == (400, 600), "태그가 픽셀에 적용돼야 한다"
    with Image.open(destination) as written:
        assert written.size == (400, 600)
        assert written.format == "PNG"
        assert written.getexif().get(274) in (None, 1)


def test_normalize_refuses_when_the_two_libraries_disagree(tmp_path, monkeypatch):
    """크기가 갈리면 그 자리에서 멈춘다 — 학습을 시작하고 나서 알면 늦다."""
    import cv2

    monkeypatch.setattr(cv2, "imdecode", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="크기 불일치"):
        ex.normalize_png(_jpeg(200, 200), tmp_path / "x.png")
