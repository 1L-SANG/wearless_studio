"""AG-IC 인식률 측정 — DB 에 실제로 올라온 프로젝트 사진으로 판정을 재실행한다.

합성 케이스(scripts/ic_calibrate.py)는 확대·조명만 커버한다. 이 스크립트는 셀러가 실제로
올린 앞/뒤/디테일 조합을 그대로 태우므로, 프롬프트를 만질 때마다 **같은 입력으로 전후를
비교**할 수 있다. 판정 하나가 통계 없이 바뀌는 것을 막는 유일한 장치다.

    cd server
    set -a; source .env; set +a
    .venv/bin/python scripts/ic_eval_live.py [프로젝트수] [--repeat N] [--dump]

--dump 을 주면 /tmp/ic_eval/<pid8>/ 에 실제로 모델에 보낸 이미지를 저장한다(눈으로 라벨링용).
--repeat N 은 같은 입력을 N 번 돌려 판정 흔들림을 본다 — 임계 근처 케이스를 찾을 때 쓴다.
"""

import asyncio
import os
import pathlib
import sys

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agents import input_consistency, mannequin              # noqa: E402
from app.agents.gemini_image import InlineImage                  # noqa: E402
from app.config import load_settings                             # noqa: E402
from app.r2 import R2Client                                      # noqa: E402
from app.workers.analyze_job import shrink_for_vision            # noqa: E402

DUMP_DIR = pathlib.Path("/tmp/ic_eval")


def _load(cur, pid: str, dump: bool):
    """워커와 같은 경로로 이미지를 만든다 — 기준 색상 그룹, 슬롯 순서, 같은 축소."""
    cur.execute("select colors from products where project_id=%s", (pid,))
    row = cur.fetchone()
    pairs = mannequin.base_color_images({"colors": row["colors"]} if row else {"colors": []})
    if len(pairs) < 2 or pairs[0][0] != "Front":
        return None, None, pairs
    r2 = R2Client(load_settings())
    images, slots = [], []
    out = DUMP_DIR / pid[:8]
    if dump:
        out.mkdir(parents=True, exist_ok=True)
    for i, (slot, aid) in enumerate(pairs, 1):
        cur.execute("select r2_key, mime_type from assets where id=%s", (aid,))
        a = cur.fetchone()
        data, mime = shrink_for_vision(r2.get_bytes(a["r2_key"]), a["mime_type"])
        if dump:
            (out / f"{i}_{slot}.jpg").write_bytes(data)
        images.append(InlineImage(mime, data))
        slots.append(slot)
    return images, slots, pairs


def main() -> int:
    args = [a for a in sys.argv[1:]]
    dump = "--dump" in args
    repeat = 1
    if "--repeat" in args:
        repeat = int(args[args.index("--repeat") + 1])
    limit = next((int(a) for a in args if a.isdigit()), 10)

    if not os.getenv("DATABASE_URL") or not os.getenv("GEMINI_API_KEY"):
        print("DATABASE_URL·GEMINI_API_KEY 가 필요해요: `set -a; source .env; set +a`")
        return 2

    settings = load_settings()
    print(f"모델={settings.model_text_gemini} thinking={input_consistency._THINKING} "
          f"임계={input_consistency.MIN_MISMATCH_CONFIDENCE}\n")

    counts = {"match": 0, "mismatch": 0, "unclear": 0}
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn, \
            conn.cursor() as cur:
        cur.execute(
            "select p.id::text pid from projects p "
            "where (select count(*) from assets a where a.project_id = p.id) >= 2 "
            "order by p.created_at desc limit %s", (limit,))
        for row in cur.fetchall():
            pid = row["pid"]
            images, slots, pairs = _load(cur, pid, dump)
            if images is None:
                print(f"  {pid[:8]} SKIP  {[s for s, _ in pairs]}")
                continue
            for r in range(repeat):
                try:
                    out = asyncio.run(input_consistency.judge(settings, images, slots))
                except Exception as e:                      # noqa: BLE001
                    print(f"  {pid[:8]} ERROR {e!r}"[:120])
                    continue
                counts[out["verdict"]] = counts.get(out["verdict"], 0) + 1
                reasons = "; ".join(o["reason"] for o in out["offending"])
                tag = f"{pid[:8]}" if r == 0 else " " * 8
                print(f"  {tag} {'/'.join(slots):18} {out['verdict']:9} "
                      f"{out['confidence']:.2f} {reasons[:72]}")

    print(f"\nmatch {counts['match']} · mismatch {counts['mismatch']} "
          f"· unclear {counts['unclear']}")
    print("판정이 맞는지는 사람이 봐야 합니다 — --dump 로 저장한 이미지와 대조하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
