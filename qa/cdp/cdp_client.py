"""CDP 로 실 DOM 을 직접 조작한다(새 의존성 없음 — urllib + 소켓 websocket 최소 구현)."""
import base64, json, os, socket, struct, sys, time, urllib.request

def ws_connect(url):
    _, rest = url.split("://", 1)
    hostport, path = rest.split("/", 1)
    host, port = hostport.split(":")
    s = socket.create_connection((host, int(port)), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    return s

def send(s, payload):
    data = json.dumps(payload).encode()
    hdr = bytearray([0x81])
    n = len(data)
    mask = os.urandom(4)
    if n < 126: hdr.append(0x80 | n)
    elif n < 65536: hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
    else: hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
    hdr += mask
    s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

def recv(s):
    def rd(n):
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c: raise IOError("closed")
            b += c
        return b
    while True:
        h = rd(2)
        ln = h[1] & 0x7F
        if ln == 126: ln = struct.unpack(">H", rd(2))[0]
        elif ln == 127: ln = struct.unpack(">Q", rd(8))[0]
        payload = rd(ln)
        if h[0] & 0x0F == 1:
            return json.loads(payload.decode())

class CDP:
    def __init__(self, ws): self.s = ws_connect(ws); self.i = 0
    def call(self, method, **params):
        self.i += 1
        send(self.s, {"id": self.i, "method": method, "params": params})
        while True:
            m = recv(self.s)
            if m.get("id") == self.i:
                if "error" in m: raise RuntimeError(m["error"])
                return m.get("result", {})
    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True,
                      returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:300])
        return r["result"].get("value")

def page_ws():
    d = json.load(urllib.request.urlopen("http://localhost:9333/json/list"))
    for t in d:
        if t.get("type") == "page" and "qa-review-gate" in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    raise SystemExit("QA page not found")
