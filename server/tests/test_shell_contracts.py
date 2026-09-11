"""쉘 스크립트 계약 테스트를 CI 가 실제로 돌게 잇는다.

tests/shell/*.test.sh 는 파이썬으로 못 재는 분기(부팅 인자·bootstrap 의 코드 수신)를 고정한다.
그런데 지금까지 아무도 실행하지 않았다 — 워크플로에도, 파이썬 스위트에도 연결돼 있지 않았다
(2026-09-11 확인). 그래서 자동 생성 파드가 부팅을 못 하는 회귀가 테스트 없이 배포됐다.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "tests/shell").glob("*.test.sh"))


def test_there_are_shell_contracts_to_run():
    assert SCRIPTS, "tests/shell/*.test.sh 가 사라졌다"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_shell_contract(script):
    if not shutil.which("sha256sum"):
        pytest.skip("sha256sum 없음(coreutils 미설치 환경)")
    proc = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
