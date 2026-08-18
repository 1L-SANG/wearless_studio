from pathlib import Path

import yaml

from app.config import Settings, load_settings


def test_output_qc_defaults_off_and_accepts_repair(monkeypatch):
    assert Settings.__dataclass_fields__["cut_output_qc_mode"].default == "off"
    assert Settings.__dataclass_fields__["page_output_qc_mode"].default == "off"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "shadow")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "shadow")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "shadow"
    assert settings.page_output_qc_mode == "shadow"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "repair")
    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE", "1K")
    monkeypatch.setenv("DETAIL_CUT_IMAGE_SIZE", "4K")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "repair"
    assert settings.mannequin_image_size == "1K"
    assert settings.detail_cut_image_size == "4K"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "enforce")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "invalid")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "off"
    assert settings.page_output_qc_mode == "off"


def test_production_manifest_bounds_4k_gemini_repair_concurrency():
    manifest_path = Path(__file__).resolve().parents[2] / "copilot/api/manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    variables = manifest["variables"]

    assert variables["MODEL_ROUTING_IMAGE_HIGH"] == "gemini-3-pro-image"
    assert variables["DETAIL_CUT_IMAGE_SIZE"] == "4K"
    assert variables["DETAIL_CUT_CONCURRENCY"] == "5"
    assert variables["DETAIL_CUT_STAGGER_MS"] == "3000"
    assert variables["GARMENT_QC_MODE"] == "off"
    assert variables["CUT_OUTPUT_QC_MODE"] == "repair"

    # 동시 실행과 메모리는 **같이** 움직여야 한다. 4K 컷 하나가 응답 base64 + 디코딩본 +
    # QC 입력으로 ~150~180MB 를 잡는다(9컷 병렬이 2GB 태스크를 죽인 실측, 2026-08-18).
    # 메모리만 되돌리고 동시 실행을 5 로 두면 같은 OOM 이 재발해, 이미 만든 컷을 버리고
    # 처음부터 다시 생성한다(실비 누수). 둘의 관계를 여기서 못 박는다.
    # 컷당 최대치는 4K 이미지 **2장**을 기준으로 잡는다: repair 모드는 1차를 버리지 않고
    # (비교·폴백용) 2차를 만들어 둘이 동시에 메모리에 있다(detail_page_job.py 의
    # `chosen = repaired` 앞뒤 참조). 응답 base64 + 디코딩본 + QC 입력까지 합쳐 ~360MB.
    # 실측 대조: 9컷 → 700 + 9×360 = 3940MB ≫ 2048 (죽음, 2026-08-18)
    #            2컷 → 700 + 2×360 = 1420MB < 2048 (버팀, 그래서 2 로 눌러 뒀었다)
    #            5컷 → 700 + 5×360 = 2500MB → 2048 로는 부족, 4096 필요
    per_cut_mb = 360
    baseline_mb = 700          # 파이썬 + FastAPI + 얼굴 QC ONNX 상주분
    need = baseline_mb + int(variables["DETAIL_CUT_CONCURRENCY"]) * per_cut_mb
    assert manifest["memory"] >= need, (
        f"동시 {variables['DETAIL_CUT_CONCURRENCY']}컷에는 최소 {need}MB 가 필요한데 "
        f"manifest 는 {manifest['memory']}MB 다 — OOM 으로 생성분이 통째로 버려진다"
    )
    # Fargate 제약: 0.5 vCPU(512) 는 메모리 4096 까지만 붙일 수 있다.
    if manifest["cpu"] == 512:
        assert manifest["memory"] <= 4096, "0.5 vCPU 로는 4096MB 초과 배포가 거부된다"


def test_detail_cut_image_size_inherits_mannequin_size_when_unset(monkeypatch):
    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE", "2K")
    monkeypatch.delenv("DETAIL_CUT_IMAGE_SIZE", raising=False)

    settings = load_settings()

    assert settings.mannequin_image_size == "2K"
    assert settings.detail_cut_image_size == "2K"
