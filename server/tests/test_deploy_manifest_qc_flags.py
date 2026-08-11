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

# (매니페스트 변수명, Settings 속성명) — 값이 로더를 통과해 살아남아야 하는 플래그.
QC_FLAGS = [
    ("IMAGE_QC", "image_qc"),
    ("MANNEQUIN_AXIS_QC", "mannequin_axis_qc"),
    ("MANNEQUIN_QC_ENABLED", "mannequin_qc_enabled"),
    # 편집 패스 3종도 같은 사고 경로다 — 미선언이면 config 기본 off 로 조용히 안 돈다.
    ("MANNEQUIN_UNTUCK_PASS", "mannequin_untuck_pass"),
    ("MANNEQUIN_FABRIC_PASS", "mannequin_fabric_pass"),
    ("MANNEQUIN_BUST_PASS", "mannequin_bust_pass"),
]


@pytest.fixture(scope="module")
def manifest_vars() -> dict:
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return doc.get("variables") or {}


def test_qc_flags_are_declared(manifest_vars):
    """QC 플래그는 매니페스트에 명시돼야 한다 — 기본값 의존이 무측정 사고의 원인이었다."""
    missing = [name for name, _ in QC_FLAGS if name not in manifest_vars]
    assert not missing, f"매니페스트에 QC 플래그 미선언: {missing}"


@pytest.mark.parametrize("env_name,attr", QC_FLAGS)
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
    편집 패스(untuck·fabric·bust)와 공유된다. 기본값 2 로 두면 재시도 전에 소진돼서
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
