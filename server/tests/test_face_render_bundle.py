"""코드 묶음 배달 경로 — CI 가 올리고, 어댑터가 presigned 로 넘기고, 파드가 sha 로 확인한다.

자동 생성된 파드에는 코드가 없다(컨테이너 디스크는 매번 초기화). 맥에서 미는 건 사람 손이라
운영 경로가 될 수 없다 — 그래서 CI 가 커밋 sha 로 R2 에 올리고 어댑터가 그때그때 서명 URL 을
env 로 넣는다. 서버와 파드 코드가 같은 sha 라는 보장이 여기서 나온다.
"""

import hashlib
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "server/scripts/face_render_bundle.sh"
ANGLE_BUNDLE = ROOT / "server/scripts/comfy_angle_bundle.sh"
WORKFLOW = ROOT / ".github/workflows/deploy-server.yml"
#: 업로드 단계 이름. 얼굴 렌더와 각도 교체(ComfyUI) 묶음을 **한 단계에서 같은 커밋 sha 로** 올린다.
UPLOAD_STEP = "파드 코드 묶음 업로드 (R2 · 얼굴 렌더 + 각도 교체)"


def upload_step() -> str:
    return WORKFLOW.read_text(encoding="utf-8").split(
        "- name: " + UPLOAD_STEP)[1].split("- name: ")[0]


def test_bundle_contains_only_what_the_pod_needs(tmp_path):
    sha = subprocess.run([str(BUNDLE), str(tmp_path)], capture_output=True, text=True, check=True)
    digest = sha.stdout.strip().splitlines()[-1]
    tgz = tmp_path / "face_render.tgz"
    assert len(digest) == 64
    assert hashlib.sha256(tgz.read_bytes()).hexdigest() == digest
    names = set(tarfile.open(tgz).getnames())
    assert "code/face_render_service.py" in names
    assert "code/app/agents/face_identity_qwen.py" in names
    assert "deploy/bootstrap.sh" in names and "deploy/start.sh" in names
    # 레포의 app/__init__.py 를 넣으면 서버 전체가 끌려온다 — 빈 파일이어야 한다
    member = tarfile.open(tgz).extractfile("code/app/__init__.py")
    assert member.read() == b""
    # 서버 코드·테스트가 섞여 들어가면 안 된다
    assert not any(n.startswith("code/app/routes") or "tests/" in n for n in names)


def test_the_bundle_imports_on_its_own(tmp_path):
    """★ 번들을 **실제로 풀어서 import** 한다 — 파일 목록 검사로는 못 잡는 게 있다.

    `face_recipe` 는 모듈 최상단에서 `from . import face_mask_lock as fml` 을 한다. 그 파일을
    안 넣으면 파드가 face_recipe 를 import 하는 순간 ModuleNotFoundError 로 죽고, /healthz 가
    아예 안 떠서 증상은 "파드가 600초 안에 안 떴다" 로만 보인다(원인이 코드 누락이라는 단서가
    어디에도 안 남는다). 그래서 목록이 아니라 **import 가 되는지**를 본다.

    레포 경로가 아니라 **푼 디렉터리만** sys.path 에 둔다 — 레포에서 끌어오면 빠진 파일이
    있어도 통과한다(이 테스트가 존재하는 이유 자체가 사라진다).
    """
    subprocess.run([str(BUNDLE), str(tmp_path)], capture_output=True, text=True, check=True)
    extracted = tmp_path / "x"
    with tarfile.open(tmp_path / "face_render.tgz") as tar:
        tar.extractall(extracted, filter="data")

    probe = (
        "import sys; sys.path.insert(0, %r);"
        # 파드가 부팅에서 실제로 import 하는 것들. face_recipe 는 /healthz 의 레시피 해시,
        # face_mask_lock 은 그 상수의 정본, face_render_service 는 서비스 본체다.
        "import app.agents.face_recipe as r;"
        "import app.agents.face_mask_lock as m;"
        "import app.agents.face_identity as i;"
        # import 만으로는 '빈 껍데기'도 통과한다 — 파드가 부르는 이름을 실제로 짚는다.
        "print(r.recipe_id({}), m.gen_mask.__name__, i.CROP)"
        % str(extracted / "code")
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                          cwd=tmp_path)   # 레포 밖에서 — cwd 가 app/ 을 주워 오지 않게
    assert done.returncode == 0, done.stderr
    assert "gen_mask" in done.stdout


def test_bundle_carries_the_mask_lock_rules():
    """상수·마스크 규칙의 정본. 서버와 파드가 다른 규칙을 쓰면 경계가 조용히 깨진다."""
    text = BUNDLE.read_text(encoding="utf-8")
    assert "face_mask_lock.py" in text


def test_bundle_is_reproducible(tmp_path):
    """같은 코드면 같은 sha — 파드가 "이미 최신"을 판단하는 근거다."""
    a = subprocess.run([str(BUNDLE), str(tmp_path / "a")], capture_output=True, text=True, check=True)
    b = subprocess.run([str(BUNDLE), str(tmp_path / "b")], capture_output=True, text=True, check=True)
    assert a.stdout.strip().splitlines()[-1] == b.stdout.strip().splitlines()[-1]


def test_ci_uploads_to_the_private_bucket_with_the_commit_sha():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "face_render_bundle.sh" in text
    assert 'KEY="face_render/$GITHUB_SHA.tgz"' in text
    assert 's3://$R2_FACE_BUCKET/$2' in text          # put <파일> <키> <내용sha>
    # 올렸다는 응답만 믿지 않는다 — 실제로 그 키가 있는지 확인한다
    assert "head-object" in text
    # 공개 URL 을 만들지 않는다(비공개 버킷 + presigned 만)
    assert "public" not in text.split(UPLOAD_STEP)[1][:1600]
    step = upload_step()
    # 배포는 막지 않되(continue-on-error) 실패는 **빨갛게** 보여야 한다 — 이전 판은 exit 0 이라
    # 업로드가 0건인데도 초록불이었다.
    assert "continue-on-error: true" in step
    assert "::error::" in step and "exit 1" in step
    # 엔드포인트·버킷은 SSM 이 아니라 매니페스트 평문이다(그래서 SSM 루프에 없어야 한다)
    loop = step.split("for key in")[1].split("do")[0]
    assert "R2_ENDPOINT" not in loop and "R2_FACE_BUCKET" not in loop
    # 시크릿은 use1(us-east-1)에 있다 — 워크플로 기본 리전으로 읽으면 못 찾는다
    assert "SSM_REGION: us-east-1" in step and '--region "$SSM_REGION"' in step


def test_sync_script_is_marked_dev_only():
    text = (ROOT / "server/scripts/face_render_sync.sh").read_text(encoding="utf-8")
    assert "개발·검증 전용" in text
    assert "CI 가 코드 묶음을 R2 에 올리고" in text


# ── R2 호출은 OIDC 세션 토큰 없이 ──
def test_r2_calls_drop_the_oidc_session_token():
    """★ PutObject … InvalidArgument: X-Amz-Security-Token — OIDC 로 받은 AWS_SESSION_TOKEN 이
    env 에 남아 있으면 R2 가 거부한다. SSM 읽기는 그 토큰이 있어야 하므로 **R2 호출만** 지운다."""
    step = upload_step()
    assert "env -u AWS_SESSION_TOKEN" in step
    # ssm 읽기는 그대로(토큰 필요)
    ssm_line = [ln for ln in step.splitlines() if "aws ssm get-parameter" in ln][0]
    assert "env -u" not in ssm_line
    # R2 를 부르는 줄은 전부 헬퍼를 거친다
    for call in ("s3 cp", "s3api head-object"):
        line = [ln for ln in step.splitlines() if call in ln][0]
        assert line.strip().startswith("r2 ") or "r2 " in line, call


def test_r2_calls_force_the_auto_region():
    """★ 워크플로 env 의 AWS_REGION(ap-northeast-2)이 AWS_DEFAULT_REGION 을 이긴다(CLI v2 우선순위).

    AWS_DEFAULT_REGION=auto 만 두면 R2 호출이 InvalidRegionName 'ap-northeast-2' 로 죽는다
    (2026-09-11 run 34567423982 실측). 헬퍼가 **두 변수 모두** auto 로 덮어야 한다.
    """
    step = upload_step()
    helper = step.split("r2() {")[1].split("}")[0]
    assert "AWS_REGION=auto" in helper
    assert "AWS_DEFAULT_REGION=auto" in helper
    assert "env -u AWS_SESSION_TOKEN" in helper
    # 워크플로 기본 리전은 그대로 — SSM·ECR·copilot 이 그 값을 쓴다.
    assert "AWS_REGION: ap-northeast-2" in WORKFLOW.read_text(encoding="utf-8")


def test_upload_attaches_the_content_hash_and_verifies_it():
    """키는 커밋 sha 라 내용과 다르다 — 서버가 파드에 줄 검증값은 이 메타에서 읽는다."""
    step = upload_step()
    assert '--metadata "sha256=$3"' in step
    assert "--query 'Metadata.sha256'" in step
    assert 'if [ "$GOT" != "$3" ]' in step
    # 두 묶음 다 자기 내용 해시로 올라간다 — 하나만 올라가면 그 파드만 코드를 못 받는다.
    assert 'put dist/face_render.tgz "$KEY" "$SHA"' in step
    assert 'put dist/comfy_angle.tgz "comfy_angle/$GITHUB_SHA.tgz" "$ANGLE_SHA"' in step


def test_upload_runs_before_the_ecs_deploy_and_never_blocks_it():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert text.index(UPLOAD_STEP) < text.index("배포 (이미지 빌드→ECR→ECS 롤링)")
    assert "continue-on-error: true" in upload_step()


# ── PR 실행이 대기 중 배포를 취소하지 않게 ──
def test_pr_runs_and_main_deploys_use_separate_concurrency_groups():
    """2026-09-11 두 번: 대기 중 main 배포가 새 PR 푸시에 취소됐다(같은 그룹 + 공유 취소 규칙)."""
    import yaml

    conc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["concurrency"]
    group, cancel = conc["group"], conc["cancel-in-progress"]
    assert "github.event_name == 'pull_request'" in group
    assert "head_ref" in group and "'main'" in group     # PR 은 브랜치별, main 은 단독
    assert cancel == "${{ github.event_name == 'pull_request' }}"


# ── 서버가 자기 커밋 sha 를 안다 ──
def test_ci_bakes_the_build_sha_before_deploying():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "server/BUILD_SHA" in text
    assert text.index("server/BUILD_SHA") < text.index("배포 (이미지 빌드→ECR→ECS 롤링)")


def test_build_sha_file_is_not_excluded_from_the_image():
    ignore = (ROOT / "server/.dockerignore").read_text(encoding="utf-8").split("\n")
    assert not any(line.strip() in {"BUILD_SHA", "/BUILD_SHA"} for line in ignore)
    assert "COPY . ./" in (ROOT / "server/Dockerfile").read_text(encoding="utf-8")


def test_config_reads_the_build_sha_file(tmp_path, monkeypatch):
    """env 가 우선, 없으면 파일. 둘 다 없으면 None — 지어내지 않는다."""
    from app import config

    monkeypatch.delenv("FACE_RENDER_CODE_VERSION", raising=False)
    build_sha = ROOT / "server/BUILD_SHA"
    existed = build_sha.exists()
    original = build_sha.read_text() if existed else None
    try:
        build_sha.write_text("deadbeef\n")
        assert config._build_sha() == "deadbeef"
        assert config.load_settings().face_render_code_version == "deadbeef"
        monkeypatch.setenv("FACE_RENDER_CODE_VERSION", "from-env")
        assert config.load_settings().face_render_code_version == "from-env"
    finally:
        if existed:
            build_sha.write_text(original)
        else:
            build_sha.unlink(missing_ok=True)
    monkeypatch.delenv("FACE_RENDER_CODE_VERSION", raising=False)
    assert config._build_sha() is None


# ── 각도 교체(ComfyUI) 묶음 ──
def test_angle_bundle_layout_matches_the_pod_boot_contract(tmp_path):
    """묶음 안의 **자리**가 곧 부팅 계약이다(face_autoscale.POD_BOOT_SCRIPT).

    부팅 스크립트는 `deploy/*.sh` 를 $R/ 로 복사한 뒤 $R/pre_start.sh 를 /pre_start.sh 로 만든다.
    그 pre_start.sh 는 /root/face_render/comfy_setup.sh 를 절대경로로 부르고, comfy_setup.sh 는
    같은 자리의 auth_proxy.py 를 띄운다. 하나라도 자리가 어긋나면 파드는 켜지고 ComfyUI 만
    영영 안 뜬다 — 증상은 "파드가 안 떴다" 뿐이라 원인이 안 보인다.
    """
    import subprocess as sp
    import tarfile as tf

    out = sp.run([str(ANGLE_BUNDLE), str(tmp_path)], capture_output=True, text=True, check=True)
    digest = out.stdout.strip().splitlines()[-1]
    tgz = tmp_path / "comfy_angle.tgz"
    assert len(digest) == 64
    assert hashlib.sha256(tgz.read_bytes()).hexdigest() == digest
    names = set(tf.open(tgz).getnames())
    assert "deploy/pre_start.sh" in names            # → /pre_start.sh 가 된다
    assert "comfy_setup.sh" in names                 # pre_start 가 절대경로로 부른다
    assert "auth_proxy.py" in names                  # comfy_setup 이 같은 자리에서 띄운다
    body = tf.open(tgz).extractfile("deploy/pre_start.sh").read().decode()
    assert "/root/face_render/comfy_setup.sh" in body


def test_angle_bundle_is_reproducible(tmp_path):
    import subprocess as sp

    a = sp.run([str(ANGLE_BUNDLE), str(tmp_path / "a")], capture_output=True, text=True, check=True)
    b = sp.run([str(ANGLE_BUNDLE), str(tmp_path / "b")], capture_output=True, text=True, check=True)
    assert a.stdout.strip().splitlines()[-1] == b.stdout.strip().splitlines()[-1]


def test_comfy_proxy_is_closed_without_a_real_token(tmp_path):
    """ComfyUI 에는 인증이 없다. 프록시 토큰이 비거나 시크릿 자리표시자 그대로면 전부 401 이어야
    한다 — 프록시 주소는 파드 id 만 알면 누구나 때릴 수 있다."""
    proxy = (ROOT / "server/deploy/comfy_angle/auth_proxy.py").read_text(encoding="utf-8")
    assert 'TOKEN.startswith("{{")' in proxy         # RunPod 시크릿이 안 풀렸을 때
    assert "hmac.compare_digest" in proxy            # 타이밍 비교
    assert "127.0.0.1" in proxy                      # ComfyUI 는 바깥에 안 연다
