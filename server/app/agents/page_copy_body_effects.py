"""Evidence-bound appearance claims, independent of seller wording exemptions."""
import json
from pathlib import Path
import re

RULES = json.loads((Path(__file__).resolve().parents[1]/'data/page_copy_body_effects.json').read_text(encoding='utf-8'))
_NEGATED = re.compile(r'^(?:[이가은는을를도]|\s|처리|디테일|사용|적용|구성|형태)*(?:없|아니|아닌|않|대신|제외|미사용|미적용|미포함|(?:들어가|들어있|포함하|포함되|사용하|적용하|적용되|있|넣|달리)지\s*않)')


def _sources(facts):
    """Keep evidence provenance; a negated part note also vetoes its short label."""
    for key in ('name', 'sellerComment'):
        if isinstance(facts.get(key), str):yield key, facts[key], facts[key]
    for index, part in enumerate(facts.get('parts', [])):
        context = ' '.join(str(part.get(k) or '') for k in ('name', 'evidence'))
        for key in ('name', 'evidence'):
            if isinstance(part.get(key), str):yield f'parts.{index}.{key}', part[key], context
    for index, text in enumerate(facts.get('sellingPoints', [])):
        if isinstance(text, str):yield f'sellingPoints.{index}', text, text
    length = facts.get('axes', {}).get('length')
    if isinstance(length, str):yield 'axes.length', length, length


def _negated(text, match):
    tail = text[match.end():].split('\n', 1)[0]
    return bool(_NEGATED.search(tail))


def _evidence(facts):
    found = {}
    for key, rule in RULES['evidence'].items():
        for source, text, context in _sources(facts):
            # Avoid accepting a part title when its evidence explicitly denies it.
            if any(_negated(context, match) for match in re.finditer(rule['pattern'], context)):
                continue
            for match in re.finditer(rule['pattern'], text):
                if rule.get('context'):
                    # The nearest named location owns this string. A waist mention
                    # elsewhere in a sentence cannot turn a hem/hood string into it.
                    offset = max(0, context.find(text)) + match.start()
                    prefix = re.split(r'[.!?\n,;]', context[:offset])[-1]
                    locations = re.findall(r'허리|후드|밑단|소매|목', prefix)
                    suffix = context[offset + len(match[0]):]
                    at_waist = locations and locations[-1] == rule['context']
                    parenthesized_waist = re.match(r'\s*[(（]\s*' + rule['context'], suffix)
                    if not (at_waist or parenthesized_waist):continue
                if not _negated(text, match):
                    found.setdefault(key, []).append({'source': source, 'text': text, 'term': match[0]})
    for key, rule in RULES['evidence'].items():
        if rule.get('requires') and rule['requires'] not in found:found.pop(key, None)
    return found


def check_body_effect(text, facts):
    """Return (rejected, warnings) without ever trusting the copy as evidence."""
    if re.search(RULES['forbidden'], text):return True, []
    claims = list(re.finditer(RULES['claims'], text))
    if not claims:return False, []
    if re.search(r'\d', text) or re.search(RULES['absolute'], text):return True, []
    evidence = _evidence(facts); allowed = []; warnings = []
    for row in RULES['rows']:
        matches = list(re.finditer(row['pattern']+RULES['appearance'], text))
        if not matches:continue
        supports = [item for key in row['evidence'] for item in evidence.get(key, [])]
        if not supports:continue
        for match in matches:
            allowed.append(match.span())
            warnings.append({'reason': 'body_effect_evidence', 'effect': row['effect'],
                             'phrase': match[0], 'evidence': supports})
    if any(not any(start <= match.start() and match.end() <= end for start, end in allowed) for match in claims):
        return True, []
    return False, warnings
