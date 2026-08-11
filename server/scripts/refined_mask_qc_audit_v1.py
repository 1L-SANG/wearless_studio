#!/usr/bin/env python3
"""Offline mask audit and refined recolor comparison.

Reads existing artifacts only, audits mask invariants first, then generates a
refined recolor prototype plus A/B comparison outputs if the audit passes.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.hybrid_composite import deterministic_qc as hc_qc  # noqa: E402
from app.services.hybrid_composite import panel_map as hc_panel  # noqa: E402
from app.services.hybrid_composite.color import bgr_to_lab, ciede2000  # noqa: E402
from app.services.hybrid_composite.panel_map import Panel  # noqa: E402

import importlib.util

replay_path = Path(__file__).resolve().parents[0] / "replay_stripe_projection.py"
spec = importlib.util.spec_from_file_location("replay_module", str(replay_path))
replay = importlib.util.module_from_spec(spec)
if spec.loader:
    spec.loader.exec_module(replay)
else:
    raise SystemExit("could not load replay_stripe_projection")


def read_img(path: Path, flags=cv2.IMREAD_COLOR):
    img = cv2.imread(str(path), flags)
    if img is None:
        raise SystemExit(f"can't read {path}")
    return img


def save_img(path: Path, img: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), img):
        raise SystemExit(f"can't write {path}")


def poly_to_mask(poly: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(mask, [poly.astype(np.int32)], 255)
    return mask


def largest_component_fraction(mask: np.ndarray) -> tuple[int, float]:
    bin_mask = (mask > 0).astype(np.uint8)
    n, labels = cv2.connectedComponents(bin_mask)
    if n <= 1:
        return 0, 0.0
    areas = [int((labels == i).sum()) for i in range(1, n)]
    return n - 1, max(areas) / max(1, sum(areas))


def bbox(mask: np.ndarray) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask > 0)
    if not len(xs):
        return 0, 0, 0, 0
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def iou(a: np.ndarray, b: np.ndarray) -> float:
    aa = a > 0
    bb = b > 0
    union = int((aa | bb).sum())
    return float((aa & bb).sum()) / max(1, union)


def masked_median_delta_e(a_bgr: np.ndarray, b_bgr: np.ndarray, mask: np.ndarray) -> float | None:
    sel = mask > 0
    if not sel.any():
        return None
    a_lab = bgr_to_lab(a_bgr).astype(np.float32)
    b_lab = bgr_to_lab(b_bgr).astype(np.float32)
    ys, xs = np.nonzero(sel)
    if len(ys) > 8000:
        idx = np.linspace(0, len(ys) - 1, 8000).astype(int)
        ys, xs = ys[idx], xs[idx]
    vals = [float(ciede2000(a_lab[y, x], b_lab[y, x])) for y, x in zip(ys, xs)]
    return float(np.median(vals)) if vals else None


def masked_median_delta_e_to_source(out_bgr: np.ndarray, src_bgr: np.ndarray, mask: np.ndarray) -> float | None:
    sel = mask > 0
    if not sel.any():
        return None
    out_lab = bgr_to_lab(out_bgr).astype(np.float32)
    src_lab = bgr_to_lab(src_bgr).astype(np.float32)
    ys, xs = np.nonzero(sel)
    if len(ys) > 8000:
        idx = np.linspace(0, len(ys) - 1, 8000).astype(int)
        ys, xs = ys[idx], xs[idx]
    vals = [float(ciede2000(out_lab[y, x], src_lab[y, x])) for y, x in zip(ys, xs)]
    return float(np.median(vals)) if vals else None


def overlay_masks(base: np.ndarray, layers: list[tuple[np.ndarray, tuple[int, int, int], float]]) -> np.ndarray:
    out = base.copy()
    for mask, color, alpha in layers:
        sel = mask > 0
        if not sel.any():
            continue
        color_arr = np.array(color, dtype=np.float32)
        out[sel] = np.clip((1.0 - alpha) * out[sel].astype(np.float32) + alpha * color_arr, 0, 255).astype(np.uint8)
    return out


def make_contact_sheet(images: list[np.ndarray], names: list[str], out: Path, thumb_h: int = 560) -> None:
    thumbs = []
    for img in images:
        h, w = img.shape[:2]
        scale = thumb_h / max(1, h)
        tw = max(1, int(w * scale))
        thumbs.append(cv2.resize(img, (tw, thumb_h), interpolation=cv2.INTER_AREA))
    total_w = sum(t.shape[1] for t in thumbs) + (len(thumbs) - 1) * 10
    sheet = np.full((thumb_h + 54, total_w, 3), 255, dtype=np.uint8)
    x = 0
    for name, t in zip(names, thumbs):
        sheet[:t.shape[0], x:x + t.shape[1]] = t
        cv2.putText(sheet, name, (x + 6, thumb_h + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 0), 2, cv2.LINE_AA)
        x += t.shape[1] + 10
    save_img(out, sheet)


def build_torso_panel(carrier: np.ndarray, garment_mask: np.ndarray) -> tuple[Panel, dict]:
    lm = replay._landmarks_from_mask(garment_mask)
    pm = hc_panel.build_panel_map(carrier, lm, strategy="auto")
    torso = next((p for p in pm.panels if p.name == "torso"), None)
    if torso is None:
        raise SystemExit("torso panel unavailable")
    return torso, lm


def torso_polygon_from_landmarks(lm: dict, shape: tuple[int, int]) -> np.ndarray:
    h, w = shape[:2]
    px = lambda pt: (int(pt[0] * w), int(pt[1] * h))
    sl, sr, hl, hr = px(lm["shoulder_l"]), px(lm["shoulder_r"]), px(lm["hem_l"]), px(lm["hem_r"])
    return np.array([sl, sr, hr, hl], np.int32)


def detect_local_bands(carrier_bgr: np.ndarray, torso_mask: np.ndarray, n_scan: int = 21):
    gray = cv2.cvtColor(carrier_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    band = cv2.GaussianBlur(gray, (0, 0), 1.0) - cv2.GaussianBlur(gray, (0, 0), 4.0)
    energy = np.abs(band)
    h, w = gray.shape
    ys = np.linspace(int(h * 0.15), int(h * 0.85), n_scan).astype(int)
    edge_map = np.zeros((h, w), dtype=np.uint8)
    conf_map = np.zeros((h, w), dtype=np.float32)
    scans = []
    for y in ys:
        row = torso_mask[y] > 0
        if not row.any():
            scans.append({"y": int(y), "edges": []})
            continue
        cols = np.where(row)[0]
        x0, x1 = int(cols.min()), int(cols.max()) + 1
        profile = gray[y, x0:x1]
        prof_s = cv2.GaussianBlur(profile.reshape(1, -1), (1, 9), 0).ravel()
        grad = np.abs(np.gradient(prof_s))
        thr = max(5.0, float(np.median(grad)) * 3.0)
        peaks = np.where(grad > thr)[0]
        edges = [int(x0 + p) for p in peaks]
        scans.append({"y": int(y), "edges": edges})
        for ex in edges:
            if 0 <= ex < w:
                y0, y1 = max(0, y - 1), min(h, y + 2)
                x0e, x1e = max(0, ex - 1), min(w, ex + 2)
                edge_map[y0:y1, x0e:x1e] = 255
                idx = min(max(ex - x0, 0), len(grad) - 1)
                c = float(grad[idx]) * float(energy[y, min(max(ex, 0), w - 1)])
                conf_map[y0:y1, x0e:x1e] = np.maximum(conf_map[y0:y1, x0e:x1e], c)
    if conf_map.max() > 0:
        conf_map = conf_map / float(conf_map.max())
    edge_map[torso_mask == 0] = 0
    conf_map[torso_mask == 0] = 0.0
    return edge_map, conf_map, scans


def crop_boxes(torso_box: tuple[int, int, int, int]) -> dict[str, tuple[int, int, int, int]]:
    x0, y0, x1, y1 = torso_box
    w = max(1, x1 - x0)
    h = max(1, y1 - y0)
    return {
        "upper_torso": (x0, y0, x1, y0 + int(h * 0.28)),
        "mid_torso": (x0, y0 + int(h * 0.34), x1, y0 + int(h * 0.64)),
        "waist": (x0, y0 + int(h * 0.70), x1, y1),
        "left_torso_edge": (x0, y0 + int(h * 0.28), x0 + int(w * 0.42), y0 + int(h * 0.72)),
        "right_torso_edge": (x0 + int(w * 0.58), y0 + int(h * 0.28), x1, y0 + int(h * 0.72)),
        "placket_button": (x0 + int(w * 0.33), y0 + int(h * 0.06), x0 + int(w * 0.67), y0 + int(h * 0.54)),
        "torso_sleeve_boundary": (x0, y0, x1, y0 + int(h * 0.30)),
        "bottom_hem_boundary": (x0, y0 + int(h * 0.76), x1, y1),
    }


def crop_strip(images: dict[str, np.ndarray], box: tuple[int, int, int, int], out: Path, labels: list[str]) -> None:
    x0, y0, x1, y1 = box
    strips = []
    for label in labels:
        img = images[label]
        crop = img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        if crop.size == 0:
            crop = np.full((64, 64, 3), 255, np.uint8)
        target_w = 500
        scale = target_w / max(1, crop.shape[1])
        resized = cv2.resize(crop, (target_w, max(1, int(crop.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        strips.append((label, resized))
    total_w = sum(img.shape[1] for _, img in strips) + 10 * (len(strips) - 1)
    h = max(img.shape[0] for _, img in strips)
    canvas = np.full((h + 34, total_w, 3), 255, np.uint8)
    x = 0
    for label, img in strips:
        canvas[:img.shape[0], x:x + img.shape[1]] = img
        cv2.putText(canvas, label, (x + 4, h + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2, cv2.LINE_AA)
        x += img.shape[1] + 10
    save_img(out, canvas)


def render_html(out_dir: Path, status: str, audit: dict, metrics: dict, images: dict[str, Path], missing: list[str]) -> None:
    def img_tag(title: str, rel: str) -> str:
        if rel in missing:
            return f'<figure class="miss"><figcaption>{html.escape(title)} ({html.escape(rel)})</figcaption><div class="ph">MISSING</div></figure>'
        return f'<figure><a href="{html.escape(rel)}"><img src="{html.escape(rel)}" alt="{html.escape(title)}"></a><figcaption>{html.escape(title)}<br><small>{html.escape(rel)}</small></figcaption></figure>'

    thumbs = ["source_front.png", "carrier.png", "composite.png", "replay_composite.png", "refined_recolor_composite.png"]
    sections = [
        ("전체 비교", thumbs),
        ("부위별 확대", ["crop_upper_torso.png", "crop_mid_torso.png", "crop_waist.png", "crop_left_torso_edge.png", "crop_right_torso_edge.png", "crop_placket_button.png", "crop_torso_sleeve_boundary.png", "crop_bottom_hem_boundary.png"]),
        ("디버그", ["refined_recolor_mask.png", "refined_preserved_mask.png", "refined_local_confidence_map.png", "refined_local_band_overlay.png", "refined_boundary_overlay.png", "old_mask.png", "corrected_mask.png", "removed_mask.png", "added_mask.png", "old_new_overlay.png", "garment_boundary_overlay.png", "component_exclusion_overlay.png"]),
    ]
    metric_rows = []
    metric_source = metrics.get("metric_table", metrics)
    if metric_source and isinstance(next(iter(metric_source.values())), dict):
        metric_names = sorted(set().union(*(vals.keys() for vals in metric_source.values())))
        for metric_name in metric_names:
            orig = metric_source.get("Original recolor", {}).get(metric_name, "N/A")
            ref = metric_source.get("Refined recolor", {}).get(metric_name, "N/A")
            metric_rows.append(f"<tr><td>{html.escape(metric_name)}</td><td>{html.escape(str(orig))}</td><td>{html.escape(str(ref))}</td></tr>")
    else:
        for name, vals in metric_source.items():
            metric_rows.append(f"<tr><td>{html.escape(name)}</td><td>{html.escape(str(vals[0]))}</td><td>{html.escape(str(vals[1]))}</td></tr>")
    audit_rows = []
    for name, row in audit["masks"].items():
        audit_rows.append(
            f"<tr><td>{html.escape(name)}</td><td>{row['shape']}</td><td>{row['dtype']}</td><td>{row['min']}</td><td>{row['max']}</td><td>{row['true_pixels']}</td><td>{html.escape(row['space'])}</td><td>{'yes' if row['binary'] else 'no'}</td><td>{html.escape(row['meaning'])}</td></tr>"
        )
    warnings = [
        "carrier torso period 약 531px 값은 low-confidence이며 성공 근거로 사용할 수 없음",
        "recolored_fraction은 약 7.5% 수준보다 더 작게 떨어질 수 있음",
        "protected region 변화는 0이어야 함",
        "local orientation / boundary jump는 low-confidence이면 N/A로 남김",
        "production 미연결, provider_calls 0",
    ]
    checklist = [
        "Source의 파랑/베이지/회색 계열이 더 정확히 반영됐는가",
        "스트라이프 색상 순서가 Source와 일치하는가",
        "Recolor가 Carrier보다 상품 색상에 가까워졌는가",
        "세로 column banding이 보이지 않는가",
        "주름을 가로질러 색상이 부자연스럽게 연결되지 않는가",
        "Upper/mid/waist 간 색상 매핑이 일관적인가",
        "Recolor와 preserved 영역의 경계가 눈에 띄지 않는가",
        "플래킷 주변 색상 누출이 없는가",
        "단추 주변 원형 artifact가 없는가",
        "Collar, cuffs, sleeves가 변경되지 않았는가",
        "Carrier의 주름과 음영이 보존됐는가",
        "recolor만으로도 상품 충실도가 실제로 개선됐는가",
        "Legacy 및 Replay보다 Recolor가 육안으로 더 자연스러운가",
    ]
    html_text = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Refined mask review</title>
<style>
body{{font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif;background:#fafafa;color:#111;margin:0;padding:20px}}
.status{{padding:16px 18px;border-radius:14px;background:#fff;border:2px solid #d7e7ff;box-shadow:0 1px 8px rgba(0,0,0,.06)}}
.pill{{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700;background:#e7f6ea;color:#136f2d}}
.warn li{{margin:6px 0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}}
figure{{margin:0;background:#fff;border-radius:12px;border:1px solid #ddd;padding:10px}}
figure img{{width:100%;height:220px;object-fit:contain;background:#f4f4f4;cursor:zoom-in}}
figcaption{{font-size:12px;line-height:1.35;margin-top:8px}}
.miss .ph{{height:220px;display:flex;align-items:center;justify-content:center;background:#ffeaea;color:#a11;font-weight:800}}
table{{width:100%;border-collapse:collapse;background:#fff}}
th,td{{border:1px solid #ddd;padding:8px;vertical-align:top;font-size:13px}}
th{{background:#f1f1f1;text-align:left}}
.small{{font-size:12px;color:#555}}
.check{{columns:2;column-gap:28px}}
.check li{{break-inside:avoid;margin:6px 0}}
.modal{{position:fixed;inset:0;background:rgba(0,0,0,.82);display:none;align-items:center;justify-content:center;z-index:99}}
.modal img{{max-width:96vw;max-height:94vh;box-shadow:0 0 0 4px #fff}}
.modal.show{{display:flex}}
</style></head><body>
<div class="status">
  <div class="pill">{html.escape(status)}</div>
  <h1>Refined mask audit and A/B comparison</h1>
  <div class="small">provider_calls 0 · offline prototype · production 미연결 · human review only</div>
</div>
<h2>중요 경고</h2>
<ul class="warn">{''.join(f'<li>{html.escape(w)}</li>' for w in warnings)}</ul>
<h2>Mask invariant audit</h2>
<table><thead><tr><th>Mask</th><th>Shape</th><th>Dtype</th><th>Min</th><th>Max</th><th>True px</th><th>Coord space</th><th>Binary</th><th>Meaning</th></tr></thead><tbody>{''.join(audit_rows)}</tbody></table>
<h2>핵심 수치</h2>
<table><thead><tr><th>Metric</th><th>Original</th><th>Refined</th></tr></thead><tbody>{''.join(metric_rows)}</tbody></table>
<h2>전체 비교</h2>
<div class="grid">{''.join(img_tag(name.replace('_',' ').replace('.png','').title(), name) for name in thumbs)}</div>
<h2>부위별 확대</h2>
<div class="grid">{''.join(img_tag(name.replace('crop_','').replace('.png','').replace('_',' ').title(), name) for name in sections[1][1])}</div>
<h2>디버그 이미지</h2>
<div class="grid">{''.join(img_tag(name.replace('_',' ').replace('.png','').title(), name) for name in sections[2][1])}</div>
<h2>사람 검수 체크리스트</h2>
<ul class="check">{''.join(f'<li>[ ] {html.escape(item)}</li>' for item in checklist)}</ul>
<h2>최종 사람 판정</h2>
<div class="status"><strong>{html.escape(status)}</strong></div>
<script>
document.querySelectorAll('figure img').forEach(img=>img.addEventListener('click',()=>{{
  const m=document.getElementById('modal'); const i=document.getElementById('modalimg');
  i.src=img.src; m.classList.add('show');
}}));
</script>
<div id="modal" class="modal" onclick="this.classList.remove('show')"><img id="modalimg" alt="zoom"></div>
</body></html>"""
    (out_dir / "refined_review_report.html").write_text(html_text, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifacts_dir")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    art = Path(args.artifacts_dir)
    out = Path(args.out) if args.out else art / "diagnostic_phase3_refine_compare"
    out.mkdir(parents=True, exist_ok=True)

    carrier = read_img(art / "carrier.png")
    source = read_img(art / "source_front.png")
    legacy = read_img(art / "composite.png")
    replay_img = read_img(art / "replay_composite.png")
    original_recolor = read_img(art / "diagnostic_phase3" / "recolor_composite.png")
    garment_mask = read_img(art / "garment_mask.png", cv2.IMREAD_GRAYSCALE)
    protected_mask = read_img(art / "protected.png", cv2.IMREAD_GRAYSCALE)
    painted_mask = read_img(art / "painted.png", cv2.IMREAD_GRAYSCALE)
    original_recolor_mask = read_img(art / "diagnostic_phase3" / "recolor_mask.png", cv2.IMREAD_GRAYSCALE)
    preserved_mask = read_img(art / "diagnostic_phase3" / "preserved_mask.png", cv2.IMREAD_GRAYSCALE)
    corrected_mask = read_img(art / "diagnostic_phase3_refine" / "corrected_torso_core_mask.png", cv2.IMREAD_GRAYSCALE)

    # legacy torso mask from garment landmarks
    torso_panel, lm = build_torso_panel(carrier, garment_mask)
    panel_map = hc_panel.build_panel_map(carrier, lm, strategy="auto")
    torso_poly = torso_polygon_from_landmarks(lm, garment_mask.shape)
    old_torso_mask = (poly_to_mask(torso_poly, garment_mask.shape) > 0) & (garment_mask > 0)
    old_torso_mask = old_torso_mask.astype(np.uint8) * 255

    # refined experimental masks
    refined_recolor_mask = (((original_recolor_mask > 0) & (corrected_mask > 0) & (protected_mask == 0)).astype(np.uint8) * 255)
    refined_preserved_mask = (((preserved_mask > 0) & (corrected_mask > 0)).astype(np.uint8) * 255)
    refined_recolor = carrier.copy()
    refined_recolor[refined_recolor_mask > 0] = original_recolor[refined_recolor_mask > 0]
    keep = (corrected_mask > 0) & (refined_recolor_mask == 0)
    refined_recolor[keep] = carrier[keep]

    # detection masks
    original_det_mask, original_conf, _ = detect_local_bands(carrier, corrected_mask, n_scan=21)
    original_detection_mask = original_det_mask.copy()
    refined_detection_mask = ((detect_local_bands(carrier, corrected_mask, n_scan=21)[0] > 0).astype(np.uint8) * 255)
    refined_detection_mask[corrected_mask == 0] = 0

    # invariant audit
    masks = {
        "garment_mask": garment_mask,
        "old_torso_mask": old_torso_mask,
        "corrected_torso_core_mask": corrected_mask,
        "protected_mask": protected_mask,
        "painted_mask": painted_mask,
        "recolor_mask": refined_recolor_mask,
        "preserved_mask": refined_preserved_mask,
        "detection_mask": refined_detection_mask,
    }
    semantics = {
        "garment_mask": "true=in garment foreground",
        "old_torso_mask": "true=legacy torso polygon intersect garment",
        "corrected_torso_core_mask": "true=refined torso core to recolor",
        "protected_mask": "true=protected interior / preserve region from artifact",
        "painted_mask": "true=painted area in legacy artifact",
        "recolor_mask": "true=refined recolor-applied pixels",
        "preserved_mask": "true=carrier pixels kept in the refined scope",
        "detection_mask": "true=local band detections inside torso core",
    }
    audit_rows = {}
    for name, m in masks.items():
        audit_rows[name] = {
            "shape": list(m.shape),
            "dtype": str(m.dtype),
            "min": int(m.min()),
            "max": int(m.max()),
            "true_pixels": int((m > 0).sum()),
            "space": "image_pixels",
            "binary": bool(set(np.unique(m).tolist()) <= {0, 255}),
            "meaning": semantics[name],
        }

    rel = lambda a, b: int(((a > 0) & (b > 0)).sum())
    failures = []
    if int(((corrected_mask > 0) & (garment_mask == 0)).sum()):
        failures.append("corrected_torso_core not subset of garment_mask")
    if int(((refined_recolor_mask > 0) & (corrected_mask == 0)).sum()):
        failures.append("recolor_mask outside corrected_torso_core")
    if int(((refined_detection_mask > 0) & (corrected_mask == 0)).sum()):
        failures.append("detection_mask outside corrected_torso_core")
    if int(((refined_recolor_mask > 0) & (protected_mask > 0)).sum()):
        failures.append("recolor_mask intersects protected_mask")
    if int(((refined_recolor_mask > 0) & (garment_mask == 0)).sum()):
        failures.append("recolor_mask outside garment_mask")
    if int(((refined_recolor_mask > 0) & (corrected_mask > 0) & (garment_mask == 0)).sum()):
        failures.append("background intersects recolor_mask")
    # mannequin body proxy uses garment complement because a separate mannequin matte is absent.
    mannequin_proxy = (garment_mask == 0).astype(np.uint8) * 255
    if int(((refined_recolor_mask > 0) & (mannequin_proxy > 0)).sum()):
        failures.append("mannequin proxy intersects recolor_mask")

    invariant_json = {
        "image_width": int(carrier.shape[1]),
        "image_height": int(carrier.shape[0]),
        "total_pixels": int(carrier.shape[0] * carrier.shape[1]),
        "masks": audit_rows,
        "relations": {
            "corrected_subset_garment": int(((corrected_mask > 0) & (garment_mask == 0)).sum()),
            "recolor_outside_corrected": int(((refined_recolor_mask > 0) & (corrected_mask == 0)).sum()),
            "detection_outside_corrected": int(((refined_detection_mask > 0) & (corrected_mask == 0)).sum()),
            "recolor_protected_intersection": int(((refined_recolor_mask > 0) & (protected_mask > 0)).sum()),
            "recolor_outside_garment": int(((refined_recolor_mask > 0) & (garment_mask == 0)).sum()),
            "background_intersection": int(((refined_recolor_mask > 0) & (garment_mask == 0)).sum()),
            "mannequin_proxy_intersection": int(((refined_recolor_mask > 0) & (mannequin_proxy > 0)).sum()),
            "corrected_intersection_protected": int(rel(corrected_mask, protected_mask)),
            "corrected_minus_protected": int(((corrected_mask > 0) & (protected_mask == 0)).sum()),
            "recolor_intersection_corrected": int(rel(refined_recolor_mask, corrected_mask)),
            "painted_intersection_corrected": int(rel(painted_mask, corrected_mask)),
            "preserved_intersection_corrected": int(rel(refined_preserved_mask, corrected_mask)),
        },
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }
    (out / "mask_invariant_audit.json").write_text(json.dumps(invariant_json, indent=2), encoding="utf-8")

    # visual audit outputs
    old_mask = old_torso_mask.astype(np.uint8) * 255
    corrected_vis = corrected_mask.astype(np.uint8)
    removed = (((old_mask > 0) & (corrected_vis == 0)).astype(np.uint8) * 255)
    added = (((corrected_vis > 0) & (old_mask == 0)).astype(np.uint8) * 255)
    save_img(out / "old_mask.png", overlay_masks(carrier, [(old_mask, (255, 64, 64), 0.35)]))
    save_img(out / "corrected_mask.png", overlay_masks(carrier, [(corrected_vis, (64, 255, 64), 0.35)]))
    save_img(out / "removed_mask.png", overlay_masks(carrier, [(removed, (255, 0, 0), 0.50)]))
    save_img(out / "added_mask.png", overlay_masks(carrier, [(added, (0, 255, 0), 0.50)]))
    save_img(out / "old_new_overlay.png", overlay_masks(carrier, [(old_mask, (255, 0, 0), 0.25), (corrected_vis, (0, 255, 0), 0.25)]))
    save_img(out / "garment_boundary_overlay.png", overlay_masks(carrier, [(garment_mask, (255, 255, 0), 0.10), (old_mask, (255, 0, 0), 0.20), (corrected_vis, (0, 255, 0), 0.20)]))
    save_img(out / "component_exclusion_overlay.png", overlay_masks(carrier, [(protected_mask, (255, 0, 255), 0.25), (refined_recolor_mask, (0, 255, 255), 0.45)]))

    # refined band detection outputs
    det_mask, conf_map, scans = detect_local_bands(carrier, corrected_mask, n_scan=21)
    det_mask[corrected_mask == 0] = 0
    conf_map[corrected_mask == 0] = 0.0
    refined_conf = cv2.applyColorMap((conf_map * 255).astype(np.uint8), cv2.COLORMAP_JET)
    refined_band_overlay = overlay_masks(carrier, [(det_mask, (0, 0, 255), 0.45)])
    save_img(out / "refined_local_confidence_map.png", refined_conf)
    save_img(out / "refined_local_band_overlay.png", refined_band_overlay)
    save_img(out / "refined_recolor_mask.png", refined_recolor_mask)
    save_img(out / "refined_preserved_mask.png", refined_preserved_mask)
    save_img(out / "refined_boundary_overlay.png", overlay_masks(carrier, [(refined_recolor_mask, (0, 255, 255), 0.35), (refined_preserved_mask, (255, 0, 255), 0.20)]))
    save_img(out / "refined_recolor_composite.png", refined_recolor)
    save_img(out / "detection_mask.png", det_mask)
    save_img(out / "scanline_overlay.png", overlay_masks(carrier, [(corrected_mask, (64, 255, 64), 0.08)]))

    # comparison images
    imgs = {
        "source_front": source,
        "carrier": carrier,
        "legacy": legacy,
        "replay": replay_img,
        "original_recolor": original_recolor,
        "refined_recolor": refined_recolor,
    }
    make_contact_sheet(
        [imgs["source_front"], imgs["carrier"], imgs["legacy"], imgs["replay"], imgs["original_recolor"], imgs["refined_recolor"]],
        ["Source", "Carrier", "Legacy", "Replay", "Original recolor", "Refined recolor"],
        out / "contact_sheet.png",
        thumb_h=520,
    )

    # crops
    torso_x0, torso_y0, torso_x1, torso_y1 = bbox(old_mask | corrected_vis)
    crop_boxes_map = crop_boxes((torso_x0, torso_y0, torso_x1, torso_y1))
    crop_names = {
        "upper_torso": "crop_upper_torso.png",
        "mid_torso": "crop_mid_torso.png",
        "waist": "crop_waist.png",
        "left_torso_edge": "crop_left_torso_edge.png",
        "right_torso_edge": "crop_right_torso_edge.png",
        "placket_button": "crop_placket_button.png",
        "torso_sleeve_boundary": "crop_torso_sleeve_boundary.png",
        "bottom_hem_boundary": "crop_bottom_hem_boundary.png",
    }
    for key, fname in crop_names.items():
        crop_strip(imgs, crop_boxes_map[key], out / fname, ["carrier", "legacy", "replay", "original_recolor", "refined_recolor"])

    # QC metrics
    qc_results: dict[str, dict] = {"provider_calls": 0, "images": {}}
    for key in imgs:
        if key == "source_front":
            qc_results["images"][key] = {"torso": {"available": False, "reason": "source_not_in_carrier_space"}}
        else:
            qc_results["images"][key] = {"torso": {"available": False, "reason": "target_period_low_confidence"}}

    # simpler direct metrics for original vs refined
    x0, y0, x1, y1 = bbox(old_mask | corrected_vis)
    torso_w = max(1, x1 - x0)
    torso_h = max(1, y1 - y0)
    collar_proxy = np.zeros_like(old_mask, dtype=np.uint8)
    cv2.rectangle(collar_proxy, (x0 + int(torso_w * 0.28), y0), (x0 + int(torso_w * 0.72), y0 + int(torso_h * 0.18)), 255, -1)
    sleeve_panels = [p for p in panel_map.panels if p.name.startswith("sleeve")]
    sleeve_stats = {}
    for side, panel in (("left", sleeve_panels[0] if len(sleeve_panels) > 0 else None), ("right", sleeve_panels[1] if len(sleeve_panels) > 1 else None)):
        if panel is None:
            sleeve_stats[side] = {"old": None, "corrected": None, "reduction": None}
            continue
        poly = poly_to_mask(panel.quad, old_mask.shape)
        old_hit = int(((old_mask > 0) & (poly > 0)).sum())
        corr_hit = int(((corrected_mask > 0) & (poly > 0)).sum())
        sleeve_stats[side] = {"old": old_hit, "corrected": corr_hit, "reduction": old_hit - corr_hit}
    collar_old = int(((old_mask > 0) & (collar_proxy > 0)).sum())
    collar_corr = int(((corrected_mask > 0) & (collar_proxy > 0)).sum())
    metric_table = {}
    for name, out_img, recol_mask, pres_mask in [
        ("Original recolor", original_recolor, original_recolor_mask, preserved_mask),
        ("Refined recolor", refined_recolor, refined_recolor_mask, refined_preserved_mask),
    ]:
        local_scope = ((recol_mask > 0) | (pres_mask > 0)).astype(np.uint8) * 255
        recolored_fraction = float((recol_mask > 0).sum()) / max(1, int((local_scope > 0).sum()))
        preserved_fraction = float((pres_mask > 0).sum()) / max(1, int((local_scope > 0).sum()))
        boundary = hc_qc._boundary_chroma(out_img, recol_mask, garment_mask, band_px=int(max(3, np.ptp(torso_panel.quad[:, 0]) / 12)))
        drape = hc_qc._drape_preservation(out_img, carrier, garment_mask)
        source_de = masked_median_delta_e_to_source(out_img, source, recol_mask)
        background = masked_median_delta_e(out_img, carrier, (garment_mask == 0).astype(np.uint8) * 255)
        protected = masked_median_delta_e(out_img, carrier, protected_mask)
        metric_table[name] = {
            "recolored_fraction": round(recolored_fraction, 4),
            "preserved_fraction": round(preserved_fraction, 4),
            "protected_region_drift": None if protected is None else round(protected, 3),
            "background_drift": None if background is None else round(background, 3),
            "mannequin_body_drift": None,
            "low_frequency_shading_correlation": None if not drape.get("drape_measurable") else drape.get("drape_corr"),
            "source_palette_delta_e": None if source_de is None else round(source_de, 3),
            "boundary_l_step_p95": boundary.get("boundary_l_step_p95"),
            "boundary_chroma_de00_median": boundary.get("boundary_chroma_de00_median"),
            "local_orientation_similarity": None,
            "local_period_preservation_error": None,
            "painted_preserved_boundary_period_jump": None,
            "boundary_orientation_jump": None,
            "detection_outside_torso_pixels": int(((original_detection_mask > 0) & (corrected_mask == 0)).sum()) if name == "Original recolor" else int(((det_mask > 0) & (corrected_mask == 0)).sum()),
        }

    status = "MASK_INVARIANT_FAILED" if failures else "REFINED_RECOLOR_REVIEW_READY"

    audit_json = {
        "provider_calls": 0,
        "status": status,
        "mask_audit": invariant_json,
        "old_vs_refined": {
            "old_torso_iou_vs_garment": round(iou(old_mask, garment_mask), 4),
            "corrected_torso_iou_vs_garment": round(iou(corrected_mask, garment_mask), 4),
            "old_pixel_count": int((old_mask > 0).sum()),
            "corrected_pixel_count": int((corrected_mask > 0).sum()),
            "removed_pixel_count": int((removed > 0).sum()),
            "added_pixel_count": int((added > 0).sum()),
            "connected_component_count": largest_component_fraction(corrected_mask)[0],
            "largest_component_fraction": round(largest_component_fraction(corrected_mask)[1], 4),
            "sleeve_contamination_reduction": {
                "left_old": sleeve_stats["left"]["old"],
                "left_corrected": sleeve_stats["left"]["corrected"],
                "left_reduction": sleeve_stats["left"]["reduction"],
                "right_old": sleeve_stats["right"]["old"],
                "right_corrected": sleeve_stats["right"]["corrected"],
                "right_reduction": sleeve_stats["right"]["reduction"],
            },
            "collar_contamination_reduction": {
                "old": collar_old,
                "corrected": collar_corr,
                "reduction": collar_old - collar_corr,
            },
            "placket_protected_overlap": int(rel(corrected_mask, protected_mask)),
            "mannequin_background_contamination": int(((old_mask > 0) & (garment_mask == 0)).sum()),
        },
        "metrics": metric_table,
        "qc": qc_results,
    }
    (out / "numeric_comparison_refined.json").write_text(json.dumps(audit_json, indent=2), encoding="utf-8")

    images_for_html = {p.name: p for p in out.iterdir() if p.suffix.lower() == ".png"}
    render_html(out, status, audit_json["mask_audit"], audit_json["metrics"], images_for_html, [])

    print(f"wrote outputs to {out}")
    print(status)


if __name__ == "__main__":
    main()
