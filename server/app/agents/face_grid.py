"""실존 모델 grid_sedcard 합성 — 업로드 3장을 2×2 아이덴티티 대조 시트로.

Gemini 생성이 아니다(handoff §03 "얼굴 새로 생성 금지"). 실사진을 정사각 크롭해 배치만 한다.
4번째 칸은 중립(빈 회색) — MODEL SHEET 는 아이덴티티 참조 전용이고 컷 생성 프롬프트가
그리드 레이아웃·포즈 복제를 금지하므로, 4번째 칸을 원본 반복으로 채워 특정 포즈를 과대표집
하지 않는다(codex 리뷰 반영).
"""

import io

from PIL import Image

_CELL = 640  # 셀 한 변 → 1280×1280 그리드(컷 아이덴티티 앵커로 충분)
_NEUTRAL = (230, 230, 230)


def _fit_square(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    side = min(im.width, im.height)
    left = (im.width - side) // 2
    top = (im.height - side) // 2
    im = im.crop((left, top, left + side, top + side))
    return im.resize((_CELL, _CELL), Image.LANCZOS)


def compose_sedcard(images: list[bytes]) -> bytes:
    """앞 3장을 2×2(좌상·우상·좌하)로 배치, 우하는 중립. PNG bytes 반환."""
    if not images:
        raise ValueError("no images")
    cells = [_fit_square(d) for d in images[:3]]
    canvas = Image.new("RGB", (_CELL * 2, _CELL * 2), _NEUTRAL)
    positions = [(0, 0), (_CELL, 0), (0, _CELL)]
    for im, pos in zip(cells, positions):
        canvas.paste(im, pos)
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
