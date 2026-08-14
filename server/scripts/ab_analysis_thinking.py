"""AG-01 상품분석 모델·thinking A/B — 지연·토큰(=비용)·출력 품질 실측.

배경: prod 는 `MODEL_ROUTING_TEXT_GEMINI=gemini-3.1-pro-preview` + thinkingLevel=low 로 돈다
(2026-07-07 manifest, 404 핫픽스의 잔재). 오너 질문(2026-08-14):
  ① thinking 을 켜면 특징(aiSuggestedPoints)을 더 잘 잡나, 그때 얼마나 느려지나
  ② 분석 1회당 비용이 모델별로 얼마나 차이나나

프로덕션 경로를 그대로 재현한다 — 같은 프롬프트(build_prompt), 같은 responseSchema,
같은 이미지 축소(analyze_job.shrink_for_vision, 최장변 1024 q82), 같은 요청 바디.
다른 것은 model 과 thinkingLevel 뿐이다. usageMetadata 를 받아 토큰 실측으로 비용을 낸다.

사용:
    .venv/bin/python -m scripts.ab_analysis_thinking --items 3 --out ../documents/research/xxx.jsonl

표본: reference/upload_examples/<카테고리>/<상품폴더>/*.jpeg (노션 의류 페이지 미러).
폴더명이 약한 정답 라벨을 준다 — 카테고리(상의/하의/아우터/원피스), '여성)'·'남성)' 접두.
"""

import argparse
import asyncio
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scripts import _env  # noqa: E402

_env.load_env()

import httpx  # noqa: E402

from app.agents.product_analyst import analysis_schema, build_prompt, distribute, validate  # noqa: E402
from app.agents.vision_llm import _to_gemini_schema  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.workers.analyze_job import shrink_for_vision  # noqa: E402

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "reference" / "upload_examples"

# 폴더 카테고리 → 계약 clothingType (common_data_contract §4)
_CATEGORY_TYPE = {"상의": "top", "하의": "bottom", "아우터": "outer", "원피스": "dress"}

# (arm 이름, 모델, thinkingLevel). low = 현 prod 설정.
ARMS = [
    ("pro-low", "gemini-3.1-pro-preview", "low"),      # 현 prod
    ("pro-high", "gemini-3.1-pro-preview", "high"),
    ("flash37-low", "gemini-3.7-flash", "low"),
    ("flash37-medium", "gemini-3.7-flash", "medium"),
    ("flash37-high", "gemini-3.7-flash", "high"),
]


def _eligible(cat: str) -> list[dict]:
    """카테고리 안의 후보 상품(사진 2장 이상). 정렬 고정 = 재현 가능."""
    out = []
    for folder in sorted((_EXAMPLES / cat).iterdir()):
        if not folder.is_dir():
            continue
        photos = sorted(p for p in folder.iterdir()
                        if p.suffix.lower() in (".jpeg", ".jpg", ".png"))
        if len(photos) < 2:
            continue
        name = folder.name
        gender = "women" if name.startswith("여성)") else "men" if name.startswith("남성)") else None
        out.append({
            "id": f"{cat}/{name}",
            "expectedType": _CATEGORY_TYPE[cat],
            "expectedGender": gender,
            "photos": [str(p) for p in photos[:4]],  # prod 는 기준색 슬롯 최대 4장
        })
    return out


def _items(per_category: int, limit: int | None, exclude: set[str]) -> list[dict]:
    """--limit 이 있으면 카테고리 라운드로빈으로 총 N개(표본 균형), 없으면 카테고리당 N개.
    exclude 는 이미 돌린 상품 id — 이어붙일 때 중복 호출(=돈)을 막는다."""
    pools = {cat: [it for it in _eligible(cat) if it["id"] not in exclude]
             for cat in sorted(_CATEGORY_TYPE)}
    if limit is None:
        return [it for cat in pools for it in pools[cat][:per_category]]
    out: list[dict] = []
    while len(out) < limit and any(pools.values()):
        for cat in sorted(pools):
            if pools[cat] and len(out) < limit:
                out.append(pools[cat].pop(0))
    return out


def _load_images(paths: list[str]) -> list[dict]:
    """prod 와 동일하게 축소 → Gemini inline_data 파트."""
    import base64
    parts = []
    for p in paths:
        raw = pathlib.Path(p).read_bytes()
        data, mime = shrink_for_vision(raw, "image/jpeg")
        parts.append({"inline_data": {"mime_type": mime,
                                      "data": base64.b64encode(data).decode()}})
    return parts


async def _call(key: str, model: str, level: str, prompt: str,
                image_parts: list[dict], schema: dict, timeout: float) -> dict:
    gen = {"responseMimeType": "application/json", "responseSchema": _to_gemini_schema(schema)}
    if level != "off":
        gen["thinkingConfig"] = {"thinkingLevel": level}
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}, *image_parts]}],
            "generationConfig": gen}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            res = await client.post(_URL.format(model=model), json=body,
                                    headers={"x-goog-api-key": key})
    except (httpx.HTTPError, OSError) as e:
        # 한 콜의 네트워크 오류로 유료 런 전체(130콜·16분)를 잃지 않는다 — 행으로 남기고 계속.
        return {"ok": False, "latencyS": time.monotonic() - t0,
                "error": f"{type(e).__name__}: {str(e)[:200]}"}
    elapsed = time.monotonic() - t0
    if res.status_code != 200:
        return {"ok": False, "latencyS": elapsed, "error": f"{res.status_code}: {res.text[:300]}"}
    data = res.json()
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
    usage = data.get("usageMetadata") or {}
    try:
        raw = json.loads(text)
    except Exception as e:
        return {"ok": False, "latencyS": elapsed, "usage": usage, "error": f"json: {e}"}
    return {"ok": True, "latencyS": elapsed, "usage": usage, "raw": raw}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=3, help="카테고리당 상품 수 (--limit 없을 때)")
    ap.add_argument("--limit", type=int, default=None, help="총 상품 수 — 카테고리 라운드로빈")
    ap.add_argument("--exclude", default=None, help="이미 돌린 결과 jsonl — 그 상품들은 건너뛴다")
    ap.add_argument("--arms", default=None, help="쉼표 구분 arm 이름 — 미지정이면 전부")
    ap.add_argument("--only", default=None, help="이 jsonl 에 있는 상품만 (프롬프트 수정 전후 비교용)")
    ap.add_argument("--reps", type=int, default=1, help="같은 (상품, arm) 반복 — 지연 분산 확인용")
    ap.add_argument("--out", required=True)
    ap.add_argument("--append", action="store_true", help="--out 에 이어쓰기")
    args = ap.parse_args()

    settings = load_settings()
    key = settings.gemini_api_key
    if not key:
        raise SystemExit("GEMINI_API_KEY 미설정 (server/.env)")

    out_path = pathlib.Path(args.out)
    # ① `--exclude runs.jsonl --out runs.jsonl` (=이어달리기)는 done-set 을 읽은 **뒤** "w" 로
    #    열려 이미 결제한 행을 통째로 날린다. 실제로 재현됨 — 같은 파일이면 append 를 요구한다.
    for other, flag in ((args.exclude, "--exclude"), (args.only, "--only")):
        if other and pathlib.Path(other).resolve() == out_path.resolve() and not args.append:
            raise SystemExit(
                f"{flag} 와 --out 이 같은 파일입니다. --append 없이 열면 이미 결제한 결과가 "
                f"지워집니다. --append 를 붙이거나 --out 을 다른 파일로 주세요.")
    if out_path.exists() and out_path.stat().st_size and not args.append:
        raise SystemExit(
            f"{out_path} 에 이미 결과가 있습니다. 덮어쓰려면 파일을 먼저 지우고, "
            f"이어붙이려면 --append 를 주세요.")
    # ② --only 는 "이 상품들을 다시 돌린다"(프롬프트 수정 전후 비교), --exclude 는 "이미 돌린 건
    #    건너뛴다" — 의도가 정면으로 충돌한다. 같이 주면 --only 가 --exclude 를 조용히 무시하고
    #    같은 상품을 두 번 결제한다. 재현됨 → 함께 못 쓰게 막는다.
    if args.only and args.exclude:
        raise SystemExit("--only 와 --exclude 는 함께 쓸 수 없습니다 (의도가 충돌 — 중복 결제).")

    done: set[str] = set()
    if args.exclude:
        for line in pathlib.Path(args.exclude).read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["item"])
    items = _items(args.items, args.limit, done)
    if args.only:
        keep = {json.loads(line)["item"]
                for line in pathlib.Path(args.only).read_text(encoding="utf-8").splitlines()
                if line.strip()}
        items = [it for cat in sorted(_CATEGORY_TYPE) for it in _eligible(cat)
                 if it["id"] in keep]
    arms = [a for a in ARMS if not args.arms or a[0] in args.arms.split(",")]
    prompt = build_prompt({})
    schema = analysis_schema()
    out = out_path
    out.parent.mkdir(parents=True, exist_ok=True)

    if not items or not arms:
        raise SystemExit(
            f"실행할 게 없습니다 (상품 {len(items)}개 · arm {len(arms)}개). "
            f"--arms 이름 오타나 --only/--exclude 조합을 확인하세요 — 출력 파일은 건드리지 않았습니다.")

    print(f"표본 {len(items)}개 × arm {len(arms)} × rep {args.reps} "
          f"= {len(items) * len(arms) * args.reps} 콜", flush=True)

    with out.open("a" if args.append else "w", encoding="utf-8") as f:
        for item in items:
            parts = _load_images(item["photos"])
            for arm, model, level in arms:
                for rep in range(args.reps):
                    r = await _call(key, model, level, prompt, parts, schema,
                                    settings.analysis_timeout_seconds)
                    rec = {"item": item["id"], "photos": len(item["photos"]),
                           "expectedType": item["expectedType"],
                           "expectedGender": item["expectedGender"],
                           "arm": arm, "model": model, "thinking": level, "rep": rep,
                           **{k: v for k, v in r.items() if k != "raw"}}
                    if r.get("ok"):
                        # prod 는 validate/distribute 를 거친 결과만 저장한다 — 같게 본다.
                        rec["out"] = distribute(validate(r["raw"]))
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    print(f"  {item['id'][:34]:36} {arm:16} "
                          f"{r['latencyS']:6.2f}s {'ok' if r.get('ok') else r.get('error', '')[:60]}",
                          flush=True)
    print(f"\n저장: {out}")


if __name__ == "__main__":
    asyncio.run(main())
