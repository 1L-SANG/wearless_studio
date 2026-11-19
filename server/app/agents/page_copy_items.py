"""Normalize completed text lists without changing source detail/photo identities."""
from copy import deepcopy
import re

_ITEM = re.compile(r'^items\.(\d+)(?:\.(.+))?$')


def compact_section_texts(texts, *, kind=None):
    """Pack surviving list groups, keeping FAQ and review fields on their item.

    Details are bound to source photos by their original indexes and are handled
    by each assembler. Non-list roles and per-row metadata remain unchanged.
    """
    rows = [deepcopy(row) for row in texts or [] if isinstance(row, dict)]
    if kind == 'points':return rows
    groups = {}
    for row in rows:
        match = _ITEM.fullmatch(str(row.get('role') or ''))
        if match and isinstance(row.get('text'), str) and row['text'].strip():
            groups.setdefault(int(match[1]), set()).add(match[2])
    valid = sorted(index for index, fields in groups.items()
                   if (kind != 'faq' or {'q', 'a'} <= fields) and
                      (kind != 'review' or 'text' in fields))
    numbering = {old: new for new, old in enumerate(valid)}
    output = []
    for row in rows:
        match = _ITEM.fullmatch(str(row.get('role') or ''))
        if match:
            old = int(match[1])
            if old not in numbering or not isinstance(row.get('text'), str) or not row['text'].strip():continue
            row['role'] = f'items.{numbering[old]}' + (f'.{match[2]}' if match[2] else '')
        output.append(row)
    return output
