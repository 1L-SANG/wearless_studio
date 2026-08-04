"""stripe protected-composite QA 리포트 생성기 (로컬 전용, provider 호출 0).

수집 산출물(마스크·지표·판정)을 하나의 HTML 로 묶는다. 성공 여부와 무관하게 항상
생성한다 — 실패한 시도도 증거로 남겨야 다음 판단이 가능하다.

민감정보 정책: 이미지는 축소 후 **재인코딩**해 EXIF/GPS 를 떨어뜨리고, signed URL·토큰·
프롬프트 전문·provider 원문 오류·SQL 은 싣지 않는다. 원본 픽셀은 저장소에 커밋하지
않으며 이 스크립트는 ab_out(ignored) 아래에만 쓴다.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import sys
from pathlib import Path

import cv2
import numpy as np

MAX_EDGE = 900          # 임베드 이미지 최대 변 — 리포트 크기와 가독성의 절충
JPEG_QUALITY = 82


def _strip_and_embed(img: np.ndarray | None, *, gray: bool = False) -> str | None:
    """축소 + 재인코딩 → data URI. 재인코딩이 EXIF/GPS 를 제거한다."""
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    scale = min(1.0, MAX_EDGE / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _read(path: str | Path | None) -> np.ndarray | None:
    if not path:
        return None
    p = Path(path)
    return cv2.imread(str(p)) if p.exists() else None


def _sha256_file(path: str | Path | None) -> str | None:
    if not path or not Path(path).exists():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fig(title: str, uri: str | None, note: str = "") -> str:
    if not uri:
        return (f'<figure class="missing"><figcaption>{html.escape(title)}</figcaption>'
                f'<div class="ph">산출물 없음</div></figure>')
    n = f'<p class="note">{html.escape(note)}</p>' if note else ""
    return (f'<figure><img src="{uri}" alt="{html.escape(title)}">'
            f'<figcaption>{html.escape(title)}</figcaption>{n}</figure>')


def _metric_rows(metrics: dict, gates: dict) -> str:
    rows = []
    for key, value in metrics.items():
        if isinstance(value, (dict, list)):
            continue
        gate = gates.get(key)
        verdict, cls = "", ""
        if gate is not None and isinstance(value, (int, float)):
            lo, hi = gate
            ok = (lo is None or value >= lo) and (hi is None or value <= hi)
            verdict = "통과" if ok else "위반"
            cls = "ok" if ok else "bad"
        rows.append(
            f"<tr><td><code>{html.escape(str(key))}</code></td>"
            f"<td>{html.escape(str(value))}</td>"
            f"<td>{html.escape(str(gate)) if gate else '—'}"
            f"<br><span class='note'>{html.escape(GATE_BASIS.get(key, '근거 미기록 — 잠정'))}</span></td>"
            f'<td class="{cls}">{verdict}</td></tr>')
    return "\n".join(rows) or '<tr><td colspan="4">지표 없음</td></tr>'


GATES = {
    "period_rel_err_max": (None, 0.15),
    "repeat_count_rel_err_max": (None, 0.15),
    "direction_error_max": (None, 0.10),
    "color_delta_e00_max": (None, 16.0),
    "outside_drift_frac": (None, 0.01),
    "seam_ramp_excess": (None, 1.6),
    "seam_grad_norm": (None, 0.35),
    "boundary_chroma_severe_frac": (None, 0.02),
    "drape_local_amp_p2": (0.30, None),
    "boundary_chroma_de00": (None, 14.0),
    "drape_amp_ratio": (0.55, None),
    "drape_corr": (0.0, None),
}

# 임계의 출처를 리포트에 함께 싣는다 — 숫자만 보면 캘리브레이션된 것처럼 읽힌다.
GATE_BASIS = {
    "seam_ramp_excess": "정상 0.822 / 계단 2.0~6.67 (위치별)",
    "seam_grad_norm": "정상 0.097~0.099 / 계단 0.5 (진폭·위치 무관)",
    "boundary_chroma_severe_frac": "정상 0.0 (6종) / 국소 단절 시 발화",
    "drape_local_amp_p2": "정상 0.395~0.462 / 타일 미만 평탄화 0.082",
    "boundary_chroma_de00": "정상 2.95~9.31 / 주입 불연속 중앙값 23",
    "drape_amp_ratio": "정상 최악 타일 0.757~0.981 / 45% 평탄화 0.39",
    "drape_corr": "정상 0.298 이상 / 접힘 반전 -0.84 (0 은 방향 판정선)",
    "period_rel_err_max": "합성 fixture 집계 게이트",
    "outside_drift_frac": "설계상 0 — mask 밖은 carrier 와 동일해야 한다",
}


def build(spec: dict) -> str:
    a = spec.get("attempts", [])
    verdict = spec.get("final_verdict", "rejected")
    v_cls = "pass" if verdict == "usable" else "fail"

    attempts_html = []
    for i, at in enumerate(a, 1):
        imgs = "\n".join(
            _fig(t, _strip_and_embed(_read(p)))
            for t, p in (at.get("images") or {}).items())
        attempts_html.append(f"""
<section>
  <h2>시도 {i} — {html.escape(str(at.get('label', '')))}</h2>
  <p class="meta">판정 <strong class="{'pass' if at.get('usable') else 'fail'}">
     {'사용 가능' if at.get('usable') else '거절'}</strong>
     · 실패 사유 <code>{html.escape(str(at.get('failure_reason') or '—'))}</code></p>
  <div class="grid">{imgs}</div>
  <table><thead><tr><th>지표</th><th>값</th><th>게이트</th><th>판정</th></tr></thead>
  <tbody>{_metric_rows(at.get('metrics') or {}, GATES)}</tbody></table>
  <h3>육안 검수</h3>
  <ul>{''.join(f'<li>{html.escape(x)}</li>' for x in at.get('visual', []))}</ul>
</section>""")

    prov = spec.get("provenance", {})
    prov_rows = "\n".join(
        f"<tr><td>{html.escape(str(k))}</td><td><code>{html.escape(str(v))}</code></td></tr>"
        for k, v in prov.items())

    refs = "\n".join(
        _fig(t, _strip_and_embed(_read(p)))
        for t, p in (spec.get("references") or {}).items())

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Stripe Protected Composite — QA 리포트</title>
<style>
 body{{font:15px/1.65 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;
   margin:0;padding:32px;max-width:1180px;margin-inline:auto;color:#16181d;background:#fbfbfc}}
 h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:19px;margin:36px 0 10px}}
 h3{{font-size:15px;margin:20px 0 6px;color:#444}}
 .verdict{{display:inline-block;padding:6px 14px;border-radius:999px;font-weight:600}}
 .verdict.pass{{background:#e6f6ec;color:#0f6b32}} .verdict.fail{{background:#fdeaea;color:#a11}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}}
 figure{{margin:0;background:#fff;border:1px solid #e6e8ec;border-radius:10px;padding:8px}}
 figure img{{width:100%;height:auto;display:block;border-radius:6px;background:#f0f0f2}}
 figcaption{{font-size:12px;color:#555;margin-top:6px;text-align:center}}
 .ph{{height:150px;display:grid;place-items:center;color:#aaa;background:#f4f4f6;border-radius:6px}}
 .missing{{opacity:.6}} .note{{font-size:11px;color:#888;margin:4px 0 0;text-align:center}}
 table{{width:100%;border-collapse:collapse;margin-top:12px;background:#fff;
   border:1px solid #e6e8ec;border-radius:10px;overflow:hidden}}
 th,td{{padding:8px 10px;border-bottom:1px solid #eef0f3;text-align:left;font-size:13px}}
 th{{background:#f6f7f9;font-weight:600}} td.ok{{color:#0f6b32;font-weight:600}}
 td.bad{{color:#a11;font-weight:600}} code{{font-size:12px;background:#f2f3f5;padding:1px 5px;border-radius:4px}}
 .meta{{color:#555;font-size:13px}} ul{{margin:6px 0 0 18px;padding:0}} li{{margin:3px 0}}
 .cav{{background:#fff8e6;border:1px solid #f0dfae;border-radius:10px;padding:12px 16px;margin:18px 0}}
</style>
<h1>Stripe Protected Composite — QA 리포트</h1>
<p class="meta">{html.escape(str(spec.get('subtitle', '')))}</p>
<p><span class="verdict {v_cls}">최종: {'사용 가능' if verdict == 'usable' else '거절 — 원본 실사 Hero 유지'}</span></p>

<div class="cav"><strong>판정 규칙</strong> — 수치 QC 통과만으로는 사용 가능이라 하지 않는다.
결정론 QC · 프레임 QC · 독립 육안 검수를 모두 통과해야 사용 가능이다.
거절된 결과는 저장·출고·캔버스 삽입·baseline 승격 대상이 아니며, 사용자에게는
원본 실사 Hero 와 재생성만 제공된다.</div>

<h2>기준 자료</h2>
<div class="grid">{refs}</div>
{''.join(attempts_html)}

<h2>실행·계보</h2>
<table><tbody>{prov_rows}</tbody></table>

<h2>Codex 독립 검증</h2>
<pre style="white-space:pre-wrap;background:#fff;border:1px solid #e6e8ec;border-radius:10px;
 padding:14px;font-size:12.5px">{html.escape(str(spec.get('codex', '(미실행)')))}</pre>
"""


def main() -> int:
    spec_path = Path(sys.argv[1])
    out = Path(sys.argv[2])
    spec = json.loads(spec_path.read_text())
    for name, path in (spec.get("references") or {}).items():
        spec.setdefault("provenance", {})[f"sha256[{name}]"] = _sha256_file(path) or "—"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(spec))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
