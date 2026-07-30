"""가슴 2패스 A/B — **비용은 계측됐는데 편익은 측정된 적이 없다**.

관측(`qc_observe`)에서 2패스가 편집 전/후 쌍의 42% 를 되돌리게 만들고 그중 7건은 없던
치명오류(`garment shape broken`)를 만든다는 게 나왔다. 그런데 2패스가 애초에 무엇을 벌어
주는지는 숫자가 없다 — "여성 마네킹이 여성 체형으로 보이는가"는 A~C 축이 재는 게 아니다.
손익을 같은 표에 놓지 않으면 재캘리브를 판단할 근거가 없다.

**짝 비교**로 잰다. 같은 베이스 컷에 2패스만 얹어 before/after 를 만든다(재생성으로 두 장을
뽑으면 무작위 변동이 효과보다 크다). 판정은 둘:
  - 편익: blinded pairwise — 어느 쪽이 여성 상체 형태를 더 분명히 보여주는가.
    좌우를 **바꿔서 두 번** 물어 위치 편향을 상쇄한다. 두 번 다 같은 쪽을 골라야 1승.
  - 비용: image_qc 4축 재판정 + `edit_regressed` 가 되돌릴지 여부.

실행:
    cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
      .venv/bin/python -m scripts.ab_bust_pass --projects 3

`--variant` 로 대안 템플릿을 주면 **같은 베이스 컷**에 여러 프롬프트를 각각 얹어 나란히
비교한다 — 재캘리브의 손익을 한 표에서 본다.

    .venv/bin/python -m scripts.ab_bust_pass --projects 3 --variant prompts/mannequin_bust_v2.txt

비용: 프로젝트당 이미지모델 1+N 콜(생성 1 + 비교군 N) + vision 3N+1 콜. 읽기 전용 아님 —
베이스 컷 생성이 실제로 프로젝트에 컷을 남긴다(2패스 결과는 R2 에 저장하지 않는다).
"""
import argparse
import asyncio
import json
import pathlib
import uuid

from scripts._env import load_env

load_env()

from app import repo  # noqa: E402
from app.agents import image_qc, mannequin_bust  # noqa: E402
from app.agents.gemini_image import InlineImage  # noqa: E402
from app.agents.prompts import load_bust_prompt_template  # noqa: E402
from app.agents.vision_llm import _call_gemini  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.workers.mannequin_job import edit_regressed, resolve_model, score_outcome  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parents[1] / "ab_out/bust_ab"

_PICK_SQL = """
select p.id::text as project_id, p.user_id::text as user_id, pr.clothing_type
from projects p
join products pr on pr.project_id = p.id
where exists (select 1 from analyses a where a.project_id = p.id)
  and jsonb_array_length(coalesce(pr.colors, '[]'::jsonb)) > 0
  and p.deleted_at is null
order by p.created_at desc
limit %s
"""

# 기대 방향은 프롬프트에 노출하지 않는다 — 어느 쪽이 2패스본인지 알려주면 판정이 기운다
# (mannequin_pairwise_qc 와 같은 규율). 채점 매핑은 여기 바깥에서 한다.
_BENEFIT_PROMPT = (
    "You are comparing two e-commerce studio photos of the SAME garment on a mannequin, shown "
    "side by side. The FIRST image is LEFT, the SECOND image is RIGHT.\n"
    "Question: on which side does the mannequin's torso read more clearly as a WOMAN'S body "
    "shape (defined bust and waist), rather than a flat or unisex form?\n"
    "Judge ONLY this. Ignore color, print, logo, fabric, background, and framing. Do NOT assume "
    "either side is a target or 'correct' version.\n"
    'Answer moreSide = "left" or "right". If both read the same, answer "similar". If you '
    'cannot tell, answer "unclear". Give a one-sentence visual reason.'
)
_SCHEMA = {
    "type": "object",
    "properties": {
        "moreSide": {"type": "string", "enum": ["left", "right", "similar", "unclear"]},
        "reason": {"type": "string"},
    },
    "required": ["moreSide", "reason"],
}


# 편익만 물으면 **손상이 이긴다**. 2026-07-31 육안 실측: 오버사이즈 티를 몸에 붙는
# 미니원피스로 바꾼 출력이 "여성 체형이 더 분명하다"로 3/3 승리했다. 옷을 조일수록 이기는
# 지표라 재캘리브의 방향을 거꾸로 가리킨다. 그래서 **실루엣 보존을 통과 조건으로 건다** —
# 핏 등급(오버사이즈/레귤러/슬림)이 바뀌면 편익 승리는 무효다.
_FIT_PROMPT = (
    "You are comparing two e-commerce studio photos of the SAME garment on a mannequin, shown "
    "side by side. The FIRST image is LEFT, the SECOND image is RIGHT.\n"
    "Question: do both photos show the garment worn with the SAME FIT — the same amount of ease "
    "between the garment and the body, the hem sitting at the same height, the same silhouette "
    "class (an oversized tee stays an oversized tee; it has not become a fitted one)?\n"
    "Judge ONLY the fit and silhouette. Ignore bust size, color, print, background and framing.\n"
    'Answer changed = false if the fit is the same, true if one side is visibly tighter, looser, '
    "shorter or longer than the other. Give a one-sentence visual reason citing a landmark "
    "(hem height, waist ease, sleeve width)."
)
_FIT_SCHEMA = {
    "type": "object",
    "properties": {"changed": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["changed", "reason"],
}


async def _benefit_vote(s, before: bytes, after: bytes, mime: str) -> dict:
    """좌우를 바꿔 두 번 묻는다. 두 번 다 2패스본을 고르고 **핏이 유지될 때만** win."""
    async def ask(prompt, schema, left, right, key):
        raw = await _call_gemini(
            s, s.model_text_gemini, prompt,
            [InlineImage(mime, left), InlineImage(mime, right)], schema, 60.0,
            thinking_level="low")
        return raw.get(key), str(raw.get("reason") or "")[:200]

    a, _ = await ask(_BENEFIT_PROMPT, _SCHEMA, before, after, "moreSide")   # after = right
    b, _ = await ask(_BENEFIT_PROMPT, _SCHEMA, after, before, "moreSide")   # after = left
    fit_changed, fit_why = await ask(_FIT_PROMPT, _FIT_SCHEMA, before, after, "changed")
    out = {"votes": [a, b], "fit_changed": fit_changed, "fit_reason": fit_why}
    if a == "right" and b == "left":
        # 핏이 바뀌었으면 "옷을 조여서 이긴 것"이라 편익으로 세지 않는다.
        out["verdict"] = "won_by_damage" if fit_changed else "bust_wins"
    elif a == "left" and b == "right":
        out["verdict"] = "base_wins"
    else:
        out["verdict"] = "inconclusive"
    return out


async def _run_one(app, pool, s, row: dict, templates: dict) -> dict:
    from app.workers.mannequin_job import run_mannequin_job

    from app.agents import mannequin

    pid, uid = row["project_id"], row["user_id"]
    async with pool.connection() as conn:
        analysis = await repo.get_analysis(conn, pid)
        product = await repo.get_product(conn, pid)
        before_cuts = await repo.list_mannequin_cuts(conn, uid, pid)
    # 2패스는 **여성 기본 마네킹 전용**이다(`mannequin_bust.should_apply`). 남성 프로젝트에
    # 강제로 걸면 운영에서 절대 일어나지 않는 조합을 재고 있는 셈이라 결과가 무의미해진다.
    gender = mannequin.select_base_gender(
        analysis or {}, (product or {}).get("clothing_type"))
    if gender != "women":
        return {"project_id": pid, "status": "skipped", "reason": f"base_gender={gender}"}

    async with pool.connection() as conn:
        job, created = await repo.create_job(
            conn, user_id=uid, project_id=pid, kind="mannequin", payload={},
            idempotency_key=None, credits_reserved=0, metadata={"ab": "bust"})
        await conn.commit()
    if not created:
        return {"project_id": pid, "status": "skipped", "reason": "활성 잡 중복"}

    lease = f"ab-bust:{uuid.uuid4()}"
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"update jobs set status='running', locked_by=%s, locked_at=now(), "
                f"started_at=coalesce(started_at, now()) "
                f"where id=%s and status='pending' returning {repo._JOB_COLS}, "
                f"locked_by as lease_token", (lease, job["id"]))
            claimed = await cur.fetchone()
        await conn.commit()
    if claimed is None:
        return {"project_id": pid, "status": "stolen"}

    await run_mannequin_job(app, claimed)          # 베이스 컷 (bust 는 off 로 돌린다)
    async with pool.connection() as conn:
        after_cuts = await repo.list_mannequin_cuts(conn, uid, pid)
    # `list_mannequin_cuts` 는 (candidate, version) 정렬이라 마지막 원소가 방금 만든 컷이라는
    # 보장이 없다 — 기존 B 컷이 있으면 과거 B 를 집는다. 집합 차이로 고른다.
    seen = {(c["candidate"], c["version"]) for c in before_cuts}
    fresh = [c for c in after_cuts if (c["candidate"], c["version"]) not in seen]
    if not fresh:
        return {"project_id": pid, "status": "no_cut"}
    cut = fresh[-1]

    base = await asyncio.to_thread(app.state.r2.get_bytes, cut["r2_key"])
    mime = cut.get("mime_type") or "image/png"
    prod_imgs = await _load_product_images(pool, s, app, pid, uid)
    pre = await image_qc.verdict(s, prod_imgs, InlineImage(mime, base), scored=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{pid[:8]}_base.png").write_bytes(base)

    arms = {}
    for name, template in templates.items():
        out = await app.state.gemini.generate_content_image(
            resolve_model(s, "image_high"), mannequin_bust.build_prompt(template),
            [InlineImage(mime, base)], s.mannequin_image_size,
            aspect_ratio=s.mannequin_aspect_ratio)
        post = await image_qc.verdict(
            s, prod_imgs, InlineImage(out.mime, out.image), scored=True)
        (OUT / f"{pid[:8]}_{name}.png").write_bytes(out.image)
        arms[name] = {
            "benefit": await _benefit_vote(s, base, out.image, mime),
            "cost": {
                "pre_grade": score_outcome(s, pre), "post_grade": score_outcome(s, post),
                "pre_fidelity": pre.get("product_fidelity"),
                "post_fidelity": post.get("product_fidelity"),
                "new_critical": [e for e in (post.get("critical_errors") or [])
                                 if e not in (pre.get("critical_errors") or [])],
                "would_revert": edit_regressed(s, pre, post),
            },
        }
    return {"project_id": pid, "status": "ok", "clothing_type": row["clothing_type"],
            "arms": arms}


async def _load_product_images(pool, s, app, project_id: str, user_id: str) -> list:
    """image_qc 의 기준이 되는 상품 원본 — **워커와 같은 경로**로 고른다.

    직접 SQL 을 짜면 워커가 실제로 무엇을 판정 기준으로 삼는지와 어긋난다(색상 슬롯 선택
    로직이 `mannequin.base_color_images` 안에 있다). 기준이 다르면 비교 자체가 무의미하다.
    """
    from app.agents import mannequin

    async with pool.connection() as conn:
        product = await repo.get_product(conn, project_id)
        imgs = []
        for _slot, aid in mannequin.base_color_images(product):
            a = await repo.get_asset_for_user(conn, user_id, aid)
            if a:
                imgs.append(InlineImage(
                    a["mime_type"], await asyncio.to_thread(app.state.r2.get_bytes, a["r2_key"])))
    return imgs


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", type=int, default=3)
    ap.add_argument("--variant", action="append", default=[],
                    help="비교할 2패스 템플릿 파일. 여러 번 지정 가능. 미지정이면 현행만.")
    args = ap.parse_args()

    import psycopg
    from types import SimpleNamespace

    from psycopg.rows import dict_row

    from app.agents.gemini_image import GeminiImageClient
    from app.db import create_pool
    from app.r2 import R2Client

    s = load_settings()
    assert s.mannequin_bust_pass == "off", (
        "베이스 컷은 2패스 없이 뽑아야 짝 비교가 성립한다 — MANNEQUIN_BUST_PASS=off 로 실행할 것")
    with psycopg.connect(s.database_url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(_PICK_SQL, (args.projects,))
        rows = cur.fetchall()
    templates = {"v1": load_bust_prompt_template()}
    for path in args.variant:
        templates[pathlib.Path(path).stem.replace("mannequin_bust_", "")] = \
            pathlib.Path(path).read_text(encoding="utf-8")
    print(f"[ab_bust] 대상 {len(rows)}건 · 비교군 {list(templates)}")

    pool = create_pool(s.database_url)
    await pool.open()
    app = SimpleNamespace(state=SimpleNamespace(
        settings=s, pool=pool, r2=R2Client(s), gemini=GeminiImageClient(s)))
    results = []
    for row in rows:
        try:
            results.append(await _run_one(app, pool, s, row, templates))
        except Exception as e:                       # 한 건 실패가 나머지를 죽이지 않게
            results.append({"project_id": row["project_id"], "status": "error",
                            "error": f"{type(e).__name__}: {e}"})
        r = results[-1]
        if r["status"] == "ok":
            for name, arm in r["arms"].items():
                c = arm["cost"]
                print(f"  {r['project_id'][:8]} {name:4} 편익={arm['benefit']['verdict']:14} "
                      f"fid {c['pre_fidelity']}→{c['post_fidelity']} 되돌림={c['would_revert']} "
                      f"{c['new_critical'] or ''}")
        else:
            print(f"  {r['project_id'][:8]} {r['status']} {r.get('reason') or r.get('error','')}")
    await pool.close()

    ok = [r for r in results if r["status"] == "ok"]
    print(f"\n[ab_bust] n={len(ok)}")
    for name in templates:
        arms = [r["arms"][name] for r in ok]
        wins = sum(a["benefit"]["verdict"] == "bust_wins" for a in arms)
        damaged = sum(a["benefit"]["verdict"] == "won_by_damage" for a in arms)
        base_wins = sum(a["benefit"]["verdict"] == "base_wins" for a in arms)
        fit_broken = sum(bool(a["benefit"]["fit_changed"]) for a in arms)
        reverts = sum(a["cost"]["would_revert"] for a in arms)
        crit = sum(bool(a["cost"]["new_critical"]) for a in arms)
        print(f"  {name:4} 순편익 {wins}/{len(arms)}  (조여서이김 {damaged} · 베이스승 {base_wins})"
              f"  ·  핏깨짐 {fit_broken}/{len(arms)}  되돌림 {reverts}  신규치명 {crit}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"  → {OUT/'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
