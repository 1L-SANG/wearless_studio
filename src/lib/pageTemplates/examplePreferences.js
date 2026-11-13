import tags from '../../../server/app/data/example_tags.json' with { type: 'json' };

export function trustedEmptyBottom(exampleId, data = tags) {
  const entry = data.examples?.[exampleId];
  if (!entry || !['ok', 'review'].includes(entry.status)
    || entry.review?.some(value => value === 'emptyBottom' || value === 'cut.emptyBottom')) return null;
  return typeof entry.cut?.emptyBottom === 'boolean' ? entry.cut.emptyBottom : null;
}

export function preferExamples(candidates, preference, data = tags) {
  if (preference !== 'bottomClear') return candidates;
  const preferred = candidates.filter(item => trustedEmptyBottom(item.id, data) === true);
  return preferred.length ? [...preferred, ...candidates.filter(item => !preferred.includes(item))] : candidates;
}
