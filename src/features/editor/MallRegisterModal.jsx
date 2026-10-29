import { useEffect, useRef, useState } from 'react';
import { Button, Modal } from '@/components/ui.jsx';
import { api } from '@/lib/api/index.js';
import { clientEvents } from '@/lib/clientEvents.js';
import { MallRegisterFields } from './MallRegisterFields.jsx';
import { MallNoticeFields } from './MallNoticeFields.jsx';
import { MallRegisterResults } from './MallRegisterResults.jsx';
import { buildMallRegisterDraft, MALL_LABELS, normalizeMallRegisterDraft, openRegistrationIssue, prepareMallRegisterDraft, preflightForDisplayedSources,
  registrationActive, registrationSelection, trapRegistrationFocus, validateMallRegisterDraft } from './mallRegisterDraft.js';
import { resolveMallNotice } from './mallNotice.js';
import { createMallRegisterSession } from './mallRegisterSession.js';
import styles from './MallRegisterModal.module.css';

export function MallRegisterModal({ projectId, product, blocks, assets, malls, initialMall,
  waitForSave, onSaveNotice, onSaveSizes, onPublish, onConflict, onClose, onRegistered, anchorRef }) {
  const rootRef = useRef(null), summaryRef = useRef(null), closeRef = useRef(null), controller = useRef(null);
  const displayedSources = useRef({}), lifecycle = useRef(0), scopeGuard = useRef(() => false);
  const alive = useRef(false), generation = useRef(0), locked = useRef(false), latest = useRef(null), draftRef = useRef(null);
  latest.current = { projectId, product, blocks, assets, malls, waitForSave, onSaveNotice, onSaveSizes, onPublish, onConflict, onRegistered };
  const [draft, setDraft] = useState(null), [preflight, setPreflight] = useState(null), [batch, setBatch] = useState(null);
  const [defaults, setDefaults] = useState({}), [catalogs, setCatalogs] = useState({}), [publication, setPublication] = useState(null);
  const [phase, setPhase] = useState('restore'), [error, setError] = useState(''), [noticeError, setNoticeError] = useState('');
  const [localIssues, setLocalIssues] = useState([]), [attempt, setAttempt] = useState(0), [retryMall, setRetryMall] = useState(null);
  const [connections, setConnections] = useState(malls);
  const [checking, setChecking] = useState(false), [returnAddressName, setReturnAddressName] = useState('');
  const setCurrentDraft = (value) => { draftRef.current = value; setDraft(value); };
  const busy = phase !== 'input' && phase !== 'results';
  const assertCurrent = () => {
    if (!scopeGuard.current()) throw new Error('계정이나 상품이 바뀌었어요. 다시 열어 주세요');
  };
  const report = (failure) => {
    if (!alive.current) return;
    if (['editor_conflict', 'editor_revision_changed'].includes(failure.code)) latest.current.onConflict?.();
    setError(failure.message || '등록 정보를 확인하지 못했어요');
  };
  const check = async (value) => {
    const issues = validateMallRegisterDraft(value);
    if (issues.length) return { items: value.selectedMalls.map((mall) => ({ mall, status: latest.current.malls?.items?.find((item) => item.mall === mall)?.registration?.status === 'preparing' ? 'preparing' : 'blocked', issues: [] })), localIssues: issues };
    const checked = await api.preflightMallRegistration(normalizeMallRegisterDraft(value));
    return preflightForDisplayedSources(checked, displayedSources.current);
  };
  const prepare = async (value) => {
    const guard = scopeGuard.current;
    const assert = () => { if (!guard()) throw new Error('계정이나 상품이 바뀌었어요. 다시 열어 주세요'); };
    setPhase('saving');
    const result = await prepareMallRegisterDraft(value, {
      assertCurrent: assert, waitForSave: () => latest.current.waitForSave(),
      onPublish: async (assert, revision) => { setPhase('publishing'); return latest.current.onPublish(assert, revision); },
      preflight: async (next) => { setPhase('checking'); return check(next); },
    });
    assert(); setPublication(result.publication); setPreflight(result.preflight);
    setLocalIssues(result.preflight.localIssues || []); setCurrentDraft(result.draft); setPhase('input');
    return result;
  };
  const loadSources = async () => {
    const guard = scopeGuard.current;
    const saved = await api.getSalesDefaults();
    const currentMalls = api.listMalls ? await api.listMalls() : latest.current.malls;
    const entries = await Promise.all((currentMalls?.items || []).filter((item) => item.registration?.status === 'available').map(async (item) => {
      const [catalog, mallDefaults] = await Promise.allSettled([api.getMallRegistrationCatalog(item.mall), api.getMallDefaults?.(item.mall)]);
      return [item.mall, catalog.status === 'fulfilled' ? catalog.value : { complete: false, categories: [] }, mallDefaults.status === 'fulfilled' ? mallDefaults.value : null];
    }));
    if (!guard()) throw new Error('계정이나 상품이 바뀌었어요. 다시 열어 주세요');
    const nextCatalogs = Object.fromEntries(entries.map(([mall, catalog]) => [mall, catalog]));
    const nextDefaults = Object.fromEntries(entries.map(([mall, _catalog, value]) => [mall, value]));
    displayedSources.current = { defaults: saved, malls: currentMalls, catalogs: nextCatalogs, mallDefaults: nextDefaults };
    setDefaults(saved); setCatalogs(nextCatalogs); setConnections(currentMalls);
    if (currentMalls?.items?.some((item) => item.mall === 'zigzag' && item.status === 'connected') && api.listZigzagAddresses) {
      try {
        const addresses = await api.listZigzagAddresses();
        if (!guard()) throw new Error('계정이나 상품이 바뀌었어요. 다시 열어 주세요');
        setReturnAddressName(addresses.find((row) => row.id === nextDefaults.zigzag?.addresses?.returnAddressId)?.name || '');
      } catch { if (guard()) setReturnAddressName(''); }
    }
    return saved;
  };
  const reloadSources = async () => {
    if (locked.current) return; locked.current = true; setError('');
    try { await loadSources(); assertCurrent(); await prepare(draftRef.current); }
    catch (failure) { report(failure); if (alive.current) setPhase('input'); }
    finally { locked.current = false; }
  };
  useEffect(() => {
    alive.current = true;
    const epoch = ++lifecycle.current;
    const accountGeneration = clientEvents.getAccountGeneration();
    const scope = projectId;
    const current = () => alive.current && epoch === lifecycle.current && latest.current.projectId === scope && accountGeneration === clientEvents.getAccountGeneration();
    // This guard also covers the save/publication awaits, not just registration responses.
    scopeGuard.current = current;
    controller.current = createMallRegisterSession({ api, createId: () => crypto.randomUUID(), isCurrent: current,
      onChange: (value) => { if (current()) setBatch(value); } });
    closeRef.current?.focus();
    const start = async () => {
      try {
        setPhase('restore'); setError('');
        const restored = await controller.current.restore(projectId);
        if (!current()) return;
        if (restored) { setPhase('results'); return; }
        const saved = await loadSources();
        if (!current()) return;
        await prepare(buildMallRegisterDraft({ ...latest.current, defaults: saved, selectedMall: initialMall }));
      } catch (failure) { if (current()) { report(failure); setPhase('input'); } }
    };
    void start();
    return () => { alive.current = false; generation.current++; anchorRef?.current?.focus(); };
  }, [projectId, attempt]);
  useEffect(() => {
    const token = ++generation.current;
    if (!draft?.publicationId || phase !== 'input') return;
    setChecking(true); setPreflight(null);
    const timer = setTimeout(async () => {
      try {
        const result = await check(draft);
        assertCurrent(); if (token !== generation.current) return;
        const next = { ...draft, confirmedSourcesByMall: {} };
        for (const item of result.items) if (item.status === 'ready') next.confirmedSourcesByMall[item.mall] = item.sourceVersions;
        draftRef.current = next; setPreflight(result); setLocalIssues(result.localIssues || []);
      } catch (failure) { if (token === generation.current) report(failure); }
      finally { if (alive.current && token === generation.current) setChecking(false); }
    }, 250);
    return () => clearTimeout(timer);
  }, [draft, phase]);
  useEffect(() => {
    if (!batch || batch.timedOut || !batch.items.some((item) => registrationActive(item.registration) || item.status === 'accepted' && !item.registration)) return;
    const timer = setTimeout(() => { controller.current.refresh().catch(report); }, 1500);
    return () => clearTimeout(timer);
  }, [batch]);
  useEffect(() => { if (error) summaryRef.current?.focus(); }, [error]);
  const change = (value) => { generation.current++; setPreflight(null); setLocalIssues([]); setError(''); setCurrentDraft(value); };
  const saveNotice = async (value) => {
    if (locked.current) return; locked.current = true; setNoticeError('');
    try { setPhase('saving'); await latest.current.onSaveNotice(value); assertCurrent(); await prepare(draftRef.current); }
    catch (failure) { if (alive.current) { setNoticeError(failure.message); report(failure); setPhase('input'); } }
    finally { locked.current = false; }
  };
  const saveSizes = async (sizes) => {
    if (locked.current) return; locked.current = true;
    try {
      setPhase('saving'); await latest.current.onSaveSizes(sizes); assertCurrent();
      const keys = new Set(registrationVariantsForSizes(sizes));
      await prepare({ ...draftRef.current, sizes, variantOverrides: draftRef.current.variantOverrides.filter((item) => keys.has(item.variantKey)) });
    } catch (failure) { report(failure); if (alive.current) setPhase('input'); throw failure; }
    finally { locked.current = false; }
  };
  const registrationVariantsForSizes = (sizes) => (latest.current.product?.colors || []).flatMap((color) => sizes.map((size) => JSON.stringify([color.id, size])));
  const retryFailed = async (mall) => {
    if (locked.current) return; locked.current = true; setError(''); setRetryMall(mall);
    try {
      const saved = await loadSources(); assertCurrent();
      const value = draftRef.current || buildMallRegisterDraft({ ...latest.current, defaults: saved, selectedMall: mall });
      await prepare({ ...value, selectedMalls: [mall] });
    } catch (failure) { report(failure); if (alive.current) { setRetryMall(null); setPhase('results'); } }
    finally { locked.current = false; }
  };
  const submit = async () => {
    if (locked.current) return; locked.current = true; setError(''); generation.current++;
    try {
      if (controller.current.getState().pendingRequest) {
        setPhase('submitting'); await controller.current.submit(draftRef.current); assertCurrent(); setPhase('results'); return;
      }
      const value = draftRef.current;
      const issues = validateMallRegisterDraft(value); setLocalIssues(issues);
      if (issues.length) { setError('입력한 항목을 확인해 주세요'); return; }
      const previouslyConfirmed = value.confirmedSourcesByMall;
      const prepared = await prepare(value); assertCurrent();
      const selected = registrationSelection(value.selectedMalls, prepared.preflight);
      if (!selected.readyMalls.length) { setError('몰마다 확인할 항목을 살펴봐 주세요'); return; }
      if (selected.readyMalls.some((mall) => JSON.stringify(previouslyConfirmed[mall]) !== JSON.stringify(prepared.draft.confirmedSourcesByMall[mall]))) {
        setError('확인한 정보가 바뀌었어요. 점검 결과를 보고 다시 등록해 주세요'); return;
      }
      setPhase('submitting');
      const result = retryMall ? await controller.current.retryFailed(retryMall, prepared.draft) : await controller.current.submit(prepared.draft);
      assertCurrent(); setRetryMall(null); setPhase('results'); latest.current.onRegistered?.(result);
    } catch (failure) { report(failure); if (alive.current) setPhase(controller.current.getState().batch ? 'results' : 'input'); }
    finally { locked.current = false; }
  };
  const resultAction = async (method, jobId) => {
    if (locked.current) return; locked.current = true; setError(''); setPhase('updating');
    try { await controller.current[method](jobId); } catch (failure) { report(failure); }
    finally { locked.current = false; if (alive.current) setPhase('results'); }
  };
  const resolution = resolveMallNotice({ blocks, product: { ...product, name: draft?.productName || product?.name }, defaults });
  const resultMode = Boolean(batch && !retryMall) && phase !== 'input';
  const failedCount = batch?.items.filter((item) => item.registration?.outcome === 'failed').length || 0;
  const resultTitle = batch?.receiptUnconfirmed ? '접수를 다시 확인해 주세요'
    : batch?.items.some((item) => registrationActive(item.registration) || item.status === 'accepted' && !item.registration) ? '등록하고 있어요'
      : batch?.items.some((item) => ['unknown', 'duplicate_suspected'].includes(item.registration?.outcome)) ? '등록 여부를 확인해 주세요'
        : failedCount ? `${failedCount}곳은 올리지 못했어요`
          : batch?.items.some((item) => item.registration?.setupStatus === 'needs_attention') ? '설정을 확인해 주세요' : '등록했어요';
  const selection = registrationSelection(draft?.selectedMalls || [], preflight);
  const items = preflight?.items || [], issues = [...localIssues, ...items.flatMap((item) => item.issues || [])];
  const jump = (issue) => {
    if (['verify_existing', 'resume_existing'].includes(issue.action)) { void resultAction('restore', projectId); return; }
    if (issue.action === 'fill_notice') { const details = rootRef.current?.querySelector('#mall-notice'); if (details) details.open = true; }
    openRegistrationIssue(rootRef.current, issue);
  };
  return <Modal onClose={onClose} wide><div className={styles.dialog} ref={rootRef} role="dialog" aria-modal="true" aria-labelledby="mall-register-title" tabIndex={-1}
    onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); onClose?.(); } else trapRegistrationFocus(event); }}>
    <header className={styles.header}><h2 id="mall-register-title">{resultMode ? resultTitle : '확인하고 등록해요'}</h2><p>{resultMode ? product?.name || product?.productName : '판매가와 재고만 넣으면 돼요.'}</p></header>
    <div className={styles.body}>
      {error && <div className={styles.errorSummary} role="alert" tabIndex={-1} ref={summaryRef}><p>{error}</p>
        {issues.map((issue, index) => <button key={index} type="button" className={styles.textButton} onClick={() => jump(issue)}>{issue.message}</button>)}
        {batch && <Button disabled={busy} onClick={() => resultAction('refresh')}>결과 다시 확인</Button>}
        {!draft && !batch && <Button onClick={() => setAttempt((value) => value + 1)}>다시 불러오기</Button>}
      </div>}
      {busy && <p role="status">{{ restore: '이전 등록을 확인하고 있어요', saving: '편집 내용을 저장하고 있어요', publishing: '지금 편집본의 상세 이미지를 준비하고 있어요', checking: '몰마다 등록 정보를 점검하고 있어요', submitting: '등록을 접수하고 있어요', updating: '등록 상태를 확인하고 있어요' }[phase]}</p>}
      {resultMode && <fieldset className={styles.fields} disabled={busy}><MallRegisterResults batch={batch} onVerify={(jobId) => resultAction('verify', jobId)} onResume={(jobId) => resultAction('resume', jobId)} onRetryFailed={retryFailed} /></fieldset>}
      {!resultMode && draft && <>
        <fieldset className={styles.mallChoices} disabled={busy}><legend className={styles.srOnly}>등록할 쇼핑몰</legend>{['cafe24', 'zigzag', 'ably'].map((mall) => {
          const connection = connections?.items?.find((item) => item.mall === mall), preparing = connection?.registration?.status !== 'available' || items.find((item) => item.mall === mall)?.status === 'preparing';
          return <label key={mall}><img src={`/assets/malls/${mall}-48.png`} alt="" className={styles.logo} /><span>{MALL_LABELS[mall]}{preparing ? connection?.status === 'connected' ? ' 준비 중' : ' 연결 필요' : ''}</span>
            <input type="checkbox" data-field={mall === initialMall ? 'selectedMalls' : undefined} checked={draft.selectedMalls.includes(mall) && !preparing} disabled={busy || preparing || Boolean(retryMall) && mall !== retryMall}
              onChange={(event) => change({ ...draftRef.current, selectedMalls: event.target.checked ? [...draftRef.current.selectedMalls, mall] : draftRef.current.selectedMalls.filter((item) => item !== mall) })} /></label>;
        })}</fieldset>
        <section className={styles.checks} aria-label="몰마다 점검" aria-live="polite">{checking && <p>몰마다 점검 중이에요</p>}{items.map((item) => <div key={item.mall}>
          <img src={`/assets/malls/${item.mall}-48.png`} alt="" className={styles.logo} /><span>{MALL_LABELS[item.mall]} · {item.status === 'ready' ? '준비됐어요' : item.status === 'preparing' ? '준비 중' : item.issues[0]?.message || '입력한 항목을 확인해 주세요'}</span>
          {item.issues?.map((issue, index) => ['open_defaults', 'reconnect'].includes(issue.action) ? <a className={styles.goLink} href={issue.action === 'open_defaults' ? '/settings/malls?tab=sales' : '/settings/malls'} key={index}>설정에서 확인</a>
            : <button type="button" className={styles.textButton} key={index} onClick={() => jump(issue)}>{issue.action === 'fill_notice' ? '채우기' : '확인하기'}</button>)}
          {item.warnings?.map((warning, index) => <p className={styles.notice} key={index}>{warning.message}</p>)}
        </div>)}{items.some((item) => item.issues?.some((issue) => ['defaults_changed', 'catalog_changed', 'connection_changed'].includes(issue.code))) && <Button disabled={busy} onClick={reloadSources}>기본 정보 다시 확인</Button>}<p className={styles.notice}>막힌 몰은 빼고 준비된 몰만 먼저 올릴 수 있어요.</p></section>
        <MallRegisterFields draft={draft} preflight={{ ...preflight, localIssues, context: { assets, catalogs, defaults, publication,
          colors: product?.colors || [], returnAddressName, onSaveSizes: saveSizes, gifNotice: product?.registrationGifNotice } }} onChange={change} disabled={busy || controller.current.getState().pendingRequest} />
        <details id="mall-notice" className={styles.disclosure} onToggle={(event) => event.currentTarget.querySelector('summary')?.setAttribute('aria-expanded', String(event.currentTarget.open))}>
          <summary aria-expanded="false" aria-controls="mall-notice-fields">고시 10항목 · 빈칸 {resolution.missingCount}개 · 채우기{resolution.conflicts.length ? ' · 블록 선택 필요' : ''}</summary>
          <div id="mall-notice-fields"><MallNoticeFields key={JSON.stringify(resolution.fields)} resolution={{ ...resolution, issues }} onSave={saveNotice} busy={busy || controller.current.getState().pendingRequest} error={noticeError} /></div>
        </details><p className={styles.notice}>카페24에는 원산지와 소재만 함께 보내요.</p>
      </>}
    </div>
    <footer className={styles.footer}><div className="modal-actions"><button type="button" className="btn btn-ghost" ref={closeRef} onClick={onClose}>{resultMode ? '닫기' : '취소'}</button>
      {resultMode && !batch.receiptUnconfirmed && !batch.items.some((item) => registrationActive(item.registration) || ['unknown', 'duplicate_suspected'].includes(item.registration?.outcome) || item.registration?.setupStatus === 'needs_attention') && <Button disabled={busy} onClick={() => { controller.current.startNew(); setRetryMall(null); setAttempt((value) => value + 1); }}>새 등록</Button>}
      {controller.current?.getState?.().pendingRequest ? <Button variant="primary" disabled={busy} onClick={submit}>같은 접수 다시 확인</Button>
        : !resultMode && <Button variant="primary" disabled={busy || checking || !draft || !selection.readyMalls.length} onClick={submit}>{selection.label}</Button>}
    </div></footer>
  </div></Modal>;
}
