#!/usr/bin/env python3
"""제로샷 신원주입 스파이크 실행 스켈레톤 (Phase 0 T0-2).

방법론·판정 기준은 docs/personalization/phase0-spike-plan.md 를 단일 진실원으로 한다.
이 파일은 그 방법론을 실행 가능한 구조로 배선한 "스파이크 키트"다.

============================================================
실행 가이드
============================================================
필요한 것 (전부 갖춰야 실제 생성·측정이 수행된다):
  1) 환경변수
     - 경로 α(기존 Gemini 배관 재사용): GEMINI_API_KEY 또는 VERTEX_PROJECT
     - 경로 β(관리형 Qwen-Image-Edit-2511, Replicate 등): REPLICATE_API_TOKEN
       (+ REPLICATE_MODEL_VERSION — 모델 카드에서 확인한 owner/model:version 슬러그. TBD)
  2) 테스트/합성 얼굴 디렉토리 (--faces-dir) — 정면/측면/45도 등 동일 인물 사진 여러 장.
     **합성 또는 본인(자기동의)/서면동의 팀원 얼굴만. 실사용자 얼굴 절대 금지.**
  3) pip install insightface onnxruntime numpy pillow
     (얼굴 임베딩 코사인 유사도 측정용 — 없으면 유사도 측정 단계만 건너뛴다)

실행 예:
  python3 scripts/personalization_spike.py --faces-dir ./my-test-faces --path alpha

**2026-07-15 현재: 위 1)~3)이 전부 부재함을 확인했다.** 이 스크립트를 그대로 실행하면
사전조건 점검 결과를 보여주고 무엇이 없는지 안내한 뒤 안전하게 종료한다(예외로 죽지 않음).

============================================================
생체 안전 (계획서 Principle 2 · docs/personalization/phase0-spike-plan.md §4)
============================================================
- 합성/본인/서면동의 얼굴만 사용. 실사용자 얼굴 금지.
- 스파이크 종료 시 원본 얼굴·생성 결과·임베딩 전량 파기(로컬 디스크 포함, 예외 없음).
- 얼굴 임베딩(및 그 해시)도 생체정보 — 로그·JSON 리포트에 임베딩 벡터를 절대 기록하지
  않는다. 저장 허용 범위는 코사인 유사도 수치·Δcos 수치·지연·비용 같은 집계 스코어뿐.
- 다운로드 폴더의 "모바일신분증 API 샘플"·해커톤 관련 외부 신원 자산은 본 스파이크에
  전용(轉用) 금지.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 선택 의존성 — 없으면 해당 기능만 비활성화하고 안내 (무거운 의존은 optional import)
# ---------------------------------------------------------------------------
try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

try:
    from insightface.app import FaceAnalysis
except ImportError:
    FaceAnalysis = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# 경로 α: 기존 server/app/agents/gemini_image.py 재사용
# (InlineImage · GeminiImageClient.generate_content_image 인터페이스 그대로)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
_SERVER_DIR = _ROOT / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

_GEMINI_IMPORT_ERROR: str | None = None
try:
    from app.agents.gemini_image import GeminiError, GeminiImageClient, InlineImage
    from app.config import load_settings
except Exception as exc:  # noqa: BLE001 — 임포트 실패는 사전조건 리포트로 흡수한다
    GeminiError = RuntimeError  # type: ignore[assignment,misc]
    GeminiImageClient = None  # type: ignore[assignment]
    InlineImage = None  # type: ignore[assignment]
    load_settings = None  # type: ignore[assignment]
    _GEMINI_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

# gemini-3-pro-image 가격표 — 출처: spike/spike.js 주석(2026-06-12 기준가).
# 실행 전 최신 요금 재확인 필수(docs/personalization/phase0-spike-plan.md §5).
GEMINI_PRO_IMAGE_USD_BY_RES = {"1K": 0.134, "2K": 0.134, "4K": 0.24}
GEMINI_FLASH_IMAGE_USD_1K = 0.067


# ============================================================================
# 1. 사전조건 점검 — "무엇이 없는지" 친절하게 안내하고 안전 종료
# ============================================================================


@dataclass
class Precondition:
    name: str
    ok: bool
    detail: str


def check_preconditions(faces_dir: Path | None, path: str) -> list[Precondition]:
    """경로(α/β/both) 선택에 맞춰 실행에 필요한 것들을 점검한다."""
    checks: list[Precondition] = []
    needs_alpha = path in ("alpha", "both")
    needs_beta = path in ("beta", "both")

    if needs_alpha:
        has_gemini_key = bool(os.getenv("GEMINI_API_KEY"))
        has_vertex = bool(os.getenv("VERTEX_PROJECT"))
        checks.append(
            Precondition(
                "경로 α 인증 (GEMINI_API_KEY 또는 VERTEX_PROJECT)",
                has_gemini_key or has_vertex,
                "OK"
                if (has_gemini_key or has_vertex)
                else "환경변수 없음 — server/.env.local 또는 셸 export 필요",
            )
        )
        checks.append(
            Precondition(
                "server/app.agents.gemini_image 임포트 (경로 α 배관 재사용)",
                _GEMINI_IMPORT_ERROR is None,
                _GEMINI_IMPORT_ERROR or "OK",
            )
        )

    if needs_beta:
        has_replicate = bool(os.getenv("REPLICATE_API_TOKEN"))
        checks.append(
            Precondition(
                "경로 β 인증 (REPLICATE_API_TOKEN)",
                has_replicate,
                "OK" if has_replicate else "REPLICATE_API_TOKEN 없음 — Replicate 계정에서 발급 필요",
            )
        )
        has_model_version = bool(os.getenv("REPLICATE_MODEL_VERSION"))
        checks.append(
            Precondition(
                "REPLICATE_MODEL_VERSION (Qwen-Image-Edit-2511 슬러그)",
                has_model_version,
                "OK" if has_model_version else "미설정 — Replicate 모델 카드에서 owner/model:version 확인 필요(TBD)",
            )
        )

    if faces_dir is None:
        checks.append(
            Precondition(
                "테스트/합성 얼굴 디렉토리 (--faces-dir)",
                False,
                "미지정 — --faces-dir <경로> 필요 (합성/본인/동의 얼굴만, 실사용자 금지)",
            )
        )
    else:
        imgs = list_face_images(faces_dir) if faces_dir.exists() else []
        ok = faces_dir.exists() and len(imgs) > 0
        checks.append(
            Precondition(
                f"테스트/합성 얼굴 디렉토리 ({faces_dir})",
                ok,
                f"OK ({len(imgs)}장)" if ok else "디렉토리 없음 또는 이미지 0장(jpg/jpeg/png/webp)",
            )
        )

    checks.append(
        Precondition(
            "httpx (HTTP 클라이언트)",
            httpx is not None,
            "OK" if httpx is not None else "미설치 — pip install httpx",
        )
    )
    checks.append(
        Precondition(
            "pillow — PIL (이미지 디코딩)",
            Image is not None,
            "OK" if Image is not None else "미설치 — pip install pillow",
        )
    )
    checks.append(
        Precondition(
            "insightface + numpy (얼굴 임베딩 코사인 유사도 측정)",
            FaceAnalysis is not None and np is not None,
            "OK" if (FaceAnalysis is not None and np is not None) else "미설치 — pip install insightface onnxruntime numpy",
        )
    )
    return checks


def print_precondition_report(checks: list[Precondition]) -> bool:
    print("=" * 78)
    print("실행 전제조건 점검 — Phase 0 T0-2 제로샷 신원주입 스파이크")
    print("=" * 78)
    all_ok = True
    for c in checks:
        mark = "OK     " if c.ok else "MISSING"
        print(f"[{mark}] {c.name}")
        if not c.ok:
            print(f"          -> {c.detail}")
            all_ok = False
    print("=" * 78)
    return all_ok


# ============================================================================
# 2. 얼굴 로드 (디렉토리)
# ============================================================================


def list_face_images(faces_dir: Path) -> list[Path]:
    if not faces_dir.exists():
        return []
    return sorted(p for p in faces_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)


def load_face_bytes(paths: list[Path]) -> list[tuple[bytes, str]]:
    """(원본 바이트, mime) 리스트. 호출자가 사용 직후 폐기 — 별도 캐시/저장 금지."""
    out = []
    for p in paths:
        mime = _MIME_BY_EXT.get(p.suffix.lower(), "image/jpeg")
        out.append((p.read_bytes(), mime))
    return out


def build_body_prompt(height_cm: float, weight_kg: float, body_type: str) -> str:
    return (
        "다음은 동일 인물의 얼굴 레퍼런스 사진입니다. 이 인물의 identity(얼굴 특징: 눈/코/입/"
        "윤곽)를 최대한 유지하면서, 아래 신체 정보를 반영한 전신 착장 컷을 생성하세요.\n"
        f"- 키: {height_cm}cm\n- 몸무게: {weight_kg}kg\n- 체형: {body_type}\n"
        "신체 실루엣만 위 정보에 맞게 조정하고 얼굴은 레퍼런스와 일치시키세요."
    )


# ============================================================================
# 3. 경로별 호출 — α: gemini_image 재사용 / β: replicate 스텁
# ============================================================================


@dataclass
class GenerationOutcome:
    path: str
    image_bytes: bytes | None
    mime: str
    latency_ms: int
    cost_usd: float | None
    note: str = ""


async def call_path_alpha(
    prompt: str,
    face_refs: list[tuple[bytes, str]],
    model: str,
    image_size: str,
) -> GenerationOutcome:
    """경로 α: 기존 gemini_image.py 배관 재사용.

    얼굴 3장 + 신체 프롬프트를 InlineImage 다중 레퍼런스로 그대로 주입한다
    (gemini_image.py의 _body()가 images: list[InlineImage]를 이미 순회 지원).
    """
    if GeminiImageClient is None or InlineImage is None or load_settings is None:
        return GenerationOutcome(
            "alpha", None, "", 0, None,
            note=f"경로 α 임포트 실패로 실행 불가: {_GEMINI_IMPORT_ERROR}",
        )
    settings = load_settings()
    client = GeminiImageClient(settings)
    images = [InlineImage(mime=mime, data=data) for data, mime in face_refs]
    try:
        result = await client.generate_content_image(
            model=model, prompt=prompt, images=images, image_size=image_size,
        )
    except GeminiError as exc:
        return GenerationOutcome("alpha", None, "", 0, None, note=f"Gemini 호출 실패: {exc}")
    cost = (
        GEMINI_FLASH_IMAGE_USD_1K
        if "flash" in model and image_size == "1K"
        else GEMINI_PRO_IMAGE_USD_BY_RES.get(image_size)
    )
    return GenerationOutcome("alpha", result.image, result.mime, result.latency_ms, cost)


def call_path_beta_replicate(
    prompt: str,
    face_refs: list[tuple[bytes, str]],
    api_token: str,
    model_version: str,
    timeout: float = 180.0,
) -> GenerationOutcome:
    """경로 β: 관리형 Qwen-Image-Edit-2511(Replicate) 스텁.

    Replicate 표준 predictions API(POST 생성 → GET 폴링) 흐름만 배선했다.
    TBD: input 스키마의 정확한 필드명(다중 레퍼런스 얼굴을 어떻게 넣는지)은
    모델 카드 확인 후 채운다 — 현재는 첫 번째 얼굴 이미지만 단일 레퍼런스로 전달.
    비용은 Replicate 콘솔 실측 필요(TBD, docs/personalization/phase0-spike-plan.md §2).
    """
    import base64

    if httpx is None:
        return GenerationOutcome("beta", None, "", 0, None, note="httpx 미설치 — pip install httpx")
    if not face_refs:
        return GenerationOutcome("beta", None, "", 0, None, note="얼굴 레퍼런스 없음")

    data, mime = face_refs[0]
    data_uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    body = {
        "version": model_version,
        "input": {
            "prompt": prompt,
            "image": data_uri,
            # TBD: Qwen-Image-Edit-2511 실제 input 스키마 확인 후 다중 레퍼런스 필드 추가.
        },
    }
    t0 = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            res = client.post("https://api.replicate.com/v1/predictions", headers=headers, json=body)
            res.raise_for_status()
            prediction = res.json()
            poll_url = prediction["urls"]["get"]
            while prediction.get("status") in ("starting", "processing"):
                time.sleep(2)
                res = client.get(poll_url, headers=headers)
                res.raise_for_status()
                prediction = res.json()
            if prediction.get("status") != "succeeded":
                return GenerationOutcome(
                    "beta", None, "", int((time.perf_counter() - t0) * 1000), None,
                    note=f"Replicate 예측 실패: {prediction.get('status')} {prediction.get('error')}",
                )
            output = prediction.get("output")
            output_url = output[0] if isinstance(output, list) else output
            img_res = client.get(output_url)
            img_res.raise_for_status()
            image_bytes = img_res.content
    except httpx.HTTPError as exc:
        return GenerationOutcome(
            "beta", None, "", int((time.perf_counter() - t0) * 1000), None,
            note=f"Replicate 호출 실패: {exc}",
        )
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return GenerationOutcome("beta", image_bytes, "image/png", latency_ms, None, note="비용 TBD — Replicate 콘솔 실측 필요")


# ============================================================================
# 4. InsightFace 임베딩 유사도 측정
# ============================================================================


def build_face_analyzer() -> "FaceAnalysis":
    """InsightFace 얼굴 분석기 lazy 초기화 (buffalo_l — 최초 실행 시 모델 자동 다운로드)."""
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    return app


def embed_face(app: "FaceAnalysis", image_bytes: bytes) -> "np.ndarray | None":
    """이미지 바이트 → 가장 큰 얼굴의 임베딩. 얼굴 미검출 시 None.

    생체 안전: 반환된 임베딩은 호출자가 코사인 유사도 계산 직후 폐기한다.
    파일·DB·로그에 저장 금지 — 스파이크 종료 시 메모리 상의 값도 전량 파기 대상.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)[:, :, ::-1]  # RGB -> BGR (insightface 기대 포맷)
    faces = app.get(arr)
    if not faces:
        return None
    best = max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))
    return best.normed_embedding


def cosine_similarity(a: "np.ndarray", b: "np.ndarray") -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


# ============================================================================
# 5. 리포트 (유사도 · 지연 · 비용 · Δcos)
# ============================================================================


@dataclass
class SpikeRow:
    path: str
    similarity: float | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    delta_cos: float | None = None
    note: str = ""


def print_report(rows: list[SpikeRow]) -> None:
    print("\n" + "=" * 92)
    print(f"{'경로':<8}{'유사도(vs ref)':<16}{'지연(ms)':<12}{'비용(USD)':<12}{'Δcos':<10}{'비고'}")
    print("-" * 92)
    for r in rows:
        sim = f"{r.similarity:.4f}" if r.similarity is not None else "n/a"
        lat = str(r.latency_ms) if r.latency_ms is not None else "n/a"
        cost = f"{r.cost_usd:.3f}" if r.cost_usd is not None else "TBD"
        dcos = f"{r.delta_cos:.4f}" if r.delta_cos is not None else "n/a"
        print(f"{r.path:<8}{sim:<16}{lat:<12}{cost:<12}{dcos:<10}{r.note}")
    print("=" * 92)
    print(
        "판정 기준(go/no-go): docs/personalization/phase0-spike-plan.md §3.7 — "
        "정책 게이트 선통과 + 유사도/비용/지연 3지표 통과 + Δcos 게이트 통과."
    )


def save_report_json(rows: list[SpikeRow], out_path: Path) -> None:
    """집계 스코어만 저장한다. 임베딩 벡터·이미지 바이트는 절대 포함하지 않는다(생체 안전)."""
    payload = [
        {
            "path": r.path,
            "similarity": r.similarity,
            "latencyMs": r.latency_ms,
            "costUsd": r.cost_usd,
            "deltaCos": r.delta_cos,
            "note": r.note,
        }
        for r in rows
    ]
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n리포트 저장: {out_path} (임베딩·이미지 바이트 미포함 — 집계 스코어만)")


# ============================================================================
# 6. 오케스트레이션
# ============================================================================


async def run_alpha_flow(args: argparse.Namespace, face_refs: list[tuple[bytes, str]]) -> SpikeRow:
    model = args.model or (load_settings().model_image_high if load_settings else "gemini-3-pro-image")
    prompt_a = build_body_prompt(args.height_cm, args.weight_kg, args.body_type)
    prompt_b = build_body_prompt(args.height_cm, args.weight_kg, args.body_type_b)

    outcome_a = await call_path_alpha(prompt_a, face_refs, model, args.image_size)
    if outcome_a.image_bytes is None:
        return SpikeRow("alpha", note=outcome_a.note)

    row = SpikeRow("alpha", latency_ms=outcome_a.latency_ms, cost_usd=outcome_a.cost_usd)

    if FaceAnalysis is not None and np is not None:
        analyzer = build_face_analyzer()
        ref_emb = embed_face(analyzer, face_refs[0][0])
        gen_a_emb = embed_face(analyzer, outcome_a.image_bytes)
        if ref_emb is not None and gen_a_emb is not None:
            sim_a = cosine_similarity(ref_emb, gen_a_emb)
            row.similarity = sim_a

            # Δcos: 신체 파라미터(체형)만 바꿔 재생성 후 identity 유지 여부 측정.
            outcome_b = await call_path_alpha(prompt_b, face_refs, model, args.image_size)
            if outcome_b.image_bytes is not None:
                gen_b_emb = embed_face(analyzer, outcome_b.image_bytes)
                if gen_b_emb is not None:
                    sim_b = cosine_similarity(ref_emb, gen_b_emb)
                    row.delta_cos = abs(sim_a - sim_b)
        # 임베딩은 여기서 스코프 종료 — 이후 어디에도 보존하지 않는다(생체 안전).
    else:
        row.note = "insightface 미설치 — 유사도/Δcos 측정 생략"

    return row


def run_beta_flow(args: argparse.Namespace, face_refs: list[tuple[bytes, str]]) -> SpikeRow:
    api_token = os.getenv("REPLICATE_API_TOKEN", "")
    model_version = os.getenv("REPLICATE_MODEL_VERSION", "")
    prompt = build_body_prompt(args.height_cm, args.weight_kg, args.body_type)
    outcome = call_path_beta_replicate(prompt, face_refs, api_token, model_version)
    if outcome.image_bytes is None:
        return SpikeRow("beta", note=outcome.note)

    row = SpikeRow("beta", latency_ms=outcome.latency_ms, cost_usd=outcome.cost_usd, note=outcome.note)
    if FaceAnalysis is not None and np is not None:
        analyzer = build_face_analyzer()
        ref_emb = embed_face(analyzer, face_refs[0][0])
        gen_emb = embed_face(analyzer, outcome.image_bytes)
        if ref_emb is not None and gen_emb is not None:
            row.similarity = cosine_similarity(ref_emb, gen_emb)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--faces-dir", type=Path, default=None, help="합성/본인/동의 얼굴 사진 디렉토리")
    parser.add_argument("--path", choices=["alpha", "beta", "both"], default="alpha")
    parser.add_argument("--photos", type=int, default=3, choices=[1, 2, 3], help="레퍼런스 얼굴 장수(1~3장 A/B용)")
    parser.add_argument("--height-cm", type=float, default=165.0)
    parser.add_argument("--weight-kg", type=float, default=55.0)
    parser.add_argument("--body-type", default="보통", help="1차 생성 체형")
    parser.add_argument("--body-type-b", default="근육", help="Δcos(신원-속성분리) 측정용 2차 체형")
    parser.add_argument("--image-size", default="1K", choices=["1K", "2K", "4K"])
    parser.add_argument("--model", default=None, help="미지정 시 settings.model_image_high(gemini-3-pro-image)")
    parser.add_argument("--out", type=Path, default=None, help="JSON 리포트 저장 경로(집계 스코어만, 선택)")
    args = parser.parse_args()

    checks = check_preconditions(args.faces_dir, args.path)
    ready = print_precondition_report(checks)
    if not ready:
        print(
            "\n하나 이상의 실행 전제조건이 충족되지 않았습니다. 위 안내를 따라 준비 후 "
            "다시 실행하세요."
        )
        print(
            "(2026-07-15 기준: API 키·평가 라이브러리·테스트 얼굴 전부 부재 확인됨 — "
            "docs/personalization/phase0-spike-plan.md §7 실행 블로커 참고)"
        )
        return 1

    # ---- 여기부터는 전제조건이 모두 충족된 경우에만 실행된다 ----
    all_faces = list_face_images(args.faces_dir)
    selected = all_faces[: args.photos]
    print(f"\n레퍼런스 얼굴 {len(selected)}장 사용: {[p.name for p in selected]}")
    face_refs = load_face_bytes(selected)

    rows: list[SpikeRow] = []
    if args.path in ("alpha", "both"):
        rows.append(asyncio.run(run_alpha_flow(args, face_refs)))
    if args.path in ("beta", "both"):
        rows.append(run_beta_flow(args, face_refs))

    print_report(rows)
    if args.out:
        save_report_json(rows, args.out)

    print(
        "\n[생체 안전 리마인더] 스파이크 종료 시 원본 얼굴·생성 결과 이미지·임베딩을 "
        "전량 파기하세요(로컬 디스크 포함, 예외 없음). "
        "docs/personalization/phase0-spike-plan.md §4 참고."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
