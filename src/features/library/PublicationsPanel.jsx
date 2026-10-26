import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api/index.js';
import { clientEvents } from '@/lib/clientEvents.js';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { Button, Modal, useToast } from '@/components/ui.jsx';
import { buildPublicationHtml, downloadPublicationZip } from '@/features/editor/detailPublish.js';
import styles from './PublicationsPanel.module.css';

const dateLabel = (value) => new Date(value).toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' }).replace(/-/g, '.');

export function PublicationsPanel({ projectId, productName, onClose }) {
  const { session } = useAuth();
  const { push } = useToast();
  const scope = `${projectId}:${session?.user?.id || ''}`;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const requestRef = useRef(0);
  const [data, setData] = useState({ scope: null, items: [] });
  const [registrations, setRegistrations] = useState({ scope: null, items: [] });
  const [registrationError, setRegistrationError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [manualHtml, setManualHtml] = useState('');
  const items = data.scope === scope ? data.items : [];
  useEffect(() => {
    const token = ++requestRef.current;
    const accountGeneration = clientEvents.getAccountGeneration();
    const isCurrent = () => token === requestRef.current && scope === scopeRef.current
      && accountGeneration === clientEvents.getAccountGeneration();
    setLoading(true); setError(''); setBusy(false); setConfirm(null); setManualHtml('');
    setRegistrationError('');
    api.listMallRegistrations(projectId).then((response) => {
      if (isCurrent()) setRegistrations({ scope, items: response.items });
    }).catch((failure) => {
      if (isCurrent() && failure.status !== 404) setRegistrationError('마지막 등록을 불러오지 못했어요');
    });
    api.listDetailPublications(projectId).then((response) => {
      if (isCurrent()) setData({ scope, items: response.items });
    }).catch((error) => { if (isCurrent()) setError(error.message || '게시 목록을 불러오지 못했어요.'); })
      .finally(() => { if (isCurrent()) setLoading(false); });
    return () => { requestRef.current++; };
  }, [projectId, scope, attempt]);

  const actionGuard = () => {
    const token = requestRef.current;
    const accountGeneration = clientEvents.getAccountGeneration();
    return () => token === requestRef.current && scope === scopeRef.current
      && accountGeneration === clientEvents.getAccountGeneration();
  };
  const copyHtml = async (publication) => {
    const isCurrent = actionGuard();
    const html = buildPublicationHtml(publication.files, { productName, width: publication.width });
    try {
      await navigator.clipboard.writeText(html);
      if (isCurrent()) push('복사했어요');
    } catch {
      if (!isCurrent()) return;
      setManualHtml(html);
      push('복사하지 못했어요. 텍스트를 길게 눌러 복사해 주세요');
    }
  };
  const receiveZip = async (publication) => {
    if (busy) return;
    const isCurrent = actionGuard();
    setBusy(true); setError('');
    try {
      const blob = await downloadPublicationZip(publication.files, productName, {});
      if (!isCurrent()) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = `${String(productName || '').replace(/[\\/:*?"<>|]/g, ' ').trim() || '상세페이지'}.zip`;
      document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) { if (isCurrent()) setError(error.message || 'ZIP을 받지 못했어요.'); }
    finally { if (isCurrent()) setBusy(false); }
  };
  const deleteConfirmed = async () => {
    if (busy || !confirm) return;
    const isCurrent = actionGuard();
    setBusy(true); setError('');
    try {
      await api.deleteDetailPublication(confirm.publicationId);
      if (!isCurrent()) return;
      setData((current) => ({ ...current, items: current.items.filter((item) => item.publicationId !== confirm.publicationId) }));
      setConfirm(null);
    } catch (error) { if (isCurrent()) setError(error.message || '게시를 지우지 못했어요.'); }
    finally { if (isCurrent()) setBusy(false); }
  };

  const lastRegistration = registrations.scope === scope ? registrations.items[0] : null;
  const lastRegistrationLabel = lastRegistration && (['pending', 'running', 'queued'].includes(lastRegistration.status) ? '등록 중'
    : lastRegistration.outcome === 'created' ? `상품 ${lastRegistration.productNo}`
    : ['unknown', 'duplicate_suspected'].includes(lastRegistration.outcome) ? '등록 여부 확인 필요' : '등록하지 못했어요');
  return (
    <Modal onClose={() => { if (!busy && !confirm) onClose(); }} wide>
      <div className={styles.panel}>
        <h3 className={styles.title}>붙여넣을 코드</h3>
        {lastRegistration && <p className={styles.lastRegistration}>마지막 등록 카페24 · {lastRegistrationLabel} · {dateLabel(lastRegistration.finishedAt || lastRegistration.createdAt)}
          {lastRegistration.outcome === 'created' && lastRegistration.productUrl
            && <> · <a href={lastRegistration.productUrl} target="_blank" rel="noopener noreferrer">자사몰에서 보기 ›</a></>}
        </p>}
        {registrationError && <p className={styles.lastRegistration} role="status">{registrationError}</p>}
        {loading && <p role="status">붙여넣을 코드를 불러오는 중이에요</p>}
        {!loading && !error && items.length === 0 && <p className={styles.notice}>만든 코드가 없어요</p>}
        {error && <div role="alert" className={styles.error}><p>{error}</p>
          {!items.length && <div className="modal-actions"><Button disabled={busy} onClick={() => setAttempt((value) => value + 1)}>다시 불러오기</Button></div>}
        </div>}
        <ul className={styles.list}>
          {items.map((publication) => {
            const days = publication.expiresAt ? Math.max(0, Math.ceil((new Date(publication.expiresAt).getTime() - Date.now()) / 86400000)) : null;
            return <li className={styles.row} key={publication.publicationId}>
              <p className={styles.metadata}>버전 {publication.version} · {dateLabel(publication.createdAt)} · {publication.width}px · 조각 {publication.sliceCount}개</p>
              <p className={days !== null && days <= 14 ? styles.warning : styles.notice}>
                {days === null ? '구독 중 보관' : days <= 14 ? `${days}일 뒤 지워져요` : `${dateLabel(publication.expiresAt)}까지 보관`}
              </p>
              <div className={`modal-actions ${styles.actions}`}>
                <Button disabled={busy} onClick={() => copyHtml(publication)}>HTML 복사</Button>
                <Button disabled={busy} onClick={() => receiveZip(publication)}>ZIP 받기</Button>
                <Button disabled={busy} onClick={() => setConfirm(publication)}>삭제</Button>
              </div>
            </li>;
          })}
        </ul>
        {data.scope === scope && manualHtml && <label className={styles.copyLabel}>HTML<textarea className={styles.copyText} readOnly value={manualHtml} onFocus={(event) => event.target.select()} /></label>}
        {busy && <p className={styles.notice} role="status">처리 중이에요</p>}
        <div className="modal-actions"><Button disabled={busy} onClick={onClose}>닫기</Button></div>
      </div>
      {data.scope === scope && confirm && <Modal onClose={() => { if (!busy) setConfirm(null); }}>
        <h3 className={styles.title}>게시를 지울까요?</h3>
        <p className={styles.confirmCopy}>몰에 붙여 둔 상세페이지가 깨져요. 몰에서 먼저 바꾼 뒤 지우세요.</p>
        {error && <p role="alert" className={styles.error}>{error}</p>}
        <div className="modal-actions">
          <Button disabled={busy} onClick={() => setConfirm(null)}>취소</Button>
          <Button variant="primary" disabled={busy} onClick={deleteConfirmed}>지우기</Button>
        </div>
      </Modal>}
    </Modal>
  );
}
