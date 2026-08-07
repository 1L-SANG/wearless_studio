"""AG-IC 입력 사진 동일성 캘리브레이션 — 실제 사진으로 오탐률을 잰다.

이 축은 미탐이 아니라 **오탐**이 유일한 실패 모드다(멀쩡한 사진을 지우게 만든다). 그래서
이 스크립트가 보는 숫자도 하나다: **정상 케이스에서 mismatch 가 몇 건 나오는가.** 0 이 아니면
INPUT_CONSISTENCY=warn 을 켜지 않는다.

쓰는 법:
    cd server
    .venv/bin/python scripts/ic_calibrate.py <케이스_디렉터리>

케이스 디렉터리 구조 — 하위 폴더 하나가 상품 하나(= 판정 1회):

    cases/
      ok-knit/                      # 폴더명에 'bad' 가 없으면 정상 케이스로 간주
        Front_1.jpg
        Back_1.jpg
        Detail_1.jpg
      ok-hoodie/
        Front.png
        BackDetail.png
      bad-mixed-jacket/             # 'bad' 로 시작·포함 = 일부러 다른 옷을 섞은 케이스
        Front.jpg
        Back.jpg                    # ← 이게 다른 옷
파일명 앞부분이 슬롯(Front/Back/Detail/BackDetail)이고, Front 가 레퍼런스다. 대소문자 무관.
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import input_consistency                      # noqa: E402
from app.agents.gemini_image import InlineImage               # noqa: E402
from app.config import load_settings                          # noqa: E402
from app.workers.analyze_job import shrink_for_vision         # noqa: E402

_SLOT_ORDER = {"Front": 0, "Back": 1, "Detail": 2, "BackDetail": 3}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def _slot_of(path: Path) -> str | None:
    head = path.stem.split("_")[0].split("-")[0].strip().lower()
    for slot in _SLOT_ORDER:
        if head == slot.lower():
            return slot
    return None


def _load_case(case_dir: Path) -> tuple[list[InlineImage], list[str], list[str]]:
    """폴더 → (이미지, 슬롯, 파일명). 워커와 **같은 순서·같은 축소**를 태워야 판정이 재현된다."""
    found = []
    for path in sorted(case_dir.iterdir()):
        slot = _slot_of(path)
        if slot is None or path.suffix.lower() not in _MIME:
            continue
        found.append((_SLOT_ORDER[slot], path.name, slot, path))
    found.sort(key=lambda t: (t[0], t[1]))

    images, slots, names = [], [], []
    for _order, name, slot, path in found:
        data, mime = shrink_for_vision(path.read_bytes(), _MIME[path.suffix.lower()])
        images.append(InlineImage(mime, data))
        slots.append(slot)
        names.append(name)
    return images, slots, names


async def _run(case_dir: Path, settings):
    images, slots, names = _load_case(case_dir)
    if len(images) < 2:
        return {"skip": "사진 2장 미만"}
    if slots[0] != "Front":
        return {"skip": "Front 없음 — 워커도 같은 이유로 건너뛴다"}
    try:
        out = await input_consistency.judge(settings, images, slots)
    except Exception as e:                      # noqa: BLE001 — 캘리브레이션은 실패도 기록한다
        return {"error": repr(e)[:160]}
    out["names"] = names
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"디렉터리가 아니에요: {root}")
        return 2
    if not os.getenv("GEMINI_API_KEY"):
        print("GEMINI_API_KEY 가 없어요. `set -a; source .env; set +a` 후 다시 실행하세요.")
        return 2

    settings = load_settings()
    cases = sorted(p for p in root.iterdir() if p.is_dir())
    if not cases:
        print(f"케이스 폴더가 없어요: {root}")
        return 2

    false_alarms, caught, missed, other = [], [], [], []
    print(f"모델={settings.model_text_gemini}  케이스={len(cases)}\n")
    for case in cases:
        expect_bad = "bad" in case.name.lower()
        res = asyncio.run(_run(case, settings))

        if "skip" in res or "error" in res:
            mark, detail = "· SKIP", res.get("skip") or res["error"]
            other.append(case.name)
        else:
            verdict, conf = res["verdict"], res["confidence"]
            detail = f"{verdict} ({conf:.2f}) {[o['slot'] for o in res['offending']]}"
            if verdict == "mismatch" and not expect_bad:
                mark = "✗ 오탐"
                false_alarms.append(case.name)
            elif verdict == "mismatch":
                mark = "✓ 검출"
                caught.append(case.name)
            elif expect_bad:
                mark = "· 미탐"       # 비용 0 — 이 축의 설계상 허용된 실패
                missed.append(case.name)
            else:
                mark = "✓ 정상"
            for off in res.get("offending", []):
                detail += f"\n        └ {res['names'][off['index'] - 1]}: {off['reason']}"
        print(f"  {mark:8} {case.name:32} {detail}")

    normal_total = sum(1 for c in cases if "bad" not in c.name.lower())
    bad_total = len(cases) - normal_total
    print(f"\n정상 {normal_total}건 중 오탐 {len(false_alarms)}건"
          f" · 섞임 {bad_total}건 중 검출 {len(caught)}건(미탐 {len(missed)})"
          f" · 판정불가 {len(other)}건")
    if false_alarms:
        print(f"\n오탐: {', '.join(false_alarms)}")
        print("→ INPUT_CONSISTENCY=warn 을 켜지 마세요. 프롬프트/임계를 먼저 고쳐야 합니다.")
    else:
        print("\n오탐 0 — warn 승격 조건 충족. (미탐은 이 축의 허용된 실패입니다.)")
    return 1 if false_alarms else 0


if __name__ == "__main__":
    raise SystemExit(main())
