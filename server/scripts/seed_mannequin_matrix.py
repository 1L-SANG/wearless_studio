"""여성 베이스 마네킹 체형 매트릭스 시드 — 현행 베이스에서 8칸을 생성한다.

regular_regular 은 만들지 않는다(현행 MANNEQUIN_BASE_WOMEN_ASSET_ID 가 담당).
생성물은 자동 승격하지 않는다 — 로컬에 내려받아 사람이 포즈·프레이밍을 검수하고,
살아남은 파일만 --promote 로 R2·assets·env 에 올린다(탈락한 칸은 파일을 지우면 된다).

--promote 는 이번 실행에서 승격한 칸만 기존 MANNEQUIN_BASE_WOMEN_MATRIX 에 병합한다 —
이미 살아있는 다른 칸은 건드리지 않는다. 그래서 칸 하나만 재검수해서 --promote 해도
나머지 7칸이 사라지지 않는다. 반대로, 이미 살아있는 칸을 빼려면 이 스크립트로는 안 되고
server/.env 의 MANNEQUIN_BASE_WOMEN_MATRIX 값을 손으로 편집해야 한다.

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
from app.r2 import IMMUTABLE_CACHE, MIME_EXT, R2Client, ext_for_mime  # noqa: E402

SERVER_ENV = pathlib.Path(__file__).resolve().parents[1] / ".env"

# regular_regular 은 제외 — 현행 단일 에셋이 담당한다(그래야 기본값 셀러의 결과가 안 바뀐다).
CELLS = [(b, h) for b, h in itertools.product(LEVELS, LEVELS) if not (b == DEFAULT and h == DEFAULT)]

# 볼륨 레벨 → 고정 영문 문구. 상수만 보간한다 — 자유 문자열이 끼어들 자리가 없다.
_VOLUME_EN = {
    "slim": "noticeably slimmer and flatter than the reference",
    "regular": "unchanged from the reference",
    "volume": "noticeably fuller and rounder than the reference",
}

# ext → mime 역방향 조회. cmd_promote 가 로컬 파일 확장자만 보고도 원래 mime 을 복원하려면
# 필요하다(사이드카 메타 파일 없이). MIME_EXT 의 5개 값이 서로 달라 손실 없는 역매핑이다.
_EXT_MIME = {ext: mime for mime, ext in MIME_EXT.items()}

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


def r2_key(bust: str, hip: str, ext: str) -> str:
    return f"seed/mannequin/base-women-{cell_key(bust, hip)}-2K.{ext}"


def _find_cell_file(out_dir: pathlib.Path, bust: str, hip: str) -> pathlib.Path | None:
    """cmd_out 이 실제로 어떤 확장자로 저장했는지 가정하지 않고 찾는다 — Gemini 응답 mime 은
    호출마다 달라질 수 있다(cmd_out 이 항상 .png 로 쓴다고 가정하면 Fix 1 이 다시 깨진다)."""
    matches = sorted(out_dir.glob(f"{cell_key(bust, hip)}.*"))
    if len(matches) > 1:
        raise SystemExit(f"{cell_key(bust, hip)} 에 후보 파일이 여러 개입니다: {matches}")
    return matches[0] if matches else None


def _mime_for_ext(ext: str) -> str:
    mime = _EXT_MIME.get(ext.lower())
    if not mime:
        raise SystemExit(f"알 수 없는 확장자 .{ext} — 지원: {sorted(_EXT_MIME)}")
    return mime


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


def merge_matrix(existing: dict[str, str], updates: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """--promote 병합 — 기존 매트릭스에 이번 실행에서 승격한 칸만 얹는다.

    이번 실행의 out_dir 에 없는(=검수 탈락했거나 애초에 생성하지 않은) 기존 칸은 그대로
    남는다 — 삭제는 이 함수의 책임이 아니다(모듈 docstring 참고, 삭제는 .env 수동 편집).
    순수 함수라 네트워크·DB 없이 단위 검증 가능하다.

    반환: (병합된 매트릭스, {cell: '추가'|'갱신'} — 이번 실행에서 실제로 바뀐 칸만).
    """
    merged = dict(existing)
    changes: dict[str, str] = {}
    for cell, asset_id in updates.items():
        changes[cell] = "갱신" if cell in existing else "추가"
        merged[cell] = asset_id
    return merged, changes


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


async def generate(settings, base: InlineImage, bust: str, hip: str) -> tuple[bytes, str]:
    """(image bytes, 실제 응답 mime). mime 은 하드코딩하지 않는다 — Gemini 가 png 가 아닌
    형식을 돌려줄 수 있고, 그러면 R2 Content-Type·assets.mime_type 이 틀어진다.

    해상도는 하드코딩 "2K" — env MANNEQUIN_IMAGE_SIZE 로 조절되는 런타임 컷 출력 해상도
    설정(기본값 "1K")을 쓰지 않는다. 베이스 에셋은 한 번 생성되면 앞으로 모든 생성의 입력
    이미지로 영구히 쓰이므로, 그때그때 바뀌는 런타임 출력 설정과 무관하게 항상 최대 실용
    해상도로 만든다 — 안 그러면 옆에 있는 기존 2K 기본 베이스보다 낮은 해상도의 매트릭스
    셀이 조용히 섞여 들어간다."""
    gemini = GeminiImageClient(settings)
    prompt = PROMPT.format(bust=_VOLUME_EN[bust], hip=_VOLUME_EN[hip])
    res = await gemini.generate_content_image(
        resolve_model(settings, "image_high"), prompt, [base],
        "2K", aspect_ratio=settings.mannequin_aspect_ratio)
    return res.image, res.mime


def cmd_out(settings, out_dir: pathlib.Path, only: str | None) -> int:
    all_keys = [cell_key(b, h) for b, h in CELLS]
    if only is not None and only not in all_keys:  # 부작용(mkdir·DB 연결·쓰기) 전에 먼저 검증
        print(f"알 수 없는 칸: {only} (가능: {all_keys})")
        return 1
    targets = [(b, h) for b, h in CELLS if only is None or cell_key(b, h) == only]
    out_dir.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(settings.database_url) as conn:
        data, mime = load_base_bytes(settings, conn)
    (out_dir / "_reference.png").write_bytes(data)
    base = InlineImage(mime, data)
    for bust, hip in targets:
        image, img_mime = asyncio.run(generate(settings, base, bust, hip))
        ext = ext_for_mime(img_mime) or "png"
        path = out_dir / f"{cell_key(bust, hip)}.{ext}"
        path.write_bytes(image)
        print(f"  생성 {path} ({len(image)} bytes, mime={img_mime})")
    print(f"\n검수: {out_dir}/_reference.png 와 나란히 비교하세요. "
          f"포즈·프레이밍·배경이 어긋난 칸은 파일을 지우면 승격에서 빠집니다.")
    return 0


def cmd_promote(settings, out_dir: pathlib.Path) -> int:
    r2 = R2Client(settings)
    updates: dict[str, str] = {}
    with psycopg.connect(settings.database_url) as conn:
        for bust, hip in CELLS:
            cell = cell_key(bust, hip)
            path = _find_cell_file(out_dir, bust, hip)
            if path is None:
                print(f"  건너뜀 {cell} (검수 탈락 또는 미생성)")
                continue
            data = path.read_bytes()
            ext = path.suffix.lstrip(".")
            mime = _mime_for_ext(ext)
            key = r2_key(bust, hip, ext)
            r2.put_bytes(key, data, mime, cache=IMMUTABLE_CACHE)
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
                        "values (%s, null, null, 'seed', 'private', %s, %s, %s, %s)",
                        (asset_id, settings.r2_bucket, key, mime, len(data)))
            updates[cell] = asset_id
            print(f"  승격 {cell}: asset {asset_id} key={key} mime={mime}")
        conn.commit()
    if not updates:
        print("승격할 칸이 없습니다.")
        return 1
    mapping, changes = merge_matrix(settings.base_mannequin_women_matrix, updates)
    for cell in sorted(changes):
        print(f"    매트릭스 {cell}: {changes[cell]}")
    carried = len(mapping) - len(changes)
    print(f"  기존 매트릭스에서 그대로 유지된 칸: {carried}개")
    upsert_env(SERVER_ENV, "MANNEQUIN_BASE_WOMEN_MATRIX",
               json.dumps(mapping, ensure_ascii=False, separators=(",", ":")))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=pathlib.Path, help="8칸 생성 후 저장할 검수 디렉터리")
    ap.add_argument("--cell", help="그 칸만 재생성 (예: slim_volume)")
    ap.add_argument("--promote", type=pathlib.Path, help="검수 통과 파일만 R2·assets·env 로 승격")
    args = ap.parse_args()
    if args.out and args.promote:  # 동시 지정은 조용히 한쪽을 무시하지 않고 명시적으로 거절
        ap.error("--out 과 --promote 는 함께 쓸 수 없습니다 (한 번에 한 동작만 실행하세요).")
    settings = load_settings()
    if args.promote:
        return cmd_promote(settings, args.promote)
    if args.out:
        return cmd_out(settings, args.out, args.cell)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
