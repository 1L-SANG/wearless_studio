"""Shadow provenance 계약 한 벌 — 수집·resume·backfill·manifest 가 **같은 것**을 본다.

직전까지 정본이 두 개였다. 수집기는 nested `provenance.run` / `provenance.case` 를
쓰는데 manifest 는 옛 평면 7필드만 검사해서, run/case 가 아예 없는 legacy row 도
서로 다른 run 이 섞인 dataset 도 `validForCalibration=true` 를 받았다. 검증하는
쪽과 기록하는 쪽이 다른 구조를 보면 검증은 통과 도장을 찍는 절차가 된다.

그래서 구조와 검사를 여기 한 곳에 둔다. 문제는 **코드 목록**으로 돌려준다 —
"왜 못 쓰는가"가 manifest·리포트·CLI 어디서든 같은 말로 나와야 하기 때문이다.

책임은 세 갈래다. 섞이면 "순수 모듈"이라는 거짓 문서가 남는다(실제로 남아 있었다).
  1) provenance **구조** 검증 — 순수. 파일도 네트워크도 건드리지 않는다.
  2) artifact **파일** 검증/번들 해시 — 디스크를 읽는다(safe_paths 경유).
  3) manifest **binding** 검증 — 순수. manifest 딕셔너리 자체만 본다.
네트워크는 어느 갈래도 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json

# 실험 조건. 하나라도 다르면 다른 실험이고, 섞으면 분리할 수 없다.
RUN_KEYS = (
    "generationModel", "generationTemplateSha256",
    "visionPromptTemplateVersion", "visionTemplateSha256",
    "qcPolicyVersion", "caseSetSha256", "imageSize", "aspectRatio",
)

# 그 조건 아래 case 마다 실제로 렌더링된 것. case 별로 달라야 정상이다.
CASE_KEYS = ("case", "editType", "changes",
             "generationPromptSha256", "visionPromptSha256")

# 성공 row 가 반드시 들고 있어야 하는 것.
ROW_KEYS = ("sourceSha256", "outputSha256", "qcPolicyVersion", "run", "case")

# 해시로 쓰이는 필드 — 64자리 소문자 hex 가 아니면 해시가 아니다.
ROW_SHA_KEYS = ("sourceSha256", "outputSha256")
RUN_SHA_KEYS = ("generationTemplateSha256", "visionTemplateSha256", "caseSetSha256")
CASE_SHA_KEYS = ("generationPromptSha256", "visionPromptSha256")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def has_output(row) -> bool:
    """출력이 있는 row 만 provenance 증거다 — 실패 row 는 증명할 결과가 없다."""
    return bool((row or {}).get("output_id"))


def run_of(row) -> dict:
    return ((row or {}).get("provenance") or {}).get("run") or {}


def case_of(row) -> dict:
    return ((row or {}).get("provenance") or {}).get("case") or {}


def run_key(run: dict) -> bytes:
    return canonical({k: (run or {}).get(k) for k in RUN_KEYS})


def case_key(case: dict) -> bytes:
    return canonical({k: (case or {}).get(k) for k in CASE_KEYS})


def _row_problems(row) -> list[str]:
    prov = (row or {}).get("provenance") or {}
    out = []
    if not prov:
        return ["missing_provenance"]
    if not prov.get("run"):
        out.append("missing_run_fingerprint")
    if not prov.get("case"):
        out.append("missing_case_fingerprint")
    for k in ROW_KEYS:
        if k in ("run", "case"):
            continue
        if not prov.get(k):
            out.append(f"missing_row_field:{k}")
    for k in RUN_KEYS:
        if prov.get("run") and run_of(row).get(k) in (None, ""):
            out.append(f"missing_run_field:{k}")
    # 해시로 쓰이는 값은 전부 정확히 64자리 소문자 hex 여야 한다. 형식이 틀린 값은
    # "다른 해시"가 아니라 **해시가 아니다** — 비교로 걸러지지 않으므로 여기서 잡는다.
    from .safe_paths import is_sha256_hex
    for k in ROW_SHA_KEYS:
        if prov.get(k) is not None and not is_sha256_hex(prov.get(k)):
            out.append(f"invalid_sha_format:{k}")
    for k in RUN_SHA_KEYS:
        v = run_of(row).get(k)
        if prov.get("run") and v is not None and not is_sha256_hex(v):
            out.append(f"invalid_sha_format:run.{k}")
    for k in CASE_SHA_KEYS:
        v = case_of(row).get(k)
        if prov.get("case") and v is not None and not is_sha256_hex(v):
            out.append(f"invalid_sha_format:case.{k}")
    for k in CASE_KEYS:
        if prov.get("case") and case_of(row).get(k) in (None, ""):
            # changes 는 빈 배열이 정상값이다("비슷한 컷") — 키 존재만 본다.
            if not (k == "changes" and "changes" in case_of(row)):
                out.append(f"missing_case_field:{k}")
    return out


def _expected_problems(row, expected: dict) -> list[str]:
    """저장된 case fingerprint 를 현재 expected 와 **전 항목** 비교한다.

    이름과 caseSetSha256 만 보던 게 문제였다. 그러면 row 가 하나뿐인 case 의
    changes·editType·프롬프트 해시를 고쳐도 통과한다 — dataset 내부에서는 서로
    비교할 상대가 없으니 "일관적"이기 때문이다. 현재 정의와 맞춰야 잡힌다.
    """
    c = case_of(row)
    name = str(c.get("case"))
    exp = expected.get(name)
    if exp is None:
        return [f"unknown_case:{name}"]
    out = []
    if c.get("editType") != exp.get("editType"):
        out.append(f"case_edit_type_mismatch:{name}")
    if canonical(c.get("changes")) != canonical(exp.get("changes")):
        out.append(f"case_changes_mismatch:{name}")
    if c.get("generationPromptSha256") != exp.get("generationPromptSha256"):
        out.append(f"generation_prompt_mismatch:{name}")
    if c.get("visionPromptSha256") != exp.get("visionPromptSha256"):
        out.append(f"vision_prompt_mismatch:{name}")
    return out


def validate_dataset(rows, *, expected_cases: dict | list | None = None) -> list[str]:
    """데이터셋 전체의 문제 코드(정렬·중복 제거). 빈 목록이면 캘리브레이션에 쓸 수 있다.

    expected_cases 는 `{case: fingerprint}` 매핑이다(구버전 호환으로 리스트도 받되
    그때는 이름 집합만 본다). 주지 않으면 현재 정의와의 비교는 하지 않는다 —
    호출자가 "확인 못 함"을 별도 문제 코드로 남길 책임이 있다.
    """
    problems: set[str] = set()
    done = [r for r in rows if has_output(r)]
    if not done:
        # 출력이 하나도 없으면 증명할 결과가 없다. "문제 없음"이 아니라 "쓸 수 없음"이다.
        return ["no_output_rows"]

    for r in done:
        problems.update(_row_problems(r))

    runs = {run_key(run_of(r)) for r in done}
    if len(runs) > 1:
        problems.add("mixed_run_fingerprint")

    # dataset 내부 일관성 — 같은 case 이름이 두 얼굴을 가지면 안 된다.
    by_case: dict[str, set] = {}
    for r in done:
        c = case_of(r)
        by_case.setdefault(str(c.get("case")), set()).add(case_key(c))
    for name, keys in by_case.items():
        if len(keys) > 1:
            problems.add(f"inconsistent_case_fingerprint:{name}")

    if expected_cases is not None:
        expected = (expected_cases if isinstance(expected_cases, dict)
                    else {c["case"]: c for c in expected_cases})
        full = all(isinstance(v, dict) and "generationPromptSha256" in v
                   for v in expected.values())
        for r in done:
            if full:
                problems.update(_expected_problems(r, expected))
            elif str(case_of(r).get("case")) not in expected:
                problems.add(f"unknown_case:{case_of(r).get('case')}")
        expected_sha = sha256_hex(canonical(sorted(
            [{k: v[k] for k in ("case", "editType", "changes")}
             for v in expected.values()], key=lambda c: c["case"])))
        for r in done:
            if run_of(r).get("caseSetSha256") != expected_sha:
                problems.add("case_set_mismatch")
                break

    return sorted(problems)


def dataset_run_fingerprint(rows) -> dict | None:
    """성공 row 들이 하나의 run 을 가리킬 때 그 run. 섞였거나 없으면 None."""
    done = [r for r in rows if has_output(r)]
    if not done:
        return None
    runs = {run_key(run_of(r)) for r in done}
    if len(runs) != 1:
        return None
    return {k: run_of(done[0]).get(k) for k in RUN_KEYS}


def case_index(rows) -> dict[str, dict]:
    """case 이름 → 저장된 case fingerprint(성공 row 기준)."""
    out: dict[str, dict] = {}
    for r in rows:
        if not has_output(r):
            continue
        c = case_of(r)
        out.setdefault(str(c.get("case")), c)
    return out


# ── artifact 무결성 ─────────────────────────────────────────────────────────
# manifest 가 PNG 존재만 보던 게 문제였다. 파일이 바뀌어도, source 가 달라도,
# 해시가 hex 조차 아니어도 통과했다(마지막 건 _bundle_sha 에서 crash 했다).

def artifact_problems(rows, *, dataset_dir, source_dir) -> list[str]:
    """성공 row 의 실제 파일을 provenance 해시와 대조한다. → 문제 코드 목록."""
    from .safe_paths import (SAFE_FILENAME, SAFE_ID, UnsafePath, UnsafePathReason,
                             file_sha256, is_sha256_hex, safe_resolve)

    problems: set[str] = set()
    for r in rows:
        if not has_output(r):
            continue                      # 실패 row 는 대조할 출력이 없다
        prov = (r.get("provenance") or {})
        for field in ("sourceSha256", "outputSha256"):
            if not is_sha256_hex(prov.get(field)):
                problems.add(f"invalid_sha_format:{field}")
        for kind, base, name, pattern, suffix in (
                ("output", dataset_dir, r.get("id"), SAFE_ID, ".png"),
                ("source", source_dir, r.get("source"), SAFE_FILENAME, None)):
            try:
                path = safe_resolve(base, name, pattern, suffix=suffix)
            except UnsafePath as e:
                # 사유는 enum 으로 온다 — 메시지를 다시 파싱하지 않는다.
                problems.add(f"{kind}_artifact_missing"
                             if e.reason is UnsafePathReason.NOT_REGULAR_FILE
                             else f"unsafe_{kind}_path")
                continue
            field = "outputSha256" if kind == "output" else "sourceSha256"
            if is_sha256_hex(prov.get(field)) and file_sha256(path) != prov[field]:
                problems.add(f"{kind}_hash_mismatch")
    return sorted(problems)


def _safe_bundle(items, base, pattern, *, suffix=None) -> str | None:
    """이름 + 내용 해시. 경계 밖은 **읽지 않고**, 하나라도 못 읽으면 None 이다.

    "일부만 넣은 체크섬"은 무엇을 잰 값인지 말할 수 없다 — 계산 불가는 null 로 둔다.
    """
    from .safe_paths import UnsafePath, file_sha256, safe_resolve

    h = hashlib.sha256()
    seen = 0
    for name in sorted({n for n in items if isinstance(n, str) and n}):
        try:
            path = safe_resolve(base, name, pattern, suffix=suffix)
        except UnsafePath:
            return None
        h.update(name.encode())
        h.update(bytes.fromhex(file_sha256(path)))
        seen += 1
    return h.hexdigest() if seen else None


def source_bundle_sha256(rows, source_dir) -> str | None:
    """성공 row 의 source 파일 묶음 해시. 안전하게 못 읽으면 None."""
    from .safe_paths import SAFE_FILENAME
    return _safe_bundle([r.get("source") for r in rows if has_output(r)],
                        source_dir, SAFE_FILENAME)


def output_bundle_sha256(rows, dataset_dir) -> str | None:
    """성공 row 의 결과 이미지 묶음 해시. 안전하게 못 읽으면 None."""
    from .safe_paths import SAFE_ID
    return _safe_bundle([r.get("id") for r in rows if has_output(r)],
                        dataset_dir, SAFE_ID, suffix=".png")


# ── manifest binding schema ────────────────────────────────────────────────
# manifest 는 "이 표본·이 파일들"에 대한 진술이다. 진술에 필요한 필드가 비어 있으면
# 비교가 통째로 생략되고, 그러면 아무 manifest 나 붙여도 통과한다. `{}` 조차도.

MANIFEST_REQUIRED = ("datasetId", "rawSampleManifestSha256", "sourceDataset",
                     "validForCalibration", "provenanceUnverified",
                     "provenanceProblems")

def manifest_binding_problems(manifest, *, has_output_rows: bool = True) -> list[str]:
    """manifest 자체의 형식 문제. 값 비교 이전에 **필드가 있는지**부터 본다."""
    from .safe_paths import SAFE_FILENAME, is_sha256_hex

    if manifest is None:
        return ["manifest_absent"]
    if not isinstance(manifest, dict):
        return ["manifest_binding_invalid:manifest"]
    out: list[str] = []
    for field in MANIFEST_REQUIRED:
        if field not in manifest:
            out.append(f"manifest_binding_missing:{field}")
    ds = manifest.get("datasetId")
    if "datasetId" in manifest and not (isinstance(ds, str) and ds.strip()
                                        and SAFE_FILENAME.match(ds)):
        out.append("manifest_binding_invalid:datasetId")
    if "rawSampleManifestSha256" in manifest and \
            not is_sha256_hex(manifest.get("rawSampleManifestSha256")):
        out.append("manifest_binding_invalid:rawSampleManifestSha256")
    src = manifest.get("sourceDataset")
    if "sourceDataset" in manifest:
        if not isinstance(src, dict):
            out.append("manifest_binding_invalid:sourceDataset")
        elif not is_sha256_hex(src.get("sha256")):
            out.append("manifest_binding_invalid:sourceDataset.sha256")
    for field in ("validForCalibration", "provenanceUnverified"):
        if field in manifest and not isinstance(manifest.get(field), bool):
            out.append(f"manifest_binding_invalid:{field}")
    if "provenanceProblems" in manifest and \
            not isinstance(manifest.get("provenanceProblems"), list):
        out.append("manifest_binding_invalid:provenanceProblems")
    # 필드끼리 모순되면 그 manifest 는 자기 자신을 반박한다. "쓸 수 있다"고 하면서
    # 동시에 "검증 못 했다"거나 "문제가 있다"고 적혀 있으면 어느 쪽도 믿을 수 없다.
    if manifest.get("validForCalibration") is True:
        contradictions = (
            manifest.get("provenanceUnverified") is True,
            bool(manifest.get("provenanceProblems")),
            bool(manifest.get("invalidReasons")),
        )
        if any(contradictions):
            out.append("manifest_binding_invalid:calibration_state")
    # 성공 output 이 있으면 그 묶음 해시도 진술의 일부여야 한다.
    if has_output_rows:
        if "outputBundleSha256" not in manifest:
            out.append("manifest_binding_missing:outputBundleSha256")
        elif not is_sha256_hex(manifest.get("outputBundleSha256")):
            out.append("manifest_binding_invalid:outputBundleSha256")
    return sorted(set(out))
