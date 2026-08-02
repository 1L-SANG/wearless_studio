"""Shadow provenance 계약 한 벌 — 수집·resume·backfill·manifest 가 **같은 것**을 본다.

직전까지 정본이 두 개였다. 수집기는 nested `provenance.run` / `provenance.case` 를
쓰는데 manifest 는 옛 평면 7필드만 검사해서, run/case 가 아예 없는 legacy row 도
서로 다른 run 이 섞인 dataset 도 `validForCalibration=true` 를 받았다. 검증하는
쪽과 기록하는 쪽이 다른 구조를 보면 검증은 통과 도장을 찍는 절차가 된다.

그래서 구조와 검사를 여기 한 곳에 둔다. 문제는 **코드 목록**으로 돌려준다 —
"왜 못 쓰는가"가 manifest·리포트·CLI 어디서든 같은 말로 나와야 하기 때문이다.

이 모듈은 파일·네트워크에 손대지 않는다. 순수 검증이다.
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
    from .safe_paths import (SAFE_FILENAME, SAFE_ID, UnsafePath, file_sha256,
                             is_sha256_hex, safe_resolve)

    problems: set[str] = set()
    for r in rows:
        if not has_output(r):
            continue                      # 실패 row 는 대조할 출력이 없다
        prov = (r.get("provenance") or {})
        for field in ("sourceSha256", "outputSha256"):
            if not is_sha256_hex(prov.get(field)):
                problems.add(f"invalid_sha_format:{field}")
        try:
            out = safe_resolve(dataset_dir, r.get("id"), SAFE_ID, suffix=".png")
        except UnsafePath as e:
            problems.add("output_artifact_missing" if "regular file" in str(e)
                         else "unsafe_output_path")
        else:
            if is_sha256_hex(prov.get("outputSha256")) and \
                    file_sha256(out) != prov["outputSha256"]:
                problems.add("output_hash_mismatch")
        try:
            src = safe_resolve(source_dir, r.get("source"), SAFE_FILENAME)
        except UnsafePath as e:
            problems.add("source_artifact_missing" if "regular file" in str(e)
                         else "unsafe_source_path")
        else:
            if is_sha256_hex(prov.get("sourceSha256")) and \
                    file_sha256(src) != prov["sourceSha256"]:
                problems.add("source_hash_mismatch")
    return sorted(problems)
