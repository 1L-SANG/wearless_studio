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

export async function getFlowDraftSources({ slotClient, enabled, flowSource, throwOnError = false, check = () => slotClient.get() }) {
  if (!enabled) return null;
  // 조회 실패는 기존 작업을 이어가는 비파괴적 진입을 막지 않는다.
  const slot = await check().catch((error) => { if (throwOnError) throw error; return null; });
  if (!isVisibleDraftSlot(slot, slotClient)) return null;
  return [flowSource, {
    ...remoteDraftSource(slot.meta),
    notice: '지금 만들던 상세페이지 대신 이 상품으로 시작해요',
  }];
}

export async function openRemoteDraft({ slotClient, beginProject, context }) {
  const wait = context ? context.wait : (value) => value;
  const isCurrent = context ? context.isCurrent : () => true;
  const takeover = await wait(slotClient.takeover({ isCurrent }));
  if (!takeover?.payload) {
    const error = new Error('저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.');
    error.icon = 'alertTri';
    throw error;
  }
  await wait(beginProject({ isCurrent }));
  slotClient.stage(takeover);
}

export function createDraftEntryActions({
  slotClient, beginProject, onOpened, onStarted, onChange, onError, onKept = () => {}, onBusy = () => {},
  getUserId = () => null, mobile = () => false,
}) {
  let dialog = null;
  let confirmation = null;
  let busy = false;
  let actionEpoch = 0;
  const stale = new Error('stale_entry');
  const show = (value) => { dialog = value; onChange(value); };
  const clear = () => { confirmation = null; show(null); };
  const run = async (action, fallback = '저장된 내용을 열지 못했어요. 잠시 후 다시 시도해 주세요.', automatic = false) => {
    if (busy) return;
    const userId = getUserId();
    const identityEpoch = slotClient.getIdentityEpoch?.();
    const epoch = ++actionEpoch;
    const isCurrent = () => epoch === actionEpoch && userId === getUserId()
      && identityEpoch === slotClient.getIdentityEpoch?.()
      && (!automatic || !slotClient.hasOtherTabOwner());
    const context = { isCurrent, async wait(value) {
      try {
        const result = await value;
        if (!isCurrent()) throw stale;
        return result;
      } catch (error) { if (!isCurrent()) throw stale; throw error; }
    } };
    busy = true;
    onBusy(true);
    try { await action(context); }
    catch (error) { if (error !== stale && isCurrent()) onError(error?.message || fallback, { icon: error?.icon || 'alert' }); }
    finally { if (epoch === actionEpoch) { busy = false; onBusy(false); } }
  };
  const readVisible = async (context) => {
    let slot;
    try { slot = await context.wait(slotClient.get()); }
    catch (error) { if (error === stale) throw error; throw new Error('잠시 후 다시 시도해 주세요.'); }
    return isVisibleDraftSlot(slot, slotClient) ? slot : null;
  };
  const gone = () => {
    const error = new Error('저장돼 있던 내용이 다른 곳에서 이미 정리됐어요.');
    error.icon = 'alertTri';
    throw error;
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
  const protect = async (context, action, partial = false) => {
    const slot = await readVisible(context);
    if (requiresConfirmation(slot, partial)) { showConfirmation(slot, action, partial); return; }
    await action(context, slot);
    clear();
  };
  const open = async (context, slot, saved = false) => {
    if (!slot) gone();
    if (mobile() && !isMobileDraftWriter(slot.meta)) { clear(); onKept(slot.meta); return; }
    await context.wait(openRemoteDraft({ slotClient, beginProject, context }));
    clear();
    onOpened(saved ? slot.meta : undefined);
  };
  const showPending = (meta) => show({ kind: 'pending', meta: { ...meta }, savedAt: draftSavedTime(meta.updatedAt),
    message: `${getDraftDeviceNoun(meta.deviceLabel) || '다른 기기'}에서 아직 사진을 올리고 있어요. 잠시 후 다시 확인해 주세요.` });
  const chooseRemote = async (context, slot) => {
    if (!slot) gone();
    if (mobile() && !isMobileDraftWriter(slot.meta)) { showMobile(slot.meta, true); return; }
    if (slot.meta?.photosPending) { showPending(slot.meta); return; }
    await open(context, slot);
  };
  const startMobile = async (context) => {
    await context.wait(slotClient.removeForNewFlow({ isCurrent: context.isCurrent }));
    await context.wait(beginProject({ isCurrent: context.isCurrent }));
    clear();
    onStarted();
  };
  const startEmpty = async (context) => {
    slotClient.resetIdentity({ sameUser: true });
    await context.wait(beginProject({ isCurrent: context.isCurrent }));
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
      const { entry } = dialog;
      return run(async (context) => {
        const slot = await readVisible(context);
        if (!slot) { clear(); gone(); }
        if (!isMobileDraftWriter(slot.meta)) { clear(); onKept(slot.meta); }
        else if (!entry) clear();
        else if (slot.meta.photosPending) showPending(slot.meta);
        else await open(context, slot, true);
      });
    },
    nextMobile({ enabled = true, entry = false } = {}) {
      return run(async (context) => {
        const slot = enabled ? await readVisible(context) : null;
        if (slot) { showMobile(slot.meta, entry); return; }
        await startEmpty(context);
      });
    },
    startNew({ enabled = true, mobilePhotoCount } = {}) {
      return run(async (context) => {
        const start = async (current) => {
          if (enabled) await current.wait(slotClient.removeForNewFlow({ isCurrent: current.isCurrent }));
          await current.wait(beginProject({ isCurrent: current.isCurrent }));
          clear();
          onStarted();
        };
        if (mobilePhotoCount != null) {
          const slot = enabled ? await readVisible(context) : null;
          showConfirmation(slot || { meta: {} }, start, false, mobilePhotoCount);
        } else if (enabled) await protect(context, start);
        else await start(context);
      }, '이전 작업을 정리하지 못했어요. 잠시 후 다시 시도해 주세요.');
    },
    openLocal(source, { resetFlow = false, enabled = true, automatic = false } = {}) {
      return run(async (context) => {
        const openLocal = async (current) => {
          if (automatic && slotClient.hasOtherTabOwner()) return;
          if (enabled) await current.wait(slotClient.takeover({ isCurrent: current.isCurrent }));
          if (resetFlow) await current.wait(beginProject({ isCurrent: current.isCurrent }));
          slotClient.stage({ payload: source.draft, meta: source.meta });
          clear();
          onOpened();
        };
        if (enabled) await protect(context, openLocal);
        else await openLocal(context);
      }, undefined, automatic);
    },
    openRemote(source, { automatic = false } = {}) {
      return run(async (context) => {
        const slot = await readVisible(context);
        if (automatic && (slotClient.hasOtherTabOwner() || slotClient.getSyncedUserId() !== getUserId()
            || slot?.meta?.updatedAt !== slotClient.getServerSyncedAt())) return;
        await chooseRemote(context, slot);
      }, undefined, automatic);
    },
    refreshRemote() { return run(async (context) => chooseRemote(context, await readVisible(context))); },
    openPartial() {
      if (dialog?.kind !== 'pending') return;
      return run((context) => protect(context, open, true));
    },
    confirm() {
      if (!confirmation) return;
      const confirmed = confirmation;
      return run(async (context) => {
        const { action, partial, mobilePhotoCount, updatedAt, mobile: mobileDialog, entry } = confirmed;
        const slot = await readVisible(context);
        if (mobileDialog) {
          if (slot && slot.meta?.updatedAt !== updatedAt) { showMobile(slot.meta, entry); return; }
          if (slot) await action(context, slot);
          else await startEmpty(context);
          return;
        }
        if ((mobilePhotoCount != null || requiresConfirmation(slot, partial)) && slot?.meta?.updatedAt !== updatedAt) {
          showConfirmation(slot || { meta: {} }, action, partial, mobilePhotoCount);
          return;
        }
        await action(context, slot);
        clear();
      });
    },
    cancel() { actionEpoch += 1; busy = false; onBusy(false); clear(); },
  };
}
