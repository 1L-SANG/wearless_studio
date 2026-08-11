"""The spike's only deliverable: one self-contained page, built for looking at.

Nothing here is a thumbnail. A contact sheet is what hid a pattern defect twice already in
this project, so source and canonical sit side by side at a size where a stripe, a check grid
and a button are resolvable, and every image links to the full-resolution file on disk.

The page keeps two questions apart, because the first one failing must not stop the second
from being answered:

  A. can the automatic selector find the garment?  — the AUTO block
  B. GIVEN a good mask, is source-preserving canonicalisation useful? — the DIAGNOSTIC block,
     whose mask was chosen by a human looking at the candidates and which automates nothing.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib

from PIL import Image

BIG_W = 1020
CAND_W = 340


def data_uri(path: str | pathlib.Path, max_w: int) -> str:
    with Image.open(path) as im:
        if im.mode == "RGBA":
            flat = Image.new("RGB", im.size, (242, 242, 242))
            flat.paste(im, mask=im.split()[3])
            im = flat
        else:
            im = im.convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, max(1, int(im.height * max_w / im.width))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def dims(path) -> str:
    with Image.open(path) as im:
        return f"{im.width}x{im.height}"


def figure(path: str | None, caption: str, width: int = BIG_W) -> str:
    if not path or not pathlib.Path(path).exists():
        return (f"<figure class='missing'><div class='none'>not produced</div>"
                f"<figcaption>{html.escape(caption)}</figcaption></figure>")
    return (f"<figure><a href='file://{html.escape(str(path))}' target='_blank'>"
            f"<img src='{data_uri(path, width)}' loading='lazy'></a>"
            f"<figcaption>{html.escape(caption)} · <span class='muted'>{dims(path)}</span> · "
            f"<a href='file://{html.escape(str(path))}' target='_blank'>full res</a>"
            f"</figcaption></figure>")


def result_block(res: dict | None, title: str, *, kind: str, note: str = "") -> str:
    if not res:
        return (f"<div class='result bad'><h4>{html.escape(title)}</h4>"
                f"<p class='muted'>{html.escape(note or 'not produced')}</p></div>")
    pres = res.get("preservation") or {}
    prov = res.get("provenance") or {}
    occ = res.get("occlusion") or {}
    flags = ", ".join(occ.get("flags") or []) or "none detected"
    return f"""
    <div class="result {kind}">
      <h4>{html.escape(title)}</h4>
      {f"<p class='note'>{html.escape(note)}</p>" if note else ""}
      <dl>
        <dt>candidate</dt><dd>#{res.get('candidateId')}</dd>
        <dt>mask coverage</dt><dd>{res.get('maskCoverage')} of frame</dd>
        <dt>bbox</dt><dd>{html.escape(str(prov.get('sourceBBox')))}
          <span class="muted">crop {html.escape(str(prov.get('cropBox')))} ·
          pad {prov.get('padPx')}px</span></dd>
        <dt>RGB preserved</dt><dd class="{'ok' if pres.get('rgbPreserved') else 'warn'}">
          <b>{pres.get('rgbPreservedPct')}%</b> of {pres.get('comparedPixels')} interior px
          identical <span class="muted">max |diff| {pres.get('maxAbsDiff')}</span></dd>
        <dt>transformations</dt><dd>EXIF rotate · crop+pad · scale {prov.get('scale')}
          ({html.escape(str(prov.get('resample')))}) · {prov.get('featherPx')}px alpha feather
          · neutral backdrop {html.escape(str(prov.get('backdrop')))}
          <span class="muted">no sharpen, no smooth, no recolour, no generative fill</span></dd>
        <dt>occlusion</dt><dd>{html.escape(flags)}
          <span class="muted">skin inside mask {occ.get('skinInsideMaskRatio')} — preserved,
          never reconstructed</span></dd>
        <dt>duration</dt><dd>{res.get('durationMs')} ms</dd>
      </dl>
      <div class="pair">
        {figure(res.get('overlayFile'), 'MASK on source')}
        {figure(res.get('canonicalFile'), 'CANONICAL result')}
      </div>
    </div>"""


def product_block(p: dict) -> str:
    if not p.get("maskAvailable"):
        return (f"<section class='product'><h2>{html.escape(p['label'])}</h2>"
                f"<p class='bad'>no SAM2 mask available for this image</p></section>")
    sel = p.get("selection") or {}
    auto_failed = sel.get("state") != "SELECTED"
    cands = "".join(
        f"<figure class='cand {'picked' if c.get('humanPicked') else ''}"
        f"{' auto' if c.get('autoSelected') else ''}'>"
        f"<a href='file://{html.escape(c['file'])}' target='_blank'>"
        f"<img src='{data_uri(c['file'], CAND_W)}' loading='lazy'></a>"
        f"<figcaption>#{c['id']}"
        f"{' · AUTO-SELECTED' if c.get('autoSelected') else ''}"
        f"{' · HUMAN PICK' if c.get('humanPicked') else ''}<br>"
        f"score {c.get('score')} · area {c.get('stats', {}).get('areaFrac')}"
        f"</figcaption></figure>"
        for c in p.get("candidates") or [])

    banner = ""
    if auto_failed:
        banner = (f"<div class='banner bad'>AUTO_SELECTOR_FAILED — state "
                  f"<b>{html.escape(str(sel.get('state')))}</b>, margin {sel.get('margin')}. "
                  f"No automatic mask was produced; the canonical result below is the "
                  f"human-picked diagnostic only.</div>")
    auto_note = ""
    if p.get("auto") and p.get("autoWrong"):
        banner = ("<div class='banner bad'>AUTO_SELECTOR_FAILED — a mask was selected but it "
                  "is not the garment. Its canonical output is shown so the failure is "
                  "visible, and it is not a valid result.</div>")
        auto_note = "the automatic pick is wrong; this output is evidence of the failure"

    return f"""
    <section class="product">
      <h2>{html.escape(p['label'])}</h2>
      <p class="meta">{html.escape(pathlib.Path(p['sourceFile']).name)} ·
        source {p['sourceSize'][0]}x{p['sourceSize'][1]} ·
        EXIF applied {p.get('exif', {}).get('exifApplied')} ·
        {p.get('candidateCount')} SAM2 candidates, {p.get('plausibleCount')} plausible ·
        cross-view support from {html.escape(str(p.get('supportSlots') or 'none'))} ·
        selector <b>{html.escape(str(sel.get('state')))}</b>, margin {sel.get('margin')}</p>
      {banner}
      {figure(p.get('sourceFile'), 'SOURCE Front')}
      <h3>SAM2 candidates offered</h3>
      <div class="cands">{cands or "<p class='muted'>none</p>"}</div>
      {result_block(p.get('auto'), 'AUTO-SELECTED MASK → canonical',
                    kind='bad' if p.get('autoWrong') else 'ok', note=auto_note)
       if p.get('auto') else ""}
      {result_block(p.get('humanPicked'), 'BEST_VISIBLE_CANDIDATE_CANONICALIZATION',
                    kind='diag',
                    note='HUMAN-SELECTED EXPERIMENTAL DIAGNOSTIC — the mask was chosen by a '
                         'person looking at the candidates above. This is not production '
                         'automation and proves nothing about the selector; it exists to '
                         'answer whether a GOOD mask yields a useful canonical image.')}
    </section>"""


CSS = """
:root { --ok:#1a7f37; --warn:#b45309; --bad:#c1121f; --diag:#4338ca; --line:#e5e7eb; }
* { box-sizing:border-box; }
body { margin:0; padding:28px; background:#fafafa; color:#111;
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
  "Apple SD Gothic Neo",sans-serif; }
h1 { font-size:26px; margin:0 0 4px; } h2 { font-size:21px; margin:0 0 6px; }
h3 { font-size:15px; margin:18px 0 6px; text-transform:uppercase; letter-spacing:.05em;
  color:#374151; } h4 { margin:0 0 8px; font-size:16px; }
.lede { color:#374151; max-width:80ch; margin:0 0 24px; }
section.product { background:#fff; border:1px solid var(--line); border-radius:10px;
  padding:22px; margin:0 0 26px; }
.meta,.muted { color:#6b7280; font-size:12.5px; font-weight:400; }
.meta { margin:0 0 12px; }
.banner { padding:10px 14px; border-radius:6px; margin:0 0 14px; font-size:14px; }
.banner.bad { background:#fee2e2; color:var(--bad); border:1px solid var(--bad); }
.result { border-left:5px solid var(--line); padding:12px 16px; margin:18px 0;
  background:#fbfbfc; border-radius:0 8px 8px 0; }
.result.ok { border-left-color:var(--ok); } .result.bad { border-left-color:var(--bad); }
.result.diag { border-left-color:var(--diag); background:#f5f5ff; }
.result .note { font-size:12.5px; color:var(--diag); margin:0 0 8px; }
.result.bad .note { color:var(--bad); }
dl { display:grid; grid-template-columns:170px 1fr; gap:3px 14px; margin:0 0 14px;
  font-size:13.5px; }
dt { color:#6b7280; } dd { margin:0; } dd.ok { color:var(--ok); } dd.warn { color:var(--warn); }
.pair { display:flex; gap:18px; flex-wrap:wrap; }
.pair figure { margin:0; flex:1 1 460px; }
figure img { width:100%; border:1px solid var(--line); border-radius:6px; display:block;
  background:#f2f2f2; }
figcaption { font-size:12px; color:#4b5563; margin-top:5px; }
figure.missing .none { border:1px dashed var(--line); border-radius:6px; padding:48px;
  text-align:center; color:#9ca3af; }
.cands { display:flex; gap:10px; flex-wrap:wrap; }
.cands figure { margin:0; width:340px; }
.cand.auto img { border:3px solid var(--warn); }
.cand.picked img { border:3px solid var(--diag); }
.cand.picked figcaption { color:var(--diag); font-weight:600; }
a { color:#1d4ed8; }
"""


def build(results_path: pathlib.Path, out_html: pathlib.Path, wrong: set[str]) -> None:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    seg = payload.get("segmentation") or {}
    for p in payload["products"]:
        p["autoWrong"] = p["key"] in wrong
    body = "".join(product_block(p) for p in payload["products"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Source-preserving augmentation spike — Front views</title><style>{CSS}</style></head>
<body>
<h1>Source-preserving augmentation spike — Front views</h1>
<p class="lede">Can a messy seller photograph become a clean Product Truth input without
redrawing the garment? Three Front images. Segmentation is pretrained SAM2
(<code>{html.escape(str(seg.get('model')))}</code>, {seg.get('grid')}×{seg.get('grid')} point
grid, restored from <code>{html.escape(str(seg.get('restoredFrom')))}</code>); the automatic
pick is the SigLIP cross-view ranker from
<code>{html.escape(str(seg.get('selectorRestoredFrom')))}</code>, unmodified except for being
aimed at a chosen slot. Everything after the mask is deterministic: EXIF rotation, hole fill,
crop and pad, a 2048 square canvas, a neutral backdrop, a 2px alpha feather.
<b>{payload.get('generativeApiCalls')} generative API calls.</b> No garment pixel is sharpened,
smoothed, recoloured or invented — the RGB-preserved figure is measured by comparing the
canonical image back to the source inside the eroded mask. A hand or hanger over the garment
is labelled and kept, never reconstructed. Click any image for full resolution.</p>
{body}
</body></html>"""
    out_html.write_text(page, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--auto-wrong", default="",
                    help="product keys whose automatic pick a human judged wrong")
    args = ap.parse_args()
    wrong = {k.strip() for k in args.auto_wrong.split(",") if k.strip()}
    build(pathlib.Path(args.results), pathlib.Path(args.out), wrong)
    print(pathlib.Path(args.out).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
