// Reference scope is derived internally. The pose token belongs only to place
// series; it is not a standalone choice in the generation-example gallery.
export function referenceScopeForBlock(block) {
  const cut = block?.cutType || block?.cut_type;
  const group = block?.spaceGroupId || block?.space_group_id;
  return cut === 'styling' && group ? 'pose' : 'all';
}
