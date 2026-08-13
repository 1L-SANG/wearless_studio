"""재태깅 제안 검수용 정적 HTML을 생성한다.

    cd server
    .venv/bin/python scripts/retag_review_html.py

입력 60건의 id가 정확히 대응할 때만 ``../mockups/matching-retag-review.html``을 쓴다.
생성물의 외부 요청은 items.json에 든 공개 CDN 썸네일뿐이다.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections import Counter
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = SERVER_DIR.parent
ITEMS_PATH = SERVER_DIR / "scripts" / "retag" / "items.json"
PROPOSALS_PATH = SERVER_DIR / "scripts" / "retag" / "proposals.json"
OUTPUT_PATH = REPO_DIR / "mockups" / "matching-retag-review.html"

sys.path.insert(0, str(SERVER_DIR))

from app.agents.style_tags import STYLE_TAG_SET  # noqa: E402

_GENDER_LABEL = {"women": "여성", "men": "남성", "unisex": "공용"}
_TYPE_LABEL = {"top": "상의", "bottom": "하의", "outer": "아우터", "dress": "원피스"}
_GENDER_ORDER = {"women": 0, "men": 1, "unisex": 2}
_TYPE_ORDER = {"top": 0, "bottom": 1, "outer": 2, "dress": 3}


def _load_list(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"파일이 없습니다: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 형식 오류: {path}: {exc}") from exc
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise ValueError(f"객체 배열이어야 합니다: {path}")
    return data


def _index(rows: list[dict], label: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in rows:
        item_id = row.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"{label}에 유효하지 않은 id가 있습니다.")
        if item_id in result:
            raise ValueError(f"{label}에 중복 id가 있습니다: {item_id}")
        result[item_id] = row
    return result


def _validate(items: list[dict], proposals: list[dict]) -> dict[str, dict]:
    items_by_id = _index(items, "items.json")
    proposals_by_id = _index(proposals, "proposals.json")
    missing = set(items_by_id) - set(proposals_by_id)
    extra = set(proposals_by_id) - set(items_by_id)
    if missing or extra:
        raise ValueError(
            f"id 불일치 — 제안 누락 {sorted(missing)}, 대상 밖 제안 {sorted(extra)}"
        )
    for item_id, proposal in proposals_by_id.items():
        tags = proposal.get("proposed_tags")
        if (
            not isinstance(tags, list)
            or not 2 <= len(tags) <= 4
            or len(tags) != len(set(tags))
            or not all(isinstance(tag, str) and tag in STYLE_TAG_SET for tag in tags)
        ):
            raise ValueError(f"{item_id}: 유효하지 않은 proposed_tags: {tags!r}")
        reason = proposal.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{item_id}: reason이 비어 있습니다.")
    return proposals_by_id


def _chip(tag: object, css_class: str) -> str:
    return f'<span class="chip {css_class}">{html.escape(str(tag))}</span>'


def _histogram(items: list[dict], proposals_by_id: dict[str, dict]) -> str:
    before = Counter(tag for item in items for tag in (item.get("current_tags") or []))
    after = Counter(
        tag
        for item in items
        for tag in proposals_by_id[item["id"]]["proposed_tags"]
    )
    tags = sorted(set(before) | set(after), key=lambda tag: (-max(before[tag], after[tag]), tag))
    maximum = max([*before.values(), *after.values(), 1])
    rows = []
    for tag in tags:
        safe_tag = html.escape(str(tag))
        before_width = before[tag] / maximum * 100
        after_width = after[tag] / maximum * 100
        rows.append(
            f"""<div class="hist-row">
              <code>{safe_tag}</code>
              <div class="bar-track"><span class="bar before" style="width:{before_width:.2f}%"></span></div>
              <b>{before[tag]}</b>
              <div class="bar-track"><span class="bar after" style="width:{after_width:.2f}%"></span></div>
              <b>{after[tag]}</b>
            </div>"""
        )
    return "\n".join(rows)


def _card(item: dict, proposal: dict) -> str:
    current = "".join(_chip(tag, "old") for tag in (item.get("current_tags") or []))
    proposed = "".join(_chip(tag, "new") for tag in proposal["proposed_tags"])
    name = html.escape(str(item.get("name") or "이름 없음"))
    category = html.escape(str(item.get("category") or "미상"))
    color = html.escape(str(item.get("color_name") or "미상"))
    fit = html.escape(str(item.get("fit") or "미상"))
    item_id = html.escape(str(item["id"]))
    image_url = html.escape(str(item.get("thumb_url") or ""), quote=True)
    reason = html.escape(" ".join(str(proposal.get("reason") or "").split()))
    return f"""<article class="card">
      <div class="image-wrap"><img src="{image_url}" alt="{name}" loading="lazy" decoding="async"></div>
      <div class="card-body">
        <p class="item-id">{item_id}</p>
        <h3>{name}</h3>
        <p class="meta">{category} · {color} · {fit}</p>
        <div class="tag-change">
          <div><span class="tag-label">현재</span><span class="chips">{current}</span></div>
          <span class="arrow" aria-hidden="true">→</span>
          <div><span class="tag-label">제안</span><span class="chips">{proposed}</span></div>
        </div>
        <p class="reason">{reason}</p>
      </div>
    </article>"""


def render_html(items: list[dict], proposals: list[dict]) -> str:
    proposals_by_id = _validate(items, proposals)
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        key = (str(item.get("gender") or "unknown"), str(item.get("clothing_type") or "unknown"))
        groups.setdefault(key, []).append(item)

    sections = []
    ordered_groups = sorted(
        groups.items(),
        key=lambda pair: (
            _GENDER_ORDER.get(pair[0][0], 99),
            _TYPE_ORDER.get(pair[0][1], 99),
            pair[0],
        ),
    )
    for (gender, clothing_type), group_items in ordered_groups:
        label = f"{_GENDER_LABEL.get(gender, gender)} · {_TYPE_LABEL.get(clothing_type, clothing_type)}"
        cards = "\n".join(_card(item, proposals_by_id[item["id"]]) for item in group_items)
        sections.append(
            f"""<section class="review-section">
      <div class="section-heading"><h2>{html.escape(label)}</h2><span>{len(group_items)}벌</span></div>
      <div class="cards">{cards}</div>
    </section>"""
        )

    histogram = _histogram(items, proposals_by_id)
    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>매칭 의류 AI 재태깅 검수</title>
  <style>
    :root {{ color-scheme: light; --ink:#171717; --muted:#737373; --line:#e5e5e5; --accent:#5b35d5; --accent-soft:#eee9ff; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#f5f5f3; color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ width:min(1480px, calc(100% - 40px)); margin:0 auto; padding:52px 0 88px; }}
    header {{ display:flex; justify-content:space-between; gap:32px; align-items:end; margin-bottom:28px; }}
    h1 {{ margin:0 0 8px; font-size:clamp(28px,4vw,48px); letter-spacing:-.04em; }}
    header p {{ margin:0; color:var(--muted); }}
    .total {{ flex:none; font-size:15px; font-weight:700; background:#fff; border:1px solid var(--line); border-radius:999px; padding:10px 16px; }}
    .summary {{ background:#fff; border:1px solid var(--line); border-radius:18px; padding:24px; margin-bottom:44px; }}
    .summary h2 {{ margin:0 0 6px; font-size:20px; }}
    .legend {{ color:var(--muted); font-size:13px; margin:0 0 18px; }}
    .legend .before-key::before,.legend .after-key::before {{ content:""; display:inline-block; width:9px; height:9px; border-radius:2px; margin:0 5px 0 12px; }}
    .legend .before-key::before {{ background:#b8b8b8; margin-left:0; }} .legend .after-key::before {{ background:var(--accent); }}
    .histogram {{ display:grid; gap:8px; }}
    .hist-row {{ display:grid; grid-template-columns:110px minmax(80px,1fr) 28px minmax(80px,1fr) 28px; gap:9px; align-items:center; font-size:12px; }}
    .hist-row code {{ overflow:hidden; text-overflow:ellipsis; font-size:12px; }}
    .bar-track {{ height:10px; background:#f1f1ef; border-radius:999px; overflow:hidden; }}
    .bar {{ display:block; height:100%; min-width:0; border-radius:999px; }} .bar.before {{ background:#b8b8b8; }} .bar.after {{ background:var(--accent); }}
    .section-heading {{ display:flex; align-items:baseline; gap:10px; margin:40px 0 15px; border-bottom:1px solid #d8d8d5; padding-bottom:10px; }}
    .section-heading h2 {{ margin:0; font-size:23px; letter-spacing:-.02em; }} .section-heading span {{ color:var(--muted); font-size:13px; }}
    .cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:16px; }}
    .card {{ overflow:hidden; background:#fff; border:1px solid var(--line); border-radius:16px; box-shadow:0 2px 12px rgba(0,0,0,.025); }}
    .image-wrap {{ aspect-ratio:4/5; background:#ececea; display:grid; place-items:center; overflow:hidden; }}
    .image-wrap img {{ width:100%; height:100%; object-fit:cover; }}
    .card-body {{ padding:17px; }} .item-id {{ margin:0 0 5px; color:#999; font:11px ui-monospace,SFMono-Regular,monospace; }}
    h3 {{ margin:0; font-size:17px; line-height:1.35; letter-spacing:-.01em; }} .meta {{ margin:6px 0 15px; color:var(--muted); font-size:13px; }}
    .tag-change {{ border-top:1px solid #eee; border-bottom:1px solid #eee; padding:13px 0; display:grid; gap:7px; }}
    .tag-change > div {{ display:grid; grid-template-columns:36px 1fr; gap:6px; align-items:start; }} .tag-label {{ color:#888; font-size:11px; padding-top:4px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:5px; }} .chip {{ display:inline-block; padding:4px 8px; border-radius:999px; font:600 11px ui-monospace,SFMono-Regular,monospace; }}
    .chip.old {{ color:#8c8c8c; background:#eee; text-decoration:line-through; text-decoration-thickness:1px; }}
    .chip.new {{ color:#4622b5; background:var(--accent-soft); border:1px solid #d9ceff; }}
    .arrow {{ color:#aaa; font-size:13px; padding-left:9px; line-height:1; }} .reason {{ margin:13px 0 0; font-size:13px; line-height:1.55; color:#3f3f3f; }}
    @media (max-width:680px) {{ main {{ width:min(100% - 24px,1480px); padding-top:28px; }} header {{ align-items:start; flex-direction:column; gap:14px; }} .summary {{ padding:17px; overflow-x:auto; }} .histogram {{ min-width:550px; }} }}
  </style>
</head>
<body>
  <main>
    <header><div><h1>매칭 의류 AI 재태깅 검수</h1><p>현재 태그와 제안 태그를 이미지·메타데이터 근거와 함께 비교합니다.</p></div><div class="total">총 {len(items)}벌</div></header>
    <section class="summary"><h2>태그 분포 Before / After</h2><p class="legend"><span class="before-key">현재</span><span class="after-key">제안</span> · 막대 기준 최대 빈도</p><div class="histogram">{histogram}</div></section>
    {''.join(sections)}
  </main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=Path, default=ITEMS_PATH)
    parser.add_argument("--proposals", type=Path, default=PROPOSALS_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    try:
        rendered = render_html(_load_list(args.items), _load_list(args.proposals))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print(f"생성: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
