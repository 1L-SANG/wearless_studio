"""A/B report — throwaway, one page, built for looking at.

No score is computed anywhere. The verdicts are a person's, written into VERDICTS below after
looking at the images; the page shows them next to the evidence so they can be disagreed with.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib

from PIL import Image

BIG = 900
SMALL = 340


def uri(path, w):
    with Image.open(path) as im:
        im = im.convert("RGB")
        if im.width > w:
            im = im.resize((w, max(1, int(im.height * w / im.width))), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def dim(path):
    with Image.open(path) as im:
        return f"{im.width}x{im.height}"


def fig(path, cap, w=BIG):
    if not path or not pathlib.Path(path).exists():
        return f"<figure class='missing'><div class='none'>—</div><figcaption>{html.escape(cap)}</figcaption></figure>"
    return (f"<figure><a href='file://{html.escape(str(path))}' target='_blank'>"
            f"<img src='{uri(path, w)}' loading='lazy'></a><figcaption>{html.escape(cap)} · "
            f"<span class='muted'>{dim(path)}</span> · "
            f"<a href='file://{html.escape(str(path))}' target='_blank'>full res</a></figcaption></figure>")


#: Written by a person after looking at the six images. Not model output.
VERDICTS = {
    "stripe": ("AUGMENTED BETTER",
               "B restores the source's alternating TAUPE + BLUE stripe at visible contrast; "
               "A washes both colours toward a single pale beige and reads nearly plain. "
               "Collar, placket and buttons are equivalent in both. B's body is slightly "
               "longer and looser than A's, which is marginally further from the cropped "
               "source silhouette — the pattern gain is much larger than that loss."),
    "check": ("AUGMENTED BETTER",
              "B shows the puckered / crinkled surface relief across the front, which is the "
              "source's defining material and which A renders flat and smooth. B's grid is "
              "also slightly finer, closer to the source's fine windowpane. Collar, placket "
              "and length are equivalent."),
    "4ff2132f": ("MIXED",
                 "B renders the pointelle/rib surface far better — A's body is plain. But B "
                 "regresses on the two properties that decide product identity: the source's "
                 "ROUND SCOOP neckline becomes a SQUARE neck with a hard folded corner, and "
                 "the dusty-mauve base colour shifts to a saturated rust/orange. A keeps both "
                 "correct. Surface texture improved; neckline and colour got worse."),
}

CHECKLIST = ["silhouette / length", "neckline / collar", "sleeve construction",
             "buttons / pockets / placket", "stripe / check / pattern",
             "material / puckering / texture", "base colour",
             "overall same-product impression"]

ZOOMS = [("neckline", "neckline / collar"), ("torso", "torso · pattern · placket"),
         ("hem", "hem / silhouette")]


def product_block(p: dict, root: pathlib.Path) -> str:
    verdict, note = VERDICTS.get(p["key"], ("MIXED", ""))
    cls = {"AUGMENTED BETTER": "aug", "RAW BETTER": "raw", "SAME": "same"}.get(verdict, "mixed")
    raws = "".join(fig(f, f"SOURCE {s}", SMALL)
                   for f, s in zip(p["rawFiles"], p["rawSlots"]))
    zooms = ""
    for name, cap in ZOOMS:
        fp = root / p["key"] / f"zoom_{name}.jpg"
        if fp.exists():
            zooms += fig(str(fp), f"ZOOM · {cap} — LEFT = A raw, RIGHT = B augmented", 1100)
    rows = "".join(f"<tr><td>{html.escape(c)}</td><td></td><td></td></tr>" for c in CHECKLIST)
    a, b = p["arms"]["A"], p["arms"]["B"]
    return f"""
    <section class="product">
      <h2>{html.escape(p['label'])} <span class="verdict {cls}">{html.escape(verdict)}</span></h2>
      <p class="note">{html.escape(note)}</p>
      <p class="meta">model {html.escape(p['generationModel'])} · {html.escape(p['imageSize'])}
        {html.escape(p['aspectRatio'])} · prompt {html.escape(p['promptVersion'])} ·
        truth {html.escape(p['truthOrigin'])} · garment RGB modified in the canonical:
        <b>{p['garmentRgbModified']}</b> · 2 image calls (1 per arm)</p>

      <h3>Source truth</h3>
      <div class="row">{raws}</div>

      <h3>Canonical Front <span class="tag">HUMAN_SELECTED_EXPERIMENTAL_MASK</span></h3>
      <p class="meta">{html.escape(p['canonicalFile'])} — SAM2 candidate chosen by a person,
        background removed, original garment pixels only. Not production automation.</p>
      {fig(p['canonicalFile'], 'CANONICAL FRONT (source pixels, background removed)', 620)}

      <h3>A — raw control vs B — augmented input</h3>
      <div class="row">
        <div class="arm">
          <div class="armhead raw">A · RAW CONTROL</div>
          {fig(a.get('file'), 'A generated')}
          <p class="meta">inputs: {html.escape(', '.join(a.get('inputs') or []))}<br>
            prompt sha {html.escape((a.get('promptSha256') or '')[:16])} ·
            {a.get('latencyMs')} ms</p>
        </div>
        <div class="arm">
          <div class="armhead aug">B · AUGMENTED INPUT</div>
          {fig(b.get('file'), 'B generated')}
          <p class="meta">inputs: {html.escape(', '.join(b.get('inputs') or []))}<br>
            prompt sha {html.escape((b.get('promptSha256') or '')[:16])} ·
            {b.get('latencyMs')} ms</p>
        </div>
      </div>

      <h3>Zoom comparison <span class="muted">(presentation only — no scoring)</span></h3>
      {zooms}

      <h3>Human comparison checklist</h3>
      <table class="check"><thead><tr><th>attribute</th><th>A raw control</th>
        <th>B augmented</th></tr></thead><tbody>{rows}</tbody></table>
    </section>"""


CSS = """
:root{--ok:#1a7f37;--warn:#b45309;--bad:#c1121f;--diag:#4338ca;--line:#e5e7eb;}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#fafafa;color:#111;
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
 "Apple SD Gothic Neo",sans-serif}
h1{font-size:26px;margin:0 0 4px}h2{font-size:21px;margin:0 0 8px}
h3{font-size:14px;margin:22px 0 8px;text-transform:uppercase;letter-spacing:.05em;color:#374151}
.lede{color:#374151;max-width:80ch;margin:0 0 22px}
section.product{background:#fff;border:1px solid var(--line);border-radius:10px;padding:22px;
 margin:0 0 26px}
.meta,.muted{color:#6b7280;font-size:12.5px;font-weight:400}
.note{color:#374151;font-size:14px;margin:0 0 10px;max-width:90ch}
.verdict{font-size:13px;padding:3px 12px;border-radius:99px;color:#fff;vertical-align:middle}
.verdict.aug{background:var(--ok)}.verdict.raw{background:var(--bad)}
.verdict.mixed{background:var(--warn)}.verdict.same{background:#6b7280}
.tag{font-size:11px;padding:2px 9px;border-radius:99px;background:#eef2ff;color:var(--diag);
 border:1px solid var(--diag);letter-spacing:.02em}
.row{display:flex;gap:16px;flex-wrap:wrap}
.arm{flex:1 1 420px}
.armhead{font-size:13px;font-weight:600;padding:5px 10px;border-radius:5px;margin-bottom:8px;
 display:inline-block}
.armhead.raw{background:#fee2e2;color:var(--bad)}
.armhead.aug{background:#dcfce7;color:var(--ok)}
figure{margin:0 0 10px}
figure img{width:100%;border:1px solid var(--line);border-radius:6px;display:block;background:#f2f2f2}
figcaption{font-size:12px;color:#4b5563;margin-top:5px}
figure.missing .none{border:1px dashed var(--line);border-radius:6px;padding:40px;
 text-align:center;color:#9ca3af}
table.check{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
table.check th,table.check td{border:1px solid var(--line);padding:7px 10px;text-align:left}
table.check td:nth-child(2),table.check td:nth-child(3){height:34px;background:#fcfcfd}
.summary{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 22px;
 margin:0 0 24px}
.summary table{border-collapse:collapse;font-size:14px}
.summary td{padding:4px 16px 4px 0}
a{color:#1d4ed8}
"""


def build(results: pathlib.Path, out: pathlib.Path) -> None:
    d = json.loads(results.read_text(encoding="utf-8"))
    root = results.parent
    rows = "".join(
        f"<tr><td><b>{html.escape(p['key'])}</b></td>"
        f"<td>{html.escape(VERDICTS.get(p['key'], ('MIXED',''))[0])}</td></tr>"
        for p in d["products"])
    body = "".join(product_block(p, root) for p in d["products"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Augmentation → mannequin A/B</title><style>{CSS}</style></head><body>
<h1>Augmentation → mannequin A/B</h1>
<p class="lede">Does an extra source-preserving garment reference help the existing Gemini
mannequin generation keep the real product? <b>A</b> is today's production input: base
mannequin + the raw Front/Back/Detail. <b>B</b> is the same thing plus ONE additional image —
a canonical Front made only of the source's own pixels with the background removed. Same
model, same base, same size, same aspect ratio, same prompt template; B's prompt adds one
manifest line naming what the extra attachment is, and names no defect. No QC ran, no
correction, no retries. <b>{d['imageCalls']} image calls, {d['providerFailures']} provider
failures, {d['newSam2Runs']} new SAM2 runs.</b> The canonical masks were
<b>{html.escape(d['maskProvenance'])}</b> — chosen by a person from existing SAM2 candidates,
because the automatic selector failed on all three. Verdicts below are a person's, written
after looking; nothing here is scored by a model.</p>
<div class="summary"><table>{rows}</table></div>
{body}
</body></html>"""
    out.write_text(page, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(pathlib.Path(a.results), pathlib.Path(a.out))
    print(pathlib.Path(a.out).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
