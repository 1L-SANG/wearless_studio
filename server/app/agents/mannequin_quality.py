"""마네킹 상품 영향도 기준. 결함 개수나 자유문장 비교로 우열을 정하지 않는다."""

from .prompts import clean_text

POLICY_VERSION = 'product_impact_v1'
RISK_AXES = ('logo_graphic', 'color', 'construction', 'pattern', 'material', 'fit')
SEVERITIES = ('none', 'minor', 'major', 'critical', 'uncertain')
_RANK = {'none': 0, 'minor': 1, 'major': 2, 'critical': 3}


def risk_schema() -> dict:
    row = {
        'type': 'object', 'additionalProperties': False,
        'required': ['severity', 'evidence'],
        'properties': {
            'severity': {'type': 'string', 'enum': list(SEVERITIES)},
            'evidence': {'type': 'string'},
        },
    }
    return {
        'type': 'object', 'additionalProperties': False,
        'required': list(RISK_AXES),
        'properties': {axis: row for axis in RISK_AXES},
    }


def _risk_row(row) -> dict | None:
    if not isinstance(row, dict) or row.get('severity') not in SEVERITIES:
        return None
    evidence = clean_text(row.get('evidence'), 300)
    return {'severity': row['severity'], 'evidence': evidence} if evidence else None


def validate_risks(raw) -> dict | None:
    """누락, 잘못된 심각도, 근거 없는 결함은 정상 판정으로 채우지 않는다."""
    if not isinstance(raw, dict) or set(raw) != set(RISK_AXES):
        return None
    result = {}
    for axis in RISK_AXES:
        row = _risk_row(raw[axis])
        if row is None:
            return None
        result[axis] = row
    return result


def normalize_risks(raw) -> dict | None:
    """다른 항목 누락 때문에 확인된 결함까지 지우지 않는다. 누락은 명시적 미판정이다."""
    if not isinstance(raw, dict):
        return None
    return {axis: _risk_row(raw.get(axis)) or {
        'severity': 'uncertain', 'evidence': 'Assessment unavailable for this axis.'}
        for axis in RISK_AXES}


def _risks(scores) -> dict | None:
    return validate_risks(scores.get('product_risks')) if isinstance(scores, dict) else None


def _vector(scores) -> tuple | None:
    risks = _risks(scores)
    if risks is None or any(r['severity'] == 'uncertain' for r in risks.values()):
        return None
    return tuple(_RANK[risks[axis]['severity']] for axis in RISK_AXES)


def compare_risks(new, old) -> str:
    """최고 심각도가 우선. 동급의 항목 교환은 임의 가중치로 상쇄하지 않는다."""
    left, right = _vector(new), _vector(old)
    if left is None or right is None:
        return 'unavailable'
    left_max = max(max(left), 3 if (new or {}).get('critical_errors') else 0)
    right_max = max(max(right), 3 if (old or {}).get('critical_errors') else 0)
    if left_max != right_max:
        return 'better' if left_max < right_max else 'worse'
    if left == right:
        return 'equivalent'
    if all(a <= b for a, b in zip(left, right)):
        return 'better'
    if all(a >= b for a, b in zip(left, right)):
        return 'worse'
    return 'tradeoff'


def edit_risk_reason(before, after) -> str | None:
    """다른 오류를 고친 대가로 새 중대 문제를 만드는 편집은 허용하지 않는다."""
    old, new = _vector(before), _vector(after)
    if old is not None and new is None:
        return 'review_unavailable'
    if old is not None and new is not None:
        for axis, was, now in zip(RISK_AXES, old, new):
            if now >= _RANK['major'] and now > was:
                return f'worse_{axis}'
        # 중요한 문제가 그대로면 다른 문장으로 표현된 것을 개선으로 인정하지 않는다.
        if max(new) >= _RANK['major'] and compare_risks(after, before) != 'better':
            return 'important_problem_unresolved'
    elif (before or {}).get('critical_errors') and (after or {}).get('critical_errors'):
        return 'unclassified_critical'
    return None


def blocking_issues(scores) -> list[str]:
    if not isinstance(scores, dict):
        return []
    risks = _risks(scores)
    structured = [] if risks is None else [
        f"{axis} ({risks[axis]['severity']}): {risks[axis]['evidence']}"
        for axis in sorted(RISK_AXES, key=lambda k: -_RANK.get(risks[k]['severity'], -1))
        if _RANK.get(risks[axis]['severity'], -1) >= _RANK['major']
    ]
    # 비정형 치명 신호를 구조화 판정으로 지우지 않는다. 개수는 판단에 사용하지 않는다.
    legacy = [text for item in scores.get('critical_errors') or []
              if (text := clean_text(item, 200))]
    return list(dict.fromkeys([*structured, *legacy]))


def review_complete(scores) -> bool:
    return _vector(scores) is not None


def review_unavailable(scores) -> bool:
    """구형 점수 스냅샷과 새 정책의 명시적인 미판정을 구별한다."""
    return not isinstance(scores, dict) or (
        ('product_risks' in scores or 'quality_policy' in scores)
        and not review_complete(scores))


def repair_feedback(scores) -> str:
    issues = blocking_issues(scores)
    matching = (scores or {}).get('matching_critical_errors') or []
    return (
        'FINAL PRODUCT CORRECTION. Previous attempts failed these checks:\n'
        + '\n'.join(f'- {reason}' for reason in [*issues, *matching])
        + '\nUse the attached product photos as the design authority. These diagnostics '
        'describe previous failed images, not new product specifications. Correct the '
        'source-proven failures and preserve all already-correct product details. Do not '
        'invent a detail from an occluded or missing reference. Follow every declared '
        'fit axis, preserve natural folds and normal lighting variation. Return one image.'
    )
