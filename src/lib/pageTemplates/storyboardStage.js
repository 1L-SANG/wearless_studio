// Four columns at every desktop width. Short groups start a new row rather
// than splitting; only a group longer than a row continues onto another row.
export function packStageRows(blocks, { add = true } = {}) {
  const photos = blocks.filter(block => block.source !== 'text');
  const groups = [];
  const baseColor = photos[0]?.colorId;
  for (const block of photos) {
    const groupKey = block.spaceGroupId || (block.colorId !== baseColor ? `color:${block.colorId}` : null);
    const previous = groups.at(-1);
    if (groupKey && previous?.key === groupKey) previous.blocks.push(block);
    else if (block.similarSourceBlockId && previous?.blocks.some(source => source.id === block.similarSourceBlockId)) previous.blocks.push(block);
    else groups.push({ key: groupKey || block.id, kind: block.spaceGroupId ? 'space' : groupKey ? 'color' : 'single', blocks: [block] });
  }
  const rows = [];
  let row = [], occupied = 0;
  const flush = () => { if (row.length) rows.push(row); row = []; occupied = 0; };
  for (const group of groups) {
    const previousKind = row.at(-1)?.blocks.at(-1)?.stageRowKind;
    if (previousKind && group.blocks[0]?.stageRowKind && previousKind !== group.blocks[0].stageRowKind) flush();
    if (occupied + group.blocks.length > 4) flush();
    for (let offset = 0; offset < group.blocks.length; offset += 4) {
      const chunk = { ...group, blocks: group.blocks.slice(offset, offset + 4), continued: offset > 0, last: offset + 4 >= group.blocks.length };
      row.push(chunk);
      occupied += chunk.blocks.length;
      if (offset + 4 < group.blocks.length || occupied === 4) flush();
    }
  }
  if (add && photos.filter(block => !block.paleAddition).length < 12) {
    if (occupied === 4) flush();
    row.push({ kind: 'add', key: 'add', blocks: [] });
  }
  flush();
  return rows;
}

// A suggestion may use spare columns, but must keep every real photo and the
// add slot on its current row. Judge later suggestions against accepted ones.
export function withStageAdditions(photos, additions, { add = true } = {}) {
  const byId = new Map(photos.map(block => [block.sourceId || block.id, block]));
  const groupIdFor = block => block.spaceGroupId || byId.get(block.similarSourceBlockId)?.spaceGroupId;
  const lastGroupIndex = (items, groupId) => items.findLastIndex(block => groupIdFor(block) === groupId);
  const rowPositions = rows => new Map(rows.flatMap((row, index) => row.flatMap(group => group.kind === 'add'
    ? [[null, index]] : group.blocks.filter(block => !block.paleAddition).map(block => [block, index]))));
  let staged = [...photos];
  const candidates = [...additions].sort((left, right) =>
    (left.groupId ? lastGroupIndex(photos, left.groupId) : photos.length)
    - (right.groupId ? lastGroupIndex(photos, right.groupId) : photos.length));
  for (const item of candidates) {
    const index = item.groupId ? lastGroupIndex(staged, item.groupId) : item.kind === 'firstGhost' ? 0 : staged.length - 1;
    if (item.groupId && index < 0) continue;
    const tile = { id: item.id, paleAddition: item,
      ...(item.groupId || item.kind === 'firstGhost' ? { spaceGroupId: item.groupId || null, colorId: staged[index]?.colorId } : {}),
      ...(item.kind === 'pointsFabric' ? { stageRowKind: 'detail' } : {}) };
    const next = [...staged.slice(0, index + 1), tile, ...staged.slice(index + 1)];
    if (item.kind === 'spaceMember') {
      const before = packStageRows(staged, { add }), after = packStageRows(next, { add });
      const previousRows = rowPositions(before), nextRows = rowPositions(after);
      const tileRow = after.findIndex(row => row.some(group => group.blocks.includes(tile)));
      if (tileRow !== nextRows.get(staged[index]) || before.length !== after.length
        || [...previousRows].some(([block, row]) => nextRows.get(block) !== row)) continue;
    }
    staged = next;
  }
  return staged;
}
