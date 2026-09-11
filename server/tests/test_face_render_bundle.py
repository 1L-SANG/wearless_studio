"""코드 묶음 배달 경로 — CI 가 올리고, 어댑터가 presigned 로 넘기고, 파드가 sha 로 확인한다.

자동 생성된 파드에는 코드가 없다(컨테이너 디스크는 매번 초기화). 맥에서 미는 건 사람 손이라
운영 경로가 될 수 없다 — 그래서 CI 가 커밋 sha 로 R2 에 올리고 어댑터가 그때그때 서명 URL 을
env 로 넣는다. 서버와 파드 코드가 같은 sha 라는 보장이 여기서 나온다.
"""

import hashlib
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "server/scripts/face_render_bundle.sh"
WORKFLOW = ROOT / ".github/workflows/deploy-server.yml"


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


def test_bundle_is_reproducible(tmp_path):
    """같은 코드면 같은 sha — 파드가 "이미 최신"을 판단하는 근거다."""
    a = subprocess.run([str(BUNDLE), str(tmp_path / "a")], capture_output=True, text=True, check=True)
    b = subprocess.run([str(BUNDLE), str(tmp_path / "b")], capture_output=True, text=True, check=True)
    assert a.stdout.strip().splitlines()[-1] == b.stdout.strip().splitlines()[-1]


def test_ci_uploads_to_the_private_bucket_with_the_commit_sha():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "face_render_bundle.sh" in text
    assert 's3://$R2_FACE_BUCKET/face_render/$GITHUB_SHA.tgz' in text
    # 공개 URL 을 만들지 않는다(비공개 버킷 + presigned 만)
    assert "public" not in text.split("얼굴 렌더 코드 묶음 업로드")[1][:1200]
    # 업로드 실패가 배포를 막지 않는다 — 대신 경고를 남긴다
    step = text.split("- name: 얼굴 렌더 코드 묶음 업로드")[1].split("- name: ")[0]
    assert "continue-on-error: true" in step
    assert "::warning::" in step


def test_sync_script_is_marked_dev_only():
    text = (ROOT / "server/scripts/face_render_sync.sh").read_text(encoding="utf-8")
    assert "개발·검증 전용" in text
    assert "CI 가 코드 묶음을 R2 에 올리고" in text
