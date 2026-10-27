import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '@/lib/api/index.js';
import { clientEvents } from '@/lib/clientEvents.js';
import { Button, Icon, Modal } from '@/components/ui.jsx';
import styles from './MallRegisterModal.module.css';

const FAILURE_COPY = {
  reconnect_required: '카페24 연결이 끊겼어요. 쇼핑몰 연결에서 다시 설치해 주세요',
  app_removed: '카페24에서 앱이 삭제됐어요. 다시 설치해 주세요',
  missing_permission: '상품 등록 권한이 없어요. 앱을 다시 설치하며 권한을 허용해 주세요',
  invalid_request: '카페24가 입력을 받지 않았어요. 판매가와 상품명을 확인해 주세요',
  price_setting_unavailable: '카페24 가격 설정을 확인하지 못했어요. 잠시 뒤 다시 등록해 주세요',
  unreachable: '카페24에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요',
  rate_limited: '카페24 호출이 잠시 막혔어요. 1분 뒤 다시 시도해 주세요',
  image_upload_mismatch: '상세 이미지를 올리지 못했어요. 잠시 뒤 다시 시도해 주세요',
  connection_changed: '그 사이 카페24 연결이 바뀌었어요. 다시 등록해 주세요',
  cafe24_not_allowed: '지금은 시범 운영 중이라 등록된 몰만 등록할 수 있어요',
  cafe24_disabled: '카페24 등록이 잠시 꺼져 있어요',
  cafe24_unconfigured: '카페24 등록을 준비하고 있어요',
  real_model_not_allowed: '실존 모델이 포함된 상세페이지는 등록할 수 없어요',
  account_closed: '계정 삭제가 완료되어 사용할 수 없습니다.',
  cancelled: '등록을 취소했어요',
  unknown: '카페24에서 오류가 났어요. 잠시 뒤 다시 시도해 주세요',
};
const reconnectCodes = ['reconnect_required', 'app_removed', 'missing_permission', 'not_connected', 'connection_changed'];
const validMoney = (value, minimum = 1) => /^\d+$/.test(value) && Number(value) >= minimum && Number(value) <= 2147483647;
const won = (value) => Number(value || 0).toLocaleString('ko-KR');

export function MallRegisterModal({ projectId, productName, connection, assets = [], waitForSave, onPublish, onConflict, onClose, onRegistered, anchorRef }) {
  const [phase, setPhase] = useState('preparing');
  const [publication, setPublication] = useState(null);
  const [name, setName] = useState(productName || '');
  const [price, setPrice] = useState('');
  const [supplyPrice, setSupplyPrice] = useState('');
  const [display, setDisplay] = useState(true);
  const [selling, setSelling] = useState(true);
  const [taxType, setTaxType] = useState('A');
  const [categoryNo, setCategoryNo] = useState('');
  const [thumbnailAssetId, setThumbnailAssetId] = useState(assets[0]?.assetId || '');
  const [categories, setCategories] = useState([]);
  const [categoryError, setCategoryError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [result, setResult] = useState(null);
  const [progress, setProgress] = useState(0);
  const request = useRef(null);
  const locked = useRef(false);
  const active = useRef(false);
  const prepareRun = useRef(0);
  const categoryRun = useRef(0);
  const dialogRef = useRef(null);
  const scope = useRef({ projectId, mallId: connection?.mallId, accountGeneration: clientEvents.getAccountGeneration() });
  const connectionChanged = useRef(false);
  const isCurrent = () => active.current && !connectionChanged.current && projectId === scope.current.projectId
    && scope.current.accountGeneration === clientEvents.getAccountGeneration();
  const busy = ['publishing', 'submitting', 'polling', 'verifying'].includes(phase);

  const prepare = async () => {
    if (!isCurrent()) return;
    const run = ++prepareRun.current;
    const isCurrentRun = () => run === prepareRun.current && isCurrent();
    setPhase('preparing'); setError('');
    try {
      const history = await api.listMallRegistrations(projectId);
      if (!isCurrentRun()) return;
      if (!history || !Object.hasOwn(history, 'recovery')) throw new Error('등록 내역을 확인하지 못했어요. 다시 불러와 주세요');
      const recovery = history.recovery;
      if (recovery) {
        if (!recovery.jobId || recovery.mallId !== connection.mallId) throw new Error('등록 내역의 쇼핑몰을 확인하지 못했어요. 다시 불러와 주세요');
        request.current = { jobId: recovery.jobId };
        setProgress(recovery.progress || 0);
        if (!applyDetail(recovery)) await poll();
        return;
      }
      await waitForSave();
      if (!isCurrentRun()) return;
      const revision = await api.peekEditorRevision(projectId);
      if (!isCurrentRun()) return;
      if (revision !== (api.editorRevisionOf?.(projectId) ?? null)) {
        onConflict?.();
        throw new Error('편집 내용이 다른 창에서 바뀌었어요. 다시 불러온 뒤 등록해 주세요');
      }
      const response = await api.listDetailPublications(projectId);
      if (!isCurrentRun()) return;
      if (response.disabled) throw new Error('상세페이지 게시를 준비하고 있어요');
      const current = [...response.items].filter((item) => revision != null && item.editorRevision === revision)
        .sort((a, b) => (b.version || 0) - (a.version || 0) || String(b.createdAt || '').localeCompare(String(a.createdAt || '')))[0];
      let ready = current;
      if (!ready) {
        setPhase('publishing');
        ready = await onPublish(() => { if (!isCurrentRun()) throw new Error('등록 창을 다시 열어 주세요'); });
        if (!isCurrentRun()) return;
        if (!ready?.publicationId) throw new Error('상세페이지 게시를 마치지 못했어요');
      }
      setPublication(ready); setName(ready.productName || productName || ''); setPhase('ready');
    } catch (failure) {
      if (isCurrentRun()) { setError(failure.message || '저장 상태를 확인하지 못했어요'); setPhase('prepare_error'); }
    }
  };
  const loadCategories = async () => {
    if (!isCurrent()) return;
    const run = ++categoryRun.current;
    const isCurrentRun = () => run === categoryRun.current && isCurrent();
    setCategoryError('');
    try { const items = await api.listCafe24Categories(); if (isCurrentRun()) setCategories(items); }
    catch { if (isCurrentRun()) setCategoryError('카테고리를 불러오지 못했어요. 선택 없이 등록할 수 있어요'); }
  };
  useEffect(() => {
    active.current = true;
    prepare(); loadCategories();
    return () => { active.current = false; };
  }, [projectId]);
  useLayoutEffect(() => {
    if (connection?.mallId !== scope.current.mallId) connectionChanged.current = true;
    if (!connectionChanged.current) return;
    setResult(null); setNotice(''); setError('카페24 연결이 바뀌었어요. 등록 창을 다시 열어 주세요'); setPhase('connection_changed');
  }, [connection?.mallId]);
  useEffect(() => {
    const previous = typeof document !== 'undefined' ? document.activeElement : null;
    return () => { (anchorRef?.current || previous)?.focus(); };
  }, [anchorRef]);
  useEffect(() => {
    const target = phase === 'ready' ? dialogRef.current?.querySelector('[name="price"]') : dialogRef.current;
    target?.focus();
  }, [phase]);

  const applyDetail = (detail) => {
    // The server derives outcome from durable metadata, even when the job status is error.
    if (detail.outcome && (['done', 'error', 'cancelled'].includes(detail.status) || !detail.status || detail.outcome === 'created')) {
      setResult(detail); setPhase('result');
      if (detail.outcome === 'created') onRegistered?.(detail);
      return true;
    }
    return false;
  };
  const poll = async () => {
    const jobId = request.current?.jobId;
    if (!jobId) return;
    setPhase('polling'); setError('');
    let pollError;
    try {
      await api.pollJob(jobId, { timeoutMs: 600000, onProgress: (value) => { if (isCurrent()) setProgress(value); } });
    } catch (failure) { pollError = failure; }
    if (!isCurrent()) return;
    try {
      const detail = await api.getCafe24Registration(jobId);
      if (!isCurrent() || applyDetail(detail)) return;
      setPhase('waiting');
      setNotice(pollError?.code === 'job_timeout' ? '아직 진행 중이에요. 이 창을 닫아도 계속돼요' : '등록 상태를 다시 확인해 주세요');
    } catch {
      if (isCurrent()) {
        setPhase('waiting');
        setNotice(pollError?.code === 'job_timeout' ? '아직 진행 중이에요. 이 창을 닫아도 계속돼요' : '등록 상태를 불러오지 못했어요. 다시 확인해 주세요');
      }
    }
  };
  const submit = async () => {
    if (!isCurrent() || locked.current || !['ready', 'submit_error'].includes(phase)) return;
    if (!request.current && (!validMoney(price) || !name.trim() || name.trim().length > 100 || supplyPrice !== '' && !validMoney(supplyPrice, 0))) return;
    locked.current = true; setPhase('submitting'); setError('');
    if (!request.current) request.current = { requestKey: crypto.randomUUID(), input: {
      projectId, productName: name.trim(), price: Number(price),
      ...(supplyPrice === '' ? {} : { supplyPrice: Number(supplyPrice) }), display: display ? 'T' : 'F', selling: selling ? 'T' : 'F', taxType,
      ...(categoryNo ? { categoryNo: Number(categoryNo) } : {}), ...(thumbnailAssetId ? { thumbnailAssetId } : {}),
    }, publication };
    try {
      const confirmed = request.current;
      if (!confirmed.publication?.publicationId) throw new Error('상세페이지 게시를 마치지 못했어요');
      const accepted = await api.createCafe24Registration({ ...confirmed.input, publicationId: confirmed.publication.publicationId, requestKey: confirmed.requestKey });
      confirmed.jobId = accepted.jobId;
      if (isCurrent()) await poll();
    } catch (failure) {
      if (!isCurrent()) return;
      if (failure.code === 'registration_in_progress' && failure.jobId) {
        request.current.jobId = failure.jobId; await poll();
      } else if (failure.status >= 400 && failure.status < 500 && failure.status !== 429) {
        setResult({ outcome: 'failed', code: failure.code, errorMessage: FAILURE_COPY[failure.code] || '등록 요청을 확인해 주세요' }); setPhase('result');
      } else { setError('등록 요청을 마치지 못했어요. 같은 요청으로 다시 시도해 주세요'); setPhase('submit_error'); }
    } finally { locked.current = false; }
  };
  const resume = async () => {
    if (!isCurrent() || locked.current) return;
    locked.current = true;
    try { await poll(); } finally { locked.current = false; }
  };
  const verify = async () => {
    if (!isCurrent() || locked.current || !request.current?.jobId) return;
    locked.current = true; setPhase('verifying'); setError('');
    let response, verifyError;
    try { response = await api.verifyCafe24Registration(request.current.jobId); }
    catch (failure) { verifyError = failure; }
    if (!isCurrent()) { locked.current = false; return; }
    try {
      const detail = await api.getCafe24Registration(request.current.jobId);
      if (!isCurrent()) return;
      applyDetail(detail);
      setNotice(response?.found === false ? '아직 확인되지 않았어요. 잠시 뒤 다시 확인하거나 카페24 관리자 상품 목록을 봐 주세요' : '');
      if (verifyError && detail.outcome !== 'created') throw verifyError;
    } catch (failure) {
      if (isCurrent()) { setPhase('result'); setError(reconnectCodes.includes((verifyError || failure).code)
        ? '연결이 바뀌어 확인할 수 없어요. 카페24 관리자에서 상품 목록을 확인해 주세요'
        : '등록 여부를 확인하지 못했어요. 잠시 뒤 다시 확인해 주세요'); }
    } finally { locked.current = false; }
  };
  const onKeyDown = (event) => {
    event.stopPropagation();
    if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
    if (event.key !== 'Tab') return;
    const controls = [...dialogRef.current.querySelectorAll('input:not(:disabled), select:not(:disabled), button:not(:disabled), a[href], summary')]
      .filter((node) => node.getClientRects().length > 0);
    const first = controls[0], last = controls[controls.length - 1];
    if (!first) { event.preventDefault(); dialogRef.current.focus(); }
    else if (event.shiftKey && [first, dialogRef.current].includes(dialogRef.current.ownerDocument.activeElement)) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && dialogRef.current.ownerDocument.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  const outcome = result?.outcome;
  const title = outcome === 'created' ? '등록 완료' : outcome === 'failed' ? '등록하지 못했어요'
    : outcome === 'duplicate_suspected' ? '카페24 관리자에서 확인해 주세요' : outcome === 'unknown' ? '등록 여부를 확인하고 있어요' : '확인하고 등록해요';
  const adminUrl = `https://${connection?.mallId}.cafe24.com/admin`;
  const selectedImage = assets.find((asset) => asset.assetId === thumbnailAssetId);
  const form = ['preparing', 'publishing', 'prepare_error', 'ready'].includes(phase);
  const valid = validMoney(price) && !!name.trim() && name.trim().length <= 100 && (supplyPrice === '' || validMoney(supplyPrice, 0));
  return <Modal onClose={onClose}>
    <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby="cafe24-register-title" ref={dialogRef} tabIndex={-1} onKeyDown={onKeyDown}>
      <header className={styles.header}>
        <span className={styles.eyebrow}>카페24 · {connection?.shopName}</span>
        <h2 id="cafe24-register-title">{title}</h2>
        {form && <p>판매가만 넣으면 돼요</p>}
        {phase === 'publishing' && <p role="status">지금 상세페이지를 먼저 게시해요</p>}
      </header>
      <div className={styles.body} aria-busy={busy || phase === 'preparing'}>
        {form && <>
          {phase === 'preparing' && <p role="status">저장 상태를 확인하고 있어요</p>}
          <section className={styles.zone} aria-labelledby="cafe24-check-title">
            <h3 id="cafe24-check-title">확인</h3>
            <label className={styles.row}><span>상품명</span><input name="productName" value={name} maxLength={100} onChange={(event) => setName(event.target.value)} disabled={phase !== 'ready'} /></label>
            <div className={styles.row}><span>상세설명</span><span>지금 상세페이지의 이미지 {publication?.files?.length || 0}장</span></div>
            <label className={styles.row}><span>대표 이미지</span><span className={styles.imageField}>
              {selectedImage && <img className={styles.thumbnail} src={selectedImage.src} alt="선택한 대표 이미지" />}
              <select name="thumbnailAssetId" value={thumbnailAssetId} onChange={(event) => setThumbnailAssetId(event.target.value)} disabled={phase !== 'ready'}>
                <option value="">없이 등록</option>{assets.map((asset, index) => <option key={asset.assetId} value={asset.assetId}>{asset.label || `사진 ${index + 1}`}</option>)}
              </select></span></label>
          </section>
          <section className={`${styles.zone} ${styles.inputZone}`} aria-labelledby="cafe24-input-title">
            <h3 id="cafe24-input-title">입력</h3>
            <label className={styles.row}><span>판매가</span><span className={styles.moneyField}><input name="price" inputMode="numeric" value={price} required
              aria-invalid={price !== '' && !validMoney(price)} onChange={(event) => setPrice(event.target.value)} disabled={phase !== 'ready'} /><span>원</span></span></label>
          </section>
          <details className={styles.zone}><summary>기본값 바꾸기</summary>
            <label className={styles.row}><span>공급가</span><span className={styles.moneyField}><input name="supplyPrice" inputMode="numeric" value={supplyPrice}
              placeholder={price || '판매가와 같음'} aria-invalid={supplyPrice !== '' && !validMoney(supplyPrice, 0)} onChange={(event) => setSupplyPrice(event.target.value)} disabled={phase !== 'ready'} /><span>원</span></span></label>
            <div className={styles.row}><span>진열·판매</span><div className={styles.choices}>
              <label><input type="checkbox" checked={display} onChange={(event) => setDisplay(event.target.checked)} disabled={phase !== 'ready'} />진열함</label>
              <label><input type="checkbox" checked={selling} onChange={(event) => setSelling(event.target.checked)} disabled={phase !== 'ready'} />판매함</label>
            </div></div>
            <div className={styles.row}><span id="cafe24-tax-label">과세</span><div className={styles.choices} role="radiogroup" aria-labelledby="cafe24-tax-label">
              <label><input type="radio" name="taxType" value="A" checked={taxType === 'A'} onChange={() => setTaxType('A')} disabled={phase !== 'ready'} />과세</label>
              <label><input type="radio" name="taxType" value="B" checked={taxType === 'B'} onChange={() => setTaxType('B')} disabled={phase !== 'ready'} />면세</label>
            </div></div>
            <label className={styles.row}><span>카테고리</span><select name="categoryNo" value={categoryNo} onChange={(event) => setCategoryNo(event.target.value)} disabled={phase !== 'ready'}>
              <option value="">선택 안 함</option>{categories.map((category) => <option key={category.categoryNo} value={category.categoryNo}>{category.fullName}</option>)}
            </select></label>
            {categoryError && <div className={styles.notice}><p role="status">{categoryError}</p><button className={styles.textButton} onClick={loadCategories}>카테고리 다시 불러오기</button></div>}
            <p className={styles.notice}>옵션과 재고는 카페24 관리자에서 더해요</p>
          </details>
        </>}
        {busy && !form && <p role="status"><Icon name="loader" className="spin" size={18} />{phase === 'verifying' ? '등록 여부를 확인하고 있어요'
          : phase === 'submitting' ? '상품 등록을 준비하고 있어요'
          : progress < 70 ? '이미지 준비 중' : '상품 전송 중'}</p>}
        {phase === 'polling' && <progress aria-label="상품 등록 진행률" max={100} value={progress} />}
        {outcome === 'created' && <div role="status"><p>카페24에 상품을 등록했어요</p><p>상품 번호 {result.productNo}</p>
          <p className={styles.notice}>옵션과 재고는 카페24 관리자에서 더해요</p>
          {result.thumbnail !== 'ok' && <p className={styles.notice}>대표 이미지는 카페24 관리자에서 넣어 주세요</p>}
          {result.warnings?.includes('마무리 확인이 늦어졌어요. 상품은 만들어졌어요')
            && <p className={styles.notice}>마무리 확인이 늦어졌어요. 상품은 만들어졌어요</p>}
          {result.productUrl && <a className={styles.goLink} href={result.productUrl} target="_blank" rel="noopener noreferrer">자사몰에서 보기 ›</a>}
        </div>}
        {outcome === 'unknown' && <p>지금 다시 등록하면 같은 상품이 두 번 생길 수 있어요</p>}
        {outcome === 'duplicate_suspected' && <p>같은 코드의 상품이 여러 개예요. 카페24 관리자에서 확인해 주세요</p>}
        {outcome === 'failed' && <p className={styles.error} role="alert">{FAILURE_COPY[result.code] || result.errorMessage || FAILURE_COPY.unknown}</p>}
        {notice && <p role="status" className={styles.notice}>{notice}</p>}
        {error && <p role="alert" className={styles.error}>{error}</p>}
      </div>
      <footer className={styles.footer}>
        {form && <p className={styles.summary}>판매가 {won(price)}원 · 사진 {publication?.files?.length || 0}장 · {display ? '진열함' : '진열 안 함'}, {selling ? '판매함' : '판매 안 함'}</p>}
        <div className="modal-actions">
          <Button onClick={onClose}>{form ? '취소' : '닫기'}</Button>
          {form && <Button variant="primary" disabled={phase !== 'ready' || !valid} onClick={submit}>카페24에 상품 등록</Button>}
          {['preparing', 'publishing', 'prepare_error'].includes(phase) && <Button disabled={phase === 'preparing' || phase === 'publishing'} onClick={prepare}>다시 불러오기</Button>}
          {phase === 'submit_error' && <Button variant="primary" onClick={submit}>다시 시도</Button>}
          {phase === 'waiting' && <Button variant="primary" onClick={resume}>상태 확인</Button>}
          {['unknown', 'duplicate_suspected'].includes(outcome) && <Button variant="primary" disabled={busy} onClick={verify}>다시 확인</Button>}
          {outcome === 'failed' && (reconnectCodes.includes(result.code)
            ? <Link className={styles.goLink} to="/settings/malls">다시 연결하기 ›</Link>
            : <Button onClick={onClose}>닫고 입력 확인하기</Button>)}
        </div>
        {outcome && outcome !== 'failed' && <a className={styles.goLink} href={adminUrl} target="_blank" rel="noopener noreferrer">카페24 관리자에서 보기 ›</a>}
      </footer>
    </div>
  </Modal>;
}
