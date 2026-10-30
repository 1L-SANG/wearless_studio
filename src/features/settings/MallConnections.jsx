import { useEffect, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Button, Icon, Modal } from '@/components/ui.jsx';
import { ExportMenu } from '@/features/editor/ExportMenu.jsx';
import { MallDefaultsForm } from './MallDefaultsChecklist.jsx';
import styles from './MallConnections.module.css';

const EMPTY_KEYS = { accessKey: '', secretKey: '' };

function KeyFields({ values = EMPTY_KEYS, onChange, showSecret, onToggleSecret, busy, rotate = false }) {
  const prefix = rotate ? 'rotate' : 'connect';
  const paste = (event, key) => {
    event.preventDefault();
    onChange?.({ ...values, [key]: event.clipboardData.getData('text').trim() });
  };
  return <div className={styles.fields}>
    <label className={styles.field} htmlFor={`${prefix}-access`}><span>{rotate ? '새 Access Key' : 'Access Key'}</span>
      <input id={`${prefix}-access`} name="accessKey" value={values.accessKey || ''} spellCheck={false} autoComplete="off" maxLength={256}
        disabled={busy} onPaste={(event) => paste(event, 'accessKey')} onChange={(event) => onChange?.({ ...values, accessKey: event.target.value })} />
    </label>
    <div className={styles.field}><label htmlFor={`${prefix}-secret`}>{rotate ? '새 Secret Key' : 'Secret Key'}</label>
      <span className={styles.secretField}>
        <input id={`${prefix}-secret`} name="secretKey" type={showSecret ? 'text' : 'password'} value={values.secretKey || ''}
          spellCheck={false} autoComplete="off" maxLength={256} disabled={busy} onPaste={(event) => paste(event, 'secretKey')}
          onChange={(event) => onChange?.({ ...values, secretKey: event.target.value })} />
        <button type="button" className={styles.reveal} disabled={busy} aria-pressed={!!showSecret} aria-controls={`${prefix}-secret`}
          aria-label={showSecret ? 'Secret Key 숨기기' : 'Secret Key 보기'} onClick={onToggleSecret}>{showSecret ? '숨기기' : '보기'}</button>
      </span>
    </div>
  </div>;
}

export function MallConnectionRow({ detail = false, returnTo, mall = 'zigzag', logo = `/assets/malls/${mall}-48.png`, title = mall === 'cafe24' ? '카페24' : '지그재그',
  help = '파트너센터 상품과 주문 API', children, submitLabel, submitDisabled,
  state, connection, values, error, showSecret, onToggleSecret, onChange, onSubmit,
  onConnect, onCollapse, anchorRef, rowRef, menuOpen, onMenuToggle, onMenuClose, menuItems = [] }) {
  const busy = ['verifying', 'starting'].includes(state);
  const editing = ['editing', 'verifying', 'starting', 'failed'].includes(state);
  const cafe24 = mall === 'cafe24';
  const Heading = detail ? 'h1' : 'h3';
  const storeName = connection?.shopName || connection?.mallId;
  return <div className={styles.mallRow} data-state={state} ref={rowRef}>
    {logo ? <img className={styles.mallLogo} src={logo} srcSet={`/assets/malls/${mall}-96.png 2x`} alt="" />
      : <span className={styles.mallMark} aria-hidden="true">{cafe24 ? 'C' : 'Z'}</span>}
    <div className={styles.mallInfo}>
      <div className={styles.mallTitle}><Heading>{title}{state === 'connected' && storeName ? ` · ${storeName}` : ''}</Heading>
        {state === 'reconnect_required' && <span className={styles.warningDot} aria-hidden="true" />}</div>
      {state === 'connected' ? <div className={styles.metadata}>
        {cafe24 && connection?.mallId && <span>몰 아이디 {connection.mallId}</span>}
        {!cafe24 && <span>인증키 끝자리 {connection?.keyHint || '••••••••'}</span>}
        {connection?.registration?.status !== 'available' && <span>{connection?.registration?.status === 'reconnect_required' ? '등록하려면 다시 연결해 주세요' : '등록 준비 중'}</span>}
        {!detail && <span>{connection?.defaultsMissingItems?.length ? `판매 정보 빈칸 ${connection.defaultsMissingItems.length}개` : '판매 정보 채움'}</span>}
      </div> : <p className={styles.help}>{state === 'reconnect_required' ? '연결이 끊겼어요. 다시 설치하면 이어서 쓸 수 있어요'
        : busy && !cafe24 ? '스토어 이름과 권한을 받아 오고 있어요' : help}</p>}
    </div>
    <div className={styles.rowActions}>
      {['none', 'reconnect_required'].includes(state) && <Button onClick={onConnect}>{state === 'none' ? '연결하기' : '다시 연결'}</Button>}
      {state === 'unavailable' && <span className={styles.status}>준비 중</span>}
      {editing && <span className={styles.status} role="status">{busy ? cafe24 ? '설치 시작 중' : '확인 중' : '입력 중'}</span>}
      {state === 'connected' && <>
        <span className={styles.status}><span className={styles.connectedDot} aria-hidden="true" />연결됨</span>
        {!detail && <Link className={styles.goLink} to={`/settings/malls/${mall}`} state={{ returnTo }}>판매 정보 ›</Link>}
        {detail && <button type="button" className={styles.manage} ref={anchorRef} aria-label={`${title} 연결 관리`} aria-haspopup="menu"
          aria-expanded={!!menuOpen} onClick={onMenuToggle}>⋯</button>}
        <ExportMenu open={detail && menuOpen} onClose={onMenuClose} anchorRef={anchorRef} items={menuItems} />
      </>}
    </div>
    {editing && <form className={styles.inlineForm} onSubmit={onSubmit} aria-busy={busy}>
      {children || <>
        <div className={styles.issuePath}><p className={styles.help}>로그인 뒤 스토어 정보 관리 › 상품과 주문 API에서 인증키 발급</p>
          <a className={styles.goLink} href="https://partners.kakaostyle.com/" target="_blank" rel="noopener noreferrer">파트너센터에서 인증키 발급하기 ›</a>
        </div>
        <KeyFields values={values} onChange={onChange} showSecret={showSecret} onToggleSecret={onToggleSecret} busy={busy} />
        <p className={styles.help}>저장하면 다시 보여 주지 않아요. 인증키 끝자리 8자만 보여요</p>
      </>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={`modal-actions ${styles.leftActions}`}>
        <Button type="submit" variant="primary" disabled={busy || (submitDisabled ?? (!values?.accessKey?.trim() || !values?.secretKey?.trim()))}>
          {busy && <Icon name="loader" size={16} className="spin" />}{busy ? cafe24 ? '설치 시작 중' : '확인 중' : submitLabel || (state === 'failed' ? '다시 확인' : '연결 확인')}</Button>
        <Button disabled={busy} onClick={onCollapse}>접기</Button>
      </div>
    </form>}
  </div>;
}

function MallDialog({ children, kind, anchorRef, fallbackRef, busy = false, onClose }) {
  const ref = useRef(null);
  useEffect(() => () => {
    const target = anchorRef.current || fallbackRef?.current?.querySelector('button:not(:disabled)');
    target?.focus();
  }, [anchorRef, fallbackRef]);
  useEffect(() => {
    const target = ref.current?.querySelector('input:not(:disabled), button:not(:disabled)') || ref.current;
    target?.focus();
  }, [busy]);
  const trapFocus = (event) => {
    if (event.key !== 'Tab') return;
    const focusable = [...ref.current.querySelectorAll('input:not(:disabled), button:not(:disabled), a[href]')];
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (!first) { event.preventDefault(); return; }
    if (event.shiftKey && ref.current.ownerDocument.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && ref.current.ownerDocument.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  return <Modal onClose={onClose} narrow><div className={styles.dialog} ref={ref} onKeyDown={trapFocus}
    role={kind === 'revoke' ? 'alertdialog' : 'dialog'} aria-modal="true" aria-labelledby={`mall-${kind}-title`} tabIndex={-1}>{children}</div></Modal>;
}

export function MallActionDialog({ mall = 'zigzag', kind, values, showSecret, onToggleSecret, onChange, busy, error, onSubmit, onClose, anchorRef, fallbackRef }) {
  return <MallDialog kind={kind} anchorRef={anchorRef} fallbackRef={fallbackRef} busy={busy} onClose={onClose}>
    <h2 id={`mall-${kind}-title`}>{kind === 'revoke' ? `${mall === 'cafe24' ? '카페24' : '지그재그'} 연결을 해제할까요?` : '인증정보 변경'}</h2>
    <p>{kind === 'revoke' ? mall === 'cafe24' ? '해제하면 에디터에서 바로 등록을 못 해요. 등록한 상품과 게시한 이미지는 그대로예요' : '스토어와의 연결이 끊겨요. 해제하면 에디터에서 바로 등록을 못 해요. 등록한 상품과 게시한 이미지는 그대로예요. 기본 설정은 남아요'
      : '새 인증키로 확인이 끝나야 바뀌어요. 확인이 안 되면 지금 연결은 그대로 남아요'}</p>
    <form onSubmit={onSubmit}>
      {kind === 'rotate' && <KeyFields rotate values={values} onChange={onChange} showSecret={showSecret} onToggleSecret={onToggleSecret} busy={busy} />}
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className="modal-actions">
        <Button disabled={busy} onClick={onClose}>취소</Button>
        <Button type="submit" variant={kind === 'revoke' ? 'ghost' : 'primary'} className={kind === 'revoke' ? styles.danger : ''}
          disabled={busy || (kind === 'rotate' && (!values?.accessKey?.trim() || !values?.secretKey?.trim()))}>
          {busy && <Icon name="loader" size={16} className="spin" />}{busy ? '확인 중' : kind === 'revoke' ? '연결 해제' : '확인하고 바꾸기'}</Button>
      </div>
    </form>
  </MallDialog>;
}

export function ZigzagConnection({ connection, onChanged, detail = false, returnTo }) {
  const [state, setState] = useState('none');
  const [values, setValues] = useState(EMPTY_KEYS);
  const [showSecret, setShowSecret] = useState(false);
  const [error, setError] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState(null);
  const [busy, setBusy] = useState(false);
  const anchorRef = useRef(null);
  const rowRef = useRef(null);
  const clearKeys = () => { setValues(EMPTY_KEYS); setShowSecret(false); setError(''); };
  const closeDialog = () => { if (!busy) { setDialog(null); clearKeys(); } };
  const submit = async (event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    if (!dialog) setState('verifying');
    try {
      const result = dialog === 'revoke' ? await api.disconnectZigzag() : dialog === 'rotate'
        ? await api.rotateZigzag(values) : await api.connectZigzag(values);
      onChanged?.(dialog === 'revoke' ? null : result);
      clearKeys(); setState('none'); setDialog(null);
    } catch (failure) {
      setError(failure.message || '연결을 확인하지 못했어요. 잠시 뒤 다시 시도해 주세요');
      if (!dialog) setState('failed');
    } finally { setBusy(false); }
  };
  const manage = (kind) => { clearKeys(); setMenuOpen(false); setDialog(kind); };
  return <>
    <MallConnectionRow detail={detail} returnTo={returnTo} state={['connected', 'unavailable'].includes(connection?.status) ? connection.status : state} connection={connection}
      values={values} onChange={setValues} showSecret={showSecret} onToggleSecret={() => setShowSecret((value) => !value)} error={error}
      onSubmit={submit} onConnect={() => setState('editing')} onCollapse={() => { setState('none'); clearKeys(); }}
      anchorRef={anchorRef} rowRef={rowRef} menuOpen={menuOpen} onMenuToggle={() => setMenuOpen(!menuOpen)} onMenuClose={() => setMenuOpen(false)}
      menuItems={[{ id: 'rotate', label: '인증정보 변경', kind: 'action', onSelect: () => manage('rotate') },
        { id: 'revoke', label: '연결 해제', kind: 'action', onSelect: () => manage('revoke') }]} />
    {dialog && <MallActionDialog kind={dialog} values={values} onChange={setValues} showSecret={showSecret}
      onToggleSecret={() => setShowSecret((value) => !value)} busy={busy} error={error} onSubmit={submit} onClose={closeDialog} anchorRef={anchorRef} fallbackRef={rowRef} />}
  </>;
}

const CAFE24_INSTALL_COPY = {
  cancelled: '설치를 취소했어요. 다시 시도하면 돼요',
  state_invalid: '설치 확인이 만료됐어요. 같은 브라우저에서 다시 시작해 주세요',
  mall_mismatch: '입력한 몰 아이디와 로그인한 몰이 달라요. 몰 아이디를 확인해 주세요',
  missing_permission: '권한을 모두 허용해야 등록할 수 있어요. 다시 설치하며 권한을 허용해 주세요',
  not_allowed: '지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요',
  cafe24_not_allowed: '지금은 시범 운영 중이라 등록된 몰만 연결할 수 있어요',
  invalid_mall_id: '몰 아이디를 확인해 주세요',
  connection_changed: '그 사이 연결이 바뀌었어요. 지금 상태를 확인한 뒤 다시 시도해 주세요',
  token_exchange_failed: '카페24에서 인증을 마치지 못했어요. 잠시 뒤 다시 시도해 주세요',
  account_closed: '계정 삭제가 완료되어 사용할 수 없습니다.',
};

const normalizeMallId = (value) => String(value ?? '').trim().toLowerCase().replace(/^https?:\/\//, '')
  .split(/[/?#]/, 1)[0].replace(/\.(cafe24|cafe24api)\.com$/, '');
const CAFE24_APP_MALL_ID_KEY = 'cafe24-app-mall-id';

function MallIdField({ value, onChange, busy }) {
  return <>
    <p className={styles.help} id="cafe24-id-help">카페24 관리자 주소 앞부분이 몰 아이디예요(예: myshop.cafe24.com의 myshop)</p>
    <label className={styles.field} htmlFor="cafe24-mall-id">몰 아이디
      <input id="cafe24-mall-id" name="mallId" value={value} disabled={busy} autoCapitalize="none" autoCorrect="off"
        autoComplete="off" spellCheck={false} aria-describedby="cafe24-id-help"
        onChange={(event) => onChange?.(normalizeMallId(event.target.value))} />
    </label>
    <p className={styles.help}>지금 쓰는 브라우저에서 10분 안에 끝내 주세요</p>
  </>;
}

export function Cafe24Connection({ connection, onChanged, returnTo, unavailable = false, storageKey = 'cafe24-install-mall-id', detail = false }) {
  const [state, setState] = useState(null);
  const [mallId, setMallId] = useState(connection?.mallId || '');
  const [error, setError] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const anchorRef = useRef(null);
  const rowRef = useRef(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has('mall_id')) {
      const candidate = url.searchParams.get('mall_id').trim().toLowerCase();
      if (/^[a-z0-9][a-z0-9_-]{1,29}$/.test(candidate)) {
        try { window.sessionStorage.setItem(CAFE24_APP_MALL_ID_KEY, candidate); } catch { /* Prefilling does not require storage. */ }
        setMallId(candidate); setState('editing');
      }
      for (const key of [...url.searchParams.keys()]) if (key !== 'returnTo') url.searchParams.delete(key);
      window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
      return;
    }
    if (url.searchParams.get('mall') !== 'cafe24' || !url.searchParams.has('result')) return;
    const result = url.searchParams.get('result');
    try { setMallId(window.sessionStorage.getItem(storageKey) || connection?.mallId || ''); } catch { /* Storage may be unavailable. */ }
    if (result === 'connected') { onChanged?.(); }
    else { setState('failed'); setError(CAFE24_INSTALL_COPY[result] || CAFE24_INSTALL_COPY.token_exchange_failed); }
    url.searchParams.delete('result'); url.searchParams.delete('mall');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
  }, [storageKey]);
  useEffect(() => {
    // 인증 초기화와 연결 조회가 끝나기 전에는 계정 key 변경으로 다시 마운트될 수 있다.
    if (unavailable) return;
    try {
      const candidate = window.sessionStorage.getItem(CAFE24_APP_MALL_ID_KEY);
      if (candidate == null) return;
      window.sessionStorage.removeItem(CAFE24_APP_MALL_ID_KEY);
      if (/^[a-z0-9][a-z0-9_-]{1,29}$/.test(candidate)) { setMallId(candidate); setState('editing'); }
    } catch { /* Storage may be unavailable. */ }
  }, [unavailable]);
  const submit = async (event) => {
    event.preventDefault();
    if (locked.current || !mallId.trim() || unavailable) return;
    locked.current = true; setError(''); setState('starting');
    try {
      try { window.sessionStorage.setItem(storageKey, mallId); } catch { /* Connecting does not require local storage. */ }
      const { startUrl } = await api.startCafe24Install({ mallId, returnTo });
      if (active.current) window.location.assign(startUrl);
    } catch (failure) {
      if (active.current) { setError(CAFE24_INSTALL_COPY[failure.code] || '연결을 시작하지 못했어요. 잠시 뒤 다시 시도해 주세요'); setState('failed'); }
      locked.current = false;
    }
  };
  const revoke = async (event) => {
    event.preventDefault();
    if (locked.current) return;
    locked.current = true; setBusy(true); setError('');
    try {
      await api.disconnectCafe24();
      if (active.current) { setState(null); setMallId(''); setDialog(false); onChanged?.(null); }
    } catch (failure) { if (active.current) setError(failure.message || '연결을 해제하지 못했어요'); }
    finally { locked.current = false; if (active.current) setBusy(false); }
  };
  return <>
    <MallConnectionRow detail={detail} returnTo={returnTo} mall="cafe24" title="카페24" help="자사몰 관리자 로그인으로 연결"
      state={unavailable ? 'unavailable' : state || connection?.status || 'none'} connection={connection}
      error={error} onSubmit={submit} submitLabel="카페24에서 앱 설치" submitDisabled={!mallId.trim()}
      onChange={setMallId} onConnect={() => { setMallId(connection?.mallId || mallId); setError(''); setState('editing'); }}
      onCollapse={() => { setState(null); setError(''); }} rowRef={rowRef} anchorRef={anchorRef}
      menuOpen={menuOpen} onMenuToggle={() => setMenuOpen(!menuOpen)} onMenuClose={() => setMenuOpen(false)}
      menuItems={[{ id: 'revoke', label: '연결 해제', onSelect: () => { setMenuOpen(false); setError(''); setDialog(true); } }]}>
      <MallIdField value={mallId} onChange={setMallId} busy={state === 'starting'} />
    </MallConnectionRow>
    {dialog && <MallActionDialog mall="cafe24" kind="revoke" busy={busy} error={error} onSubmit={revoke}
      onClose={() => { if (!locked.current) setDialog(false); }} anchorRef={anchorRef} fallbackRef={rowRef} />}
  </>;
}

export function MallConnections() {
  const { session } = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const userId = session?.user?.id;
  const queryKey = ['mall-connections', userId];
  const { data, isPending, error, refetch } = useQuery({ queryKey, queryFn: () => api.listMalls(), enabled: !!userId, retry: false });
  const zigzag = data?.items?.find((item) => item.mall === 'zigzag');
  const requestedReturn = location.state?.returnTo || new URLSearchParams(location.search).get('returnTo');
  const returnTo = typeof requestedReturn === 'string' && requestedReturn.startsWith('/')
    && !requestedReturn.startsWith('//') && !requestedReturn.includes('\\') ? requestedReturn : null;
  const changed = () => {
    queryClient.invalidateQueries({ queryKey });
    queryClient.invalidateQueries({ queryKey: ['mall-defaults', userId] });
    queryClient.invalidateQueries({ queryKey: ['mall-addresses', userId] });
    queryClient.invalidateQueries({ queryKey: ['mall-registration-catalog', userId] });
  };
  return <main className={styles.page}>
    <header className={styles.pageHead}><div><h1 id="mall-connections-title">쇼핑몰 연결</h1><p>한 번 연결해 두면 에디터에서 바로 등록할 수 있어요.</p></div>
      {returnTo && <Link className={styles.goLink} to={returnTo}>에디터로 돌아가기 ›</Link>}
    </header>
      <section className={styles.mallList} aria-labelledby="mall-connections-title">
        {isPending && <p className={styles.cardHead} role="status">연결 정보를 불러오는 중이에요</p>}
        {error && <div className={styles.cardHead} role="alert"><p>{error.message}</p><Button onClick={refetch}>다시 확인</Button></div>}
        {data?.disabled && <p className={styles.cardHead}>쇼핑몰 연결을 준비하고 있어요</p>}
        {!isPending && !error && !data?.disabled && <div>
          <ZigzagConnection returnTo={returnTo} key={`${userId}:${zigzag?.connectionVersion}`} connection={zigzag} onChanged={changed} />
        </div>}
        <Cafe24Connection key={`cafe24:${userId}`} connection={data?.items?.find((item) => item.mall === 'cafe24')}
          unavailable={isPending || !!error || !!data?.disabled || data?.items?.find((item) => item.mall === 'cafe24')?.status === 'unavailable'}
          returnTo={returnTo} storageKey={`cafe24-install-mall-id:${userId}`} onChanged={changed} />
        <div className={styles.mallRow}>
          <img className={styles.mallLogo} src="/assets/malls/ably-48.png" srcSet="/assets/malls/ably-96.png 2x" alt="" />
          <div className={styles.mallInfo}><h3>에이블리</h3><p className={styles.help}>셀러스 입점만 연결할 수 있어요. 파트너스는 연결할 수 없어요.</p></div>
          <span className={styles.status}>준비 중</span>
        </div>
      </section>
    {!data?.disabled && <p className={styles.securityNote}><Icon name="lock" size={16} />인증 정보는 서버에 암호화해 저장해요</p>}
  </main>;
}

export function MallSettings() {
  const { mall } = useParams();
  const { session } = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const userId = session?.user?.id;
  const supported = ['cafe24', 'zigzag'].includes(mall);
  const connectionsKey = ['mall-connections', userId];
  const connections = useQuery({ queryKey: connectionsKey, queryFn: () => api.listMalls(), enabled: !!userId && supported, retry: false });
  const connection = connections.data?.items?.find((item) => item.mall === mall);
  const defaultsKey = ['mall-defaults', userId, mall, connection?.connectionVersion];
  const defaults = useQuery({ queryKey: defaultsKey, queryFn: () => api.getMallDefaults(mall),
    enabled: !!userId && supported && connection?.status === 'connected', retry: false });
  const requestedReturn = location.state?.returnTo;
  const returnTo = typeof requestedReturn === 'string' && requestedReturn.startsWith('/') && !requestedReturn.startsWith('//') && !requestedReturn.includes('\\') ? requestedReturn : null;
  const changed = () => {
    queryClient.invalidateQueries({ queryKey: connectionsKey });
    queryClient.invalidateQueries({ queryKey: ['mall-defaults', userId] });
    queryClient.invalidateQueries({ queryKey: ['mall-addresses', userId] });
    queryClient.invalidateQueries({ queryKey: ['mall-registration-catalog', userId] });
  };
  if (!supported || connections.data && connection?.status !== 'connected') return <Navigate to="/settings/malls" state={{ returnTo }} replace />;
  return <main className={styles.page}>
    <header className={styles.pageHead}><Link className={styles.goLink} to="/settings/malls" state={{ returnTo }}>‹ 쇼핑몰 연결</Link>
      {returnTo && <Link className={styles.goLink} to={returnTo}>에디터로 돌아가기 ›</Link>}</header>
    {connections.isPending ? <p role="status">연결 정보를 불러오는 중이에요</p> : connections.error
      ? <div role="alert"><p>{connections.error.message}</p><Button onClick={() => connections.refetch()}>다시 확인</Button></div>
      : <>
        <section className={styles.detailHead} aria-label="쇼핑몰 연결 관리">
          {mall === 'cafe24' ? <Cafe24Connection detail connection={connection} onChanged={changed} returnTo={returnTo} />
            : <ZigzagConnection detail connection={connection} onChanged={changed} />}
        </section>
        {defaults.isPending ? <p role="status">판매 정보를 불러오는 중이에요</p> : defaults.error
          ? <div role="alert"><p>{defaults.error.message}</p><Button onClick={() => defaults.refetch()}>다시 확인</Button></div>
          : defaults.data && <MallDefaultsForm key={`${userId}:${mall}:${connection?.connectionVersion}`} mall={mall} defaults={defaults.data}
            onSaved={(value) => { queryClient.setQueryData(defaultsKey, value); queryClient.invalidateQueries({ queryKey: connectionsKey }); }} />}
      </>}
  </main>;
}
