"""Category router validation — one page, six products, human evidence first.

The verdicts below were written by a person after looking at the six A/B pairs at full size.
The single Vision comparison per product is printed beside each one as SUPPORTING evidence and
is allowed to disagree; where it does, the page says so rather than hiding it.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib

from PIL import Image

BIG = 860
SMALL = 300

#: Written by a person from the images. `winner` is scored against the §8 rule; `severe` marks
#: a regression from the brief's automatic-failure list (wrong length class, missing major
#: component, changed neckline, changed sleeve construction, different product identity).
VERDICTS = {
    "stripe": dict(category="SHIRT", winner="AUGMENTED", severe=None,
        note="A washes both stripe colours into one pale beige and reads nearly plain; B "
             "restores the source's alternating taupe AND blue at visible contrast. Collar, "
             "placket and buttons equivalent. B's body is a little longer and looser than the "
             "cropped source — a drift, not an identity change."),
    "check": dict(category="SHIRT", winner="MIXED", severe=None,
        note="B renders the source's puckered/crinkled surface, which A leaves flat and "
             "smooth, and B's grid is slightly finer. But B also slims the relaxed, "
             "dropped-shoulder silhouette that A keeps. Each wins on something important."),
    "collar-stripe-shirt": dict(category="SHIRT", winner="AUGMENTED", severe=None,
        note="The source is a white ground with BOTH blue and taupe fine stripes. A renders "
             "warm cream stripes only — the blue is gone and the whole shirt reads warm. B "
             "keeps both colours and the cool ground. Collar, placket and buttons equivalent."),
    "4ff2132f": dict(category="BLOUSE", winner="RAW", severe="changed neckline (round scoop → square) and base colour shifted mauve → rust",
        note="B renders the pointelle/rib surface better than A's plain body. But B turns the "
             "source's round scoop neckline into a square neck with a hard folded corner and "
             "pushes dusty mauve toward saturated rust. A keeps both correct. Surface texture "
             "is worth less than neckline and colour."),
    "lace-top": dict(category="BLOUSE", winner="RAW", severe=None,
        note="A keeps the pale pink, the scoop neck with lettuce/picot edge, the bow sitting "
             "at the empire seam under the bust, and the hip length. B saturates the pink, "
             "raises the bow toward the collarbone and tightens the body."),
    "tie-pintuck-blouse": dict(category="BLOUSE", winner="AUGMENTED",
        severe="RAW drops the neck tie entirely — a missing major component",
        note="The source has fabric ties hanging at the neck opening. A renders no tie at "
             "all; B renders it as a tied bow. Pintucks, shirred waist seam, peplum and "
             "banded sleeves are equivalent in both. The dissenting case for the blouse rule."),
}

ZOOMS = [("neckline", "neckline / collar"), ("torso", "torso · pattern · placket"),
         ("hem", "hem / silhouette")]
CHECKLIST = ["silhouette / length", "neckline / collar", "sleeve construction",
             "buttons / placket / pockets", "pattern", "material / texture", "base colour",
             "major construction", "overall same-product impression"]

VERDICT = "CATEGORY_ROUTER_PROVEN"
VERDICT_NOTE = (
    "SHIRT → AUGMENTED: 2 of 3 (stripe, collar-stripe) show AUGMENTED materially better with "
    "no severe regression; check is MIXED. BLOUSE → RAW: 2 of 3 (4ff2132f, lace-top) show RAW "
    "materially better, and on 4ff2132f the augmented arm caused a listed severe regression "
    "(changed neckline). The tie-pintuck blouse dissents — the raw arm dropped the neck tie "
    "entirely, which is a missing major component. Six products decide a default, not a law, "
    "and RAW remains the fallback for everything not confidently a shirt."
)


def uri(p, w):
    with Image.open(p) as im:
        im = im.convert("RGB")
        if im.width > w:
            im = im.resize((w, max(1, int(im.height * w / im.width))), Image.LANCZOS)
        b = io.BytesIO(); im.save(b, format="JPEG", quality=87)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def fig(p, cap, w=BIG):
    if not p or not pathlib.Path(p).exists():
        return f"<figure class='missing'><div class='none'>—</div><figcaption>{html.escape(cap)}</figcaption></figure>"
    with Image.open(p) as im:
        d = f"{im.width}x{im.height}"
    return (f"<figure><a href='file://{html.escape(str(p))}' target='_blank'>"
            f"<img src='{uri(p, w)}' loading='lazy'></a><figcaption>{html.escape(cap)} · "
            f"<span class='muted'>{d}</span></figcaption></figure>")


def product_block(p, vision):
    v = VERDICTS[p["key"]]
    cls = {"AUGMENTED": "aug", "RAW": "raw", "MIXED": "mixed"}.get(v["winner"], "mixed")
    vis = vision.get(p["key"]) or {}
    root = pathlib.Path(p["arms"]["A"]["file"]).parent
    raws = "".join(fig(f, f"SOURCE {s}", SMALL) for f, s in zip(p["rawFiles"], p["rawSlots"]))
    zooms = "".join(fig(root / f"zoom_{n}.jpg", f"ZOOM · {c} — LEFT A raw, RIGHT B augmented", 1080)
                    for n, c in ZOOMS if (root / f"zoom_{n}.jpg").exists())
    rows = "".join(f"<tr><td>{html.escape(c)}</td><td></td><td></td></tr>" for c in CHECKLIST)
    disagree = ""
    if vis.get("winner") and vis["winner"] != v["winner"]:
        disagree = "<span class='tag warn'>vision disagrees</span>"
    severe = (f"<div class='banner bad'>SEVERE REGRESSION — {html.escape(v['severe'])}</div>"
              if v.get("severe") else "")
    a, b = p["arms"]["A"], p["arms"]["B"]
    return f"""
    <section class="product">
      <h2>{html.escape(p['label'])}
        <span class="cat">{html.escape(v['category'])}</span>
        <span class="verdict {cls}">{html.escape(v['winner'])}</span></h2>
      <p class="note">{html.escape(v['note'])}</p>
      {severe}
      <p class="meta">supporting Vision comparison (1 call, temp 0):
        <b>{html.escape(str(vis.get('winner')))}</b> {disagree} —
        {html.escape((vis.get('reason') or '')[:400])}</p>
      <p class="meta">model {html.escape(p['generationModel'])} · {html.escape(p['imageSize'])}
        {html.escape(p['aspectRatio'])} · prompt {html.escape(p['promptVersion'])} ·
        2 image calls · canonical from a HUMAN_SELECTED_EXPERIMENTAL_MASK ·
        garment RGB modified: <b>{p['garmentRgbModified']}</b></p>
      <h3>Source truth</h3><div class="row">{raws}</div>
      <h3>Canonical Front</h3>{fig(p['canonicalFile'], 'CANONICAL FRONT (source pixels only)', 520)}
      <h3>A raw control vs B augmented</h3>
      <div class="row">
        <div class="arm"><div class="armhead raw">A · RAW</div>{fig(a['file'], 'A')}
          <p class="meta">{html.escape(', '.join(a['inputs']))}</p></div>
        <div class="arm"><div class="armhead aug">B · AUGMENTED</div>{fig(b['file'], 'B')}
          <p class="meta">{html.escape(', '.join(b['inputs']))}</p></div>
      </div>
      <h3>Zoom comparison</h3>{zooms}
      <h3>Human comparison checklist</h3>
      <table class="check"><thead><tr><th>attribute</th><th>A raw</th><th>B augmented</th>
        </tr></thead><tbody>{rows}</tbody></table>
    </section>"""


CSS = """
:root{--ok:#1a7f37;--warn:#b45309;--bad:#c1121f;--line:#e5e7eb}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#fafafa;color:#111;
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
 "Apple SD Gothic Neo",sans-serif}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:0 0 8px}
h3{font-size:13px;margin:20px 0 8px;text-transform:uppercase;letter-spacing:.05em;color:#374151}
.lede{color:#374151;max-width:82ch;margin:0 0 20px}
section.product{background:#fff;border:1px solid var(--line);border-radius:10px;padding:22px;
 margin:0 0 24px}
.meta,.muted{color:#6b7280;font-size:12.5px}
.note{color:#374151;font-size:14px;margin:0 0 10px;max-width:92ch}
.cat{font-size:12px;padding:2px 10px;border-radius:99px;background:#eef2ff;color:#4338ca;
 border:1px solid #4338ca;vertical-align:middle}
.verdict{font-size:13px;padding:3px 12px;border-radius:99px;color:#fff;vertical-align:middle}
.verdict.aug{background:var(--ok)}.verdict.raw{background:var(--bad)}
.verdict.mixed{background:var(--warn)}
.tag{font-size:11px;padding:2px 8px;border-radius:99px}
.tag.warn{background:#fef3c7;color:var(--warn);border:1px solid var(--warn)}
.banner{padding:9px 13px;border-radius:6px;margin:0 0 12px;font-size:13.5px}
.banner.bad{background:#fee2e2;color:var(--bad);border:1px solid var(--bad)}
.row{display:flex;gap:16px;flex-wrap:wrap}.arm{flex:1 1 400px}
.armhead{font-size:13px;font-weight:600;padding:5px 10px;border-radius:5px;margin-bottom:8px;
 display:inline-block}
.armhead.raw{background:#fee2e2;color:var(--bad)}.armhead.aug{background:#dcfce7;color:var(--ok)}
figure{margin:0 0 10px}
figure img{width:100%;border:1px solid var(--line);border-radius:6px;display:block;background:#f2f2f2}
figcaption{font-size:12px;color:#4b5563;margin-top:5px}
figure.missing .none{border:1px dashed var(--line);border-radius:6px;padding:36px;text-align:center;color:#9ca3af}
table.check{width:100%;border-collapse:collapse;font-size:13px}
table.check th,table.check td{border:1px solid var(--line);padding:7px 10px;text-align:left}
table.check td:nth-child(2),table.check td:nth-child(3){height:32px;background:#fcfcfd}
.summary{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 22px;margin:0 0 24px}
.summary h3{margin-top:0}
.summary table{border-collapse:collapse;font-size:14px;margin-bottom:14px}
.summary td{padding:4px 20px 4px 0}
.final{font-size:19px;font-weight:700;color:var(--ok)}
a{color:#1d4ed8}
"""


def build(ab_paths, vision_path, out):
    products = []
    for p in ab_paths:
        products.extend(json.loads(pathlib.Path(p).read_text(encoding="utf-8"))["products"])
    products = [p for p in products if p["key"] in VERDICTS]
    order = list(VERDICTS)
    products.sort(key=lambda p: order.index(p["key"]))
    vision = json.loads(pathlib.Path(vision_path).read_text(encoding="utf-8"))

    def rows(cat):
        return "".join(
            f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(v['winner'])}</td>"
            f"<td class='muted'>vision: {html.escape(str((vision.get(k) or {}).get('winner')))}</td></tr>"
            for k, v in VERDICTS.items() if v["category"] == cat)

    body = "".join(product_block(p, vision) for p in products)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Category router validation</title><style>{CSS}</style></head><body>
<h1>Category router validation</h1>
<p class="lede">Hypothesis: structured shirts should be generated from the raw Product Truth
PLUS a source-preserving canonical Front; drape-sensitive blouses from the raw Product Truth
alone. Six products, one generation per arm, identical model / base mannequin / size / aspect
ratio / prompt template — the only variable is B's one extra attachment, whose manifest line
names what it is and names no defect. No QC, no correction, no retries. The images are the
evidence; the single Vision comparison per product is supporting only and is shown even when
it disagrees.</p>
<div class="summary">
  <h3>SHIRT</h3><table>{rows('SHIRT')}</table>
  <h3>BLOUSE</h3><table>{rows('BLOUSE')}</table>
  <h3>Router verdict</h3>
  <p class="final">{html.escape(VERDICT)}</p>
  <p class="note">{html.escape(VERDICT_NOTE)}</p>
  <h3>Implemented</h3><p class="final">YES</p>
  <p class="note">app/services/generation_input_strategy.py — SHIRT + usable canonical →
  AUGMENTED; blouse, uncertain category, missing canonical, or any lookup failure → RAW.
  Product Truth is passed on every route; the canonical is an extra attachment in its own
  manifest slot and never replaces a source photograph. No production path produces canonical
  assets yet, so production behaviour is unchanged (every job resolves to RAW) until a
  producer is wired into the one seam that returns it.</p>
</div>
{body}
</body></html>"""
    pathlib.Path(out).write_text(page, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ab", nargs="+", required=True)
    ap.add_argument("--vision", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.ab, a.vision, a.out)
    print(pathlib.Path(a.out).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
