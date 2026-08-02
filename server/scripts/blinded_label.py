"""Blinded labeling 도구 — 로컬 전용, 사람이 실제로 눌러 라벨을 남긴다.

테스트용 함수만 두면 라벨은 영원히 0 건이다. 실제로 30쌍을 볼 수 있어야 한다.

    python scripts/blinded_label.py --dataset-dir <out>/<datasetId> --reviewer-id me

브라우저에서 http://127.0.0.1:8799 로 연다. loopback 에만 바인딩하고, 운영 API·R2 를
전혀 부르지 않으며, 화면에는 기계 판정과 QC 근거가 나가지 않는다(요청·원본·결과만).
저장 직전에 결과 이미지 SHA 를 다시 계산해 표본과 맞는지 확인한다 — 파일이 바뀌었으면
그 라벨은 다른 이미지에 대한 판단이 된다.

이 산출물은 **로컬 캘리브레이션 감사 기록**이지 운영 승인 이력이 아니다.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import pathlib
import re
import secrets
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import blinded_audit as ba  # noqa: E402

PAGE = """<!doctype html><meta charset=utf-8><title>blinded labeling</title>
<style>
body{font:14px/1.5 system-ui;margin:0;padding:20px;background:#fafafa;color:#111}
.wrap{max-width:1080px;margin:0 auto}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:14px 0}
figure{margin:0}figcaption{font-weight:600;margin-bottom:6px}
img{width:100%;border-radius:10px;background:#eee;display:block}
button{padding:9px 16px;border-radius:8px;border:1px solid #d4d4d8;background:#fff;cursor:pointer;font-size:14px}
button.pass{border-color:#16a34a;color:#166534}button.fail{border-color:#dc2626;color:#991b1b}
.bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.muted{color:#71717a}
</style>
<div class=wrap>
<h2>편집 결과 검수 (blinded)</h2>
<script>const NONCE=%%NONCE%%;</script>
<p class=muted>기계 판정은 보이지 않습니다. 원본과 결과를 보고 <b>같은 상품인가</b>만 판단해 주세요.</p>
<div class=bar>
  <span id=prog></span><span class=muted id=req></span>
</div>
<div class=pair>
  <figure><figcaption>원본</figcaption><img id=src></figure>
  <figure><figcaption>결과</figcaption><img id=out></figure>
</div>
<div class=bar>
  <button onclick="prev()">← 이전</button>
  <button class=pass onclick="label('fidelity_pass')">같은 상품 (fidelity_pass)</button>
  <button class=fail onclick="label('fidelity_fail')">달라짐 (fidelity_fail)</button>
  <button onclick="next()">다음 →</button>
  <span class=muted id=state></span>
</div>
</div>
<script>
let items=[],i=0,done={};
async function boot(){const r=await fetch('/api/items');const d=await r.json();
  items=d.items;done=d.labeled;render();}
function render(){if(!items.length)return;const it=items[i];
  document.getElementById('prog').textContent=`${i+1} / ${items.length} · 라벨 ${Object.keys(done).length}건`;
  document.getElementById('req').textContent=` · 요청: ${it.editType} / ${it.requestedChanges}`;
  document.getElementById('src').src='/img/source/'+encodeURIComponent(it.sourceImage);
  document.getElementById('out').src='/img/result/'+encodeURIComponent(it.resultImage);
  document.getElementById('state').textContent=done[it.sampleId]?('기록됨: '+done[it.sampleId]):'';}
function next(){i=Math.min(items.length-1,i+1);render();}
function prev(){i=Math.max(0,i-1);render();}
async function label(v){const it=items[i];
  const r=await fetch('/api/label',{method:'POST',
    headers:{'Content-Type':'application/json','X-QA-Nonce':NONCE},
    body:JSON.stringify({sampleId:it.sampleId,label:v})});
  const d=await r.json();
  if(d.ok){done[it.sampleId]=v;next();}else{alert('저장 실패: '+d.error);}}
document.addEventListener('keydown',e=>{if(e.key==='ArrowRight')next();if(e.key==='ArrowLeft')prev();});
boot();
</script>"""


# sampleId 는 파일 이름이 된다. 최소 문자 집합으로 조여 경로가 될 여지를 없앤다.
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# source 는 예시 이미지 파일명이다. 디렉터리 구분자도 상위 참조도 필요 없다.
_SAFE_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class UnsafePath(Exception):
    """허용 경계를 벗어난 요청. 파일 내용은 물론 경로도 응답에 싣지 않는다."""


def _safe_name(name: str, pattern) -> str:
    """이름 자체를 먼저 조인다 — 경로를 만들기 전에 막는 게 가장 확실하다."""
    if not isinstance(name, str) or "\x00" in name:
        raise UnsafePath("bad name")
    if not pattern.match(name):
        raise UnsafePath("bad name")
    if name in (".", "..") or "/" in name or "\\" in name:
        raise UnsafePath("bad name")
    return name


def safe_resolve(base: pathlib.Path, name: str, pattern) -> pathlib.Path:
    """base 안의 일반 파일만 돌려준다. GET·POST 가 **같은** 함수를 쓴다.

    한쪽만 검사하면 그쪽만 안전하다. 직전 구현이 그랬다 — GET 은 parents 를 봤지만
    POST 의 SHA 재검증은 경로를 그냥 이어 붙였다.

    resolve() 는 symlink 를 따라가므로, 링크로 밖을 가리켜도 실제 경로가 base 밖이면
    걸린다. 존재 여부보다 **경계**를 먼저 본다.
    """
    safe = _safe_name(name, pattern)
    root = base.resolve(strict=False)
    if pattern is _SAFE_ID and not safe.endswith(".png"):
        safe = f"{safe}.png"        # 결과 이미지는 <sampleId>.png 로 저장된다
    target = (root / safe).resolve(strict=False)
    if target != root and root not in target.parents:
        raise UnsafePath("outside base")
    if not target.is_file():
        raise UnsafePath("not a regular file")
    return target


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _resolve_dataset_id(dataset_dir: pathlib.Path, explicit: str | None,
                        manifest_path: str | None) -> str:
    """디렉터리 이름으로 추정하지 않는다.

    디렉터리는 옮기거나 이름을 바꿀 수 있고, 그 순간 라벨이 다른 데이터셋에 묶인다.
    라벨은 (datasetId, sampleId) 로 결합되므로 이 값이 틀리면 전부 격리되거나 —
    더 나쁘게 — 남의 데이터셋에 붙는다.
    """
    if explicit:
        return explicit.strip()
    mpath = pathlib.Path(manifest_path) if manifest_path else (
        dataset_dir / "manifest.json")
    if mpath.exists():
        did = json.loads(mpath.read_text(encoding="utf-8")).get("datasetId")
        if did:
            return str(did)
    raise SystemExit(
        "datasetId 를 알 수 없어요. --dataset-id 로 주거나 --manifest 를 지정하세요 "
        "(디렉터리 이름으로 추정하지 않습니다).")


def serve(dataset_dir: pathlib.Path, reviewer_id: str, port: int, *,
          dataset_id: str, nonce: str) -> int:
    samples_path = dataset_dir / "samples.jsonl"
    if not samples_path.exists():
        raise SystemExit(f"samples.jsonl 이 없어요: {samples_path}")
    rows = [json.loads(l) for l in samples_path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    labels_path = dataset_dir / "labels.jsonl"
    src_dir = pathlib.Path(__file__).resolve().parents[2] / "public" / "assets" / "fit-examples"
    by_id = {str(r.get("id")): r for r in rows}
    # presentation() 이 화이트리스트 + blinded 검증을 함께 한다.
    items = [ba.presentation(r) for r in rows if r.get("output_id")]

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):        # 조용히
            pass

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/":
                page = PAGE.replace("%%NONCE%%", json.dumps(nonce))
                return self._send(200, page.encode(), "text/html; charset=utf-8")
            if path == "/api/items":
                eff = ba.effective_labels(ba.load_labels(str(labels_path)))
                labeled = {sid: v["label"] for (ds, sid), v in eff.items()
                           if ds == dataset_id}
                return self._send(200, json.dumps(
                    {"items": items, "labeled": labeled}).encode())
            if path.startswith("/img/source/"):
                try:
                    f = safe_resolve(src_dir,
                                     urllib.parse.unquote(path[len("/img/source/"):]),
                                     _SAFE_SOURCE)
                except UnsafePath:
                    return self._send(404, b"{}")
                return self._send(200, f.read_bytes(), "image/jpeg")
            if path.startswith("/img/result/"):
                raw = urllib.parse.unquote(path[len("/img/result/"):])
                try:
                    f = safe_resolve(dataset_dir,
                                     raw[:-4] if raw.endswith(".png") else raw,
                                     _SAFE_ID)
                except UnsafePath:
                    return self._send(404, b"{}")
                if f.suffix != ".png":
                    return self._send(404, b"{}")
                return self._send(200, f.read_bytes(), "image/png")
            return self._send(404, b"{}")

        def do_POST(self):
            if urllib.parse.urlparse(self.path).path != "/api/label":
                return self._send(404, b"{}")
            # 로컬 서버라도 다른 페이지가 이 포트로 POST 를 쏠 수 있다(브라우저는
            # 폼·fetch 로 cross-origin 요청을 보낸다). 세 겹으로 막는다:
            # 프로세스마다 새로 만드는 nonce, Origin 검사, JSON content-type 강제.
            if self.headers.get("X-QA-Nonce") != nonce:
                return self._send(403, json.dumps({"ok": False, "error": "bad nonce"}).encode())
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}",
                                         f"http://localhost:{port}"):
                return self._send(403, json.dumps({"ok": False, "error": "bad origin"}).encode())
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype != "application/json":
                return self._send(415, json.dumps(
                    {"ok": False, "error": "content-type must be application/json"}).encode())
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            sid, lab = str(body.get("sampleId")), body.get("label")
            row = by_id.get(sid)
            if row is None:
                return self._send(400, json.dumps({"ok": False, "error": "unknown sample"}).encode())
            # 저장 직전 재검증 — 원본이든 결과든 파일이 바뀌었으면 그 라벨은
            # 화면에서 본 것과 다른 이미지에 대한 판단이 된다.
            prov = row.get("provenance") or {}
            try:
                img = safe_resolve(dataset_dir, sid, _SAFE_ID)
                src = safe_resolve(src_dir, str(row.get("source") or ""), _SAFE_SOURCE)
            except UnsafePath:
                # 경계 밖은 읽지도 않는다 — 사유만 돌려주고 경로·내용은 싣지 않는다.
                return self._send(400, json.dumps(
                    {"ok": False, "error": "unsafe path"}).encode())
            if _sha(img) != prov.get("outputSha256"):
                return self._send(409, json.dumps(
                    {"ok": False, "error": "output sha mismatch"}).encode())
            if _sha(src) != prov.get("sourceSha256"):
                return self._send(409, json.dumps(
                    {"ok": False, "error": "source sha mismatch"}).encode())
            try:
                rec = ba.make_label(sample=row, label=lab, reviewer_id=reviewer_id,
                                    dataset_id=dataset_id)
                ba.append_label(str(labels_path), rec)
            except Exception as e:                        # noqa: BLE001
                return self._send(400, json.dumps({"ok": False, "error": str(e)}).encode())
            return self._send(200, json.dumps({"ok": True}).encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)   # loopback 전용
    print(f"blinded labeling — http://127.0.0.1:{port}  (dataset={dataset_id}, "
          f"reviewer={reviewer_id}, {len(items)}건)")
    print(f"라벨 파일: {labels_path}  · 로컬 캘리브레이션 감사 기록(운영 승인 이력 아님)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="blinded labeling (local only)")
    ap.add_argument("--dataset-dir", required=True)
    ap.add_argument("--reviewer-id", required=True)
    ap.add_argument("--dataset-id", help="없으면 manifest.datasetId 를 읽는다")
    ap.add_argument("--manifest", help="manifest.json 경로 (기본: <dataset-dir>/manifest.json)")
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()
    d = pathlib.Path(a.dataset_dir).resolve()
    return serve(d, a.reviewer_id.strip(), a.port,
                 dataset_id=_resolve_dataset_id(d, a.dataset_id, a.manifest),
                 nonce=secrets.token_urlsafe(24))


if __name__ == "__main__":
    raise SystemExit(main())
