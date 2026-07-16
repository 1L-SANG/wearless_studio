"""가상모델 아이덴티티 자산 시드 — 로컬 팩 → R2(seed/models/...) + manifest JSON.

계약: ai_agent_modules §3 AG-06 '가상모델 아이덴티티 레퍼런스 계약' —
  face_front = 원본 베이스컷(생성물 재주입 금지), grid_sedcard = v2 통짜 2x2 그리드,
  나머지 4뷰(시트)는 QC 폴백 전용 보관.
소스: public/models/{gender}/{sid}.webp(앵커) + spike/runs/facepack-{sid}v2-*/(v2 팩).
산출: R2 seed/models/{modelId}/{view}.{ext} + server/app/data/virtual_models.json (파일 기반
  manifest — example_assets.json 패턴, DB 테이블 없음).

멱등: 객체 존재·크기·MIME 동일 시 재업로드 skip. 운영자 1회성 스크립트(macOS sips 사용).
실행: cd server && .venv/bin/python -m scripts.seed_virtual_models
전제: R2 자격증명·R2_PUBLIC_BASE(server/.env), spike/runs/facepack-*v2-* 로컬 존재.
"""
import glob
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_env(path: Path):
    """server/.env → os.environ (미설정 키만). smoke_* 스크립트와 동일 패턴."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / "server/.env")

from app.config import load_settings  # noqa: E402 (env 로드 후 import)
from app.r2 import R2Client  # noqa: E402
MANIFEST = ROOT / "server/app/data/virtual_models.json"
_IMMUTABLE = "public, max-age=31536000, immutable"
_MAX_EDGE = "1536"  # v2 팩 자산 리샘플 상한 — 아이덴티티 참조엔 충분, 첨부 페이로드 절감
_PACK_MIME = "image/jpeg"  # v2 팩의 .png 파일명과 달리 실제 바이트는 JPEG

# 프론트 모델 ID(src/mock/db.js AI_MODELS) ↔ 스파이크 소스 ID 매핑
MODELS = {
    "mA": {"sid": "w1", "gender": "women", "name": "모델 A"},
    "mB": {"sid": "m1", "gender": "men", "name": "모델 B"},
    "mC": {"sid": "m2", "gender": "men", "name": "모델 C"},
}
# 팩 크롭 파일명 → manifest 뷰 키 (계약의 시트 낱장 4뷰)
PACK_VIEWS = {
    "three-quarter-left.png": "three_quarter",
    "profile-left.png": "profile",
    "body-front.png": "body_front",
    "body-back.png": "body_back",
}


def _pack_dir(sid: str) -> Path:
    runs = sorted(glob.glob(str(ROOT / f"spike/runs/facepack-{sid}v2-*/pack")))
    assert runs, f"팩 없음: spike/runs/facepack-{sid}v2-*/pack"
    return Path(runs[-1])  # 최신 런 = 큐레이션 통과본


def _resample(src: Path, dst: Path) -> bytes:
    subprocess.run(
        ["sips", "-Z", _MAX_EDGE, str(src), "--out", str(dst)],
        check=True, capture_output=True,
    )
    return dst.read_bytes()


def _put_if_changed(r2: R2Client, key: str, data: bytes, mime: str) -> bool:
    head = r2.head(key)
    if head and head["size"] == len(data) and head.get("mime") == mime:
        return False
    r2.put_bytes(key, data, mime, _IMMUTABLE)
    assert r2.head(key), f"upload failed: {key}"
    return True


def main() -> None:
    settings = load_settings()
    assert settings.r2_public_base, "R2_PUBLIC_BASE 필요 (공개 서빙 전제)"
    r2 = R2Client(settings)
    manifest: dict = {
        "_meta": {
            "description": (
                "Virtual model identity assets. Contract: ai_agent_modules §3 AG-06 — "
                "face_front(원본 앵커) + grid_sedcard(v2 2x2 통짜) 기본 첨부, "
                "시트 4뷰는 QC 실패 폴백 전용(body_front)."
            ),
            "source": "spike facepack v2 + public/models 원본 앵커",
        },
        "models": {},
    }
    uploaded = skipped = 0
    with tempfile.TemporaryDirectory() as tmp:
        for model_id, m in MODELS.items():
            views: dict = {}
            # face_front = 원본 베이스컷 그대로 (리샘플·재인코딩 없음 — 앵커 보존)
            anchor = ROOT / f"public/models/{m['gender']}/{m['sid']}.webp"
            key = f"seed/models/{model_id}/face_front.webp"
            fresh = _put_if_changed(r2, key, anchor.read_bytes(), "image/webp")
            uploaded, skipped = uploaded + fresh, skipped + (not fresh)
            views["face_front"] = {"key": key, "url": r2.public_url(key), "mime": "image/webp"}
            pack = _pack_dir(m["sid"])
            # 세드카드 = v2 팩 루트의 2x2 통짜 그리드 리샘플(max 1536px)
            dst = Path(tmp) / f"{model_id}-grid_sedcard.png"
            data = _resample(pack.parent / "grid-sedcard.png", dst)
            key = f"seed/models/{model_id}/grid_sedcard.png"
            fresh = _put_if_changed(r2, key, data, _PACK_MIME)
            uploaded, skipped = uploaded + fresh, skipped + (not fresh)
            views["grid_sedcard"] = {
                "key": key, "url": r2.public_url(key), "mime": _PACK_MIME,
            }
            # 시트 4뷰 = v2 팩 크롭 리샘플(max 1536px) 후 업로드
            for fname, view in PACK_VIEWS.items():
                dst = Path(tmp) / f"{model_id}-{view}.png"
                data = _resample(pack / fname, dst)
                key = f"seed/models/{model_id}/{view}.png"
                fresh = _put_if_changed(r2, key, data, _PACK_MIME)
                uploaded, skipped = uploaded + fresh, skipped + (not fresh)
                views[view] = {"key": key, "url": r2.public_url(key), "mime": _PACK_MIME}
            manifest["models"][model_id] = {
                "gender": m["gender"], "name": m["name"],
                "thumb": f"/models/{m['gender']}/{m['sid']}.webp",  # 프론트 public 경로(기존)
                "views": views,
            }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(f"seeded {len(MODELS)} models — uploaded {uploaded}, skipped {skipped}")
    print(f"manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
