"""배포 매니페스트의 QC 플래그 계약 — 오타·미선언이 조용히 off 로 떨어지는 사고 방지.

동기(2026-07-31): `IMAGE_QC` 가 copilot manifest 에 **아예 없어서** config 기본 "off" 로
떨어졌고, 의류 동일성 QC(AG-P2)가 프로덕션에서 무측정 상태였다. 에러도 경고도 안 난다 —
`_flag()` 는 허용집합 밖 값이나 미설정을 전부 default 로 조용히 폴백하기 때문이다.

여기서 잠그는 것 두 가지:
1. 배포되는 플래그 값이 `_flag()` 허용집합 안에 있는가 (오타면 값이 살아남지 못한다).
2. QC 플래그가 매니페스트에 **명시 선언**돼 있는가 (기본값 의존 = 무측정 재발 경로).
"""
import dataclasses
import os
import pathlib

import pytest
import yaml

from app.config import load_settings

MANIFEST = pathlib.Path(__file__).resolve().parents[2] / "copilot/api/manifest.yml"
ENV_EXAMPLE = pathlib.Path(__file__).resolve().parents[1] / ".env.example"

# (매니페스트 변수명, Settings 속성명) — 값이 로더를 통과해 살아남아야 하는 플래그.
QC_FLAGS = [
    ("IMAGE_QC", "image_qc"),
    ("MANNEQUIN_AXIS_QC", "mannequin_axis_qc"),
    ("MANNEQUIN_FRAME_QC", "mannequin_frame_qc"),
    ("MANNEQUIN_QC_ENABLED", "mannequin_qc_enabled"),
    # 편집 패스 2종도 같은 사고 경로다 — 미선언이면 config 기본 off 로 조용히 안 돈다.
    ("MANNEQUIN_UNTUCK_PASS", "mannequin_untuck_pass"),
    ("MANNEQUIN_BUST_PASS", "mannequin_bust_pass"),
    # 완료된 data/QC/edit/texture rollout 플래그. 기본값 의존은 prod 에서 무측정/off 로
    # 조용히 퇴행하는 경로라 deploy/example 둘 다 명시한다.
    ("GENERATION_RUN_LOG", "generation_run_log"),
    ("ENABLE_PRODUCT_TRUTH", "enable_product_truth"),
    ("MANNEQUIN_STRUCTURED_QC", "mannequin_structured_qc"),
    ("MANNEQUIN_GARMENT_FIDELITY_QC", "mannequin_garment_fidelity_qc"),
    ("MANNEQUIN_EDIT_INTENT_QC", "mannequin_edit_intent_qc"),
    ("EDITOR_VARY_INTENT_QC", "editor_vary_intent_qc"),
    ("MANNEQUIN_HYBRID_COMPOSITE", "mannequin_hybrid_composite"),
    ("MANNEQUIN_TEXTURE_PROJECTION_2D", "mannequin_texture_projection_2d"),
]

SAFE_ROLLOUT_VALUES = {
    "GENERATION_RUN_LOG": "shadow",
    "ENABLE_PRODUCT_TRUTH": "enforce",
    "MANNEQUIN_STRUCTURED_QC": "shadow",
    # 캘리브레이션 전이므로 관측만 — enforce 는 실측 근거가 쌓인 뒤에 올린다.
    "MANNEQUIN_GARMENT_FIDELITY_QC": "shadow",
    "MANNEQUIN_EDIT_INTENT_QC": "shadow",
    "EDITOR_VARY_INTENT_QC": "shadow",
    "MANNEQUIN_HYBRID_COMPOSITE": "enforce",
    "MANNEQUIN_TEXTURE_PROJECTION_2D": "enforce",
    "MANNEQUIN_FRAME_QC": "shadow",
}

IMAGE_SIZE_POLICY = {
    "MANNEQUIN_IMAGE_SIZE": "1K",
    "MANNEQUIN_IMAGE_SIZE_CAP": "off",
    "MANNEQUIN_PATTERN_IMAGE_SIZE": "4K",
}

# 폐기된 flag — manifest 에 다시 나타나면 안 된다. 구 generative fabric pass 는 blind visual
# 3/3 FAIL 로 코드째 삭제됐다(2026-08-01, hybrid composite 로 대체). env 잔재로 재선언되면
# 로더에 대응 필드가 없어 조용히 무시되는데, 운영자는 켜졌다고 믿게 된다 — 그 사고를 막는다.
RETIRED_FLAGS = ["MANNEQUIN_FABRIC_PASS"]

# ── SAM 롤아웃에서 **의도적으로 미배포** (2026-08-11 사용자 결정) ────────────────
#
# 이 여덟 개는 기능이 완성돼 매니페스트에 미리 적혀 있었지만 배포된 적이 없다
# (wearless-prod-api:92 에 없음). SAM 배포에 얹어 함께 켜면 무관한 게이트 3개가 enforce 로
# 올라가 지금 출고되는 컷을 막을 수 있고, 관측 5개는 잡마다 추가 QC 콜을 쓴다.
#
# 그래서 이번 배포 매니페스트에서는 뺀다. **기능 폐기가 아니다** — 코드는 그대로고, 각자
# 자기 근거를 갖고 따로 켜면 된다. 아래 값은 그때 쓸 값이라 여기 보존한다.
#
# 이 목록이 존재하는 이유는 이 파일의 원래 동기와 같다: 2026-07-31 에 IMAGE_QC 가 매니페스트에서
# **그냥 빠져** 조용히 off 로 떨어졌고 아무도 몰랐다. 지금의 부재는 의도된 것이며, 그 사실이
# 코드로 적혀 있어야 다음 사람이 "빠뜨린 것"과 구분할 수 있다.
DEFERRED_FROM_PROD = {
    "GENERATION_RUN_LOG": "shadow",
    "ENABLE_PRODUCT_TRUTH": "enforce",
    "MANNEQUIN_STRUCTURED_QC": "shadow",
    "MANNEQUIN_GARMENT_FIDELITY_QC": "shadow",
    "MANNEQUIN_EDIT_INTENT_QC": "shadow",
    "EDITOR_VARY_INTENT_QC": "shadow",
    "MANNEQUIN_HYBRID_COMPOSITE": "enforce",
    "MANNEQUIN_TEXTURE_PROJECTION_2D": "enforce",
}

#: 미배포 플래그가 미설정일 때 로더가 떨어지는 값. 전부 "꺼짐"이어야 한다 —
#: shadow 는 관측만 하고 게이팅하지 않으므로 출고 동작을 바꾸지 않는다.
DISABLED_WHEN_ABSENT = {
    "generation_run_log": "off",
    "enable_product_truth": "off",
    "mannequin_structured_qc": "off",
    "mannequin_garment_fidelity_qc": "off",
    "mannequin_edit_intent_qc": "off",
    "editor_vary_intent_qc": "off",
    "mannequin_hybrid_composite": "off",
    "mannequin_texture_projection_2d": "off",
}


@pytest.fixture(scope="module")
def manifest_vars() -> dict:
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return doc.get("variables") or {}


@pytest.fixture(scope="module")
def env_example_vars() -> dict:
    values = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, raw = stripped.split("=", 1)
        values[name] = raw.split("#", 1)[0].strip()
    return values


def test_qc_flags_are_declared(manifest_vars):
    """QC 플래그는 매니페스트에 명시돼야 한다 — 기본값 의존이 무측정 사고의 원인이었다.

    DEFERRED_FROM_PROD 는 제외한다. 그건 사고가 아니라 결정이고, 아래 두 테스트가
    "정말 빠져 있고, 빠졌을 때 정말 꺼진다"를 따로 잠근다.
    """
    missing = [name for name, _ in QC_FLAGS
               if name not in manifest_vars and name not in DEFERRED_FROM_PROD]
    assert not missing, f"매니페스트에 QC 플래그 미선언: {missing}"


def test_deferred_flags_are_absent_from_the_production_manifest(manifest_vars):
    """SAM 배포에 무관한 기능이 묻어 들어가지 않는다."""
    present = [n for n in DEFERRED_FROM_PROD if n in manifest_vars]
    assert not present, (
        f"이번 배포에서 미루기로 한 플래그가 매니페스트에 있다: {present}. "
        "따로 켜기로 한 것이라면 DEFERRED_FROM_PROD 에서 빼고 근거를 남겨라.")


def test_deferred_flags_actually_resolve_to_disabled(monkeypatch):
    """미배포 = 실제로 꺼짐인지 로더로 확인한다. 부재가 곧 비활성이라는 가정을 검증한다."""
    for env_name in DEFERRED_FROM_PROD:
        monkeypatch.delenv(env_name, raising=False)
    settings = load_settings()
    active = {attr: getattr(settings, attr) for attr, expected in DISABLED_WHEN_ABSENT.items()
              if getattr(settings, attr) != expected}
    assert not active, f"미배포 플래그가 꺼져 있지 않다: {active}"


def test_rollout_flags_are_declared_in_env_example(env_example_vars):
    """예제 env 도 rollout 값을 명시해야 한다 — 로컬/문서 기본값이 off 로 퇴행하면 안 된다."""
    missing = [name for name in SAFE_ROLLOUT_VALUES if name not in env_example_vars]
    assert not missing, f".env.example 에 rollout 플래그 미선언: {missing}"


@pytest.mark.parametrize("env_name,expected", IMAGE_SIZE_POLICY.items())
def test_manifest_declares_general_1k_and_fine_pattern_4k_policy(
    env_name, expected, manifest_vars,
):
    """일반 상품은 1K, 스트라이프 등 미세 패턴은 4K로 실제 배포한다.

    CAP=1K 잔재는 패턴 4K 승급을 provider 호출 직전에 다시 낮추므로,
    세 값을 하나의 배포 계약으로 고정한다.
    """
    assert str(manifest_vars.get(env_name)) == expected


@pytest.mark.parametrize("env_name,expected", IMAGE_SIZE_POLICY.items())
def test_env_example_declares_general_1k_and_fine_pattern_4k_policy(
    env_name, expected, env_example_vars,
):
    assert env_example_vars.get(env_name) == expected


@pytest.mark.parametrize(
    "env_name,expected",
    [(k, v) for k, v in SAFE_ROLLOUT_VALUES.items() if k not in DEFERRED_FROM_PROD])
def test_manifest_uses_safe_rollout_values(env_name, expected, manifest_vars):
    """배포되는 플래그는 캘리브레이션이 끝난 축만 enforce, 나머지는 관측 shadow 로 고정한다.

    미배포로 결정된 플래그는 여기 오지 않는다 — 값이 아니라 부재가 계약이다.
    """
    assert str(manifest_vars.get(env_name)) == expected


@pytest.mark.parametrize("env_name,expected", SAFE_ROLLOUT_VALUES.items())
def test_env_example_uses_safe_rollout_values(env_name, expected, env_example_vars):
    """예제 env 는 배포 계약과 같은 safe rollout 모드를 보여준다."""
    assert env_example_vars.get(env_name) == expected


def test_retired_flags_are_not_declared(manifest_vars):
    """폐기 flag 가 manifest 에 되살아나면 안 된다 — 로더에 필드가 없어 조용히 무시된다."""
    revived = [name for name in RETIRED_FLAGS if name in manifest_vars]
    assert not revived, (
        f"폐기된 flag 가 manifest 에 재선언됨: {revived} — 대응 config 필드가 삭제돼 "
        "아무 효과 없이 켜진 것처럼 보인다"
    )


@pytest.mark.parametrize(
    "env_name,attr", [(n, a) for n, a in QC_FLAGS if n not in DEFERRED_FROM_PROD])
def test_manifest_flag_value_survives_loader(env_name, attr, manifest_vars, monkeypatch):
    """배포값을 실제 로더에 넣었을 때 그대로 살아남는가.

    `_flag()` 는 허용집합 밖 값을 조용히 default 로 바꾼다. 매니페스트 오타(예: "shadwo")는
    배포도 성공하고 앱도 뜨지만 QC 는 꺼진 채로 돈다. 값이 왕복하는지 확인해 그걸 잡는다.
    """
    raw = str(manifest_vars[env_name])
    monkeypatch.setenv(env_name, raw)
    # load_settings 는 필수 env 를 요구하므로 최소 세트를 채운다.
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    loaded = getattr(load_settings(), attr)
    expected = raw.lower() == "true" if isinstance(loaded, bool) else raw
    assert loaded == expected, (
        f"{env_name}={raw!r} 가 로더를 통과하지 못하고 {loaded!r} 로 폴백됐다 — "
        f"허용집합 밖 값(오타)이면 QC 가 조용히 꺼진다."
    )


def test_image_qc_enforce_carries_its_retry_budget(manifest_vars):
    """enforce 는 재시도 예산과 함께여야 한다 — 예산 없는 enforce 는 실제로 아무것도 못 고쳤다.

    이 테스트는 원래 "enforce 로 올리지 말 것"이었다(2026-07-07 `MANNEQUIN_QC_ENABLED=true`
    가 오탐 pass율 0% 로 전 생성을 차단한 전례). 그 조건인 캘리브레이션을 2026-07-31 끝내고
    의도적으로 승격했다 — 임계 실측 교정(90/75 는 통과율 0% 였다), 판정자 변별력 하니스,
    거짓양성 육안 점검 오탐 0, 층화 재측정, 계약 뮤테이션 26/26.

    그래서 잠그는 대상이 바뀐다. enforce 의 `regenerate` 판정은 재시도를 쓰는데, 그 예산은
    편집 패스(untuck·bust)와 공유된다. 기본값 2 로 두면 재시도 전에 소진돼서
    **판정만 하고 아무것도 고치지 못한다** — 가슴 2패스가 한 번도 출고되지 않던 실제 원인이다.
    enforce 를 켰으면 예산도 같이 올라가 있어야 한다는 것이 여기서 지킬 불변식이다.
    """
    if manifest_vars.get("IMAGE_QC") != "enforce":
        pytest.skip("enforce 가 아니면 예산 불변식은 해당 없음")
    attempts = int(manifest_vars.get("MANNEQUIN_MAX_ATTEMPTS", 0))
    assert attempts >= 3, (
        f"IMAGE_QC=enforce 인데 MANNEQUIN_MAX_ATTEMPTS={attempts} — 편집 패스와 예산을 "
        "공유하므로 재시도가 돌기 전에 소진된다. 판정만 하고 못 고치는 상태가 된다."
    )


def test_dataclass_defaults_match_loader_defaults(monkeypatch):
    """dataclass 선언 기본값과 `load_settings` 의 env 기본값이 어긋나면 안 된다.

    실행 경로는 `load_settings` 라 그쪽이 정본인데, dataclass 만 고치면 **테스트는 통과하고
    실서비스는 옛 값으로 돈다.** 2026-07-31 실측: `qc_score_auto_pass` 를 90→80 으로 낮췄는데
    로더가 "90" 을 그대로 들고 있어 enforce E2E 에서 90 이 찍혔다. 단위 테스트는 dataclass 만
    봐서 전부 통과했고, 실 파이프라인을 돌려서야 드러났다.
    """
    import pathlib
    import re

    from app.config import Settings

    src = (pathlib.Path(__file__).resolve().parents[1] / "app/config.py").read_text(
        encoding="utf-8")
    loader = src[src.index("def load_settings"):]
    pairs = re.findall(r'(\w+)=\w*\(?os\.getenv\("([A-Z0-9_]+)",\s*"([^"]*)"\)', loader)
    assert pairs, "로더에서 env 기본값 패턴을 못 찾았다 — 정규식을 갱신하라"

    for _field, env, _default in pairs:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    loaded = load_settings()

    drift = []
    for field, env, _default in pairs:
        spec = Settings.__dataclass_fields__.get(field)
        if spec is None or spec.default is dataclasses.MISSING:
            continue  # 기본값 없는 필수 필드(jwt_audience 등)는 비교 대상 아님
        if str(spec.default) != str(getattr(loaded, field)):
            drift.append(f"{field}({env}): dataclass={spec.default!r} 실제={getattr(loaded, field)!r}")
    assert not drift, "dataclass 기본값과 로더 기본값 불일치:\n  " + "\n  ".join(drift)


def test_loader_silently_falls_back_on_typo(monkeypatch):
    """가드의 전제 확인 — 오타는 예외가 아니라 조용한 폴백이다(그래서 위 테스트가 필요하다)."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("IMAGE_QC", "shadwo")
    assert load_settings().image_qc == "off"
    assert os.environ["IMAGE_QC"] == "shadwo"  # env 는 그대로인데 설정만 off
