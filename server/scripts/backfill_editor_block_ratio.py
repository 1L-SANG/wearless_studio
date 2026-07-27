"""저장된 editor_blocks 의 AI 컷 지오메트리를 이미지 비율대로 보정 (WS2).

배경: 구 page_assembler 는 AI 컷 요소를 880×560(가로) 고정으로 만들어, 2:3 세로 컷이
object-fit:cover 로 높이의 ~58% 가 잘렸다(에디터·미리보기·다운로드 동일). 신규 생성은
고쳐졌지만 **이미 저장된 블록**은 그대로라 재생성 전까지 계속 잘린다.

안전 원칙:
  · **구 생성 시그니처와 정확히 일치하는 블록만** 보정한다(x=60,y=50,w=880,h=560 + block h=660).
    사용자가 크기·위치를 손댄 블록은 건드리지 않는다(수동 편집 파괴 금지).
  · 지오메트리 계산은 프로덕션 코드(page_assembler)를 **그대로 재사용** — 백필과 생성이 갈리지 않게.
  · 기본은 dry-run. 쓰기는 --apply 명시할 때만. 멱등(이미 보정된 블록은 시그니처 불일치로 스킵).

실행:
  cd server && .venv/bin/python -m scripts.backfill_editor_block_ratio                 # 로컬 dry-run
  cd server && .venv/bin/python -m scripts.backfill_editor_block_ratio --apply         # 로컬 적용
  cd server && .venv/bin/python -m scripts.backfill_editor_block_ratio --db prod       # prod dry-run
  cd server && .venv/bin/python -m scripts.backfill_editor_block_ratio --db prod --apply
"""

import argparse
import asyncio
import json
import os
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from scripts._env import load_env

load_env()

from app.agents.page_assembler import _block_height, _image_box  # noqa: E402

LOCAL_DB = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
_SRC_RE = re.compile(r"/v1/assets/([0-9a-fA-F-]{36})/file")

# 구 page_assembler 가 만든 AI 컷 블록의 지문 — 이것과 다르면 사용자 편집분으로 보고 건너뛴다.
_OLD_EL = {"x": 60, "y": 50, "w": 880, "h": 560}
_OLD_BLOCK_H = 660
_OLD_BODY_Y = 560


def _asset_id(src) -> str | None:
    m = _SRC_RE.search(str(src or ""))
    return m.group(1) if m else None


def _plan_block(block: dict, dims_by_asset: dict) -> dict | None:
    """이 블록을 어떻게 고칠지. 대상 아니면 None."""
    if not isinstance(block, dict) or block.get("h") != _OLD_BLOCK_H:
        return None
    els = block.get("elements") or []
    img = next((e for e in els
                if isinstance(e, dict) and e.get("type") == "image" and e.get("cutType")
                and all(e.get(k) == v for k, v in _OLD_EL.items())), None)
    if img is None:
        return None
    aid = _asset_id(img.get("src"))
    dims = dims_by_asset.get(aid) if aid else None
    new_w, new_h = _image_box(*(dims or (None, None)))
    if (new_w, new_h) == (_OLD_EL["w"], _OLD_EL["h"]):
        return None  # 이미 맞는 비율(가로 소스) — 손댈 것 없음
    return {"img_id": img.get("id"), "w": new_w, "h": new_h,
            "block_h": _block_height(new_h), "dims": dims}


def _apply_block(block: dict, plan: dict) -> None:
    for el in block.get("elements") or []:
        if el.get("id") == plan["img_id"]:
            el["w"], el["h"] = plan["w"], plan["h"]
        # body 카피는 이미지 하단 근처가 원래 관계 — 구 y(560)인 텍스트만 따라 내린다.
        elif el.get("type") == "text" and el.get("y") == _OLD_BODY_Y:
            el["y"] = _OLD_EL["y"] + plan["h"] - 50
    block["h"] = plan["block_h"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", choices=["local", "prod"], default="local")
    ap.add_argument("--apply", action="store_true", help="실제 쓰기(미지정=dry-run)")
    ap.add_argument("--project", default=None, help="특정 프로젝트만")
    args = ap.parse_args()

    dsn = LOCAL_DB if args.db == "local" else os.environ["DATABASE_URL"]
    print(f"DB={args.db} mode={'APPLY' if args.apply else 'DRY-RUN'}")

    async with await psycopg.AsyncConnection.connect(
            dsn, connect_timeout=15, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            sql = ("select id, editor_blocks from projects "
                   "where jsonb_typeof(editor_blocks) = 'array' "
                   "and jsonb_array_length(editor_blocks) > 0")
            params: tuple = ()
            if args.project:
                sql += " and id = %s"
                params = (args.project,)
            await cur.execute(sql, params)
            rows = await cur.fetchall()

            # 참조된 asset dims 를 한 번에 — 없으면 _image_box 가 2:3 폴백
            asset_ids = {
                aid
                for r in rows
                for b in (r["editor_blocks"] or [])
                for e in ((b or {}).get("elements") or [])
                if isinstance(e, dict) and (aid := _asset_id(e.get("src")))
            }
            dims_by_asset: dict[str, tuple] = {}
            if asset_ids:
                await cur.execute(
                    "select id::text as id, width, height from assets where id = any(%s)",
                    (list(asset_ids),))
                dims_by_asset = {a["id"]: (a["width"], a["height"]) for a in await cur.fetchall()}

            total_projects = total_blocks = 0
            for r in rows:
                blocks = r["editor_blocks"] or []
                changed = 0
                for blk in blocks:
                    plan = _plan_block(blk, dims_by_asset)
                    if plan is None:
                        continue
                    if changed == 0:
                        print(f"  project {str(r['id'])[:8]}")
                    print(f"    {blk.get('id')}: 880x560/h660 → {plan['w']}x{plan['h']}"
                          f"/h{plan['block_h']}  (asset dims={plan['dims']})")
                    _apply_block(blk, plan)
                    changed += 1
                if not changed:
                    continue
                total_projects += 1
                total_blocks += changed
                if args.apply:
                    await cur.execute(
                        "update projects set editor_blocks = %s where id = %s",
                        (Json(blocks), r["id"]))
        if args.apply:
            await conn.commit()

    print(f"\n{'적용' if args.apply else '대상'}: 프로젝트 {total_projects}개 · 블록 {total_blocks}개")
    if not args.apply and total_blocks:
        print("실제 반영하려면 --apply 를 붙여 다시 실행")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
