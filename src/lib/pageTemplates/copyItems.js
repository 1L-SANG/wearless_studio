// Keep in parity with server/app/agents/page_copy_items.py.
export function compactSectionTexts(texts, kind) {
  const rows = (texts || []).filter(row => row && typeof row === 'object').map(row => ({ ...row }));
  if (kind === 'points') return rows;
  const item = /^items\.(\d+)(?:\.(.+))?$/;
  const groups = new Map();
  for (const row of rows) {
    const match = String(row.role || '').match(item);
    if (!match || typeof row.text !== 'string' || !row.text.trim()) continue;
    const fields = groups.get(Number(match[1])) || new Set();
    fields.add(match[2] || ''); groups.set(Number(match[1]), fields);
  }
  const valid = [...groups].filter(([, fields]) => (kind !== 'faq' || fields.has('q') && fields.has('a'))
    && (kind !== 'review' || fields.has('text'))).map(([index]) => index).sort((a, b) => a - b);
  const numbering = new Map(valid.map((old, index) => [old, index]));
  return rows.flatMap(row => {
    const match = String(row.role || '').match(item);
    if (!match) return [row];
    if (!numbering.has(Number(match[1])) || typeof row.text !== 'string' || !row.text.trim()) return [];
    return [{ ...row, role: `items.${numbering.get(Number(match[1]))}${match[2] ? `.${match[2]}` : ''}` }];
  });
}
