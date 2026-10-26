import { useEffect, useRef, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/index.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Button, Icon, Modal } from '@/components/ui.jsx';
import { seoulDate } from '@/lib/datetime.js';
import { ExportMenu } from '@/features/editor/ExportMenu.jsx';
import { MallDefaultsChecklist } from './MallDefaultsChecklist.jsx';
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

export function MallConnectionRow({ state, connection, values, error, showSecret, onToggleSecret, onChange, onSubmit,
  onConnect, onCollapse, anchorRef, rowRef, menuOpen, onMenuToggle, onMenuClose, menuItems = [] }) {
  const busy = state === 'verifying';
  const editing = ['editing', 'verifying', 'failed'].includes(state);
  return <div className={styles.mallRow} data-state={state} ref={rowRef}>
    <img className={styles.mallLogo} src="/assets/malls/zigzag-48.png" srcSet="/assets/malls/zigzag-96.png 2x" alt="" />
    <div className={styles.mallInfo}>
      <div className={styles.mallTitle}><h2>지그재그</h2>{state === 'connected' && <strong>{connection.shopName}</strong>}</div>
      {state === 'connected' ? <div className={styles.metadata}>
        <span>권한 {(connection.permissions || []).join(', ')}</span><span>인증키 끝자리 {connection.keyHint || '••••••••'}</span>
        <span>연결한 날 {seoulDate(connection.connectedAt)}</span>
        <span>마지막 등록 {connection.lastRegisteredAt ? seoulDate(connection.lastRegisteredAt) : '없음'}</span>
      </div> : <p className={styles.help}>{busy ? '스토어 이름과 권한을 받아 오고 있어요' : '파트너센터 상품과 주문 API'}</p>}
    </div>
    <div className={styles.rowActions}>
      {state === 'none' && <Button onClick={onConnect}>연결하기</Button>}
      {editing && <span className={styles.status}>{busy ? '확인 중' : '입력 중'}</span>}
      {state === 'connected' && <>
        <span className={styles.status}><span className={styles.connectedDot} aria-hidden="true" />연결됨</span>
        <button type="button" className={styles.manage} ref={anchorRef} aria-label="지그재그 연결 관리" aria-haspopup="menu"
          aria-expanded={!!menuOpen} onClick={onMenuToggle}>⋯</button>
        <ExportMenu open={menuOpen} onClose={onMenuClose} anchorRef={anchorRef} items={menuItems} />
      </>}
    </div>
    {editing && <form className={styles.inlineForm} onSubmit={onSubmit}>
      <div className={styles.issuePath}><p className={styles.help}>로그인 뒤 스토어 정보 관리 › 상품과 주문 API에서 인증키 발급</p>
        <a className={styles.goLink} href="https://partners.kakaostyle.com/" target="_blank" rel="noopener noreferrer">파트너센터에서 인증키 발급하기 ›</a>
      </div>
      <KeyFields values={values} onChange={onChange} showSecret={showSecret} onToggleSecret={onToggleSecret} busy={busy} />
      <p className={styles.help}>저장하면 다시 보여 주지 않아요</p>
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={`modal-actions ${styles.leftActions}`}>
        <Button type="submit" variant="primary" disabled={busy || !values?.accessKey?.trim() || !values?.secretKey?.trim()}>
          {busy && <Icon name="loader" size={16} className="spin" />}{busy ? '확인 중' : state === 'failed' ? '다시 확인' : '연결 확인'}</Button>
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

export function MallActionDialog({ kind, values, showSecret, onToggleSecret, onChange, busy, error, onSubmit, onClose, anchorRef, fallbackRef }) {
  return <MallDialog kind={kind} anchorRef={anchorRef} fallbackRef={fallbackRef} busy={busy} onClose={onClose}>
    <h2 id={`mall-${kind}-title`}>{kind === 'revoke' ? '지그재그 연결을 해제할까요?' : '인증정보 변경'}</h2>
    <p>{kind === 'revoke' ? '스토어와의 연결이 끊겨요. 해제하면 에디터에서 바로 등록을 못 해요. 등록한 상품과 게시한 이미지는 그대로예요. 기본 설정은 남아요'
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

export function ZigzagConnection({ connection, onChanged, onDefaultsSaved }) {
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
    <MallConnectionRow state={connection?.status === 'connected' ? 'connected' : state} connection={connection}
      values={values} onChange={setValues} showSecret={showSecret} onToggleSecret={() => setShowSecret((value) => !value)} error={error}
      onSubmit={submit} onConnect={() => setState('editing')} onCollapse={() => { setState('none'); clearKeys(); }}
      anchorRef={anchorRef} rowRef={rowRef} menuOpen={menuOpen} onMenuToggle={() => setMenuOpen(!menuOpen)} onMenuClose={() => setMenuOpen(false)}
      menuItems={[{ id: 'rotate', label: '인증정보 변경', kind: 'action', onSelect: () => manage('rotate') },
        { id: 'revoke', label: '연결 해제', kind: 'action', onSelect: () => manage('revoke') }]} />
    {connection?.status === 'connected' && <MallDefaultsChecklist defaults={connection.defaults} onSaved={onDefaultsSaved} />}
    {dialog && <MallActionDialog kind={dialog} values={values} onChange={setValues} showSecret={showSecret}
      onToggleSecret={() => setShowSecret((value) => !value)} busy={busy} error={error} onSubmit={submit} onClose={closeDialog} anchorRef={anchorRef} fallbackRef={rowRef} />}
  </>;
}

export function MallConnections() {
  const { session } = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const userId = session?.user?.id;
  const queryKey = ['mall-connections', userId];
  const { data, isPending, error, refetch } = useQuery({ queryKey, queryFn: () => api.listMalls(), enabled: !!userId, retry: false });
  const returnTo = typeof location.state?.returnTo === 'string' && location.state.returnTo.startsWith('/')
    && !location.state.returnTo.startsWith('//') ? location.state.returnTo : null;
  const updateZigzag = (patch) => queryClient.setQueryData(queryKey, (previous) => ({ ...previous,
    items: (previous?.items || []).map((item) => item.mall === 'zigzag' ? { ...item, ...patch } : item) }));
  const changed = (connection) => {
    updateZigzag(connection || { mall: 'zigzag', status: 'none', shopName: null, keyHint: null, permissions: [] });
    queryClient.invalidateQueries({ queryKey });
    queryClient.invalidateQueries({ queryKey: ['zigzag-addresses', userId] });
  };
  return <main className={styles.page}>
    <header className={styles.pageHead}><div><h1>쇼핑몰 연결</h1><p>연결해 두면 에디터에서 상세페이지를 몰에 바로 등록할 수 있어요</p></div>
      {returnTo && <Link className={styles.goLink} to={returnTo}>에디터로 돌아가기 ›</Link>}
    </header>
    {isPending ? <p role="status">연결 정보를 불러오는 중이에요</p> : error ? <div role="alert"><p>{error.message}</p><Button onClick={refetch}>다시 확인</Button></div>
      : data?.disabled ? <p>쇼핑몰 연결을 준비하고 있어요</p> : <div className={styles.mallList}>
        <ZigzagConnection key={userId} connection={data?.items?.find((item) => item.mall === 'zigzag')}
          onChanged={changed} onDefaultsSaved={(defaults) => updateZigzag({ defaults })} />
        <div className={styles.mallRow}>
          <img className={styles.mallLogo} src="/assets/malls/ably-48.png" srcSet="/assets/malls/ably-96.png 2x" alt="" />
          <div className={styles.mallInfo}><h2>에이블리</h2><p className={styles.help}>셀러스 어드민 API 토큰</p><p className={styles.help}>제휴가 끝나면 열려요</p></div>
          <span className={styles.status}>준비 중</span>
        </div>
        <div className={styles.mallRow}>
          <span className={styles.mallMark} aria-hidden="true">N</span><div className={styles.mallInfo}><h2>스마트스토어</h2><p className={styles.help}>네이버 커머스 API</p></div>
          <span className={styles.status}>준비 중</span>
        </div>
      </div>}
    {!data?.disabled && <p className={styles.securityNote}><Icon name="lock" size={16} />인증키는 서버에 암호화해 저장하고 끝자리 8자만 보여요</p>}
  </main>;
}
