"""ab_analysis_thinking.py 결과 → 요약표 + 오너 눈검수용 HTML.

지표
- 지연: 중앙값·p90 (셀러가 업로드 후 기다리는 시간)
- 비용: usageMetadata 실측 토큰 × 공식 단가(ai.google.dev/gemini-api/docs/pricing, 2026-08-14 조회)
- 정확도(약한 라벨): clothingType = 폴더 카테고리, targetGenders ⊇ 폴더 '여성)/남성)' 접두
- 특징(aiSuggestedPoints): 개수·평균 길이·arm 간 중복도 — 품질 판정은 HTML 에서 오너가 한다
"""

import argparse
import collections
import html
import json
import pathlib
import statistics

# 출력 단가는 thinking 토큰 포함 (공식 문서 명시)
PRICE = {  # model → (input $/1M, output $/1M)
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}
ARM_ORDER = ["pro-low", "pro-high", "flash37-low", "flash37-medium", "flash37-high"]
ARM_LABEL = {
    "pro-low": "3.1 pro · low (현 prod)",
    "pro-high": "3.1 pro · high",
    "flash37-low": "3.7 flash · low",
    "flash37-medium": "3.7 flash · medium",
    "flash37-high": "3.7 flash · high",
}


def cost_of(rec: dict) -> float:
    u = rec.get("usage") or {}
    pi, po = PRICE[rec["model"]]
    out_tok = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return (u.get("promptTokenCount", 0) * pi + out_tok * po) / 1e6


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--html", default=None)
    args = ap.parse_args()

    rows = [json.loads(line) for line in pathlib.Path(args.src).read_text(encoding="utf-8").splitlines() if line.strip()]
    ok = [r for r in rows if r.get("ok")]
    by_arm = collections.defaultdict(list)
    for r in ok:
        by_arm[r["arm"]].append(r)

    print(f"표본 {len(rows)} 콜 (성공 {len(ok)}, 실패 {len(rows) - len(ok)})\n")
    hdr = (f"{'arm':26} {'n':>3} {'지연중앙':>8} {'p90':>7} {'thinkTok':>9} "
           f"{'$/1회':>9} {'종류정확':>8} {'성별정확':>8} {'특징수':>6} {'특징글자':>8}")
    print(hdr)
    print("-" * len(hdr))
    summary = {}
    for arm in ARM_ORDER:
        recs = by_arm.get(arm) or []
        if not recs:
            continue
        lat = sorted(r["latencyS"] for r in recs)
        p90 = lat[min(len(lat) - 1, int(round(0.9 * (len(lat) - 1))))]
        think = statistics.mean((r.get("usage") or {}).get("thoughtsTokenCount", 0) for r in recs)
        cost = statistics.mean(cost_of(r) for r in recs)
        type_ok = sum(1 for r in recs if r["out"]["product"]["clothingType"] == r["expectedType"])
        g_recs = [r for r in recs if r["expectedGender"]]
        gender_ok = sum(1 for r in g_recs if r["expectedGender"] in (r["out"]["analysis"]["targetGenders"] or []))
        pts = [p for r in recs for p in (r["out"]["analysis"]["aiSuggestedPoints"] or [])]
        summary[arm] = {
            "n": len(recs), "median": statistics.median(lat), "p90": p90, "think": think,
            "cost": cost, "typeOk": type_ok, "genderOk": gender_ok, "genderN": len(g_recs),
            "points": len(pts) / len(recs), "chars": statistics.mean(len(p) for p in pts) if pts else 0,
        }
        s = summary[arm]
        print(f"{ARM_LABEL[arm]:26} {s['n']:>3} {s['median']:>7.2f}s {s['p90']:>6.1f}s "
              f"{s['think']:>9.0f} {s['cost']:>9.5f} {pct(type_ok, len(recs)):>8} "
              f"{pct(gender_ok, len(g_recs)):>8} {s['points']:>6.2f} {s['chars']:>8.1f}")

    base = summary.get("pro-low")
    if base:
        print("\n현 prod(3.1 pro·low) 대비")
        for arm in ARM_ORDER:
            if arm == "pro-low" or arm not in summary:
                continue
            s = summary[arm]
            print(f"  {ARM_LABEL[arm]:26} 비용 ×{s['cost'] / base['cost']:.2f}  "
                  f"지연 {s['median'] - base['median']:+.2f}s")

    if args.html:
        _write_html(ok, summary, pathlib.Path(args.html))
        print(f"\nHTML: {args.html}")


def _write_html(ok: list[dict], summary: dict, out: pathlib.Path) -> None:
    by_item = collections.defaultdict(dict)
    for r in ok:
        by_item[r["item"]][r["arm"]] = r
    arms = [a for a in ARM_ORDER if a in summary]

    def cell(r: dict | None) -> str:
        if not r:
            return "<td class='miss'>—</td>"
        a, p = r["out"]["analysis"], r["out"]["product"]
        pts = "".join(f"<li>{html.escape(x)}</li>" for x in (a["aiSuggestedPoints"] or []))
        tags = " ".join(a and r["out"]["intermediate"]["styleTags"] or [])
        okmark = "ok" if p["clothingType"] == r["expectedType"] else "bad"
        return (f"<td><div class='meta'><b class='{okmark}'>{p['clothingType']}"
                f"{'/' + a['subCategory'] if a['subCategory'] else ''}</b> · {a['fit']} · "
                f"{'/'.join(a['targetGenders'] or []) or '-'} · {r['latencyS']:.1f}s</div>"
                f"<div class='name'>{html.escape(a['suggestedName'] or '')}</div>"
                f"<ul>{pts}</ul><div class='tags'>{html.escape(tags)}</div></td>")

    rows = "".join(
        f"<tr><th>{html.escape(item)}</th>" + "".join(cell(per.get(a)) for a in arms) + "</tr>"
        for item, per in sorted(by_item.items()))
    head = "".join(f"<th>{html.escape(ARM_LABEL[a])}<br><small>${summary[a]['cost']:.4f} · "
                   f"{summary[a]['median']:.1f}s</small></th>" for a in arms)
    out.write_text(f"""<!doctype html><meta charset=utf-8>
<title>AG-01 분석 모델·thinking A/B (2026-08-14)</title>
<style>
 body{{font:14px/1.55 -apple-system,'Apple SD Gothic Neo',sans-serif;margin:24px;color:#111}}
 h1{{font-size:20px}} table{{border-collapse:collapse;width:100%}}
 th,td{{border:1px solid #ddd;padding:8px;vertical-align:top;text-align:left}}
 thead th{{background:#f6f6f6;position:sticky;top:0}}
 tbody th{{width:150px;background:#fafafa;font-weight:600;font-size:13px}}
 .meta{{font-size:12px;color:#666;margin-bottom:4px}} .ok{{color:#0a7}} .bad{{color:#c00}}
 .name{{font-weight:600;margin-bottom:4px}} ul{{margin:0;padding-left:16px}} li{{margin:2px 0}}
 .tags{{font-size:11px;color:#999;margin-top:4px}} .miss{{color:#bbb;text-align:center}}
 p.note{{color:#555;background:#f9f9f9;padding:10px 12px;border-left:3px solid #ccc}}
</style>
<h1>AG-01 상품분석 — 모델 · 깊이생각(thinking) A/B</h1>
<p class=note>같은 사진·같은 프롬프트·같은 출력스키마. 모델과 thinking 수준만 다름.
비용은 실측 토큰 × 공식 단가(2026-08-14). <b>보실 것: 각 칸의 목록(=셀러에게 보여줄 상품 특징)이
오른쪽으로 갈수록 더 구체적인가.</b> 빨간 글씨는 옷 종류를 틀린 것.</p>
<table><thead><tr><th>상품(폴더명=정답 힌트)</th>{head}</tr></thead><tbody>{rows}</tbody></table>
""", encoding="utf-8")


if __name__ == "__main__":
    main()
