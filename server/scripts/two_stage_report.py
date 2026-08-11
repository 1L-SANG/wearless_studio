"""Two-stage spike report. Human verdicts, written by a person; no model filled them in.

The point of the page is §13: for every visible defect, say WHERE IT FIRST APPEARED. A stripe
that is already wrong on the Stage-1 board is a garment-reconstruction failure; a stripe that
is right on the board and wrong on the mannequin is a dressing failure. Those two need
different fixes, and the single-call pipeline cannot tell them apart.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib

from PIL import Image

BIG = 980
MED = 620
SMALL = 300

CONTROL = {"stripe": "ab_out/aug_ab/stripe/A.png",
           "4ff2132f": "ab_out/aug_ab/4ff2132f/A.png"}

VERDICTS = {
    "stripe": dict(
        stage1="GOOD", final="TWO_STAGE_BETTER",
        stage1_note="Three consistent views of one product. Collar, full front placket and "
                    "every button present; cropped boxy length kept; cuffs and back yoke "
                    "correct. The stripe is fine, vertical and muted, with the taupe and the "
                    "blue both present and not widened — the failure that has dogged this "
                    "product through every earlier experiment did NOT happen here.",
        final_note="The control renders warm beige stripes with the blue gone; the two-stage "
                   "result keeps both colours and the finer density, on a cooler ground much "
                   "closer to the source. Collar, placket and buttons equivalent in both. The "
                   "two-stage body is fractionally longer than the cropped source.",
        limitation="BACK_CUTOUT_UNAVAILABLE_SOURCE_LIMITATION"),
    "4ff2132f": dict(
        stage1="GOOD", final="TWO_STAGE_BETTER",
        stage1_note="Round scoop neckline (not square), curved yoke seam with gathering below "
                    "it, short cap sleeves with no cuff band, dusty-mauve base colour and a "
                    "clearly rendered pointelle openwork surface, with a slightly flared hem. "
                    "This is the first render in the whole investigation that holds neckline, "
                    "colour AND pointelle at the same time.",
        final_note="Both keep the round neck and the mauve. The control invents a button and "
                   "an outward-hanging label at the centre neck that the product does not "
                   "have; the two-stage result has neither, and its yoke seam is a single "
                   "clean curve with the gathering below it. Pointelle visible in both.",
        limitation=None),
}

DEFECT_ORIGIN = {
    "stripe": [
        ("second stripe colour (blue beside taupe)", "correct", "preserved", "preserved",
         "The RAW control loses it — so on this product the loss is a DIRECT-GENERATION "
         "failure that the two-stage path avoided at both stages."),
        ("fine stripe density (not widened)", "correct", "preserved", "preserved",
         "Stage 1 read the Detail close-up as texture, not geometry, which is what the "
         "authority block asks for."),
        ("collar / front placket / buttons", "correct", "preserved", "preserved", ""),
        ("cropped, boxy length", "correct", "preserved", "mostly preserved",
         "Stage 2's body reads slightly longer than the source's crop."),
        ("rear geometry (yoke, back seams)", "NOT ESTABLISHED BY SOURCE", "inferred",
         "inferred",
         "BACK_CUTOUT_UNAVAILABLE_SOURCE_LIMITATION — the Back photograph is a tight crop of "
         "the crumpled shirt with the hem out of frame and the sleeves cut off. Stage 1's "
         "back view is therefore an inference and cannot be validated against the source. No "
         "cutout was fabricated from it."),
    ],
    "4ff2132f": [
        ("round scoop neckline", "correct", "preserved", "preserved",
         "Earlier single-call experiments turned this square when given a flattened cut-out; "
         "with the garment reconstructed first it survived."),
        ("curved yoke seam + gathering below", "correct", "preserved", "preserved", ""),
        ("short cap sleeves, no cuff band", "correct", "preserved", "preserved", ""),
        ("dusty-mauve base colour", "correct", "preserved", "preserved",
         "No drift toward rust, which the augmented single-call route produced."),
        ("pointelle openwork surface", "correct", "preserved", "preserved", ""),
        ("invented neck button + hanging label", "absent in source", "absent", "absent",
         "Present in the RAW control only — a direct-generation artefact that neither stage "
         "introduced."),
    ],
}

ZOOMS = [("neckline", "neckline / collar"), ("torso", "torso · major seams · pattern"),
         ("hem", "hem / length")]


def uri(p, w):
    with Image.open(p) as im:
        im = im.convert("RGB")
        if im.width > w:
            im = im.resize((w, max(1, int(im.height * w / im.width))), Image.LANCZOS)
        b = io.BytesIO(); im.save(b, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def fig(p, cap, w=BIG):
    if not p or not pathlib.Path(p).exists():
        return (f"<figure class='missing'><div class='none'>—</div>"
                f"<figcaption>{html.escape(cap)}</figcaption></figure>")
    with Image.open(p) as im:
        d = f"{im.width}x{im.height}"
    return (f"<figure><a href='file://{html.escape(str(pathlib.Path(p).resolve()))}' "
            f"target='_blank'><img src='{uri(p, w)}' loading='lazy'></a>"
            f"<figcaption>{html.escape(cap)} · <span class='muted'>{d}</span></figcaption></figure>")


def product_block(p):
    key = p["key"]
    v = VERDICTS[key]
    d = pathlib.Path(p["stages"]["stage1"]["file"]).parent
    srcs = "".join(fig(f, f"SOURCE {s}", SMALL)
                   for f, s in zip(p["sourceFiles"], p["sourceSlots"]))
    sam = fig(p["cutoutFile"], "SAM2 FRONT CUTOUT · HUMAN_SELECTED_EXPERIMENTAL_MASK", MED)
    if p.get("backCutout"):
        sam += fig(p["backCutout"], "SAM2 BACK CUTOUT · HUMAN_SELECTED_EXPERIMENTAL_MASK", MED)
    limitation = ""
    if v["limitation"]:
        limitation = (f"<div class='banner bad'>{html.escape(v['limitation'])} — "
                      f"{html.escape(p['backCutoutNote'])}</div>")
    zooms = "".join(fig(d / f"zoom_{n}.jpg",
                        f"ZOOM · {c} — LEFT source, MIDDLE control (raw→mannequin), "
                        f"RIGHT two-stage", 1120)
                    for n, c in ZOOMS if (d / f"zoom_{n}.jpg").exists())
    rows = "".join(
        f"<tr><td>{html.escape(a)}</td><td class='src'>{html.escape(b)}</td>"
        f"<td class='{'ok' if 'preserv' in c or c == 'absent' else 'warn'}'>{html.escape(c)}</td>"
        f"<td class='{'ok' if 'preserv' in e or e == 'absent' else 'warn'}'>{html.escape(e)}</td>"
        f"<td class='note'>{html.escape(n)}</td></tr>"
        for a, b, c, e, n in DEFECT_ORIGIN[key])
    return f"""
    <section class="product">
      <h2>{html.escape(p['label'])}</h2>
      <p class="meta">{html.escape(p['generationModel'])} · {html.escape(p['imageSize'])} ·
        stage 1 {p['stages']['stage1']['latencyMs']} ms ·
        stage 2 {p['stages']['stage2']['latencyMs']} ms · 2 image calls</p>
      <div class="verdicts">
        <div><span class="lbl">Stage 1 garment reconstruction</span>
          <span class="v {v['stage1'].lower()}">{html.escape(v['stage1'])}</span>
          <p class="note">{html.escape(v['stage1_note'])}</p></div>
        <div><span class="lbl">Final two-stage vs RAW baseline</span>
          <span class="v two">{html.escape(v['final'])}</span>
          <p class="note">{html.escape(v['final_note'])}</p></div>
      </div>
      {limitation}
      <h3>Source truth</h3><div class="row">{srcs}</div>
      <h3>SAM2 evidence</h3><div class="row">{sam}</div>
      <h3>Stage 1 — garment-only reconstruction (front · 3/4 · back)</h3>
      {fig(p['stages']['stage1']['file'], 'STAGE 1 garment board', 1400)}
      <h3>Control vs two-stage</h3>
      <div class="row">
        <div class="arm"><div class="armhead raw">CONTROL · raw → mannequin</div>
          {fig(CONTROL[key], 'CONTROL')}</div>
        <div class="arm"><div class="armhead two">NEW · two-stage</div>
          {fig(p['stages']['stage2']['file'], 'TWO-STAGE')}</div>
      </div>
      <h3>Detail comparisons</h3>{zooms}
      <h3>Where each defect first appeared</h3>
      <table class="origin"><thead><tr><th>property</th><th>source</th><th>stage 1</th>
        <th>stage 2</th><th>note</th></tr></thead><tbody>{rows}</tbody></table>
    </section>"""


CSS = """
:root{--ok:#1a7f37;--warn:#b45309;--bad:#c1121f;--two:#4338ca;--line:#e5e7eb}
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#fafafa;color:#111;
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
 "Apple SD Gothic Neo",sans-serif}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:0 0 6px}
h3{font-size:13px;margin:22px 0 8px;text-transform:uppercase;letter-spacing:.05em;color:#374151}
.lede{color:#374151;max-width:84ch;margin:0 0 20px}
section.product{background:#fff;border:1px solid var(--line);border-radius:10px;padding:22px;
 margin:0 0 26px}
.meta,.muted{color:#6b7280;font-size:12.5px}
.note{color:#374151;font-size:13.5px;margin:6px 0 0;max-width:92ch}
.verdicts{display:flex;gap:24px;flex-wrap:wrap;margin:12px 0 4px}
.verdicts>div{flex:1 1 420px;background:#fbfbfc;border:1px solid var(--line);border-radius:8px;
 padding:12px 14px}
.lbl{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;
 display:block;margin-bottom:5px}
.v{font-size:14px;font-weight:700;padding:3px 12px;border-radius:99px;color:#fff}
.v.good{background:var(--ok)}.v.mixed{background:var(--warn)}.v.bad{background:var(--bad)}
.v.two{background:var(--two)}
.banner{padding:10px 14px;border-radius:6px;margin:14px 0;font-size:13.5px}
.banner.bad{background:#fee2e2;color:var(--bad);border:1px solid var(--bad)}
.row{display:flex;gap:16px;flex-wrap:wrap}.arm{flex:1 1 430px}
.armhead{font-size:13px;font-weight:600;padding:5px 10px;border-radius:5px;margin-bottom:8px;
 display:inline-block}
.armhead.raw{background:#fee2e2;color:var(--bad)}
.armhead.two{background:#eef2ff;color:var(--two)}
figure{margin:0 0 10px}
figure img{width:100%;border:1px solid var(--line);border-radius:6px;display:block;background:#f2f2f2}
figcaption{font-size:12px;color:#4b5563;margin-top:5px}
figure.missing .none{border:1px dashed var(--line);border-radius:6px;padding:36px;
 text-align:center;color:#9ca3af}
table.origin{width:100%;border-collapse:collapse;font-size:13px}
table.origin th,table.origin td{border:1px solid var(--line);padding:7px 10px;
 text-align:left;vertical-align:top}
table.origin td.ok{color:var(--ok);font-weight:600}
table.origin td.warn{color:var(--warn);font-weight:600}
table.origin td.src{color:#374151}
table.origin td.note{color:#6b7280;font-size:12.5px}
.summary{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 22px;
 margin:0 0 24px}
.final{font-size:19px;font-weight:700;color:var(--two)}
a{color:#1d4ed8}
"""


def build(results, out):
    d = json.loads(pathlib.Path(results).read_text(encoding="utf-8"))
    body = "".join(product_block(p) for p in d["products"] if p["key"] in VERDICTS)
    rows = "".join(
        f"<tr><td><b>{html.escape(k)}</b></td><td>Stage 1: {html.escape(v['stage1'])}</td>"
        f"<td>Final: {html.escape(v['final'])}</td></tr>" for k, v in VERDICTS.items())
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Two-stage garment → mannequin spike</title><style>{CSS}</style></head><body>
<h1>Two-stage garment → mannequin spike</h1>
<p class="lede">Hypothesis: the single call asks the image model to do too much at once. Stage 1
reconstructs the GARMENT ALONE — no body — from the raw Product Truth plus a background-free
SAM2 cutout. Stage 2 then dresses the existing fixed mannequin with that reconstruction, with
the original photographs still the final authority on every product fact. The control is the
existing raw → mannequin output, reused, not regenerated.
<b>{d['imageCalls']} image calls, {d['providerFailures']} provider failures,
{d['newSam2Runs']} new SAM2 run.</b> No QC, no correction, no retries. Cutout masks are
HUMAN_SELECTED_EXPERIMENTAL_MASK — not production automation. Verdicts are a person's.</p>
<div class="summary"><table>{rows}</table>
<p class="final">Overall: TWO_STAGE_PROMISING</p>
<p class="note">Both Stage-1 boards preserve the real garment, and on both products the
two-stage mannequin keeps more of it than the direct baseline: the stripe keeps its second
colour, and 4ff2132f loses the control's invented neck button and label. Two products, one
generation per stage — enough to justify a larger test, not enough to justify integration.
The stripe's rear geometry is unverifiable because its Back photograph does not show a rear
view, and the masks were picked by hand.</p></div>
{body}
</body></html>"""
    pathlib.Path(out).write_text(page, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.results, a.out)
    print(pathlib.Path(a.out).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
