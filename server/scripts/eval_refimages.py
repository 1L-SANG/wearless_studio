"""로컬 on/off 레퍼런스 앵커 평가 하니스 — 검색 증강 Phase 3 (A3, retrieval_upgrade_prd §7).

한 프로젝트의 실제 입력(베이스 마네킹 + 업로드 상품 + 분석)으로 마네킹컷을 flag OFF/ON 두 번
생성해 나란히 저장한다. ON 은 로컬 코퍼스에서 유사 성공컷을 STYLE REFERENCE 로 첨부(+오염 가드,
워커와 동일 조립). 자기 프로젝트 컷은 코퍼스에서 임시 제외(순환 방지). image_qc(①동일성) 점수 병기.

실행: cd server && DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
        .venv/bin/python -m scripts.eval_refimages --project <uuid> [--out ab_out/refeval] [--topk N]
전제: 로컬 54322 에 코퍼스 임베딩(seed_ref_images + embed_corpus), GEMINI_API_KEY, [embeddings] 설치.
비용: 생성 2콜(off/on) + image_qc 2콜/프로젝트. Gemini 실호출.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

from scripts._env import load_env

load_env()

import base64  # noqa: E402
import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app import repo  # noqa: E402
from app.agents import image_qc, mannequin  # noqa: E402
from app.agents.gemini_image import GeminiImageClient, InlineImage  # noqa: E402
from app.agents.model_routing import resolve_model  # noqa: E402
from app.agents.prompts import load_prompt_template, render_mannequin_prompt  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.r2 import R2Client  # noqa: E402
from app.services import embeddings as E  # noqa: E402
from app.workers.mannequin_job import _STYLE_REF_GUARD, _build_manifest, _ref_manifest_lines  # noqa: E402

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _fetch_inputs(url, pid):
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("select payload from analyses where project_id=%s limit 1", (pid,))
        r = cur.fetchone()
        analysis = (r or {}).get("payload") or {}
        cur.execute("select clothing_type from products where project_id=%s", (pid,))
        p = cur.fetchone()
        clothing_type = (p or {}).get("clothing_type") or analysis.get("clothingType") or "top"
        cur.execute(
            "select r2_key, mime_type from assets where project_id=%s and source='upload' "
            "and deleted_at is null order by created_at",
            (pid,),
        )
        uploads = cur.fetchall()
    return analysis, clothing_type, uploads


async def _search_refs(url, s, front_bytes, clothing_type, gender, pid, topk):
    """자기 프로젝트 컷을 임시 제외하고(순환 방지) 코퍼스에서 top-k 검색 → InlineImage 리스트."""
    qv = E.embed_image(front_bytes, model_id=s.embed_image_model, expected_dim=s.embed_image_dim)
    async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:  # 자기 컷 임시 비활성 (평가 순환 방지)
            await cur.execute(
                "update ref_images set is_active=false where id like %s and is_active", (f"cut-{pid}-%",))
            await conn.commit()
            deactivated = cur.rowcount
        try:
            hits = await repo.search_ref_images(
                conn, qv, cut_type="mannequin", embed_model=s.embed_image_model,
                clothing_type=clothing_type or None, gender=gender or None, k=topk)
            if not hits and clothing_type:
                hits = await repo.search_ref_images(
                    conn, qv, cut_type="mannequin", embed_model=s.embed_image_model,
                    gender=gender or None, k=topk)
        finally:
            async with conn.cursor() as cur:  # 복원
                await cur.execute(
                    "update ref_images set is_active=true where id like %s", (f"cut-{pid}-%",))
                await conn.commit()
    r2 = R2Client(s, bucket=s.r2_bucket)
    refs, ids = [], []
    for h in hits:
        data = r2.get_bytes(h["r2_key"])
        ext = h["r2_key"].rsplit(".", 1)[-1].lower() if "." in h["r2_key"] else "jpg"
        refs.append(InlineImage({"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/jpeg"), data))
        ids.append((h["id"], round(h["similarity"], 4)))
    print(f"  (자기 컷 {deactivated}개 임시 제외) 검색 결과: {ids}")
    return refs


async def _score(s, prod_imgs, out_bytes, mime):
    try:
        v = await image_qc.verdict(s, prod_imgs, InlineImage(mime, out_bytes))
        return v
    except Exception as e:
        return {"error": str(e)[:120]}


async def _gen_gpt_image(s, model, prompt, images, size="1024x1536"):
    """OpenAI Images edits (gpt-image-2) — base+상품(+ref) 를 image[] 로 넣어 image-to-image 합성.
    Gemini 쿼터 소진 시 대체 백엔드. OPENAI_API_KEY 필요(server/.env). 반환: PNG bytes."""
    if not s.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY 없음 (server/.env 에 추가 필요)")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    files = [("image[]", (f"img{i}.{ext.get(im.mime, 'png')}", im.data, im.mime))
             for i, im in enumerate(images)]
    data = {"model": model, "prompt": prompt[:32000], "size": size, "n": "1"}
    async with httpx.AsyncClient(timeout=240) as c:
        r = await c.post("https://api.openai.com/v1/images/edits",
                         headers={"Authorization": f"Bearer {s.openai_api_key}"},
                         data=data, files=files)
    if r.status_code != 200:
        raise RuntimeError(f"OpenAI {r.status_code}: {r.text[:400]}")
    return base64.b64decode(r.json()["data"][0]["b64_json"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="테스트 프로젝트 UUID")
    ap.add_argument("--topk", type=int, default=None)
    ap.add_argument("--out", default="ab_out/refeval")
    ap.add_argument("--backend", choices=["gemini", "gpt"], default="gemini",
                    help="이미지 생성 백엔드. gpt=OpenAI images/edits(gpt-image-2)")
    ap.add_argument("--gpt-model", default="gpt-image-2")
    ap.add_argument("--gpt-size", default="1024x1536", help="세로 마네킹용 2:3 근사")
    args = ap.parse_args()
    s = load_settings()
    if not s.gemini_api_key:
        print("GEMINI_API_KEY 없음 (server/.env)", file=sys.stderr)
        return 2
    url = s.database_url
    pid, topk = args.project, (args.topk or s.ref_images_topk)

    analysis, clothing_type, uploads = _fetch_inputs(url, pid)
    if not uploads:
        print("업로드 상품 이미지가 없습니다.", file=sys.stderr)
        return 2
    gender = mannequin.select_base_gender(analysis)
    base_id = s.base_mannequin_women_asset_id if gender == "women" else s.base_mannequin_men_asset_id
    r2 = R2Client(s, bucket=s.r2_bucket)
    with psycopg.connect(url, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute("select r2_key, mime_type from assets where id=%s", (base_id,))
        b = cur.fetchone()
    base_img = InlineImage(b["mime_type"] or "image/png", r2.get_bytes(b["r2_key"]))
    prod_imgs = [InlineImage(u["mime_type"] or "image/jpeg", r2.get_bytes(u["r2_key"])) for u in uploads]

    fit_profile = mannequin.effective_fit_profile(analysis, has_match_image=False)
    prod_assets = [{"slot": "Front"}] + [{"slot": "Detail"}] * (len(prod_imgs) - 1)
    manifest = _build_manifest(prod_assets, has_match=False)
    template = load_prompt_template(s)
    ctx = mannequin.prompt_context(
        clothing_type=clothing_type, product_count=len(prod_imgs), base_gender=gender,
        image_manifest=manifest, fit_profile=fit_profile)
    base_prompt = render_mannequin_prompt(
        template, ctx, {"name": "", "clothing_type": clothing_type}, analysis,
        seller_canon=s.seller_text_canonicalize, knowledge=s.retrieval_knowledge)
    gemini = GeminiImageClient(s)
    model = resolve_model(s, s.mannequin_tier)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[eval] project={pid[:8]} clothing={clothing_type} gender={gender} "
          f"prod_imgs={len(prod_imgs)} topk={topk} model={model}")

    async def _gen(prompt, images, tag):
        t0 = time.time()
        if args.backend == "gpt":
            out_bytes = await _gen_gpt_image(s, args.gpt_model, prompt, images, size=args.gpt_size)
            mime = "image/png"
        else:
            res = await gemini.generate_content_image(
                model, prompt, images, s.mannequin_image_size, aspect_ratio=s.mannequin_aspect_ratio)
            out_bytes, mime = res.image, res.mime
        ext = _EXT.get(mime, "png")
        path = out_dir / f"{pid[:8]}_{tag}.{ext}"
        path.write_bytes(out_bytes)
        sc = await _score(s, prod_imgs, out_bytes, mime)
        print(f"  [{tag}] {time.time()-t0:.1f}s → {path.name}  image_qc={sc}")
        return path

    # OFF: 레퍼런스 없음
    off_images = [base_img, *prod_imgs]
    await _gen(base_prompt, off_images, "OFF")

    # ON: 레퍼런스 첨부 (자기 컷 제외 검색) + 가드 + manifest 확장
    refs = await _search_refs(url, s, prod_imgs[0].data, clothing_type, gender, pid, topk)
    if not refs:
        print("  [ON] 코퍼스에서 레퍼런스 미검색 → ON 생략(코퍼스 부족).")
        return 0
    next_i = 2 + len(prod_assets)
    on_manifest = manifest + "\n" + _ref_manifest_lines(next_i, len(refs))
    on_ctx = mannequin.prompt_context(
        clothing_type=clothing_type, product_count=len(prod_imgs), base_gender=gender,
        image_manifest=on_manifest, fit_profile=fit_profile)
    on_prompt = render_mannequin_prompt(
        template, on_ctx, {"name": "", "clothing_type": clothing_type}, analysis,
        seller_canon=s.seller_text_canonicalize, knowledge=s.retrieval_knowledge) + "\n\n" + _STYLE_REF_GUARD
    on_images = [base_img, *prod_imgs, *refs]
    await _gen(on_prompt, on_images, "ON")
    print(f"[eval] 완료 — {out_dir}/ 에 OFF/ON 나란히 저장. 육안 비교 + image_qc① 대조.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
