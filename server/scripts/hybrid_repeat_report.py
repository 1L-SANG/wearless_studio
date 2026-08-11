"""Human-review report for a --repeat run. Two arms, N repeats, side by side.

Built because the LLM QC cannot be the judge here: `garmentBodyIntegration` has passed pairs
that a person can tell apart at a glance, so this page puts the pixels in front of a human and
leaves F1-F5 and P1-P7 as empty cells to fill in. Every QC verdict is shown, but shown as data,
never as the answer.

Crops are taken from identical relative boxes on every image, so a difference on the page is a
difference in the render and not in the cropping.

Experiment-only. Reads results.json, writes HTML.
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib
import statistics as st

import numpy as np
from PIL import Image

#: Relative crop boxes, one per thing a reviewer has to check.
REGIONS = {
    "neckline / collar": ((0.38, 0.155, 0.66, 0.27), 3),
    "shoulders + armholes": ((0.30, 0.17, 0.72, 0.33), 2),
    "chest / abdomen torso": ((0.30, 0.22, 0.72, 0.42), 2),
    "side edges": ((0.28, 0.20, 0.45, 0.48), 2),
    "ribbed hem + cuffs": ((0.30, 0.38, 0.72, 0.52), 2),
}

FLOATING = [
    ("F1", "neckline gap", "collar naturally rests around neck",
     "visible separation / background-like gap"),
    ("F2", "floating torso", "garment visually wraps around body",
     "garment appears like a separate panel in front of body"),
    ("F3", "ballooning", "fabric volume looks like cloth thickness/ease",
     "chest or abdomen unnaturally inflates away from mannequin"),
    ("F4", "shoulder contact", "fabric follows shoulder volume",
     "shoulder garment floats or forms an independent slab"),
    ("F5", "armhole continuity", "sleeve and torso connect naturally",
     "sleeve/torso appear disconnected"),
]
PRESERVATION = [("P1", "garment length"), ("P2", "sleeve length"),
                ("P3", "neckline dimensions"), ("P4", "ribbed hem height"),
                ("P5", "ribbed cuff length"), ("P6", "overall fit class"),
                ("P7", "garment width / looseness")]

#: Thresholds the hem row is measured at. Agreement between them IS the reliability check —
#: a hem that moves with the threshold is a hem this method cannot locate.
HEM_THRESHOLDS = (188, 195, 202)
HEM_TOLERANCE_PX = 6


def hem_row(path: pathlib.Path, thr: int, xlo=0.44, xhi=0.52) -> int | None:
    """Lowest row in the torso centre band still dark enough to be knit rather than mannequin."""
    im = np.asarray(Image.open(path).convert("L")).astype(int)
    H, W = im.shape
    band = im[:, int(W * xlo):int(W * xhi)]
    rows = np.where((band < thr).sum(1) > band.shape[1] * 0.6)[0]
    rows = rows[(rows > int(H * 0.18)) & (rows < int(H * 0.60))]
    return int(rows.max()) if len(rows) else None


def hem_measure(path: pathlib.Path) -> dict:
    vals = [hem_row(path, t) for t in HEM_THRESHOLDS]
    wide = hem_row(path, 195, 0.40, 0.56)
    got = [v for v in vals if v is not None] + ([wide] if wide is not None else [])
    if not got:
        return {"value": None, "spread": None, "reliable": False}
    spread = max(got) - min(got)
    return {"value": vals[1], "spread": spread, "reliable": spread <= HEM_TOLERANCE_PX,
            "perThreshold": dict(zip(map(str, HEM_THRESHOLDS), vals)), "wideBand": wide}


def uri(path: pathlib.Path, box=None, zoom=1, width=None) -> str:
    im = Image.open(path).convert("RGB")
    if box:
        W, H = im.size
        im = im.crop((int(W * box[0]), int(H * box[1]), int(W * box[2]), int(H * box[3])))
    if zoom != 1:
        im = im.resize((im.width * zoom, im.height * zoom), Image.LANCZOS)
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


CSS = """
:root{--bg:#fff;--fg:#111;--mut:#666;--line:#ddd;--warn:#b45309;--legacy:#7c3aed;--new:#0369a1}
*{box-sizing:border-box}body{margin:0;padding:24px;font:14px/1.6 -apple-system,BlinkMacSystemFont,
"Segoe UI",Roboto,sans-serif;color:var(--fg);background:var(--bg)}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px;padding-top:14px;
border-top:2px solid var(--line)}h3{font-size:14px;margin:18px 0 8px;color:var(--mut)}
.lede{color:var(--mut);max-width:70em;margin:0 0 8px}
.warn{background:#fef3c7;border-left:4px solid var(--warn);padding:10px 14px;margin:14px 0;
border-radius:4px}
table{border-collapse:collapse;width:100%;margin:10px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}
th{background:#f6f6f6;font-weight:600}
td.fill{background:#fffbe6;min-width:64px}
.legacy{color:var(--legacy);font-weight:600}.new{color:var(--new);font-weight:600}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:6px;margin:10px 0}
.grid{display:grid;grid-auto-flow:column;gap:0}
.cell{border-right:2px solid #e11}.cell:last-child{border-right:0}
.cell figcaption{font:11px monospace;padding:4px 6px;background:#fafafa;
border-bottom:1px solid var(--line)}
.cell img{display:block;max-width:none}
.full{display:flex;gap:8px;overflow-x:auto}
.full figure{margin:0}.full img{height:420px;display:block}
.full figcaption{font:11px monospace;padding:3px 0}
code{background:#f4f4f4;padding:1px 5px;border-radius:3px;font-size:12px}
"""


def region_strip(paths, labels, box, zoom) -> str:
    cells = "".join(
        f'<figure class="cell"><figcaption>{html.escape(l)}</figcaption>'
        f'<img src="{uri(p, box, zoom)}"></figure>'
        for p, l in zip(paths, labels))
    return f'<div class="scroll"><div class="grid">{cells}</div></div>'


def build(results_path: str, out: str, garment: str, arms: list[str]) -> None:
    data = json.loads(pathlib.Path(results_path).read_text(encoding="utf-8"))
    g = next(x for x in data["garments"] if x["garment_id"] == garment)
    cands = g["candidates"]

    runs = []
    for arm in arms:
        for name, c in cands.items():
            if c.get("branch") == arm and c.get("file"):
                runs.append((name, arm, c))
    runs.sort(key=lambda r: (arms.index(r[1]), r[2].get("repeatIndex") or 0))
    paths = [pathlib.Path(c["file"]) for _, _, c in runs]
    labels = [n for n, _, _ in runs]

    hems = {n: hem_measure(p) for (n, _, _), p in zip(runs, paths)}
    unreliable = [n for n, h in hems.items() if not h["reliable"]]

    # ── run table ────────────────────────────────────────────────────────────
    rows = ""
    for name, arm, c in runs:
        v = c.get("vision") or {}
        integ = ((v.get("checks") or {}).get("garmentBodyIntegration") or {}).get("status")
        dim = c.get("imageDimensions") or {}
        h = hems[name]
        cls = "legacy" if arm.startswith("legacy") else "new"
        rows += (f"<tr><td class='{cls}'>{html.escape(name)}</td>"
                 f"<td>{c.get('repeatIndex')}</td>"
                 f"<td><code>{html.escape((c.get('promptSha256') or '')[:12])}</code></td>"
                 f"<td><code>{html.escape(pathlib.Path(c['file']).name)}</code></td>"
                 f"<td>{c.get('latencyMs')}</td>"
                 f"<td>{dim.get('width')}x{dim.get('height')} {dim.get('format','')}</td>"
                 f"<td>{html.escape(str(v.get('outcome')))}</td>"
                 f"<td>{html.escape(str(integ))}</td>"
                 f"<td>{html.escape(json.dumps(v.get('failedChecks') or []))}</td>"
                 f"<td>{h['value'] if h['reliable'] else 'UNRELIABLE'}</td></tr>")

    # ── review tables (empty cells for a human) ──────────────────────────────
    def review(items, head):
        hdr = "".join(f"<th>{html.escape(n)}</th>" for n in labels)
        body = ""
        for code, *rest in items:
            desc = rest[0] if len(rest) == 1 else f"{rest[0]} — PASS: {rest[1]} / FAIL: {rest[2]}"
            body += (f"<tr><td><b>{code}</b></td><td>{html.escape(desc)}</td>"
                     + "".join("<td class='fill'></td>" for _ in labels) + "</tr>")
        return (f"<h3>{head}</h3><div class='scroll'><table><tr><th>#</th><th>criterion</th>"
                f"{hdr}</tr>{body}</table></div>")

    # ── hem distribution ─────────────────────────────────────────────────────
    dist = ""
    for arm in arms:
        vals = [hems[n]["value"] for n, a, _ in runs if a == arm and hems[n]["reliable"]]
        if not vals:
            dist += f"<tr><td>{html.escape(arm)}</td><td colspan='6'>UNRELIABLE</td></tr>"
            continue
        dist += (f"<tr><td>{html.escape(arm)}</td><td>{len(vals)}</td><td>{min(vals)}</td>"
                 f"<td>{max(vals)}</td><td>{max(vals)-min(vals)}</td>"
                 f"<td>{st.median(vals):.1f}</td>"
                 f"<td>{st.pstdev(vals):.1f}</td></tr>")

    strips = "".join(
        f"<h3>{html.escape(title)}</h3>" + region_strip(paths, labels, box, zoom)
        for title, (box, zoom) in REGIONS.items())

    full = "".join(f'<figure><figcaption>{html.escape(l)}</figcaption>'
                   f'<img src="{uri(p, width=420)}"></figure>'
                   for p, l in zip(paths, labels))

    wp = g.get("wordingProfile") or {}
    wrows = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{html.escape(str(wp.get(a, {}).get('contactBlock')))}</td>"
        f"<td>{html.escape(str(wp.get(a, {}).get('knitGuidance')))}</td>"
        f"<td>{html.escape(str(wp.get(a, {}).get('cutoutWording')))}</td></tr>" for a in arms)

    warn = ""
    if unreliable:
        warn = (f"<div class='warn'><b>hem measurement UNRELIABLE</b> for "
                f"{html.escape(', '.join(unreliable))} — the row moved more than "
                f"{HEM_TOLERANCE_PX}px between thresholds. Judge those from the crops.</div>")

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(garment)} — repeat review</title><style>{CSS}</style></head><body>
<h1>{html.escape(garment)} — repeatability review</h1>
<p class="lede">{len(runs)}개의 <b>독립</b> generation. 각 repeat은 별도 provider call이고 파일도
따로 남는다. <b>LLM QC는 판정자가 아니다</b> — 아래 QC 열은 데이터로만 보여주고, F1–F5 / P1–P7은
사람이 채우도록 비워 뒀다. 모든 crop은 동일한 상대 좌표에서 잘랐다.</p>
{warn}
<h2>Wording profile</h2>
<div class="scroll"><table><tr><th>arm</th><th>contact block</th><th>knit guidance</th>
<th>cutout wording</th></tr>{wrows}</table></div>
<p class="lede">materials: <code>{html.escape(json.dumps(g.get('materials'), ensure_ascii=False))}</code>
&nbsp; subCategory: <code>{html.escape(str(g.get('subCategory')))}</code>
&nbsp; source: <code>{html.escape(str(g.get('materialsSource')))}</code></p>

<h2>Runs</h2>
<div class="scroll"><table><tr><th>run</th><th>rep</th><th>prompt sha</th><th>file</th>
<th>latency ms</th><th>dimensions</th><th>QC outcome</th><th>garmentBodyIntegration</th>
<th>failed checks</th><th>hem Y</th></tr>{rows}</table></div>

<h2>Floating / body integration — human review</h2>
{review(FLOATING, "F1–F5 (fill in per image)")}

<h2>Product preservation — human review</h2>
{review(PRESERVATION, "P1–P7 (fill in per image)")}

<h2>Garment length distribution</h2>
<div class="scroll"><table><tr><th>arm</th><th>n</th><th>min</th><th>max</th><th>range</th>
<th>median</th><th>sd</th></tr>{dist}</table></div>

<h2>Matched crops</h2>
{strips}

<h2>Full frames</h2>
<div class="full">{full}</div>
</body></html>"""
    pathlib.Path(out).write_text(page, encoding="utf-8")
    print(pathlib.Path(out).resolve())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--garment", required=True)
    ap.add_argument("--arms", default="legacy_baseline,baseline")
    a = ap.parse_args()
    build(a.results, a.out, a.garment, [x.strip() for x in a.arms.split(",") if x.strip()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
