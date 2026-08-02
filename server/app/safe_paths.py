"""허용 디렉터리 안의 파일만 여는 resolver — 여러 도구가 같은 규칙을 쓴다.

라벨 도구에만 있던 것을 올렸다. 스크립트끼리 import 하면 한쪽만 고쳐지고, 그
한쪽이 검증하지 않는 경로가 되는 순간 경계가 뚫린다. 규칙은 한 곳에 둔다.

거부는 "읽고 나서 버리기"가 아니라 **읽지 않기**다. 그리고 거부 사유에 경로나
파일 내용을 싣지 않는다 — 그 자체가 정보다.
"""

from __future__ import annotations

import hashlib
import pathlib
import re

# 파일 이름이 되는 식별자. 최소 문자 집합으로 조여 경로가 될 여지를 없앤다.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# 예시 이미지 파일명. 디렉터리 구분자도 상위 참조도 필요 없다.
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# provenance 에 저장되는 해시. 정확히 64자리 hex 여야 한다.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class UnsafePath(Exception):
    """허용 경계를 벗어난 요청."""


def is_sha256_hex(value) -> bool:
    return isinstance(value, str) and bool(SHA256_HEX.match(value))


def safe_name(name, pattern) -> str:
    """이름 자체를 먼저 조인다 — 경로를 만들기 전에 막는 게 가장 확실하다."""
    if not isinstance(name, str) or "\x00" in name:
        raise UnsafePath("bad name")
    if not pattern.match(name):
        raise UnsafePath("bad name")
    if name in (".", "..") or "/" in name or "\\" in name:
        raise UnsafePath("bad name")
    return name


def safe_resolve(base: pathlib.Path, name, pattern, *, suffix: str | None = None):
    """base 안의 일반 파일만 돌려준다.

    resolve() 는 symlink 를 따라가므로 링크로 밖을 가리켜도 실제 경로가 base 밖이면
    걸린다. 존재 여부보다 **경계**를 먼저 본다.
    """
    safe = safe_name(name, pattern)
    if suffix and not safe.endswith(suffix):
        safe = f"{safe}{suffix}"
    root = pathlib.Path(base).resolve(strict=False)
    target = (root / safe).resolve(strict=False)
    if target != root and root not in target.parents:
        raise UnsafePath("outside base")
    if not target.is_file():
        raise UnsafePath("not a regular file")
    return target


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
