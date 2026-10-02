"""옷 영역의 지어낸 곡선 잔금(crackle) 제거 — 결정적 후처리(2026-10-02).

왜: OpenAI gpt-image 계열(gpt-image-2·2.5 flare·sunburst)은 전신·미디엄 착용컷의 원단에 실제
직물과 무관한 **방향 없는 곡선 그물**을 그린다. 빈티지 워싱 데님·멜란지 티에서 가장 심하다.
같은 예시컷으로 만든 Gemini 컷은 깨끗했고(운영 컷 짝 비교), QC 보정·예시컷 없이 생성한 컷에도
똑같이 생겨서 입력 탓이 아니라 모델의 렌더링 버릇이다. quality=high 는 오히려 더 선명하게
그렸다(실측 1장, 9/28 데님 블록). 클로즈업 컷은 진짜 능직을 그린다 — 옷이 화면에서 작을 때
결을 곡선 잔금으로 채운다.

무엇을: 옷 영역 안에서 휘도의 **중간 대역(선 굵기 2~10px)** 중 **진폭이 작은 것만** 깎는다
(코어링). 잔금은 대비가 낮고, 글자·로고·솔기·단추·스트라이프는 대비가 커서 남는다. 큰 주름과
워싱 색 번짐(저주파)·실 입자(고주파)는 대역 밖이라 그대로다. 생성 호출을 더 하지 않는다.

마스크: SAM 은 사람 착용컷 경로가 없고 온디맨드라 지연이 붙는다. 대신 YuNet 얼굴 위치로 사람
영역을 씨앗 삼아 GrabCut 으로 다듬고 피부색을 뺀다. 실패하면(얼굴·옷 못 찾음, 면적 이상) 원본을
그대로 돌려준다 — 이 후처리는 컷을 막지 않는다.

켜는 범위(should_apply): 플래그 on · gpt-image 계열 · 착용컷 · 무늬/질감 원단 아님.
코듀로이·니트 골·체크처럼 **진짜 무늬가 같은 대역에 있는 원단**은 깎으면 안 되므로 건너뛴다.
"""

from __future__ import annotations

import logging
import threading

import cv2
import numpy as np

from . import face_qc, mannequin

log = logging.getLogger("wearless.garment_desquiggle")

_WORN_CUTS = ("styling", "horizon", "mirror")
#: 대역은 **출력 픽셀 기준 고정**이다. 잔금 굵기는 해상도에 비례해 줄지 않는다 — 1K 로 다시 그린
#: 컷(목 보정본)의 잔금이 2K 보다 오히려 굵었다(9/23 회색 티 실측).
_FEATHER_SIGMA = 3.0
#: 코어링 파라미터 — 9/28 데님·9/23 회색 티 운영 컷으로 맞췄다(데님 잔금 대폭 감소, 로고 유지).
_BAND_LO, _BAND_HI = 1.2, 5.0
_STRENGTH = 0.95
_TAU = 14.0  # LAB L(0~255) 단위. 이보다 작은 진폭의 중간 대역만 깎인다.
#: 마스크 면적이 이 범위를 벗어나면 추정 실패로 보고 손대지 않는다.
_MASK_MIN, _MASK_MAX = 0.02, 0.75
_GRABCUT_WIDTH = 512

#: 같은 대역에 진짜 무늬·요철이 있는 원단 — 깎으면 원단이 바뀐다. 넓게 잡는다(과탐=기존과 동일).
_TEXTURED_TOKENS = (
    "니트", "골지", "리브", "와플", "꽈배기", "케이블", "코듀로이", "골덴", "트위드", "부클",
    "퀼팅", "자카드", "레이스", "시퀸", "스팽글", "크링클", "시어서커", "플리츠", "주름",
    "벨벳", "벨루어", "스웨이드", "가죽", "레더", "퍼", "털", "뽀글", "메쉬", "망사", "펀칭",
    "knit", "rib", "waffle", "cable", "corduroy", "tweed", "boucle", "bouclé", "quilt",
    "jacquard", "lace", "sequin", "crinkle", "seersucker", "pleat", "velvet", "velour",
    "suede", "leather", "fur", "sherpa", "fleece", "mesh", "eyelet",
)


def _mode(settings) -> str:
    return str(getattr(settings, "garment_desquiggle_mode", "off") or "off").lower()


def _text(product: dict | None, analysis: dict | None) -> str:
    blob = mannequin._text_blob(product, analysis)
    mats = (analysis or {}).get("materials") or []
    names = " ".join(str(m.get("name") or "") for m in mats if isinstance(m, dict))
    return f"{blob} {names}".lower()


def should_apply(settings, model: str, spec: dict, product: dict | None,
                 analysis: dict | None) -> tuple[bool, str]:
    """이 컷에 잔금 제거를 걸까 — (여부, 사유). 사유는 로그·결과 기록용."""
    if _mode(settings) != "on":
        return False, "off"
    if not str(model or "").startswith("gpt-image"):
        return False, "model"
    if (spec or {}).get("cutType") not in _WORN_CUTS:
        return False, "cut_type"
    if mannequin.has_fine_pattern(product, analysis):
        return False, "pattern"
    text = _text(product, analysis)
    if any(tok in text for tok in _TEXTURED_TOKENS):
        return False, "texture"
    return True, "eligible"


def desquiggle(bgr: np.ndarray, mask: np.ndarray, *, strength: float = _STRENGTH,
               tau: float = _TAU) -> np.ndarray:
    """마스크(0~1) 안의 약한 진폭 중간 대역만 깎는다(순수). 마스크 밖은 바이트 그대로."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lum = lab[..., 0]
    band = cv2.GaussianBlur(lum, (0, 0), _BAND_LO) - cv2.GaussianBlur(lum, (0, 0), _BAND_HI)
    # 코어링: |band| 가 tau 보다 한참 작으면 거의 다 깎고, 크면(글자·솔기) 거의 안 깎는다.
    removed = band * (strength * np.exp(-(band / tau) ** 2))
    feather = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), _FEATHER_SIGMA)
    out_lum = lum - removed * feather
    lab[..., 0] = np.clip(out_lum, 0, 255)
    out = cv2.cvtColor(np.round(lab).astype(np.uint8), cv2.COLOR_LAB2BGR)
    # 깃털 반경 밖은 원본 바이트를 그대로 쓴다(LAB 왕복 반올림 오차도 남기지 않는다).
    untouched = feather < 1e-3
    out[untouched] = bgr[untouched]
    return out


_detector = None
#: 상세컷은 5장이 동시에 스레드에서 후처리된다 — 검출기 하나를 setInputSize→detect 로 나눠 쓰면
#: 다른 스레드의 크기로 검출할 수 있어 잠근다(검출은 512px 축소본이라 수 ms).
_detector_lock = threading.Lock()


def _faces(bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
    """YuNet 얼굴 상자 목록(x, y, w, h). 가중치가 없거나 실패하면 빈 목록."""
    global _detector
    try:
        with _detector_lock:
            if _detector is None:
                det_path, _ = face_qc.weight_paths(None)
                _detector = cv2.FaceDetectorYN.create(det_path, "", (320, 320), score_threshold=0.7)
            h, w = bgr.shape[:2]
            _detector.setInputSize((w, h))
            _, found = _detector.detect(bgr)
    except Exception:  # noqa: BLE001 — 얼굴 없이도 중앙 씨앗으로 진행한다
        return []
    if found is None:
        return []
    return [tuple(int(v) for v in row[:4]) for row in found]


def _drop_above_shoulders(m: np.ndarray) -> np.ndarray:
    """어깨선 위(머리카락·뒷머리)를 뺀다. 머리카락 결을 깎으면 뭉개진다(9/28 데님 실측).

    얼굴 검출이 안 되는 뒷모습에서도 통하도록 마스크 모양만 본다: 행별 너비가 가장 넓은 폭의
    45% 를 처음 넘는 행을 어깨로 보고 그 위를 지운다. 머리는 어깨보다 한참 좁다."""
    widths = m.sum(axis=1)
    peak = int(widths.max()) if widths.size else 0
    if peak == 0:
        return m
    rows = np.nonzero(widths >= 0.45 * peak)[0]
    out = m.copy()
    out[:rows[0], :] = 0
    return out


def garment_mask(bgr: np.ndarray) -> np.ndarray | None:
    """옷 영역 추정(0~1 float, 원본 크기). 못 찾으면 None."""
    h, w = bgr.shape[:2]
    s = _GRABCUT_WIDTH / w
    small = cv2.resize(bgr, (_GRABCUT_WIDTH, max(8, round(h * s))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    gc = np.full((sh, sw), cv2.GC_PR_BGD, np.uint8)
    border = max(2, int(0.02 * sw))
    gc[:border, :] = gc[-border:, :] = cv2.GC_BGD
    gc[:, :border] = gc[:, -border:] = cv2.GC_BGD
    faces = _faces(small)
    if faces:
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        cx = fx + fw / 2
        x0, x1 = int(max(border, cx - 1.6 * fw)), int(min(sw - border, cx + 1.6 * fw))
        y0 = int(min(sh - border - 1, fy + 1.3 * fh))
        gc[y0:sh - border, x0:x1] = cv2.GC_PR_FGD
        gc[max(0, fy - fh // 2):min(sh, fy + int(1.15 * fh)), max(0, fx):min(sw, fx + fw)] = cv2.GC_BGD
    else:
        gc[int(0.15 * sh):int(0.95 * sh), int(0.3 * sw):int(0.7 * sw)] = cv2.GC_PR_FGD
    if not (gc == cv2.GC_PR_FGD).any():
        return None
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(small, gc, None, bgd, fgd, 4, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    fg = (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)
    # 피부(얼굴·목·손·다리)는 옷이 아니다 — 깎으면 피부가 플라스틱처럼 된다.
    ycc = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
    skin = (ycc[..., 1] >= 135) & (ycc[..., 1] <= 180) & (ycc[..., 2] >= 85) & (ycc[..., 2] <= 135)
    m = (fg & ~skin).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = _drop_above_shoulders(m)
    frac = float(m.mean())
    if not (_MASK_MIN <= frac <= _MASK_MAX):
        return None
    return cv2.resize(m.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)


def apply(image: bytes, mime: str) -> tuple[bytes, str, dict]:
    """생성 결과 바이트 → (바이트, mime, 결과). 어떤 실패에도 원본을 돌려준다(예외 없음)."""
    try:
        bgr = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        bgr = None
    if bgr is None:
        return image, mime, {"status": "skipped:undecodable"}
    try:
        mask = garment_mask(bgr)
        if mask is None:
            return image, mime, {"status": "skipped:no_mask"}
        out = desquiggle(bgr, mask)
        ok, buf = cv2.imencode(".png", out)
        if not ok:
            return image, mime, {"status": "skipped:encode"}
        return buf.tobytes(), "image/png", {"status": "applied",
                                            "maskFrac": round(float(mask.mean()), 4)}
    except Exception as exc:  # noqa: BLE001 — 후처리는 컷을 막지 않는다
        log.warning("garment desquiggle failed: %r", exc)
        return image, mime, {"status": "skipped:error"}


async def maybe_apply(settings, model: str, spec: dict, product: dict | None,
                      analysis: dict | None, image: bytes, mime: str) -> tuple[bytes, str]:
    """generate()·repair() 꼬리 — 대상이면 이벤트 루프 밖에서 apply 를 돌린다."""
    eligible, reason = should_apply(settings, model, spec, product, analysis)
    if not eligible:
        return image, mime
    from .gemini_image import run_cpu_bound

    out, out_mime, outcome = await run_cpu_bound(apply, image, mime)
    log.info("garment desquiggle model=%s cut=%s status=%s mask=%s", model,
             (spec or {}).get("cutType"), outcome.get("status"), outcome.get("maskFrac"))
    return out, out_mime
