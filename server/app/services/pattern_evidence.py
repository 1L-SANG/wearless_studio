"""Evidence presentation for the specialized pattern judges. Cropping only — no decisions.

The live stripe shirt passed a judge that had genuinely looked at it. The judge was shown one
3392x5056 full-body frame, and whatever the provider's resize does to that frame, what
survives of a 6-pixel-wide stripe group is "there are vertical lines". At that scale a dense
uniform pinstripe and a grouped taupe/blue multi-stripe are the same picture.

So this module makes the thin thing big enough to see. It does not measure the pattern, does
not compare anything, and never produces a verdict — the two constants below are a viewport,
not a segmentation. Nothing here reads a pixel to decide whether a garment matches; that stays
entirely with the vision model.

Why fixed fractions and not a detector: the mannequin frame is standardized (Frame Lock: same
canonical base, same pose, same camera, same 2:3 crop, full body head to feet), so the worn
garment lands in the same part of the frame every time. A rectangle is the cheapest thing that
is true. Rebuilding segmentation to find a shirt that is always in the same place would be a
larger, less reliable machine for a smaller job.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image

VERSION = "pattern_evidence_v1"

#: Worn garment on the canonical mannequin frame: shoulders to below the hem, sleeve to
#: sleeve. Verified against 848x1264, 1696x2528 and 3392x5056 cuts of six different garments
#: (shirt, check shirt, lace top, knit blouse, henley tee, boat-neck knit).
GARMENT_RECT = (0.26, 0.13, 0.74, 0.46)

#: Chest and upper torso only — the flattest, least occluded expanse of fabric in the frame,
#: which is where a repeat reads most honestly. Deliberately inside the collar and above the
#: hem so it lands on cloth rather than on the neckline or the waistband.
TORSO_RECT = (0.36, 0.20, 0.62, 0.35)

#: Below this the crop carries no more detail than the full frame already did, so sending it
#: costs a payload and buys nothing. A 848px-wide cut yields a 220px torso crop; that is the
#: case this threshold excludes.
MIN_DETAIL_WIDTH = 320

#: Upper bound on a crop's long side. Past this the payload grows without the stripe getting
#: any more legible, because the provider resizes anyway.
MAX_EDGE = 1600


@dataclass(frozen=True)
class Crop:
    """One piece of evidence: bytes, what it is, and where it came from."""

    name: str
    data: bytes
    width: int
    height: int
    rect: tuple[float, float, float, float] | None
    source_size: tuple[int, int]

    def as_dict(self) -> dict:
        return {"name": self.name, "width": self.width, "height": self.height,
                "rect": list(self.rect) if self.rect else None,
                "sourceSize": list(self.source_size), "bytes": len(self.data)}


def _encode(im: Image.Image) -> bytes:
    if max(im.size) > MAX_EDGE:
        scale = MAX_EDGE / max(im.size)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                       Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=95, subsampling=0)
    return buf.getvalue()


def crop_rect(image_bytes: bytes, rect: tuple[float, float, float, float], *, name: str,
              min_width: int = 0) -> Crop | None:
    """Normalized rectangle -> a JPEG of that region, or None when it is not worth sending.

    Quality 95 with chroma subsampling off, on purpose. The default JPEG chroma subsample
    averages colour over 2x2 blocks, which is exactly the operation that turns a thin blue
    line between taupe lines into grey — the discrimination this crop exists to preserve.
    """
    with Image.open(io.BytesIO(image_bytes)) as im:
        im.load()
        w, h = im.size
        box = (int(rect[0] * w), int(rect[1] * h), int(rect[2] * w), int(rect[3] * h))
        if box[2] - box[0] < max(1, min_width):
            return None
        region = im.crop(box)
        return Crop(name=name, data=_encode(region), width=region.width,
                    height=region.height, rect=rect, source_size=(w, h))


def generated_crops(image_bytes: bytes) -> list[Crop]:
    """The generated cut, cropped for a judge that has to see a repeat.

    The garment crop is always produced; the torso crop only when the frame has the pixels to
    make it worth a second attachment.
    """
    out = []
    garment = crop_rect(image_bytes, GARMENT_RECT, name="generated_garment_crop")
    if garment:
        out.append(garment)
    torso = crop_rect(image_bytes, TORSO_RECT, name="generated_torso_detail_crop",
                      min_width=MIN_DETAIL_WIDTH)
    if torso:
        out.append(torso)
    return out


def source_center_crop(image_bytes: bytes, *, name: str, fraction: float = 0.42,
                       min_width: int = MIN_DETAIL_WIDTH) -> Crop | None:
    """A centred window of a source photograph, at native resolution.

    Source photos are flat-lay or hanging shots with no fixed geometry, so there is no
    equivalent of the mannequin rectangle here. The centre is the one region a photographer
    reliably fills with the garment, and for a Detail close-up the whole frame is fabric
    anyway — the crop's real job is to stop a 5712x4284 photo from being resized to a
    thumbnail before the model sees the weave.
    """
    half = max(0.05, min(0.5, fraction / 2))
    rect = (0.5 - half, 0.5 - half, 0.5 + half, 0.5 + half)
    return crop_rect(image_bytes, rect, name=name, min_width=min_width)
