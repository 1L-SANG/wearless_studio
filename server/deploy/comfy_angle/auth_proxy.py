"""ComfyUI 앞 토큰 프록시 (표준 라이브러리만).
ComfyUI 자체엔 인증이 없어 127.0.0.1:8188 에만 띄우고, 밖(0.0.0.0:8000)은 이 프록시만 연다.
  GET /healthz          인증 없음 — 설치 단계 상태·ComfyUI 응답 여부만(이미지·경로 없음)
  그 외 모든 요청         Authorization: Bearer $FACE_RENDER_TOKEN 이 맞아야 8188 로 전달
토큰이 비었거나 RunPod 시크릿 자리표시자 그대로면 전부 401(실패 시 닫힘).
"""
import hashlib
import hmac
import http.server
import json
import os
import socketserver
import urllib.error
import urllib.parse
import urllib.request

TOKEN = os.environ.get("FACE_RENDER_TOKEN", "")
UP = "http://127.0.0.1:8188"
STATUS = "/root/setup_status.txt"


def _token_ok(header: str) -> bool:
    if not TOKEN or TOKEN.startswith("{{"):
        return False
    return hmac.compare_digest(header or "", "Bearer " + TOKEN)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _health(self):
        stages = []
        try:
            stages = [l.strip() for l in open(STATUS) if l.strip()][-20:]
        except OSError:
            pass
        comfy = False
        try:
            with urllib.request.urlopen(UP + "/system_stats", timeout=3) as r:
                comfy = r.status == 200
        except Exception:
            comfy = False
        self._send(200, json.dumps({"comfy": comfy, "stages": stages}).encode())

    def _logs(self):
        out = {}
        for name in ("/root/comfy_setup.log", "/root/comfy.log"):
            try:
                out[name] = open(name, errors="replace").read()[-6000:]
            except OSError:
                out[name] = None
        self._send(200, json.dumps(out).encode())

    def _forward(self, method):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        req = urllib.request.Request(UP + self.path, data=body, method=method)
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                self._send(r.status, r.read(), r.headers.get("Content-Type"))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read(), e.headers.get("Content-Type"))
        except Exception as e:  # noqa: BLE001
            self._send(502, json.dumps({"error": type(e).__name__}).encode())

    def _fetch_lora(self):
        """POST /_fetch_lora {"url","sha256","name"} — LoRA 를 요청마다 받은 presigned R2 URL 로 받아
        sha256 확인 후 models/loras/<name> 에 둔다. URL 은 env·로그에 남기지 않는다(요청 본문에만)."""
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            url, want, name = req["url"], req["sha256"].lower(), os.path.basename(req["name"])
        except Exception:  # noqa: BLE001
            return self._send(400, b'{"error":"bad_request"}')
        host = urllib.parse.urlparse(url).hostname or ""
        if not url.startswith("https://") or not host.endswith(".r2.cloudflarestorage.com") or not name.endswith(".safetensors"):
            return self._send(400, b'{"error":"bad_url_or_name"}')
        dest_dir = "/root/ComfyUI/models/loras"
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        if os.path.exists(dest):
            return self._send(200, json.dumps({"ok": True, "cached": True, "size": os.path.getsize(dest)}).encode())
        tmp = dest + ".part"
        h = hashlib.sha256()
        try:
            with urllib.request.urlopen(url, timeout=600) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
                    f.write(chunk)
        except Exception as e:  # noqa: BLE001
            if os.path.exists(tmp):
                os.remove(tmp)
            return self._send(502, json.dumps({"error": "download_failed", "type": type(e).__name__}).encode())
        if h.hexdigest() != want:
            os.remove(tmp)
            return self._send(422, b'{"error":"sha256_mismatch"}')
        os.replace(tmp, dest)
        return self._send(200, json.dumps({"ok": True, "cached": False, "size": os.path.getsize(dest)}).encode())

    def _route(self, method):
        if self.path.startswith("/healthz"):
            return self._health()
        if not _token_ok(self.headers.get("Authorization", "")):
            return self._send(401, b'{"error":"unauthorized"}')
        if self.path.startswith("/_logs"):
            return self._logs()
        if self.path.startswith("/_fetch_lora") and method == "POST":
            return self._fetch_lora()
        return self._forward(method)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    Server(("0.0.0.0", int(os.environ.get("PROXY_PORT", "8000"))), Handler).serve_forever()
