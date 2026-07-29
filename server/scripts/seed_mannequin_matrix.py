"""여성 베이스 마네킹 체형 매트릭스 시드 — 현행 베이스에서 8칸을 생성한다.

regular_regular 은 만들지 않는다(현행 MANNEQUIN_BASE_WOMEN_ASSET_ID 가 담당).
생성물은 자동 승격하지 않는다 — 로컬에 내려받아 사람이 포즈·프레이밍을 검수하고,
살아남은 파일만 --promote 로 R2·assets·env 에 올린다(탈락한 칸은 파일을 지우면 된다).

실행:
  cd server
  .venv/bin/python -m scripts.seed_mannequin_matrix --out ./matrix_review
  .venv/bin/python -m scripts.seed_mannequin_matrix --out ./matrix_review --cell slim_volume
  .venv/bin/python -m scripts.seed_mannequin_matrix --promote ./matrix_review
"""
import argparse
import asyncio
import itertools
import json
import pathlib
import sys
import uuid

from scripts._env import load_env

load_env()

import psycopg  # noqa: E402

from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.mannequin_body import DEFAULT, LEVELS  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import IMMUTABLE_CACHE, R2Client  # noqa: E402

SERVER_ENV = pathlib.Path(__file__).resolve().parents[1] / ".env"

# regular_regular 은 제외 — 현행 단일 에셋이 담당한다(그래야 기본값 셀러의 결과가 안 바뀐다).
CELLS = [(b, h) for b, h in itertools.product(LEVELS, LEVELS) if not (b == DEFAULT and h == DEFAULT)]

# 볼륨 레벨 → 고정 영문 문구. 상수만 보간한다 — 자유 문자열이 끼어들 자리가 없다.
_VOLUME_EN = {
    "slim": "noticeably slimmer and flatter than the reference",
    "regular": "unchanged from the reference",
    "volume": "noticeably fuller and rounder than the reference",
}

PROMPT = """<role>
You are a studio product photographer preparing a fixed base mannequin for e-commerce photography.
</role>

<instruction>
- Output the SAME studio mannequin as the attached image, with ONE change: its body volume.
- Bust volume: {bust}. Hip volume: {hip}.
- Keep EVERYTHING else identical in character: the same pose, the same camera angle and framing,
  the same distance and crop, the same plain studio background, the same lighting and shadows,
  the same head shape, and the same bare feet in the same position on the floor.
- The mannequin remains undressed, matte, and featureless — no garments, no skin texture,
  no face, no hair.
</instruction>

<output format>
- Output EXACTLY ONE photorealistic image, fully opaque, portrait orientation.
- FULL BODY in frame, from the top of the head down to the feet — nothing cropped.
- No grid, no collage, no panels, no text.
</output format>"""


def cell_key(bust: str, hip: str) -> str:
    return f"{bust}_{hip}"


def r2_key(bust: str, hip: str) -> str:
    return f"seed/mannequin/base-women-{cell_key(bust, hip)}-2K.png"


def upsert_env(path: pathlib.Path, key: str, value: str) -> None:
    """append_env(seed_phase4)와 달리 기존 값을 덮어쓴다 — 매트릭스는 재승격이 정상 운영이다."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  .env: {key} {'갱신' if replaced else '추가'}")


def load_base_bytes(settings, conn) -> tuple[bytes, str]:
    """현행 여성 베이스 에셋의 (bytes, mime). 미설정이면 즉시 중단한다."""
    asset_id = settings.base_mannequin_women_asset_id
    if not asset_id:
        raise SystemExit("MANNEQUIN_BASE_WOMEN_ASSET_ID 가 없습니다 — 먼저 베이스를 시드하세요.")
    with conn.cursor() as cur:
        cur.execute("select r2_key, mime_type from assets where id = %s", (asset_id,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"베이스 에셋 {asset_id} 을 assets 에서 찾을 수 없습니다.")
    return R2Client(settings).get_bytes(row[0]), row[1]


async def generate(settings, base: InlineImage, bust: str, hip: str) -> bytes:
    gemini = GeminiImageClient(settings)
    prompt = PROMPT.format(bust=_VOLUME_EN[bust], hip=_VOLUME_EN[hip])
    res = await gemini.generate_content_image(
        resolve_model(settings, "image_high"), prompt, [base],
        settings.mannequin_image_size, aspect_ratio=settings.mannequin_aspect_ratio)
    return res.image


def cmd_out(settings, out_dir: pathlib.Path, only: str | None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.database_url) as conn:
        data, mime = load_base_bytes(settings, conn)
    (out_dir / "_reference.png").write_bytes(data)
    base = InlineImage(mime, data)
    targets = [(b, h) for b, h in CELLS if only is None or cell_key(b, h) == only]
    if not targets:
        print(f"알 수 없는 칸: {only} (가능: {[cell_key(b, h) for b, h in CELLS]})")
        return 1
    for bust, hip in targets:
        image = asyncio.run(generate(settings, base, bust, hip))
        path = out_dir / f"{cell_key(bust, hip)}.png"
        path.write_bytes(image)
        print(f"  생성 {path} ({len(image)} bytes)")
    print(f"\n검수: {out_dir}/_reference.png 와 나란히 비교하세요. "
          f"포즈·프레이밍·배경이 어긋난 칸은 파일을 지우면 승격에서 빠집니다.")
    return 0


def cmd_promote(settings, out_dir: pathlib.Path) -> int:
    r2 = R2Client(settings)
    mapping: dict[str, str] = {}
    with psycopg.connect(settings.database_url) as conn:
        for bust, hip in CELLS:
            path = out_dir / f"{cell_key(bust, hip)}.png"
            if not path.exists():
                print(f"  건너뜀 {cell_key(bust, hip)} (검수 탈락 또는 미생성)")
                continue
            data = path.read_bytes()
            key = r2_key(bust, hip)
            r2.put_bytes(key, data, "image/png", cache=IMMUTABLE_CACHE)
            with conn.cursor() as cur:  # 멱등: r2_key 기준 재사용 (seed_phase4 패턴)
                cur.execute("select id::text from assets where r2_key = %s", (key,))
                row = cur.fetchone()
                if row:
                    asset_id = row[0]
                else:
                    asset_id = str(uuid.uuid4())
                    cur.execute(
                        "insert into assets (id, user_id, project_id, source, visibility, "
                        "r2_bucket, r2_key, mime_type, byte_size) "
                        "values (%s, null, null, 'seed', 'private', %s, %s, 'image/png', %s)",
                        (asset_id, settings.r2_bucket, key, len(data)))
            mapping[cell_key(bust, hip)] = asset_id
            print(f"  승격 {cell_key(bust, hip)}: asset {asset_id} key={key}")
        conn.commit()
    if not mapping:
        print("승격할 칸이 없습니다.")
        return 1
    upsert_env(SERVER_ENV, "MANNEQUIN_BASE_WOMEN_MATRIX",
               json.dumps(mapping, ensure_ascii=False, separators=(",", ":")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, help="8칸 생성 후 저장할 검수 디렉터리")
    ap.add_argument("--cell", help="그 칸만 재생성 (예: slim_volume)")
    ap.add_argument("--promote", type=pathlib.Path, help="검수 통과 파일만 R2·assets·env 로 승격")
    args = ap.parse_args()
    settings = load_settings()
    if args.promote:
        return cmd_promote(settings, args.promote)
    if args.out:
        return cmd_out(settings, args.out, args.cell)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
