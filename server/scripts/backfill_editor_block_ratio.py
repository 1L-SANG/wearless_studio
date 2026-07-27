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
import copy
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
# 진짜 UUID 형태만 — 느슨하면 '/v1/assets/------.../file' 같은 값이 통과해 uuid 질의가 죽는다.
_SRC_RE = re.compile(
    r"/v1/assets/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/file")

# 구 page_assembler 가 만든 AI 컷 블록의 지문 — 이것과 다르면 사용자 편집분으로 보고 건너뛴다.
_OLD_EL = {"x": 60, "y": 50, "w": 880, "h": 560}
_OLD_BLOCK_H = 660
# 구 body 카피의 지문 — 지오메트리 + 생성 스타일까지 봐야 같은 자리의 사용자 라벨과 구분된다.
_OLD_BODY = {"x": 120, "y": 560, "w": 760, "h": 40}
_OLD_BODY_STYLE = {"size": 18, "color": "#4a4a45"}


def _asset_id(src) -> str | None:
    m = _SRC_RE.search(str(src or ""))
    return m.group(1) if m else None


def _plan_block(block: dict, dims_by_asset: dict) -> dict | None:
    """이 블록을 어떻게 고칠지. 대상 아니면 None."""
    if not isinstance(block, dict) or block.get("h") != _OLD_BLOCK_H:
        return None
    els = block.get("elements")
    if not isinstance(els, list):
        return None
    img = next((e for e in els
                if isinstance(e, dict) and e.get("type") == "image" and e.get("cutType")
                and e.get("id")  # id 없으면 대상 식별이 불가 — 다른 id 없는 요소까지 리사이즈된다
                and all(e.get(k) == v for k, v in _OLD_EL.items())), None)
    if img is None:
        return None
    # 사용자가 크롭을 커밋한 요소는 crop{ox,oy,iw,ih} 가 프레임과 짝을 이룬다. 프레임만 키우면
    # 크롭 이미지가 560px 로 남아 공백이 생긴다 → 크롭이 있으면 손대지 않는다.
    if img.get("crop") is not None:
        return None
    aid = _asset_id(img.get("src"))
    dims = dims_by_asset.get(aid) if aid else None
    # **실측 dims 를 아는 블록만** 고친다. 모르면 손대지 않는다 — 여기서 2:3 폴백을 쓰면
    # 가로 이미지(예: 외부 URL·구 자산)의 프레임을 세로로 바꿔 오히려 폭을 잘라먹는다.
    if not dims or not all(isinstance(v, int) and v > 0 for v in dims):
        return None
    new_w, new_h = _image_box(*dims)
    if (new_w, new_h) == (_OLD_EL["w"], _OLD_EL["h"]):
        return None  # 이미 맞는 비율(가로 소스) — 손댈 것 없음
    return {"img_id": img.get("id"), "w": new_w, "h": new_h, "dims": dims}


def _apply_block(block: dict, plan: dict) -> int:
    """블록을 제자리 수정하고 새 블록 높이를 반환."""
    els = [e for e in (block.get("elements") or []) if isinstance(e, dict)]
    for el in els:
        if el.get("id") == plan["img_id"]:
            el["w"], el["h"] = plan["w"], plan["h"]
        # 구 body 카피(지오메트리 + 생성 스타일 전부 일치)만 이미지 하단 근처로 따라 내린다.
        elif (el.get("type") == "text"
                and all(el.get(k) == v for k, v in _OLD_BODY.items())
                and isinstance(el.get("style"), dict)
                and all(el["style"].get(k) == v for k, v in _OLD_BODY_STYLE.items())):
            el["y"] = _OLD_EL["y"] + plan["h"] - 50
    block["h"] = _block_height(els)  # 이미지·카피 전부를 담는 높이(프로덕션 헬퍼 재사용)
    return block["h"]


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

            # 참조된 asset dims 를 한 번에. 파손 JSON(문자열 블록·elements 가 int 등)이 섞여도
            # 죽지 않게 전 구간 방어 — 한 프로젝트 때문에 전체 백필이 중단되면 안 된다.
            asset_ids: set[str] = set()
            for r in rows:
                for b in (r["editor_blocks"] or []):
                    if not isinstance(b, dict):
                        continue
                    els_raw = b.get("elements")
                    if not isinstance(els_raw, list):
                        continue
                    for e in els_raw:
                        if isinstance(e, dict) and (aid := _asset_id(e.get("src"))):
                            asset_ids.add(aid)
            dims_by_asset: dict[str, tuple] = {}
            if asset_ids:
                await cur.execute(
                    "select id::text as id, width, height from assets where id = any(%s)",
                    (list(asset_ids),))
                dims_by_asset = {a["id"]: (a["width"], a["height"]) for a in await cur.fetchall()}

            total_projects = total_blocks = failed = conflicts = 0
            for r in rows:
                blocks = r["editor_blocks"] or []
                original = copy.deepcopy(blocks)  # 낙관적 잠금 비교용 원본 스냅샷
                changed = 0
                try:
                    for blk in blocks:
                        plan = _plan_block(blk, dims_by_asset)
                        if plan is None:
                            continue
                        if changed == 0:
                            print(f"  project {str(r['id'])[:8]}")
                        new_block_h = _apply_block(blk, plan)
                        print(f"    {blk.get('id')}: 880x560/h660 → {plan['w']}x{plan['h']}"
                              f"/h{new_block_h}  (asset dims={plan['dims']})")
                        changed += 1
                except Exception as e:  # 파손 데이터 1건이 전체 백필을 중단시키지 않게 격리
                    failed += 1
                    print(f"  !! project {str(r['id'])[:8]} 건너뜀: {e!r}")
                    continue
                if not changed:
                    continue
                if args.apply:
                    # 낙관적 잠금 — 읽은 뒤 에디터가 자동저장했으면 editor_blocks 가 달라져
                    # 0행 갱신이 된다. 통째 덮어써 사용자의 최신 편집을 지우지 않는다.
                    await cur.execute(
                        "update projects set editor_blocks = %s "
                        "where id = %s and editor_blocks = %s",
                        (Json(blocks), r["id"], Json(original)))
                    if cur.rowcount == 0:
                        conflicts += 1
                        print(f"  !! project {str(r['id'])[:8]} 동시 편집 감지 — 건너뜀(재실행 필요)")
                        continue
                total_projects += 1
                total_blocks += changed
        if args.apply:
            await conn.commit()

    print(f"\n{'적용' if args.apply else '대상'}: 프로젝트 {total_projects}개 · 블록 {total_blocks}개"
          + (f" · 건너뜀(파손) {failed}개" if failed else "")
          + (f" · 건너뜀(동시편집) {conflicts}개" if conflicts else ""))
    if not args.apply and total_blocks:
        print("실제 반영하려면 --apply 를 붙여 다시 실행")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
