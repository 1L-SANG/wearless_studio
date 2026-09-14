"""등록 사진 16칸 → LoRA 학습셋(v7_dataset 레이아웃)으로 내보낸다. **읽기 전용.**

16칸 스펙(2026-09-14)은 등록 사진을 그대로 학습 촬영으로 받는다. 그래서 등록이 끝나면
그 사진들이 곧 학습셋이다 — 이 스크립트가 그 둘을 잇는다.

    out_dir/
      train/<조명>__<컷>.png     × 12   (조명 4 × 컷 3)
      refset/그늘__<기준>.png    ×  3   (학습 제외 · 채점용)

파일 이름은 서버와 같은 상수에서 나온다(app/facemarket_photos.export_name) — 학습 캡션이
붙는 자리라 이름이 갈라지면 조명 라벨이 통째로 어긋난다.

■ 안전 규약(이 파일의 존재 이유의 절반이다)
  · DB 는 **읽기 전용 트랜잭션**으로 연다. R2 는 face 버킷 GET 만 한다.
  · `--apply` 없이는 **아무 파일도 쓰지 않는다** — 칸이 다 찼는지만 세어 보고한다.
  · out_dir 이 레포 안이면 거부한다. 생체정보는 커밋될 수 있는 곳에 두지 않는다.
  · 출력에 R2 키·presigned URL·DSN 을 찍지 않는다. 키는 sha256 앞 12자, 사진은 길이만.
  · 내보낸 사진은 이 맥에만 둔다. 공유 링크·메신저로 보내지 않는다.

■ 진입점 정규화(v6_intake.normalize_to_png 와 같은 규칙)
  EXIF Orientation 을 **픽셀에 실제로 적용**하고 EXIF 없는 PNG 로 저장한다. 그러지 않으면
  cv2(태그 적용)와 PIL(미적용)이 서로 다른 크기를 보게 되고, 검출 좌표계와 크롭 좌표계가
  어긋나 엉뚱한 영역이 잘린다. 파일마다 PIL 크기 == cv2 크기를 확인하고 하나라도 어긋나면
  그 자리에서 멈춘다(학습 금지).

실행:
    cd server
    DATABASE_URL=... .venv/bin/python -m scripts.fm_export_training_set <enrollment_id> ~/Downloads/ds
    ... --apply            # 실제로 파일을 쓴다
    ... --apply --allow-partial   # 빠진 칸이 있어도 있는 것만 쓴다(기본은 거부)

DB 는 환경변수 DATABASE_URL 을 따른다(없으면 server/.env). 운영 DB 를 보려면 그 값을
**먼저 export** 해야 한다 — load_env() 는 미설정 키만 setdefault 라, 안 그러면 개발 DB 를 본다.
"""
import argparse
import hashlib
import io
import pathlib
import sys
import uuid

import psycopg
from psycopg.rows import dict_row

from scripts._env import load_env

load_env()

import os  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.facemarket_photos import (  # noqa: E402
    REFSET_SLOTS, TRAINING_SLOTS, export_name, resolve_photo_rows,
)
from app.r2 import R2Client  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
#: 내보낼 슬롯과 그 하위 디렉터리. 측면(sh_side)은 학습에 안 쓴다 — 공개 자산용이다.
EXPORTS: tuple[tuple[str, str], ...] = (
    tuple(("train", slot) for slot in TRAINING_SLOTS)
    + tuple(("refset", slot) for slot in REFSET_SLOTS)
)
#: 기준으로 삼을 수 있는 저장 상태. 승인 전(quarantine)도 학습에는 유효하다.
USABLE_STATES = {"quarantine", "approved"}


def _digest(text: str) -> str:
    """키·호스트를 그대로 찍지 않기 위한 짧은 지문."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _refuse_inside_repo(out_dir: pathlib.Path) -> str | None:
    try:
        out_dir.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return None
    return f"out_dir 이 레포 안이다({REPO_ROOT.name}/…) — 생체정보를 거기 두지 않는다"


def fetch_photos(dsn: str, enrollment_id: str) -> list[dict]:
    """등록 사진 메타 한 번 읽기. 읽기 전용 트랜잭션."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "select angle, r2_key, mime_type, qc_status, storage_state, byte_size "
                "from fm_biometric_enrollment_photos where enrollment_id = %s",
                (enrollment_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def plan(rows: list[dict]) -> list[dict]:
    """슬롯별 계획. 없는 칸도 자리를 남긴다 — 무엇이 비었는지가 이 스크립트의 주 출력이다."""
    out = []
    for group, slot in EXPORTS:
        resolved = resolve_photo_rows(rows, (slot,))
        row = resolved[0] if resolved else None
        usable = bool(
            row
            and row.get("qc_status") == "passed"
            and row.get("storage_state") in USABLE_STATES
            and str(row.get("r2_key") or "").strip()
        )
        out.append({
            "group": group, "slot": slot, "name": export_name(slot),
            "row": row if usable else None,
            "angle": row.get("angle") if row else None,
            "state": row.get("storage_state") if row else None,
            "qc": row.get("qc_status") if row else None,
            "bytes": row.get("byte_size") if row else None,
        })
    return out


def normalize_png(data: bytes, destination: pathlib.Path) -> tuple[int, int]:
    """바이트 → EXIF 없는 PNG. 저장 뒤 PIL 크기 == cv2 크기를 확인한다."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        image.save(destination, "PNG")
        size = image.size
    with Image.open(destination) as check:
        pil_size = check.size
        if check.getexif().get(274) not in (None, 1):
            raise RuntimeError(f"EXIF orientation 이 남아 있다: {destination.name}")
    raw = np.frombuffer(destination.read_bytes(), dtype=np.uint8)
    decoded = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    cv_size = (decoded.shape[1], decoded.shape[0]) if decoded is not None else None
    if cv_size != pil_size or pil_size != size:
        raise RuntimeError(f"크기 불일치: {destination.name} PIL {pil_size} vs cv2 {cv_size}")
    return pil_size


def main() -> int:
    ap = argparse.ArgumentParser(description="등록 사진 16칸 → LoRA 학습셋")
    ap.add_argument("enrollment_id")
    ap.add_argument("out_dir")
    ap.add_argument("--apply", action="store_true", help="없으면 세어 보기만 하고 파일을 안 쓴다")
    ap.add_argument("--allow-partial", action="store_true",
                    help="빠진 칸이 있어도 있는 것만 쓴다(기본은 거부 — 반쪽 학습셋은 조용한 품질 손실이다)")
    a = ap.parse_args()

    try:
        uuid.UUID(a.enrollment_id)
    except ValueError:
        print(f"enrollment-id 가 uuid 가 아니다: {a.enrollment_id}")
        return 2
    out_dir = pathlib.Path(a.out_dir).expanduser()
    refusal = _refuse_inside_repo(out_dir)
    if refusal:
        print(refusal)
        return 2

    settings = load_settings()
    dsn = os.getenv("DATABASE_URL") or getattr(settings, "database_url", "") or ""
    if not dsn:
        print("DATABASE_URL 이 없다")
        return 2
    host = dsn.split("@")[-1].split("/")[0] if "@" in dsn else "(unknown)"
    print(f"DB host sha256[:12] = {_digest(host)}")

    rows = fetch_photos(dsn, a.enrollment_id)
    items = plan(rows)
    have = [item for item in items if item["row"]]
    missing = [item for item in items if not item["row"]]

    print(f"등록 {a.enrollment_id[:8]}… 행 {len(rows)}개 → 내보낼 칸 {len(items)}개 중 {len(have)}개 준비됨")
    for item in items:
        mark = "o" if item["row"] else "x"
        source = f" ← {item['angle']}" if item["angle"] and item["angle"] != item["slot"] else ""
        extra = "" if item["row"] else f" (state={item['state']} qc={item['qc']})"
        size = f" {item['bytes']}B" if item["bytes"] else ""
        key = f" key={_digest(item['row']['r2_key'])}" if item["row"] else ""
        print(f"  [{mark}] {item['group']}/{item['name']}.png  ← {item['slot']}{source}{size}{key}{extra}")
    if missing:
        print(f"빠진 칸 {len(missing)}개: " + ", ".join(item["slot"] for item in missing))

    if not a.apply:
        print("확인만 했다(--apply 없음). 파일을 쓰지 않았다.")
        return 0
    if missing and not a.allow_partial:
        print("빠진 칸이 있어 쓰지 않는다. 그래도 내보내려면 --allow-partial.")
        return 1

    r2 = R2Client(settings, bucket=settings.r2_face_bucket or settings.r2_bucket)
    written = 0
    for item in have:
        target = out_dir / item["group"]
        target.mkdir(parents=True, exist_ok=True)
        destination = target / f"{item['name']}.png"
        data = r2.get_bytes(item["row"]["r2_key"])
        width, height = normalize_png(data, destination)
        written += 1
        print(f"  wrote {item['group']}/{destination.name} {width}x{height} ({len(data)}B → {destination.stat().st_size}B)")
    print(f"{written}장을 {out_dir} 에 썼다. 학습 {len([i for i in have if i['group'] == 'train'])}장 · "
          f"기준 {len([i for i in have if i['group'] == 'refset'])}장.")
    print("이 사진들은 이 맥에만 둔다. 공유 링크·메신저로 보내지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
