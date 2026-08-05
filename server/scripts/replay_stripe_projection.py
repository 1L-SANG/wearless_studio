"""캡처된 carrier 로 stripe projection 을 다시 합성한다 — 유료 호출 0회.

합성기를 고칠 때마다 4K 생성을 다시 사는 것은 지속 불가능하다. carrier 와 source 를
한 번 저장해 두면, 이후의 모든 반복은 파일만 가지고 돌 수 있다. 이 스크립트는 그
반복을 위한 것이며 **프로덕션과 같은 함수만** 호출한다 — 여기서 별도 구현을 두면
replay 가 통과해도 실제 워커는 다르게 동작해, 검증이 거짓이 된다.

금지(코드로 강제): provider 호출, DB, R2, credit, cut/output/baseline 생성.
이 모듈은 `app.workers.mannequin_job` 을 import 하지 않는다 — 그 모듈이 DB/R2 핸들을
끌고 오기 때문이다. 대신 워커가 호출하는 것과 **동일한** hybrid_composite 함수를
같은 순서·같은 인자로 호출한다.

입력(디렉터리):
  carrier.png          생성된 착용컷 (합성 대상)
  source_front.png     원본 정면 (decal·패턴 소스)
  geometry.json        검증된 landmark/inventory/component box/목표 주기
  garment_mask.png     (선택) 캡처 당시 mask — landmark 복원 충실도 검증에 쓴다

출력: composite.png, metrics.json, report.html + 200% crop 들
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.hybrid_composite import deterministic_qc as hc_qc  # noqa: E402
from app.services.hybrid_composite import panel_map as hc_panel  # noqa: E402
from app.services.hybrid_composite import warp_composite as hc_warp  # noqa: E402
from app.services.hybrid_composite import stripe_model as hc_stripe  # noqa: E402
from app.services.hybrid_composite.types import CompositeFailure  # noqa: E402

GEOMETRY_SCHEMA = "stripe_replay_geometry_v1"
# 복원 landmark 로 다시 만든 mask 가 캡처본과 이만큼은 겹쳐야 replay 를 신뢰할 수 있다.
# 미달이면 조용히 다른 그림을 합성하는 것이므로 통과시키지 않는다.
MIN_MASK_RECONSTRUCTION_IOU = 0.90
CROP_SIZE = 260          # 200% crop 의 원본 픽셀 변 (2배 확대해 표시)


# ── 입력 ──────────────────────────────────────────────────────────────────────

def _read_image(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit(f"이미지를 읽을 수 없음: {path}")
    return img


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _boxes_to_pixels(boxes: dict | None, *, width: int, height: int) -> dict:
    """정규화 → 픽셀. 워커와 같은 규약(이미지별 개별 환산)."""
    if not boxes:
        return {}
    return {name: [[float(x) * width, float(y) * height] for x, y in box]
            for name, box in boxes.items()}


def _landmarks_from_mask(mask: np.ndarray) -> dict:
    """캡처된 garment mask 에서 landmark 를 복원한다 (캡처 이전 데이터셋 전용).

    워커가 geometry.json 을 남기기 전에 만들어진 자산에는 검증된 landmark 가 없다.
    mask 는 그 landmark 로 y-클립되어 만들어졌으므로 역산이 가능하다. 다만 이것은
    **근사**이므로, 호출자는 복원 mask 를 캡처본과 대조해 충실도를 반드시 확인해야
    한다 — 확인 없이 쓰면 replay 가 다른 그림을 합성하고도 통과했다고 보고한다.
    """
    ys, xs = np.nonzero(mask)
    if len(ys) < 100:
        raise SystemExit("mask 가 비어 landmark 를 복원할 수 없음")
    h, w = mask.shape[:2]
    y0, y1 = int(ys.min()), int(ys.max())
    span = max(1, y1 - y0)

    def row_extent(y: int) -> tuple[float, float]:
        row = np.nonzero(mask[min(max(y, 0), h - 1)])[0]
        if not len(row):
            return 0.4, 0.6
        return float(row.min()) / w, float(row.max()) / w

    sl, sr = row_extent(y0 + int(span * 0.06))
    hl, hr = row_extent(y1 - int(span * 0.04))
    # 워커의 클립 규약(어깨 위 2%, 밑단 아래 3%)을 되돌린다.
    top = (y0 + h * 0.02) / h
    bot = (y1 - h * 0.03) / h
    mid_l, mid_r = row_extent((y0 + y1) // 2)
    return {
        "shoulder_l": [sl, top], "shoulder_r": [sr, top],
        "hem_l": [hl, bot], "hem_r": [hr, bot],
        "sleeve_l_end": [mid_l, (y0 + span * 0.55) / h],
        "sleeve_r_end": [mid_r, (y0 + span * 0.55) / h],
    }


def load_geometry(dataset: Path, carrier: np.ndarray) -> tuple[dict, dict]:
    """geometry.json 로드. landmark 가 없으면 복원하고 그 사실을 함께 돌려준다."""
    path = dataset / "geometry.json"
    if not path.exists():
        raise SystemExit(
            f"geometry.json 없음: {path}\n"
            "워커가 HYBRID_COMPOSITE_ARTIFACT_DIR 로 남긴 데이터셋을 쓰거나 "
            "`capture` 서브커맨드로 먼저 만들어라.")
    geo = json.loads(path.read_text())
    if geo.get("schema") != GEOMETRY_SCHEMA:
        raise SystemExit(f"알 수 없는 geometry schema: {geo.get('schema')!r}")
    provenance = {"landmarks": "captured"}
    if not geo.get("carrier_landmarks"):
        mask_path = dataset / "garment_mask.png"
        if not mask_path.exists():
            raise SystemExit("landmark 도 garment_mask.png 도 없어 replay 불가")
        mask = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
        geo["carrier_landmarks"] = _landmarks_from_mask(mask)
        provenance = {"landmarks": "reconstructed_from_mask"}
    return geo, provenance


# ── 프로덕션 경로 재실행 ───────────────────────────────────────────────────────

def run_projection(carrier: np.ndarray, source: np.ndarray, geo: dict) -> dict:
    """워커(`_apply_hybrid_composite`)와 동일한 순서·인자로 프로덕션 함수를 호출한다."""
    src_inv = geo.get("source_inventory") or {}
    car_inv = geo.get("carrier_inventory") or {}
    car_lm = geo["carrier_landmarks"]
    ch, cw = carrier.shape[:2]
    sh, sw = source.shape[:2]
    car_boxes = _boxes_to_pixels(geo.get("carrier_component_boxes_norm"),
                                 width=cw, height=ch)
    src_boxes = _boxes_to_pixels(geo.get("source_component_boxes_norm"),
                                 width=sw, height=sh)

    roi = geo.get("stripe_model", {}).get("source_roi")
    if not roi:
        raise SystemExit("geometry 에 stripe_model.source_roi 가 없음")
    # 워커와 **같은 함수·같은 인자 형태**: scan 변형에 미리 잘라낸 crop 을 넘긴다.
    # 여기서 다른 진입점을 쓰면 replay 가 통과해도 프로덕션은 다르게 동작한다.
    x0, y0, x1, y1 = (int(v) for v in roi)
    model = hc_stripe.extract_stripe_model_scan(
        source[y0:y1, x0:x1],
        source_asset_id="replay",
        source_sha256=geo.get("stripe_model", {}).get("source_sha256", "0" * 8),
        source_roi=tuple(roi))
    if isinstance(model, CompositeFailure):
        return {"stage": "stripe_model", "failure": model.reason, "detail": model.detail}

    pm = hc_panel.build_panel_map(
        carrier, car_lm, source_inventory=src_inv, carrier_inventory=car_inv,
        strategy="auto")
    if isinstance(pm, CompositeFailure):
        return {"stage": "panel_map", "failure": pm.reason, "detail": pm.detail,
                "metrics": pm.metrics}

    target_period_px = float(geo["target_period_px"])
    axis = geo["garment_axis"]
    art = hc_warp.composite_stripe(
        carrier, pm, model,
        target_period_px=target_period_px, target_axis=axis,
        component_boxes=car_boxes, source_bgr=source,
        source_component_boxes=src_boxes,
        allow_low_source_coverage=(geo.get("mode") == "shadow"))
    if isinstance(art, CompositeFailure):
        return {"stage": "composite", "failure": art.reason, "detail": art.detail,
                "metrics": art.metrics, "panel_map": pm}

    # 워커의 enforce 게이트를 그대로 반영한다 — replay 가 이걸 건너뛰면 프로덕션이
    # 거절하는 결과를 "QC 통과" 로 보고해 판단을 그르친다.
    if geo.get("mode") == "enforce" and art.components_needing_review:
        return {"stage": "protected_components", "panel_map": pm, "artifacts": art,
                "failure": "protected_component_missing",
                "detail": "protected source decal unavailable: "
                          + ", ".join(sorted(art.components_needing_review)),
                "components_needing_review": list(art.components_needing_review),
                "component_review_reasons": dict(art.component_review_reasons)}

    qc = hc_qc.verify_composite(
        art.image_bgr, carrier, pm, model,
        painted_mask=art.painted, coverage_mask=art.coverage_scope, alpha=art.alpha,
        component_scale_metrics=art.metrics.get("cross_surface_scale"),
        component_boxes=car_boxes,
        target_period_px=target_period_px, target_axis=axis)
    return {"stage": "qc", "panel_map": pm, "artifacts": art, "qc": qc,
            "components_needing_review": list(art.components_needing_review),
            "component_review_reasons": dict(art.component_review_reasons)}


def verify_reconstruction(pm, dataset: Path) -> dict:
    """복원 landmark 로 만든 mask 가 캡처본과 실제로 같은지 — 아니면 replay 는 무효다."""
    captured = dataset / "garment_mask.png"
    if not captured.exists():
        return {"checked": False}
    ref = cv2.imread(str(captured), cv2.IMREAD_GRAYSCALE)
    if ref is None or ref.shape != pm.garment_mask.shape:
        return {"checked": False}
    a, b = ref > 0, pm.garment_mask > 0
    union = int((a | b).sum())
    iou = float((a & b).sum()) / max(1, union)
    return {"checked": True, "mask_iou": round(iou, 4),
            "ok": iou >= MIN_MASK_RECONSTRUCTION_IOU,
            "threshold": MIN_MASK_RECONSTRUCTION_IOU}


# ── 리포트 ────────────────────────────────────────────────────────────────────

def _embed(img: np.ndarray | None, max_edge: int = 760) -> str | None:
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    scale = min(1.0, max_edge / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
    return ("data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
            if ok else None)


def _crop(img: np.ndarray, cx: int, cy: int, size: int = CROP_SIZE) -> np.ndarray | None:
    h, w = img.shape[:2]
    x0, y0 = max(0, cx - size // 2), max(0, cy - size // 2)
    x1, y1 = min(w, x0 + size), min(h, y0 + size)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    patch = img[y0:y1, x0:x1]
    return cv2.resize(patch, (patch.shape[1] * 2, patch.shape[0] * 2),
                      interpolation=cv2.INTER_NEAREST)


def _component_centres(car_boxes: dict, shape: tuple) -> dict:
    out = {}
    for name, box in (car_boxes or {}).items():
        arr = np.asarray(box, np.float32)
        out[name] = (int(arr[:, 0].mean()), int(arr[:, 1].mean()))
    return out


def _fig(title: str, uri: str | None) -> str:
    if not uri:
        return (f'<figure class="miss"><figcaption>{html.escape(title)}</figcaption>'
                '<div class="ph">없음</div></figure>')
    return (f'<figure><img src="{uri}" alt="{html.escape(title)}">'
            f'<figcaption>{html.escape(title)}</figcaption></figure>')


def build_report(*, dataset: Path, carrier, source, result: dict, geo: dict,
                 provenance: dict, recon: dict, hashes: dict) -> str:
    pm = result.get("panel_map")
    art = result.get("artifacts")
    qc = result.get("qc")
    car_boxes = _boxes_to_pixels(geo.get("carrier_component_boxes_norm"),
                                 width=carrier.shape[1], height=carrier.shape[0])

    stage_figs = [_fig("source front", _embed(source)),
                  _fig("carrier", _embed(carrier))]
    if pm is not None:
        stage_figs += [_fig("garment mask", _embed(pm.garment_mask)),
                       _fig("protected", _embed(pm.protected)),
                       _fig("boundary (feather)", _embed(pm.boundary))]
    if art is not None:
        ownership = np.zeros(carrier.shape[:2], np.uint8)
        ownership[art.painted > 0] = 120
        for box in (car_boxes or {}).values():
            cv2.fillPoly(ownership, [np.asarray(box, np.int32)], 255)
        stage_figs += [_fig("painted (source 유래)", _embed(art.painted)),
                       _fig("ownership map", _embed(ownership)),
                       _fig("alpha", _embed((art.alpha * 255).astype(np.uint8))),
                       _fig("coverage scope", _embed(art.coverage_scope)),
                       _fig("composite", _embed(art.image_bgr))]

    crops = []
    if art is not None:
        centres = _component_centres(car_boxes, carrier.shape)
        ys, xs = np.nonzero(art.painted)
        if len(ys):
            centres["torso"] = (int(xs.mean()), int(ys.mean()))
        for name, (cx, cy) in sorted(centres.items()):
            before, after = _crop(carrier, cx, cy), _crop(art.image_bgr, cx, cy)
            crops.append(
                f'<div class="pair"><h4>{html.escape(name)} · 200%</h4><div class="row">'
                f'{_fig("carrier", _embed(before, 420))}'
                f'{_fig("composite", _embed(after, 420))}</div></div>')

    if qc is not None:
        rows = "".join(
            f"<tr><td><code>{html.escape(str(k))}</code></td><td>{html.escape(str(v))}</td></tr>"
            for k, v in sorted(qc.metrics.items()) if not isinstance(v, (dict, list)))
        verdict = "통과" if qc.passed else "거절"
        fails = ", ".join(qc.failures) or "—"
        details = "".join(
            f"<li>{html.escape(str(d.get('detail', '')))}</li>"
            for d in qc.metrics.get("failure_details", []))
    else:
        rows, verdict, fails = "", "합성 이전 실패", result.get("failure", "—")
        details = f"<li>{html.escape(str(result.get('detail', '')))}</li>"

    recon_note = ""
    if provenance.get("landmarks") == "reconstructed_from_mask":
        ok = recon.get("ok")
        recon_note = (
            f'<div class="warn"><strong>landmark 복원본으로 replay함</strong> — '
            f'이 데이터셋은 워커가 geometry 를 남기기 전에 만들어졌다. 캡처 mask 와의 '
            f'IoU {recon.get("mask_iou", "—")} '
            f'(하한 {recon.get("threshold")}) → {"신뢰 가능" if ok else "신뢰 불가"}.</div>')

    hash_rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td><code>{html.escape(str(v))}</code></td></tr>"
        for k, v in hashes.items())

    return f"""<!doctype html>
<meta charset="utf-8"><title>Stripe Projection — offline replay</title>
<style>
 body{{font:14.5px/1.6 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;
  margin:0;padding:30px;max-width:1200px;margin-inline:auto;background:#fbfbfc;color:#16181d}}
 h1{{font-size:24px;margin:0 0 6px}} h2{{font-size:18px;margin:32px 0 10px}}
 h4{{margin:0 0 6px;font-size:13px;color:#444}}
 .v{{display:inline-block;padding:5px 13px;border-radius:999px;font-weight:600}}
 .v.pass{{background:#e6f6ec;color:#0f6b32}} .v.fail{{background:#fdeaea;color:#a11}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:12px}}
 .row{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
 .pair{{margin:16px 0;padding:12px;background:#fff;border:1px solid #e6e8ec;border-radius:10px}}
 figure{{margin:0;background:#fff;border:1px solid #e6e8ec;border-radius:9px;padding:7px}}
 figure img{{width:100%;display:block;border-radius:5px;background:#f0f0f2}}
 figcaption{{font-size:11.5px;color:#555;margin-top:5px;text-align:center}}
 .ph{{height:120px;display:grid;place-items:center;color:#aaa;background:#f4f4f6;border-radius:5px}}
 .miss{{opacity:.55}}
 table{{width:100%;border-collapse:collapse;margin-top:10px;background:#fff;
  border:1px solid #e6e8ec;border-radius:9px;overflow:hidden}}
 th,td{{padding:7px 10px;border-bottom:1px solid #eef0f3;text-align:left;font-size:12.5px}}
 code{{background:#f2f3f5;padding:1px 5px;border-radius:4px;font-size:12px}}
 .warn{{background:#fff8e6;border:1px solid #f0dfae;border-radius:9px;padding:10px 14px;margin:14px 0}}
 ul{{margin:6px 0 0 18px}}
</style>
<h1>Stripe Projection — offline replay</h1>
<p><span class="v {'pass' if (qc and qc.passed) else 'fail'}">deterministic QC: {verdict}</span>
 &nbsp; 실패 사유: <code>{html.escape(str(fails))}</code></p>
<p style="color:#666;font-size:13px">provider 호출 0 · DB 0 · R2 0 · credit 0 —
 파일만 읽고 프로덕션 합성 함수를 그대로 호출한다.</p>
{recon_note}

<h2>단계별 산출물</h2>
<div class="grid">{''.join(stage_figs)}</div>

<h2>200% 확대 — carrier vs composite</h2>
{''.join(crops) or '<p>합성 이전 단계에서 실패해 crop 없음</p>'}

<h2>deterministic 지표</h2>
<table><tbody>{rows or '<tr><td colspan=2>없음</td></tr>'}</tbody></table>
<ul>{details}</ul>

<h2>재현 해시</h2>
<table><tbody>{hash_rows}</tbody></table>
"""


# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_replay(args) -> int:
    dataset = Path(args.dataset)
    carrier = _read_image(dataset / "carrier.png")
    source = _read_image(dataset / "source_front.png")
    geo, provenance = load_geometry(dataset, carrier)

    result = run_projection(carrier, source, geo)
    pm = result.get("panel_map")
    recon = verify_reconstruction(pm, dataset) if pm is not None else {"checked": False}

    art = result.get("artifacts")
    qc = result.get("qc")
    out = Path(args.out or dataset)
    out.mkdir(parents=True, exist_ok=True)

    hashes = {"carrier_sha256": _sha256((dataset / "carrier.png").read_bytes()),
              "source_sha256": _sha256((dataset / "source_front.png").read_bytes()),
              "geometry_sha256": _sha256(
                  json.dumps(geo, sort_keys=True, ensure_ascii=False).encode())}
    if art is not None:
        composite_path = out / "replay_composite.png"
        cv2.imwrite(str(composite_path), art.image_bgr)
        hashes["output_sha256"] = _sha256(art.image_bgr.tobytes())
    metrics = {
        "stage": result.get("stage"),
        "failure": result.get("failure"),
        "qc_passed": bool(qc.passed) if qc is not None else False,
        "qc_failures": list(qc.failures) if qc is not None else [],
        "metrics": ({k: v for k, v in qc.metrics.items() if k != "failure_details"}
                    if qc is not None else result.get("metrics", {})),
        "components_needing_review": result.get("components_needing_review", []),
        "component_review_reasons": result.get("component_review_reasons", {}),
        "landmark_provenance": provenance,
        "mask_reconstruction": recon,
    }
    hashes["metrics_sha256"] = _sha256(
        json.dumps(metrics["metrics"], sort_keys=True, default=str).encode())
    (out / "replay_metrics.json").write_text(
        json.dumps({**metrics, "hashes": hashes}, ensure_ascii=False, indent=1,
                   sort_keys=True, default=str))
    (out / "replay_report.html").write_text(build_report(
        dataset=dataset, carrier=carrier, source=source, result=result, geo=geo,
        provenance=provenance, recon=recon, hashes=hashes))

    print(f"stage={metrics['stage']} qc_passed={metrics['qc_passed']} "
          f"failures={metrics['qc_failures']}")
    print(f"output_sha256={hashes.get('output_sha256', '—')}")
    print(f"metrics_sha256={hashes['metrics_sha256']}")
    if recon.get("checked"):
        print(f"mask_reconstruction_iou={recon.get('mask_iou')} ok={recon.get('ok')}")
    print(f"report={out / 'replay_report.html'}")
    if recon.get("checked") and not recon.get("ok"):
        print("복원 landmark 의 mask 충실도 미달 — 이 replay 결과는 신뢰할 수 없다.",
              file=sys.stderr)
        return 2
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay", help="캡처 자산으로 합성을 다시 실행")
    rp.add_argument("dataset", help="carrier.png/source_front.png/geometry.json 디렉터리")
    rp.add_argument("--out", default=None)
    rp.set_defaults(fn=cmd_replay)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
