"""server/.env → os.environ 로더 (스크립트 공용). smoke_* 의 _load_env 와 동일 규약:
미설정 키만 setdefault, 따옴표 벗김. load_settings() 는 os.getenv 만 보므로 import 전에 호출."""
import os
import pathlib


def load_env() -> None:
    path = pathlib.Path(__file__).resolve().parents[1] / ".env"  # server/.env
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
