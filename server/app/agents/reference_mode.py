"""Reference inputs are derived from the cut, not a user-selectable scope.

The pose token is reserved for styling series. Their registry binding must still
validate the group and load its shared scene and pose assets before generation.
"""


def reference_scope(spec) -> str:
    cut = spec.get('cutType') or spec.get('cut_type')
    group = spec.get('spaceGroupId') or spec.get('space_group_id')
    return 'pose' if cut == 'styling' and group else 'all'
