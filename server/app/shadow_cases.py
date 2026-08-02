"""Shadow 수집 case 정의 정본 — collector·resume·backfill·manifest 가 함께 import 한다.

이 정의가 collector 스크립트 안에 있던 게 문제였다. manifest 가 그걸 쓰려면
스크립트 전체를 동적 import 해야 했고(그 안에는 cv2 같은 무거운 의존이 있다),
실패하면 "검사 생략"으로 넘어가 case 정의를 확인하지 못한 dataset 이 그대로
validForCalibration=true 를 받았다. 검사를 못 한 것과 통과한 것은 다른 일이다.

여기에는 무거운 의존이 없다. 프롬프트는 이미지 바이트와 무관하게 조립되므로
provider 도, 실제 이미지도 필요 없다 — expected fingerprint 를 순수하게 계산한다.
"""

from __future__ import annotations

from .agents import cut_variator, edit_intent_vision
from .agents.gemini_image import InlineImage
from .services import edit_qc_scope, editor_vary
from .shadow_provenance import canonical, sha256_hex

# 수집 축. 이름은 fingerprint 의 일부라 바꾸면 기존 dataset 과 이어 모을 수 없다.
VARY_CASES = [
    ("bg_only", [{"type": "bg", "value": "밝은 스튜디오 배경"}]),
    ("shot", [{"type": "shot", "value": "전신"}]),
    ("direction", [{"type": "direction", "value": "측면"}]),
    ("pose", [{"type": "pose", "value": "자연스러운 서 있는 자세"}]),
    ("bg_and_shot", [{"type": "bg", "value": "회색 배경"}, {"type": "shot", "value": "상반신"}]),
]

# 프롬프트는 이미지 내용과 무관하다(첨부만 될 뿐). 그래서 fingerprint 계산에는
# 실제 이미지가 필요 없다 — 1픽셀짜리 자리표시자로 충분하다.
_PLACEHOLDER = InlineImage("image/jpeg", b"\xff\xd8\xff\xd9")


class CaseDefinitionError(Exception):
    """case 정의를 읽거나 fingerprint 를 계산하지 못했다.

    이 예외를 삼키고 "검사 생략"으로 넘어가면 안 된다 — 확인하지 못한 것을
    통과로 세는 순간 manifest 는 아무것도 보증하지 않는다.
    """


def normalized_cases(cases=None) -> list[dict]:
    """case 정의의 정본 표현(정렬). 이름·edit type·정규화 changes."""
    out = []
    for name, changes in (cases if cases is not None else VARY_CASES):
        out.append({"case": name,
                    "editType": editor_vary.edit_type_for(changes),
                    "changes": editor_vary.validate_changes(changes)})
    return sorted(out, key=lambda c: c["case"])


def case_set_sha256(cases=None) -> str:
    """case 집합 전체의 해시 — 추가·삭제·변경 어느 쪽이든 값이 바뀐다."""
    return sha256_hex(canonical(normalized_cases(cases)))


def vision_prepared(changes: list):
    """이 case 의 Vision 요청을 provider 없이 만든다 — 실행과 같은 builder 다."""
    scope = editor_vary.semantic_scope(changes)
    return edit_intent_vision.prepare(
        edit_type=editor_vary.edit_type_for(changes),
        adjustments={"changes": changes},
        allowed_scope=edit_qc_scope.vision_scope(scope))


def generation_prepared(settings, changes: list, *, source=None):
    """생성 요청을 provider 없이 만든다. 프롬프트는 이미지에 의존하지 않는다."""
    return cut_variator.prepare(settings, source or _PLACEHOLDER, changes, None)


def case_fingerprint(settings, *, case_name: str, changes: list,
                     prepared=None) -> dict:
    """이 case 가 **실제로** 어떤 프롬프트를 내는지 — 생성과 Vision 둘 다.

    Vision 템플릿 해시만 봐서는 build_prompt 로직·allowed scope·changes 렌더링
    변경을 못 잡는다(템플릿 파일은 그대로니까). 렌더링 결과의 해시를 둔다.
    """
    gen = prepared or generation_prepared(settings, changes)
    return {
        "case": case_name,
        "editType": editor_vary.edit_type_for(changes),
        "changes": editor_vary.validate_changes(changes),
        "generationPromptSha256": sha256_hex(gen.prompt.encode()),
        "visionPromptSha256": vision_prepared(changes).prompt_sha256,
    }


def expected_case_fingerprints(settings, cases=None) -> dict[str, dict]:
    """case 이름 → 전체 fingerprint. resume·backfill·manifest 가 같은 것을 쓴다.

    실패는 삼키지 않고 CaseDefinitionError 로 올린다 — 원문·경로·환경값은 싣지
    않는다(호출자가 manifest·로그에 그대로 흘릴 수 있다).
    """
    try:
        return {c["case"]: case_fingerprint(settings, case_name=c["case"],
                                            changes=c["changes"])
                for c in normalized_cases(cases)}
    except CaseDefinitionError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise CaseDefinitionError(type(exc).__name__) from None


def run_fingerprint(prepared, *, cases=None) -> dict:
    """실험 조건 스냅샷. provider 를 부르지 않고 prepare 결과만으로 만든다."""
    return {
        "generationModel": prepared.model,
        "generationTemplateSha256": cut_variator.template_sha256(),
        "visionPromptTemplateVersion": edit_intent_vision.PROMPT_VERSION,
        "visionTemplateSha256": edit_intent_vision.template_sha256(),
        "qcPolicyVersion": edit_qc_scope.QC_POLICY_VERSION,
        "caseSetSha256": case_set_sha256(cases),
        "imageSize": prepared.image_size,
        "aspectRatio": getattr(prepared, "aspect_ratio", None),
    }
