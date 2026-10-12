// 진입 확인은 매번 8초 안에 끝내고, 일시 오류는 화면을 바꾸기 전에 두 번 더 확인한다.
export async function checkDraftEntry(slotClient, { setTimer = setTimeout, clearTimer = clearTimeout, isCurrent = () => true } = {}) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (!isCurrent()) return null;
    let timer;
    try {
      const slot = await Promise.race([
        slotClient.get(),
        new Promise((_, reject) => { timer = setTimer(() => reject(new Error('entry_timeout')), 8000); }),
      ]);
      return isCurrent() ? slot : null;
    } catch (error) {
      if (!isCurrent()) return null;
      if (attempt === 2) throw error;
    } finally { clearTimer(timer); }
    await new Promise((resolve) => setTimer(resolve, (attempt + 1) * 1000));
  }
}

export function canAutoOpenInput(sources, { userId, flow, slotClient, now = Date.now() }) {
  if (!userId || sources.length !== 1 || slotClient.hasOtherTabOwner()) return false;
  const source = sources[0];
  if (source.photosPending || source.meta?.photosPending) return false;
  if (source.id === 'flow') {
    return flow.userId === userId && Number.isFinite(flow.lastUsedAt)
      && now >= flow.lastUsedAt && now - flow.lastUsedAt < 24 * 60 * 60 * 1000;
  }
  const meta = source.slotMeta || source.meta;
  return slotClient.getSyncedUserId() === userId && Boolean(meta?.updatedAt)
    && meta.updatedAt === slotClient.getServerSyncedAt();
}
