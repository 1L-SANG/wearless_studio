"""server/.env → os.environ 로더 (스크립트 공용). smoke_* 의 _load_env 와 동일 규약:
미설정 키만 setdefault, 따옴표 벗김. load_settings() 는 os.getenv 만 보므로 import 전에 호출.

⚠️ 테스트 실행 중에는 아무것도 안 한다. 이 로더는 import 시점에 불리는 스크립트가 많은데
(scripts/eval_axis_reflection.py 등), pytest 가 그 스크립트를 import 하는 테스트 파일을 수집하는
순간 .env 의 DATABASE_URL 이 세션 전역 os.environ 에 박힌다. 그러면 로컬 Postgres 를 쓰라고
쓰여 있는 테스트(tests/test_personalization.py)가 **원격 운영 계열 DB** 를 향하게 되고, 그 DB 에
새 마이그레이션이 없으면 UndefinedColumn 500 으로 무더기 실패한다(2026-09-02 실측: 전체 스위트
50건 실패). 더 나쁜 건 실패가 아니라 성공하는 경우다 — 테스트가 원격 DB 에 데이터를 쓴다.
CI 에는 .env 가 없어 여태 드러나지 않았다."""
import os
import pathlib


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ


def load_env() -> None:
    if _running_under_pytest():
        return
    path = pathlib.Path(__file__).resolve().parents[1] / ".env"  # server/.env
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
