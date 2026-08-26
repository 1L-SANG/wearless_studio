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
    monkeypatch.setenv("MODEL_ROUTING_DETAIL_CUT", "gpt-image-2-2026-04-21")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "repair"
    assert settings.mannequin_image_size == "1K"
    assert settings.detail_cut_image_size == "4K"
    assert settings.model_detail_cut == "gpt-image-2-2026-04-21"

    monkeypatch.setenv("CUT_OUTPUT_QC_MODE", "enforce")
    monkeypatch.setenv("PAGE_OUTPUT_QC_MODE", "invalid")
    settings = load_settings()
    assert settings.cut_output_qc_mode == "off"
    assert settings.page_output_qc_mode == "off"


def test_mannequin_image_size_defaults_to_2k_everywhere(monkeypatch):
    """manifest가 없는 로컬·테스트 실행도 오너 결정과 같은 전체 2K 기본값을 쓴다."""
    monkeypatch.delenv("MANNEQUIN_IMAGE_SIZE", raising=False)
    assert Settings.__dataclass_fields__["mannequin_image_size"].default == "2K"
    assert load_settings().mannequin_image_size == "2K"

    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE", "invalid")
    assert load_settings().mannequin_image_size == "2K"


def test_production_manifest_bounds_4k_gpt_repair_concurrency_without_moving_shared_tier():
    manifest_path = Path(__file__).resolve().parents[2] / "copilot/api/manifest.yml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    variables = manifest["variables"]

    assert variables["MODEL_ROUTING_IMAGE_HIGH"] == "gemini-3-pro-image"
    assert variables["MODEL_ROUTING_DETAIL_CUT"] == "gpt-image-2-2026-04-21"
    # 상세페이지 컷 출고 해상도 2K (2026-08-26 오너 결정, 4K 에서 내림). 마네킹 핀과 같은
    # 이유로 못 박는다 — manifest 병합 사고 때 소리 없이 4K 로 되돌아가면 컷당 실비가 오른다.
    assert variables["DETAIL_CUT_IMAGE_SIZE"] == "2K"
    assert variables["DETAIL_CUT_STAGGER_MS"] == "3000"
    assert variables["DETAIL_CUT_MAX_ATTEMPTS"] == "1"
    assert variables["GARMENT_QC_MODE"] == "off"
    assert variables["CUT_OUTPUT_QC_MODE"] == "repair"
    # 마네킹컷 기본 2K (2026-08-19 오너 결정 — 1K 는 로고 글자가 깨짐, pro 요금 동일).
    # 이 핀이 없으면 manifest 병합 사고 때 소리 없이 1K 로 돌아간다(같은 날 실제 겪은 사고 유형).
    assert variables["MANNEQUIN_IMAGE_SIZE"] == "2K"
    assert variables["MANNEQUIN_LOGO_IMAGE_SIZE"] == "2K"
    # 사전 게이트는 코드 기본값이 off 라 manifest 선언이 빠지면 배포가 성공해도 조용히
    # 비활성화된다. 오너가 승인한 프로덕션 동작을 함께 고정한다.
    assert variables["MANNEQUIN_UNTUCK_GATE"] == "on"
    assert variables["MANNEQUIN_BUST_GATE"] == "on"

    # 동시 컷 수는 **정확값이 아니라 메모리와의 관계**로 묶는다. 정확값으로 못 박으면
    # 위험을 발견해 안전하게 낮추는 변경까지 빨갛게 만든다(2026-08-19 Codex 리뷰).
    concurrency = int(variables["DETAIL_CUT_CONCURRENCY"])
    assert 1 <= concurrency <= 5, "실측 전까지 5 가 상한 — 올리려면 CloudWatch 사용률부터"

    # 컷 하나가 잡는 최대치(4K 실측 기준 — 2K 로 내린 지금은 보수적인 상한이다):
    # repair 가 1차를 살려둔 채 2차를 만들어 이미지가 두 장이고
    # (detail_page_job.py 의 chosen 교체 지점), 각 장이 응답 base64 + 디코딩본 + QC 입력을
    # 함께 붙든다. 실측 대조 — 9컷: 700+9×360=3940MB ≫ 2048 (죽음, 2026-08-18)
    #                  2컷: 700+2×360=1420MB < 2048 (버팀)
    # 최악 입력(색상당 사진 6장·장당 25MB)의 reference 총량은 이 식에 안 들어간다. 잡 단위
    # 상한이 생기기 전까지 이 계산은 '평균적으로 안전'까지만 보장한다(같은 리뷰 지적).
    per_cut_mb, baseline_mb = 360, 700
    need = baseline_mb + concurrency * per_cut_mb
    assert manifest["memory"] >= need, (
        f"동시 {concurrency}컷에는 최소 {need}MB 가 필요한데 manifest 는 "
        f"{manifest['memory']}MB 다 — OOM 이 나면 이미 만든 컷을 버리고 다시 생성한다"
    )
    # Fargate 제약: 0.5 vCPU(512) 는 메모리 4096 까지만 붙일 수 있다(초과 시 배포 거부).
    if manifest["cpu"] == 512:
        assert manifest["memory"] <= 4096

    secrets = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["secrets"]
    # 경로에 환경명을 하드코딩하지 않는다 — us-east-1 이전에서 환경이 prod 가 아니라
    # use1 이 됐고(Copilot 환경명은 앱 내 유일해야 한다), 하드코딩이면 새 환경의 태스크가
    # 존재하지 않는 파라미터를 읽으려다 기동에 실패한다. 확인할 것은 "OPENAI 키를
    # 현재 환경의 SSM 에서 읽는가"이지 그 환경이 prod 라는 사실이 아니다.
    assert secrets["OPENAI_API_KEY"] == (
        "/copilot/${COPILOT_APPLICATION_NAME}/${COPILOT_ENVIRONMENT_NAME}/secrets/OPENAI_API_KEY"
    )


def test_detail_cut_image_size_inherits_mannequin_size_when_unset(monkeypatch):
    monkeypatch.setenv("MANNEQUIN_IMAGE_SIZE", "2K")
    monkeypatch.delenv("DETAIL_CUT_IMAGE_SIZE", raising=False)

    settings = load_settings()

    assert settings.mannequin_image_size == "2K"
    assert settings.detail_cut_image_size == "2K"
