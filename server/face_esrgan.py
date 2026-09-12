"""RealESRGAN x4plus — 얼굴 크롭 전용 확대기 (GPU 파드에서만 돈다).

face_render_service.py 와 같은 자리에 둔다 — app/ 아래가 아니다. app/ 은 torch 를 임포트하지 않는다는
경계(test_the_main_app_still_has_no_torch)가 있고, 이 모듈은 파드에서만 도는 코드라 그 경계 밖이 맞다.
basicsr·realesrgan 패키지는 쓰지 않는다 — 그 둘은 torchvision<0.17 의 functional_tensor 를 임포트해서
설치 후 소스를 고쳐야 하고, numpy 를 1.x 로 되돌린다. 파드 venv 에는 이미 torch·diffusers 가 올라가 있어
그 위험을 감수할 이유가 없다. 그래서 RRDBNet 만 여기 옮겨 담고 공식 가중치를 그대로 읽는다.

가중치는 bootstrap 이 받아 sha256 을 대조해 둔다(deploy/bootstrap.sh). 없으면 확대기는 안 뜨고
호출자는 Lanczos 로 간다 — 확대기는 어디까지나 마감 개선이지 필수 경로가 아니다.
"""

from __future__ import annotations

import hashlib
import logging
import os

import numpy as np
from PIL import Image

log = logging.getLogger("wearless.face_esrgan")

#: 공식 릴리스 파일. bootstrap 이 이 값으로 받은 파일을 검증한다.
WEIGHTS_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
WEIGHTS_SHA256 = "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
DEFAULT_WEIGHTS_PATH = "/root/face_render/weights/RealESRGAN_x4plus.pth"
#: 이 모델은 4배 고정. 그보다 작은 배율은 4배로 올린 뒤 Lanczos 로 줄인다(RealESRGANer.outscale 과 같다).
NATIVE_SCALE = 4
#: 타일 크기·여유. 큰 크롭에서 활성값이 GPU 를 다 먹지 않게(실험에서 쓴 값 그대로).
TILE = 512
TILE_PAD = 10


#: 아키텍처 상수 — 공식 x4plus 체크포인트가 이 모양이다(블록 23 · 채널 64 · 성장 32).
NUM_FEAT, NUM_BLOCK, GROW = 64, 23, 32


def weights_path() -> str:
    return os.getenv("FACE_RENDER_ESRGAN_WEIGHTS") or DEFAULT_WEIGHTS_PATH


def expected_state_dict_keys() -> set[str]:
    """_build_net 이 만드는 파라미터 이름 전부. torch 없이 체크포인트와 대조할 수 있게 따로 둔다.

    키가 어긋나면 load_state_dict(strict=True) 가 파드에서만 터지고, 그 뒤는 조용히 Lanczos 로
    떨어져 아무도 모른다 — 그래서 이름 목록을 코드로 들고 테스트가 실물 파일과 맞춰 본다.
    """
    keys = {f"{n}.{s}" for n in ("conv_first", "conv_body", "conv_up1", "conv_up2", "conv_hr", "conv_last")
            for s in ("weight", "bias")}
    for b in range(NUM_BLOCK):
        for rdb in ("rdb1", "rdb2", "rdb3"):
            for c in range(1, 6):
                keys |= {f"body.{b}.{rdb}.conv{c}.{s}" for s in ("weight", "bias")}
    return keys


def _build_net(torch):
    """basicsr 의 RRDBNet(x4plus: 23블록·64feat·32grow) — 공식 체크포인트 키 이름 그대로."""
    import torch.nn as nn
    import torch.nn.functional as F

    class ResidualDenseBlock(nn.Module):
        def __init__(self, nf=NUM_FEAT, gc=GROW):
            super().__init__()
            self.conv1 = nn.Conv2d(nf, gc, 3, 1, 1)
            self.conv2 = nn.Conv2d(nf + gc, gc, 3, 1, 1)
            self.conv3 = nn.Conv2d(nf + 2 * gc, gc, 3, 1, 1)
            self.conv4 = nn.Conv2d(nf + 3 * gc, gc, 3, 1, 1)
            self.conv5 = nn.Conv2d(nf + 4 * gc, nf, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            x1 = self.lrelu(self.conv1(x))
            x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
            x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
            x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
            x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
            return x5 * 0.2 + x

    class RRDB(nn.Module):
        def __init__(self, nf, gc=GROW):
            super().__init__()
            self.rdb1 = ResidualDenseBlock(nf, gc)
            self.rdb2 = ResidualDenseBlock(nf, gc)
            self.rdb3 = ResidualDenseBlock(nf, gc)

        def forward(self, x):
            return self.rdb3(self.rdb2(self.rdb1(x))) * 0.2 + x

    class RRDBNet(nn.Module):
        def __init__(self, nf=NUM_FEAT, nb=NUM_BLOCK, gc=GROW):
            super().__init__()
            self.conv_first = nn.Conv2d(3, nf, 3, 1, 1)
            self.body = nn.Sequential(*[RRDB(nf, gc) for _ in range(nb)])
            self.conv_body = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up1 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_up2 = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_hr = nn.Conv2d(nf, nf, 3, 1, 1)
            self.conv_last = nn.Conv2d(nf, 3, 3, 1, 1)
            self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

        def forward(self, x):
            feat = self.conv_first(x)
            feat = feat + self.conv_body(self.body(feat))
            feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode="nearest")))
            feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode="nearest")))
            return self.conv_last(self.lrelu(self.conv_hr(feat)))

    return RRDBNet()


class Esrgan:
    """한 번 올려 두고 재사용. 없으면 만들지 않는다 — 호출자는 None 을 폴백 신호로 읽는다."""

    def __init__(self, path: str, device: str = "cuda"):
        import torch

        self.path = path
        self.device = device
        self.sha256 = _sha256_file(path)
        if self.sha256.lower() != WEIGHTS_SHA256:
            raise ValueError("esrgan weights sha256 mismatch")
        state = torch.load(path, map_location="cpu", weights_only=True)
        state = state.get("params_ema") or state.get("params") or state
        net = _build_net(torch)
        net.load_state_dict(state, strict=True)
        self.half = device.startswith("cuda")
        net.eval().to(device)
        if self.half:
            net.half()
        self.net = net

    def __call__(self, image: Image.Image, scale: int) -> Image.Image:
        """×scale 로 키운 RGB 이미지. scale < 4 면 4배로 올린 뒤 Lanczos 로 줄인다."""
        import torch

        rgb = image.convert("RGB")
        arr = np.asarray(rgb, np.float32) / 255.0
        t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(self.device)
        if self.half:
            t = t.half()
        with torch.no_grad():
            out = _tiled(self.net, t, TILE, TILE_PAD, NATIVE_SCALE)
        out = out.squeeze(0).float().clamp(0, 1).permute(1, 2, 0).cpu().numpy()
        big = Image.fromarray((out * 255.0 + 0.5).astype(np.uint8))
        want = (rgb.width * int(scale), rgb.height * int(scale))
        return big if big.size == want else big.resize(want, Image.LANCZOS)


def _tiled(net, t, tile: int, pad: int, scale: int):
    """타일로 나눠 돌린다. 타일보다 작으면 한 번에."""
    import torch

    _, _, h, w = t.shape
    if tile <= 0 or (h <= tile and w <= tile):
        return net(t)
    out = torch.zeros((t.shape[0], 3, h * scale, w * scale), dtype=t.dtype, device=t.device)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            y1, x1 = min(y + tile, h), min(x + tile, w)
            # 여유를 붙여 돌린 뒤 여유분을 잘라낸다 — 타일 경계에 이음선이 남지 않게.
            ys, xs = max(y - pad, 0), max(x - pad, 0)
            ye, xe = min(y1 + pad, h), min(x1 + pad, w)
            piece = net(t[:, :, ys:ye, xs:xe])
            top, left = (y - ys) * scale, (x - xs) * scale
            out[:, :, y * scale : y1 * scale, x * scale : x1 * scale] = piece[
                :, :, top : top + (y1 - y) * scale, left : left + (x1 - x) * scale]
    return out


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_STATE: dict = {"esrgan": None, "reason": "not_loaded"}


def get(device: str = "cuda") -> Esrgan | None:
    """확대기(없으면 None). 실패 사유는 한 번만 남기고 그 뒤로는 조용히 None 이다."""
    if _STATE["esrgan"] is not None:
        return _STATE["esrgan"]
    if _STATE["reason"] not in ("not_loaded",):
        return None
    path = weights_path()
    if not os.path.exists(path):
        _STATE["reason"] = "weights_missing"
        log.warning("face_esrgan: 가중치가 없다(%s) — 얼굴 크롭 확대 없이 간다", path)
        return None
    try:
        _STATE["esrgan"] = Esrgan(path, device)
        _STATE["reason"] = "ok"
        log.info("face_esrgan ready sha=%s", _STATE["esrgan"].sha256[:12])
    except Exception as exc:  # noqa: BLE001 — 확대기가 없다고 서비스가 죽어선 안 된다
        _STATE["reason"] = f"load_failed:{type(exc).__name__}"
        log.warning("face_esrgan 적재 실패(%s) — 얼굴 크롭 확대 없이 간다", type(exc).__name__)
        return None
    return _STATE["esrgan"]


def status() -> dict:
    esr = _STATE["esrgan"]
    return {"available": esr is not None, "reason": _STATE["reason"],
            "weights_sha12": esr.sha256[:12] if esr is not None else None}
