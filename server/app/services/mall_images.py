"""Deterministic, local image preparation. No image inference or external URLs."""
from io import BytesIO

from PIL import Image, ImageOps


def prepare_mall_jpeg(raw: bytes, *, ratio: tuple[int, int], max_side: int = 2000,
                      max_bytes: int = 5000000) -> bytes:
    if len(ratio) != 2 or any(type(n) is not int or n <= 0 for n in ratio) or max_side <= 0 or max_bytes <= 0:
        raise ValueError('invalid image limits')
    with Image.open(BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        width, height = image.size
        target = ratio[0] / ratio[1]
        if width / height > target:
            width = max(1, round(height * target))
        else:
            height = max(1, round(width / target))
        scale = min(1, max_side / max(width, height))
        image = ImageOps.fit(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                             method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        while True:
            for quality in (90, 80, 65, 50, 35):
                output = BytesIO()
                image.save(output, format='JPEG', quality=quality, optimize=True)
                if output.tell() <= max_bytes:
                    return output.getvalue()
            if max(image.size) <= 1:
                raise ValueError('image exceeds upload limit')
            image.thumbnail((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.LANCZOS)
