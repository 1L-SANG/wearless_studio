import { formatDraftRelativeTime } from './draftSlot.js';
import { getDraftDeviceNoun, isMobileDraftWriter } from './draftDeviceNoun.js';

export function isVisibleDraftSlot(slot, slotClient) {
  return Boolean(slot) && slot.meta?.hasContent !== false && !slotClient.hasPendingRemoval();
}

export function draftSavedTime(updatedAt) {
  return `${formatDraftRelativeTime(updatedAt)} 저장`;
}

export function remoteDraftSource(meta, fallbackTitle = '입력하던 상품 정보', localFallback = null) {
  const noun = getDraftDeviceNoun(meta?.deviceLabel);
  return {
    id: 'remote',
    title: meta?.productName || (noun ? `${noun} 작업 이어가기` : fallbackTitle),
    thumbnailUrl: meta?.thumbnailUrl || null,
    description: `${draftSavedTime(meta?.updatedAt)} · ${meta?.deviceLabel || '다른 기기'} · 사진 ${meta?.photoCount || 0}장`,
    meta,
    photosPending: Boolean(meta?.photosPending),
    localFallback,
  };
}

export function getInputEntryDecision({ slotClient, confirmed }) {
  if (slotClient.hasStaged()) return 'continue';
  return confirmed ? 'confirmed' : 'checking';
}

export async function getFlowDraftSources({ slotClient, enabled, flowSource, throwOnError = false }) {
  if (!enabled) return null;
  // 조회 실패는 기존 작업을 이어가는 비파괴적 진입을 막지 않는다.
  const slot = await slotClient.get().catch((error) => { if (throwOnError) throw error; return null; });
  if (!isVisibleDraftSlot(slot, slotClient)) return null;
  return [flowSource, {
    ...remoteDraftSource(slot.meta),
    notice: '지금 만들던 상세페이지 대신 이 상품으로 시작해요',
  }];
}

export async function openRemoteDraft({ slotClient, beginProject }) {
  const takeover = await slotClient.takeover();
  if (!takeover?.payload) {
    const error = new Error('저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.');
    error.icon = 'alertTri';
    throw error;
  }
  await beginProject();
  slotClient.stage(takeover);
}

export function createDraftEntryActions({
  slotClient, beginProject, onOpened, onStarted, onChange, onError, onKept = () => {}, onBusy = () => {},
}) {
  let dialog = null;
  let confirmation = null;
  let busy = false;
  const show = (value) => { dialog = value; onChange(value); };
  const clear = () => { confirmation = null; show(null); };
  const run = async (action, fallback = '저장된 내용을 열지 못했어요. 잠시 후 다시 시도해 주세요.') => {
    if (busy) return;
    busy = true;
    onBusy(true);
    try { await action(); }
    catch (error) { onError(error?.message || fallback, { icon: error?.icon || 'alert' }); }
    finally { busy = false; onBusy(false); }
  };
  const readVisible = async () => {
    let slot;
    try { slot = await slotClient.get(); }
    catch { throw new Error('잠시 후 다시 시도해 주세요.'); }
    return isVisibleDraftSlot(slot, slotClient) ? slot : null;
  };
  const showConfirmation = (slot, action, partial, mobilePhotoCount) => {
    const meta = { ...slot.meta };
    confirmation = { action, partial, mobilePhotoCount, updatedAt: meta.updatedAt };
    show({ kind: 'confirm', meta, partial, message: partial
      ? '아직 올라오지 않은 사진은 빠져요.'
      : mobilePhotoCount != null
        ? `업로드한 사진 ${Math.max(meta.photoCount || 0, mobilePhotoCount)}장이 지워져요. 한 번에 한 상품만 저장할 수 있어요.`
        : `${getDraftDeviceNoun(meta.deviceLabel) || '다른 기기'}에서 업로드한 사진 ${meta.photoCount || 0}장이 지워져요.` });
  };
  const requiresConfirmation = (slot, partial) => Boolean(slot)
    && (partial || slot.meta?.updatedAt !== slotClient.getServerSyncedAt());
  const protect = async (action, partial = false) => {
    const slot = await readVisible();
    if (requiresConfirmation(slot, partial)) { showConfirmation(slot, action, partial); return; }
    await action();
    clear();
  };
  const open = async (savedMeta) => {
    await openRemoteDraft({ slotClient, beginProject });
    clear();
    onOpened(savedMeta);
  };
  const chooseRemote = async (source) => {
    if (source.meta?.photosPending) {
      show({ kind: 'pending', meta: { ...source.meta }, savedAt: draftSavedTime(source.meta.updatedAt),
        message: `${getDraftDeviceNoun(source.meta.deviceLabel) || '다른 기기'}에서 아직 사진을 올리고 있어요. 잠시 후 다시 확인해 주세요.` });
      return;
    }
    await open();
  };
  const startMobile = async () => {
    await slotClient.removeForNewFlow();
    await beginProject();
    clear();
    onStarted();
  };
  const showMobile = (meta, entry) => {
    confirmation = { action: startMobile, updatedAt: meta.updatedAt, mobile: true, entry };
    show({ kind: 'mobile', meta: { ...meta }, entry });
  };
  return {
    showMobile(meta, { entry = false } = {}) { showMobile(meta, entry); },
    keepMobile() {
      if (dialog?.kind !== 'mobile') return;
      return run(async () => {
        const { meta, entry } = dialog;
        if (!entry) { clear(); return; }
        if (isMobileDraftWriter(meta)) await open(meta);
        else { clear(); onKept(meta); }
      });
    },
    nextMobile({ enabled = true, entry = false } = {}) {
      return run(async () => {
        const slot = enabled ? await readVisible() : null;
        if (slot) { showMobile(slot.meta, entry); return; }
        slotClient.resetIdentity();
        await beginProject();
        clear();
        onStarted();
      });
    },
    startNew({ enabled = true, mobilePhotoCount } = {}) {
      return run(async () => {
        const start = async () => {
          if (enabled) await slotClient.removeForNewFlow();
          await beginProject();
          clear();
          onStarted();
        };
        if (mobilePhotoCount != null) {
          const slot = enabled ? await readVisible() : null;
          showConfirmation(slot || { meta: {} }, start, false, mobilePhotoCount);
        } else if (enabled) await protect(start);
        else await start();
      }, '이전 작업을 정리하지 못했어요. 잠시 후 다시 시도해 주세요.');
    },
    openLocal(source, { protectRemote = false, resetFlow = false } = {}) {
      return run(async () => {
        const openLocal = async () => {
          if (resetFlow) await beginProject();
          slotClient.stage({ payload: source.draft, meta: source.meta });
          void slotClient.takeover().catch(() => {});
          clear();
          onOpened();
        };
        if (protectRemote) await protect(openLocal);
        else await openLocal();
      });
    },
    openRemote(source) { return run(() => chooseRemote(source)); },
    refreshRemote() {
      return run(async () => {
        const slot = await readVisible();
        if (!slot) {
          clear();
          onError('저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.', { icon: 'alertTri' });
          return;
        }
        await chooseRemote(remoteDraftSource(slot.meta));
      });
    },
    openPartial() {
      if (dialog?.kind !== 'pending') return;
      return run(() => protect(open, true));
    },
    confirm() {
      if (!confirmation) return;
      return run(async () => {
        const { action, partial, mobilePhotoCount, updatedAt, mobile, entry } = confirmation;
        if (mobile) {
          const slot = await readVisible();
          if (slot && slot.meta?.updatedAt !== updatedAt) { showMobile(slot.meta, entry); return; }
          if (slot) await action();
          else { slotClient.resetIdentity(); await beginProject(); clear(); onStarted(); }
          return;
        }
        const slot = await readVisible();
        if ((mobilePhotoCount != null || requiresConfirmation(slot, partial)) && slot?.meta?.updatedAt !== updatedAt) {
          showConfirmation(slot || { meta: {} }, action, partial, mobilePhotoCount);
          return;
        }
        await action();
        clear();
      });
    },
    cancel() { if (!busy) clear(); },
  };
}
