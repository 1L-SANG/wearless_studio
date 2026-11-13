const pending = new Map();
export function trackEditedContrast(node, work) {
  // Retain the job through React's paint, including the small interval between
  // setMeasured and the DOM commit. Export cannot miss a just-finished job.
  const painted = work.catch(() => {}).then(() => typeof requestAnimationFrame === 'function'
    ? new Promise(requestAnimationFrame) : undefined);
  pending.set(node, painted);
  painted.finally(() => { if (pending.get(node) === painted) pending.delete(node); });
}
export async function waitForEditedContrast(blocks) {
  let jobs;
  do {
    jobs = [...pending].filter(([node]) => blocks.some(block => block === node || block.contains?.(node))).map(([, work]) => work);
    await Promise.all(jobs);
  } while (jobs.length && [...pending.keys()].some(node => blocks.some(block => block.contains?.(node))));
}
