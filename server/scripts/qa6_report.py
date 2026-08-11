"""QA6 report — six garments, three input strategies, one page to compare them on.

Self-contained: every image is embedded, every image links to the full-resolution file. The
per-garment recommendation is written by a person into RECOMMENDATIONS after looking; the QC
column is the AI QC outcome and is shown separately so the two can disagree in public.
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import pathlib

from PIL import Image

BIG = 560
THUMB = 250

METHODS = [("baseline", "1 · BASELINE", "raw seller photos, production prompt"),
           ("sam2", "2 · SAM2", "raw photos + background-free cutouts + slot authority"),
           ("stage3d", "3 · 3D-ISH", "garment board first, then dress the mannequin")]

#: Written by a person after looking at the six cards. Filled in by --recs.
RECOMMENDATIONS: dict = {}


def uri(p, w):
    with Image.open(p) as im:
        if im.mode == "RGBA":
            f = Image.new("RGB", im.size, (242, 242, 242)); f.paste(im, mask=im.split()[3]); im = f
        else:
            im = im.convert("RGB")
        if im.width > w:
            im = im.resize((w, max(1, int(im.height * w / im.width))), Image.LANCZOS)
        b = io.BytesIO(); im.save(b, format="JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def fig(p, cap, w=BIG, cls=""):
    if not p or not pathlib.Path(p).exists():
        return (f"<figure class='missing {cls}'><div class='none'>—</div>"
                f"<figcaption>{html.escape(cap)}</figcaption></figure>")
    with Image.open(p) as im:
        d = f"{im.width}x{im.height}"
    return (f"<figure class='{cls}'><a href='file://{html.escape(str(pathlib.Path(p).resolve()))}'"
            f" target='_blank'><img src='{uri(p, w)}' loading='lazy'></a>"
            f"<figcaption>{html.escape(cap)} · <span class='muted'>{d}</span></figcaption></figure>")


QC_CLASS = {"PASS": "ok", "FAIL": "bad", "UNVERIFIABLE": "unk"}


def method_block(key, title, sub, res):
    if not res or res.get("skipped"):
        return (f"<div class='method skip'><h4>{html.escape(title)}</h4>"
                f"<p class='muted'>{html.escape(sub)}</p>"
                f"<div class='banner warn'>SKIPPED — "
                f"{html.escape((res or {}).get('reason', 'not attempted'))}</div></div>")
    if res.get("error"):
        return (f"<div class='method bad'><h4>{html.escape(title)}</h4>"
                f"<div class='banner bad'>PROVIDER ERROR — {html.escape(res['error'])}</div></div>")
    q = res.get("qc") or {}
    outcome = q.get("outcome", "UNVERIFIABLE")
    if q.get("errored"):
        outcome = "BLOCKED (QC errored)"
    cls = QC_CLASS.get(q.get("outcome"), "unk")
    failed = ", ".join(q.get("failedChecks") or []) or "—"
    unver = ", ".join(q.get("unverifiableChecks") or []) or "—"
    reasons = "; ".join(q.get("failureReasons") or []) or "—"
    pill = res.get("pillow") or {}
    stage1 = ""
    if res.get("stage1", {}).get("file"):
        stage1 = fig(res["stage1"]["file"], "STAGE 1 garment board (no body)", 560, "board")
    return f"""
    <div class="method {cls}">
      <h4>{html.escape(title)} <span class="badge {cls}">{html.escape(str(outcome))}</span></h4>
      <p class="muted">{html.escape(sub)} · inputs: {html.escape(', '.join(res.get('inputs') or []))}
        · {res.get('imageCalls', 1)} image call(s) · {res.get('latencyMs')} ms</p>
      {stage1}
      {fig(res.get('file'), title)}
      <dl>
        <dt>AI QC</dt><dd class="{cls}"><b>{html.escape(str(q.get('outcome')))}</b>
          <span class="muted">samples {html.escape(str(q.get('sampleDecisions') or []))} ·
          conf {q.get('confidence')}</span></dd>
        <dt>failed checks</dt><dd class="{'bad' if q.get('failedChecks') else ''}">{html.escape(failed)}</dd>
        <dt>unverifiable</dt><dd>{html.escape(unver)}</dd>
        <dt>QC reasons</dt><dd class="note">{html.escape(reasons)}</dd>
        <dt>deterministic ref</dt><dd class="muted">Pillow: {html.escape(str(pill.get('verdict')))}
          {html.escape(', '.join(pill.get('reasons') or []))}</dd>
      </dl>
    </div>"""


def garment_block(g):
    slots = g["chosen_views"]
    srcs = "".join(fig(g["sourceFiles"][s],
                       f"SOURCE {s} · {g['viewAuthority'][s]}", THUMB, "src")
                   for s in slots)
    cuts = "".join(fig(g["cutoutFiles"][s], f"SAM2 CUTOUT {s}", THUMB, "src")
                   for s in g.get("cutoutViews", []) if s in g.get("cutoutFiles", {}))
    if not cuts:
        cuts = "<p class='muted'>no cutout selected for any view</p>"
    missing = g.get("missing_views") or []
    miss = ""
    if missing:
        miss = (f"<div class='banner warn'>MISSING VIEWS: {html.escape(', '.join(missing))} — "
                f"no file with that slot token exists for this garment. Not silently dropped; "
                f"the arms simply had less evidence.</div>")
    rec = RECOMMENDATIONS.get(g["garment_id"], {})
    methods = "".join(method_block(k, t, s, g["method_results"].get(k))
                      for k, t, s in METHODS)
    return f"""
    <section class="garment">
      <h2>{html.escape(g['garment_name'])}
        <span class="cat">{html.escape(g['category'])}</span>
        <span class="gid">{html.escape(g['garment_id'])}</span></h2>
      <p class="meta">available views: {html.escape(', '.join(g['available_views']))} ·
        gender: {html.escape(str(g.get('genderEvidence')))} —
        {html.escape(g.get('genderBasis', ''))} ·
        model {html.escape(g['generationModel'])} @ {html.escape(g['imageSize'])}</p>
      {miss}
      <h3>Source images · slot authority</h3><div class="row">{srcs}</div>
      <h3>SAM2 cutouts <span class="muted">(HUMAN_SELECTED_EXPERIMENTAL_MASK)</span></h3>
      <div class="row">{cuts}</div>
      <h3>Results by method</h3><div class="methods">{methods}</div>
      <div class="rec {rec.get('choice', 'unset').lower().replace(' ', '-')}">
        <span class="lbl">RECOMMENDATION</span>
        <b>{html.escape(rec.get('choice', 'UNSET'))}</b>
        <p class="note">{html.escape(rec.get('note', ''))}</p>
      </div>
    </section>"""


CSS = """
:root{--ok:#1a7f37;--warn:#b45309;--bad:#c1121f;--unk:#6b7280;--line:#e5e7eb}
*{box-sizing:border-box}
body{margin:0;padding:26px;background:#fafafa;color:#111;
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,
 "Apple SD Gothic Neo",sans-serif}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:0 0 6px}
h3{font-size:12.5px;margin:18px 0 8px;text-transform:uppercase;letter-spacing:.05em;color:#374151}
h4{margin:0 0 4px;font-size:15px}
.lede{color:#374151;max-width:88ch;margin:0 0 18px}
section.garment{background:#fff;border:1px solid var(--line);border-radius:10px;padding:20px;
 margin:0 0 24px}
.meta,.muted{color:#6b7280;font-size:12.5px}
.cat{font-size:11.5px;padding:2px 9px;border-radius:99px;background:#eef2ff;color:#4338ca;
 border:1px solid #4338ca;vertical-align:middle}
.gid{font-size:11.5px;color:#9ca3af;vertical-align:middle}
.row{display:flex;gap:12px;flex-wrap:wrap}
figure{margin:0}
figure img{width:100%;border:1px solid var(--line);border-radius:6px;display:block;background:#f2f2f2}
figure.src{width:250px}figure.board img{border-color:#4338ca}
figcaption{font-size:11.5px;color:#4b5563;margin-top:4px}
figure.missing .none{border:1px dashed var(--line);border-radius:6px;padding:30px;
 text-align:center;color:#9ca3af}
.methods{display:flex;gap:14px;flex-wrap:wrap}
.method{flex:1 1 330px;border:2px solid var(--line);border-radius:8px;padding:12px}
.method.ok{border-color:var(--ok)}.method.bad{border-color:var(--bad)}
.method.unk{border-color:var(--unk)}.method.skip{border-style:dashed}
.badge{font-size:11.5px;padding:2px 9px;border-radius:99px;color:#fff}
.badge.ok{background:var(--ok)}.badge.bad{background:var(--bad)}.badge.unk{background:var(--unk)}
.banner{padding:8px 12px;border-radius:6px;margin:8px 0;font-size:13px}
.banner.warn{background:#fef3c7;color:var(--warn);border:1px solid var(--warn)}
.banner.bad{background:#fee2e2;color:var(--bad);border:1px solid var(--bad)}
dl{display:grid;grid-template-columns:120px 1fr;gap:2px 10px;margin:8px 0 0;font-size:12.5px}
dt{color:#6b7280}dd{margin:0}dd.bad{color:var(--bad)}dd.ok{color:var(--ok)}
dd.note{color:#374151}
.rec{margin-top:14px;padding:10px 14px;border-radius:6px;background:#f6f7f9;font-size:14px}
.rec .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;
 display:block;margin-bottom:3px}
.rec.sam2,.rec.augmented{background:#dcfce7}
.rec.raw,.rec.baseline{background:#fee2e2}
.rec.3d-ish,.rec.stage3d{background:#eef2ff}
.rec .note{margin:5px 0 0;color:#374151;font-size:13px}
.summary{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 22px;margin:0 0 22px}
table.tally{border-collapse:collapse;font-size:13.5px}
table.tally th,table.tally td{border:1px solid var(--line);padding:6px 12px;text-align:left}
a{color:#1d4ed8}
"""


def build(results_path, out, recs_path=None, summary_note="", title=""):
    d = json.loads(pathlib.Path(results_path).read_text(encoding="utf-8"))
    if recs_path and pathlib.Path(recs_path).exists():
        RECOMMENDATIONS.update(json.loads(pathlib.Path(recs_path).read_text(encoding="utf-8")))
    garments = d["garments"]
    tally = {}
    for g in garments:
        for m, _t, _s in METHODS:
            r = g["method_results"].get(m) or {}
            if r.get("skipped"):
                k = "SKIPPED"
            elif r.get("error"):
                k = "PROVIDER ERROR"
            else:
                q = r.get("qc") or {}
                k = "QC ERROR" if q.get("errored") else str(q.get("outcome"))
            tally.setdefault(m, {}).setdefault(k, 0)
            tally[m][k] += 1
    head = sorted({k for v in tally.values() for k in v})
    rows = "".join(
        "<tr><td><b>" + html.escape(m) + "</b></td>"
        + "".join(f"<td>{tally.get(m, {}).get(k, 0)}</td>" for k in head) + "</tr>"
        for m, _t, _s in METHODS)
    calls = sum(r.get("imageCalls", 0) for g in garments
                for r in g["method_results"].values() if isinstance(r, dict))
    body = "".join(garment_block(g) for g in garments)
    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA6 @@TITLE@@ — baseline vs SAM2 vs 3D-ish</title><style>{CSS}</style></head><body>
<h1>QA6 @@TITLE@@ — baseline vs SAM2 vs 3D-ish</h1>
<p class="lede">여섯 벌, 세 가지 입력 전략, 동일한 AI QC. 카테고리·슬롯은 <b>파일명 근거만</b>
사용했다(아우터 폴더에 청바지가 섞여 있어 폴더명은 근거로 쓰지 않았다). Front = 전면 지오메트리
권한, Back = 후면 지오메트리 권한, Detail = 소재·패턴·부속 근거 전용. QC 는 세 방식 모두
<code>garment_fidelity_qc</code> 13체크 · 3샘플 · temperature 0 · fail-closed 이며 판정 근거는
<b>원본 사진만</b>이다(컷아웃은 생성 입력이지 진실이 아니다). Pillow 판정은 참고용으로만 병기.
<b>{calls} image calls.</b> 컷아웃 마스크는 사람이 고른 HUMAN_SELECTED_EXPERIMENTAL_MASK 다.</p>
<div class="summary">
  <h3>QC outcome tally</h3>
  <table class="tally"><thead><tr><th>method</th>
    {''.join(f'<th>{html.escape(k)}</th>' for k in head)}</tr></thead>
    <tbody>{rows}</tbody></table>
  <p class="note">{html.escape(summary_note)}</p>
</div>
{body}
</body></html>"""
    pathlib.Path(out).write_text(page.replace("@@TITLE@@", title), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--recs", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--title", default="")
    a = ap.parse_args()
    build(a.results, a.out, a.recs or None, a.note, a.title)
    print(pathlib.Path(a.out).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
