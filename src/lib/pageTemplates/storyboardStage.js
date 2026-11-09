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
    if (occupied + group.blocks.length > 4) flush();
    for (let offset = 0; offset < group.blocks.length; offset += 4) {
      const chunk = { ...group, blocks: group.blocks.slice(offset, offset + 4), continued: offset > 0 };
      row.push(chunk);
      occupied += chunk.blocks.length;
      if (offset + 4 < group.blocks.length || occupied === 4) flush();
    }
  }
  if (add && photos.length < 12) {
    if (occupied === 4) flush();
    row.push({ kind: 'add', key: 'add', blocks: [] });
  }
  flush();
  return rows;
}
