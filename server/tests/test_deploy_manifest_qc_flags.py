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
    # 베이스 충실도: 판정 스위치와 거부컷 관측 스위치는 별개다. 둘 다 미선언이면 off 로 떨어진다.
    ("MANNEQUIN_BASE_FIDELITY_QC", "mannequin_base_fidelity_qc"),
    ("MANNEQUIN_BASE_FIDELITY_OBSERVE_REGENERATIONS",
     "mannequin_base_fidelity_observe_regenerations"),
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


def test_generation_budget_is_fixed_at_two_and_untuck_is_exempt(manifest_vars):
    """일반 generation/QC 예산은 2 로 고정이다 — untuck 이 전용 슬롯로 분리됐기 때문.

    이 자리는 원래 "IMAGE_QC=enforce 면 MANNEQUIN_MAX_ATTEMPTS >= 3" 불변식이었다. 근거는
    편집 패스(untuck·fabric·bust)가 재시도와 예산을 공유해서, 2 로는 재시도 전에 소진된다는
    것. 그 공유가 정확히 untuck 기아를 만들었다 — 프로덕션 실측(2026-08-12): attempt 5 를
    소진한 잡 2건이 budget_exhausted 로 tuck 인 채 출고됐고, 예산을 5 로 올린 것이 원인을
    가리고 있었다.

    지금 구조(2026-08-12)는 untuck 을 저장 직전 전용 post-pass 1회로 분리했다. 계약은:

        일반 generation/QC ≤ 2 콜  ·  untuck ≤ 1 콜  ·  최악 총합 3 콜

    그래서 잠그는 대상이 다시 바뀐다: 예산을 3·5·6 으로 올려 문제를 가리는 회귀를 막고,
    dataclass 기본값(2)·manifest·런타임 env 가 전부 같은 값을 말하게 한다.
    """
    from app.config import Settings

    assert manifest_vars.get("MANNEQUIN_MAX_ATTEMPTS") == "2", (
        f"manifest MANNEQUIN_MAX_ATTEMPTS={manifest_vars.get('MANNEQUIN_MAX_ATTEMPTS')!r} — "
        "일반 생성/QC 예산은 2 고정이다. 올려서 untuck 기아를 가리지 말 것"
        " (untuck 은 이미 예산 밖 전용 슬롯이다)."
    )
    assert Settings.__dataclass_fields__["mannequin_max_attempts"].default == 2, (
        "dataclass 기본값이 manifest(2)와 어긋난다"
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


# ── 배포 순서 계약 ───────────────────────────────────────────────────────────

WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/deploy-server.yml"


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step_names(job: str) -> list[str]:
    return [s.get("name") or s.get("uses") or "" for s in _wf()["jobs"][job]["steps"]]


#: (경로 글롭, api 배포, sam2 배포) — 각 이미지가 **실제로 COPY 하는 것**에서 나온 표다.
#: 두 이미지 모두 context 는 server/ 지만 .dockerignore 가 정반대로 걸려 있어서,
#: API 는 sam_service 를 제외하고 SAM 은 app/·prompts/·uv.lock 을 제외한다.
CHANGE_MATRIX = [
    ("server/app/routes.py", True, False),
    ("server/app/workers/mannequin_job.py", True, False),
    ("server/prompts/mannequin_generate_v1.txt", True, False),
    ("server/uv.lock", True, False),
    ("server/Dockerfile", True, False),
    ("copilot/api/manifest.yml", True, False),
    ("supabase/migrations/20260812010000_base_fidelity_observe_job_kind.sql", True, False),
    ("server/sam_service/segmentation.py", False, True),
    ("server/sam_service/requirements.txt", False, True),
    ("server/sam_service/Dockerfile", False, True),
    ("copilot/sam2/manifest.yml", False, True),
    # 워크플로 자체가 바뀌면 둘 다 — 라우팅이 달라졌을 수 있으므로 안전측으로.
    (".github/workflows/deploy-server.yml", True, True),
    # 어느 이미지에도 안 들어가는 것들
    ("server/tests/test_analyze.py", False, False),
    ("server/scripts/seed_mannequin_base.py", False, False),
    ("src/App.jsx", False, False),
    ("tests/frontend/editor-review-gate.test.mjs", False, False),
]


def _matches(glob: str, path: str) -> bool:
    """paths-filter 글롭 근사 — `**` 는 경로 구분자를 넘고 `*` 는 안 넘는다."""
    import re
    rx = re.escape(glob).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
    rx = rx.replace(r"\*", "[^/]*")
    return re.fullmatch(rx, path) is not None


def _filters() -> dict:
    for s in _wf()["jobs"]["changes"]["steps"]:
        if str(s.get("uses", "")).startswith("dorny/paths-filter"):
            return yaml.safe_load(s["with"]["filters"])
    raise AssertionError("changes 잡에서 paths-filter 를 못 찾았다")


@pytest.mark.parametrize("path,want_api,want_sam", CHANGE_MATRIX)
def test_change_matrix_routes_to_the_right_services(path, want_api, want_sam):
    """서비스별 배포 라우팅. 여기가 틀리면 무관한 변경이 SAM 을 재배포하거나(비용),
    공유 의존이 빠져 stale 이미지가 남는다(더 나쁨)."""
    f = _filters()
    got_api = any(_matches(g, path) for g in f["api"])
    got_sam = any(_matches(g, path) for g in f["sam2"])
    assert (got_api, got_sam) == (want_api, want_sam), f"{path}: api={got_api} sam2={got_sam}"


def test_sam_image_has_no_dependency_on_the_api_source():
    """SAM 필터가 app/ 을 안 보는 근거 — 실제로 임포트하지 않아야 한다.

    이게 깨지면 `server/app/**` 변경이 SAM 이미지를 stale 하게 만든다(§5 의 위험).
    """
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "sam_service"
    bad = [str(f) for f in root.rglob("*.py")
           if re.search(r"^\s*(from|import)\s+app\b", f.read_text(encoding="utf-8"), re.M)]
    assert not bad, f"sam_service 가 app/ 을 임포트한다: {bad}"


def test_migrations_run_before_the_api_deploy():
    """DB 제약이 먼저다 — 코드가 먼저 뜨면 새 job kind 가 조용히 죽는다.

    회귀(2026-08-12): `sam_preprocess` 를 워커·라우트에 등록하고 `jobs_kind_check` 에는 안
    넣었다. 잡 INSERT 가 CheckViolation 으로 죽었고 그 실패는 삼켜지도록 짜여 있어서
    **에러 하나 없이 기능만 사라졌다**.
    """
    steps = _step_names("deploy-api")
    mig = next(i for i, n in enumerate(steps) if "마이그레이션" in n)
    api = next(i for i, n in enumerate(steps) if n.startswith("배포"))
    assert mig < api, steps


def test_sam_deploys_before_api_when_both_change():
    """api 매니페스트가 SAM_SERVICE_URL 로 sam2 를 가리킨다 — 순서가 뒤집히면 api 가
    아직 없는 서비스를 호출한다. job 의존으로 순서를 강제한다."""
    jobs = _wf()["jobs"]
    assert "deploy-sam2" in jobs["deploy-api"]["needs"]
    assert "마이그레이션" not in " ".join(_step_names("deploy-sam2")), "SAM 단독 배포는 DB 를 건드리지 않는다"


def test_api_still_deploys_when_sam_is_skipped():
    """API 단독 변경에서 sam2 잡은 스킵된다. 그 스킵이 api 를 막으면 안 된다."""
    cond = _wf()["jobs"]["deploy-api"]["if"]
    assert "always()" in cond
    assert "needs.deploy-sam2.result == 'skipped'" in cond
    assert "needs.deploy-sam2.result == 'success'" in cond


def test_deploy_jobs_never_run_on_pull_requests():
    """PR 에서는 test 만 돈다 — 배포 잡이 필수 체크로 걸려 머지를 막는 구성을 피한다."""
    assert _wf()["jobs"]["changes"]["if"] == "github.event_name != 'pull_request'"


def test_migration_step_fails_loudly_without_its_secret():
    """시크릿이 없으면 **건너뛰지 말고 실패**해야 한다. 조용한 skip 이 사고의 원인이다."""
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "supabase db push" in body
    assert 'if [ -z "$SUPABASE_DB_URL" ]; then' in body
    assert "exit 1" in body


def test_migration_step_uses_the_pinned_cli_version():
    """test 잡과 같은 CLI 버전 — 버전이 갈리면 로컬 검증과 프로덕션 적용이 달라진다."""
    doc = _wf()
    versions = {s["with"]["version"] for j in ("test", "deploy-api")
                for s in doc["jobs"][j]["steps"]
                if str(s.get("uses", "")).startswith("supabase/setup-cli")}
    assert len(versions) == 1, versions
