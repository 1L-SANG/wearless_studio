"""Hybrid experiment report. Shows both gates separately so they can disagree in public."""
from __future__ import annotations

import argparse
import base64
import collections
import html
import io
import json
import pathlib

from PIL import Image

BIG, THUMB = 520, 240
BRANCH_TITLE = {"baseline": "A2 · RAW + CONTACT BLOCK", "sam2": "B2 · SAM + CONTACT BLOCK",
                "baseline_nocontact": "A1 · RAW · control (no contact block)",
                "sam2_nocontact": "B1 · SAM · control (no contact block)",
                "legacy_baseline": "LEGACY_BASELINE · RAW · pre-change wording",
                "legacy_sam2": "LEGACY_SAM · SAM · pre-change wording",
                "stage3d": "C · 3D-ISH", "baseline+corr": "A2 · RAW + correction",
                "sam2+corr": "B2 · SAM + correction"}
BRANCH_SUB = {"baseline": "base + RAW Front/Back/Detail — production prompt as it ships today",
              "baseline_nocontact": "same inputs, GARMENT-BODY CONTACT block removed",
              "sam2": "base + RAW views + SAM cutouts as proportion/construction evidence",
              "sam2_nocontact": "same inputs, GARMENT-BODY CONTACT block removed",
              "legacy_baseline": "old contact + old knit guidance — RAW only",
              "legacy_sam2": "old contact + old knit + old cutout wording — RAW + cutouts",
              "stage3d": "garment board first, then dress"}
#: Adjacent placement is the point — legacy beside control beside production, per evidence set.
BRANCH_ORDER = ["legacy_baseline", "baseline_nocontact", "baseline", "baseline+corr",
                "legacy_sam2", "sam2_nocontact", "sam2", "sam2+corr", "stage3d"]


def uri(p, w):
    with Image.open(p) as im:
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


def candidate_block(key, c, winner):
    title = BRANCH_TITLE.get(key, key)
    if c.get("skipped"):
        return (f"<div class='cand skip'><h4>{html.escape(title)}</h4>"
                f"<div class='banner warn'>SKIPPED — {html.escape(c['reason'])}</div></div>")
    if c.get("error"):
        return (f"<div class='cand bad'><h4>{html.escape(title)}</h4>"
                f"<div class='banner bad'>PROVIDER ERROR — {html.escape(c['error'])}</div></div>")
    det = c.get("deterministic") or {}
    v = c.get("vision") or {}
    if v.get("skipped"):
        outcome, cls = "BLOCKED (stage 1)", "bad"
    elif v.get("errored"):
        outcome, cls = "BLOCKED (QC errored)", "unk"
    else:
        outcome = str(v.get("outcome"))
        cls = {"PASS": "ok", "FAIL": "bad"}.get(outcome, "unk")
    ch = (v.get("checks") or {}).get("garmentBodyIntegration") or {}
    win = "<span class='tag win'>WINNER</span>" if key == winner else ""
    corr = c.get("correction") or {}
    corr_html = ""
    if corr:
        if corr.get("attempted"):
            corr_html = ("<div class='banner warn'>correction attempted — see the "
                         f"{html.escape(key)}+corr candidate</div>")
        else:
            corr_html = (f"<div class='banner warn'>correction NOT attempted — "
                         f"{html.escape(corr.get('reason', ''))}</div>")
    return f"""
    <div class="cand {cls}">
      <h4>{html.escape(title)} <span class="badge {cls}">{html.escape(outcome)}</span> {win}</h4>
      <p class="muted">{html.escape(BRANCH_SUB.get(key, 'derived candidate'))}<br>
        inputs: {html.escape(', '.join(c.get('inputs') or []))} · {c.get('latencyMs')} ms</p>
      {fig(c.get('file'), title)}
      {corr_html}
      <dl>
        <dt>stage 1 · deterministic</dt>
        <dd class="{'ok' if det.get('passed') else 'bad'}">
          {'PASS' if det.get('passed') else 'FAIL'} · {det.get('width')}x{det.get('height')}
          <span class="muted">{html.escape(', '.join(det.get('failures') or []) or 'no mechanical defect')}
          · pillow {html.escape(str((det.get('pillow') or {}).get('verdict')))}</span></dd>
        <dt>stage 2 · vision QC</dt>
        <dd class="{cls}"><b>{html.escape(outcome)}</b>
          <span class="muted">samples {html.escape(str(v.get('sampleDecisions') or []))}
          · conf {v.get('confidence')}</span></dd>
        <dt>garmentBodyIntegration</dt>
        <dd class="{'ok' if ch.get('status') == 'PASS' else 'bad'}">
          <b>{html.escape(str(ch.get('status')))}</b>
          <span class="muted">{html.escape((ch.get('evidence') or '')[:220])}</span></dd>
        <dt>category gates</dt>
        <dd class="{'bad' if (c.get('categoryGates') or {}).get('failed') else ''}">
          {html.escape(str((c.get('categoryGates') or {}).get('statuses') or '— none required'))}</dd>
        <dt>failed hard gates</dt>
        <dd class="{'bad' if v.get('failedChecks') else ''}">
          {html.escape(', '.join(v.get('failedChecks') or []) or '—')}</dd>
        <dt>unverifiable</dt>
        <dd>{html.escape(', '.join(v.get('unverifiableChecks') or []) or '—')}</dd>
        <dt>QC reasons</dt>
        <dd class="note">{html.escape('; '.join(v.get('failureReasons') or []) or '—')}</dd>
      </dl>
    </div>"""


def garment_block(g):
    sel = g.get("selection") or {}
    winner = sel.get("winner")
    srcs = "".join(fig(g["sourceFiles"][s], f"SOURCE {s}", THUMB, "src")
                   for s in g["chosen_views"])
    cuts = "".join(fig(g["cutoutFiles"][s],
                       f"SAM CUTOUT {s} · proportions/construction, never the contour",
                       THUMB, "src") for s in g.get("cutoutViews", []))
    if not cuts:
        cuts = "<p class='muted'>no cutout available for this garment</p>"
    miss = ""
    if g.get("missing_views"):
        miss = (f"<div class='banner warn'>MISSING SLOTS: "
                f"{html.escape(', '.join(g['missing_views']))} — no file with that slot token "
                f"exists. Recorded, not silently dropped.</div>")
    cands = "".join(candidate_block(k, g["candidates"][k], winner)
                    for k in BRANCH_ORDER if k in g["candidates"])
    if winner:
        verdict = (f"<div class='verdict ok'><span class='lbl'>WINNER</span>"
                   f"<b>{html.escape(winner)}</b><p class='note'>"
                   f"{html.escape(sel.get('reason', ''))}</p></div>")
    else:
        blocked = sorted({c for cand in g["candidates"].values()
                          if isinstance(cand, dict)
                          for c in ((cand.get("vision") or {}).get("failedChecks") or [])
                          + ((cand.get("vision") or {}).get("unverifiableChecks") or [])})
        verdict = (f"<div class='verdict bad'><span class='lbl'>BLOCKED</span>"
                   f"<b>no candidate cleared both gates</b><p class='note'>failed hard gates "
                   f"across candidates: {html.escape(', '.join(blocked) or '—')}</p></div>")
    routing = g.get("routing") or {}
    return f"""
    <section class="garment">
      <h2>{html.escape(g['garment_name'])}
        <span class="cat">{html.escape(g['category'])}</span>
        <span class="gid">{html.escape(g['garment_id'])}</span></h2>
      <p class="meta">views used: {html.escape(', '.join(g['chosen_views']))} ·
        cutouts: {html.escape(', '.join(g.get('cutoutViews') or []) or 'none')} ·
        gender evidence: {html.escape(str(g.get('genderEvidence')) )} ·
        {html.escape(g['generationModel'])} @ {html.escape(g['imageSize'])} ·
        {g.get('imageCalls')} image calls</p>
      <div class="banner warn">ROUTING · {html.escape(routing.get('policy', '—'))}
        {(' · category gates: ' + html.escape(', '.join(g.get('categoryGatesRequired') or []))) if g.get('categoryGatesRequired') else ''}</div>
      {miss}
      <h3>Source images</h3><div class="row">{srcs}</div>
      <h3>SAM cutouts <span class="muted">(HUMAN_SELECTED_EXPERIMENTAL_MASK · detail evidence only)</span></h3>
      <div class="row">{cuts}</div>
      <h3>Candidates</h3><div class="cands">{cands}</div>
      {verdict}
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
.lede{color:#374151;max-width:90ch;margin:0 0 18px}
section.garment,.summary{background:#fff;border:1px solid var(--line);border-radius:10px;
 padding:20px;margin:0 0 24px}
.meta,.muted{color:#6b7280;font-size:12.5px}
.cat{font-size:11.5px;padding:2px 9px;border-radius:99px;background:#eef2ff;color:#4338ca;
 border:1px solid #4338ca;vertical-align:middle}
.gid{font-size:11.5px;color:#9ca3af}
.row{display:flex;gap:12px;flex-wrap:wrap}
figure{margin:0}figure.src{width:240px}
figure img{width:100%;border:1px solid var(--line);border-radius:6px;display:block;background:#f2f2f2}
figcaption{font-size:11.5px;color:#4b5563;margin-top:4px}
figure.missing .none{border:1px dashed var(--line);border-radius:6px;padding:30px;
 text-align:center;color:#9ca3af}
.cands{display:flex;gap:14px;flex-wrap:wrap}
.cand{flex:1 1 340px;border:2px solid var(--line);border-radius:8px;padding:12px}
.cand.ok{border-color:var(--ok)}.cand.bad{border-color:var(--bad)}
.cand.unk{border-color:var(--unk)}.cand.skip{border-style:dashed}
.badge{font-size:11.5px;padding:2px 9px;border-radius:99px;color:#fff}
.badge.ok{background:var(--ok)}.badge.bad{background:var(--bad)}.badge.unk{background:var(--unk)}
.tag.win{font-size:11px;padding:2px 9px;border-radius:99px;background:#dcfce7;color:var(--ok);
 border:1px solid var(--ok)}
.banner{padding:8px 12px;border-radius:6px;margin:8px 0;font-size:12.5px}
.banner.warn{background:#fef3c7;color:var(--warn);border:1px solid var(--warn)}
.banner.bad{background:#fee2e2;color:var(--bad);border:1px solid var(--bad)}
dl{display:grid;grid-template-columns:150px 1fr;gap:2px 10px;margin:8px 0 0;font-size:12.5px}
dt{color:#6b7280}dd{margin:0}dd.bad{color:var(--bad)}dd.ok{color:var(--ok)}dd.note{color:#374151}
.verdict{margin-top:14px;padding:10px 14px;border-radius:6px;font-size:15px}
.verdict.ok{background:#dcfce7}.verdict.bad{background:#fee2e2}
.verdict .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6b7280;
 display:block;margin-bottom:3px}
.verdict .note{margin:5px 0 0;font-size:13px;color:#374151}
table{border-collapse:collapse;font-size:13.5px;margin:6px 0 14px}
th,td{border:1px solid var(--line);padding:6px 12px;text-align:left}
a{color:#1d4ed8}
"""


def build(results_path, out):
    d = json.loads(pathlib.Path(results_path).read_text(encoding="utf-8"))
    gs = d["garments"]
    outcome = collections.defaultdict(collections.Counter)
    wins = collections.Counter()
    by_cat = collections.defaultdict(list)
    integ = collections.Counter()
    calls = 0
    for g in gs:
        calls += g.get("imageCalls", 0)
        w = (g.get("selection") or {}).get("winner")
        wins[w or "BLOCKED"] += 1
        by_cat[g["category"]].append((g["garment_id"], w))
        for k, c in g["candidates"].items():
            if c.get("skipped"):
                outcome[k]["SKIPPED"] += 1; continue
            if c.get("error"):
                outcome[k]["PROVIDER ERROR"] += 1; continue
            v = c.get("vision") or {}
            key = ("BLOCKED(stage1)" if v.get("skipped")
                   else "QC ERROR" if v.get("errored") else str(v.get("outcome")))
            outcome[k][key] += 1
            st = (v.get("checks") or {}).get("garmentBodyIntegration", {}).get("status")
            if st:
                integ[st] += 1
    keys = sorted({k for c in outcome.values() for k in c})
    rows = "".join("<tr><td><b>" + html.escape(b) + "</b></td>"
                   + "".join(f"<td>{outcome[b].get(k, 0)}</td>" for k in keys) + "</tr>"
                   for b in BRANCH_ORDER if b in outcome)
    winrows = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{v}</td></tr>"
                      for k, v in wins.most_common())
    catrows = "".join(
        f"<tr><td>{html.escape(c.upper())}</td><td>"
        + html.escape(", ".join(f"{gid}→{w or 'BLOCKED'}" for gid, w in items))
        + "</td></tr>" for c, items in by_cat.items())
    body = "".join(garment_block(g) for g in gs)
    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hybrid — multi-branch 생성 + QC winner-pick</title><style>{CSS}</style></head><body>
<h1>Hybrid — multi-branch 생성 + QC winner-pick</h1>
<p class="lede">상품마다 <b>A(BASELINE)</b>와 <b>B(SAM-AUGMENTED)</b>를 각각 생성하고, 승자는
QC가 고른다. Stage 1은 결정론 검사(디코드·해상도·비율·blank)로 <i>이미지가 아닌 것</i>만
걸러내고 의미 판정은 하지 않는다. Stage 2는 Vision LLM QC(14체크 · 3샘플 · temperature 0 ·
fail-closed)이며, 모델이 스스로 쓴 <code>decision</code> 필드는 권한이 아니고 체크 상태에서
판정을 유도한다. 이번에 <b>garmentBodyIntegration</b> hard gate를 추가했다 — 옷이 몸에 입혀지지
않고 떠 보이면 FAIL. Branch B에서 SAM 컷아웃은 <b>패턴·부속·텍스처 근거로만</b> 쓰이며,
프롬프트가 컷아웃의 실루엣을 유지하지 말라고 명시적으로 금지한다.
총 {calls} image calls.</p>
<div class="summary">
  <h3>후보별 QC 결과</h3>
  <table><thead><tr><th>branch</th>{''.join(f'<th>{html.escape(k)}</th>' for k in keys)}
    </tr></thead><tbody>{rows}</tbody></table>
  <h3>승자 집계</h3><table><tbody>{winrows}</tbody></table>
  <h3>카테고리별</h3><table><tbody>{catrows}</tbody></table>
  <h3>garmentBodyIntegration 분포</h3>
  <p class="note">{html.escape(str(dict(integ)))} — 이번 배치에서는 이 게이트가 한 번도
  FAIL을 내지 않았다. 게이트는 동작하지만 <b>판별력은 아직 검증되지 않았다</b>: 떠 보이는
  결과물이 하나도 생성되지 않았기 때문이다.</p>
</div>
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
