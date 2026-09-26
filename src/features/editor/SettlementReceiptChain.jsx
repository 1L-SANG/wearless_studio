/* =============================================================
   영수증의 체인 칸 (2026-09-26) — 트랜잭션 해시·체인 ID·기록 블록 + [체인에서 확인].

   [체인에서 확인]은 서버가 컨트랙트 getSettlement 를 **그 자리에서 eth_call** 로 읽어
   우리 장부(fm_settlements)와 칸마다 대조한 결과를 보여 준다. 잡 소유 셀러만 부를 수 있다.
   체인을 못 읽으면 조용한 실패 문구만 — "일치" 배지는 서버 판정(verdict=match)일 때만 뜬다.
   ============================================================= */
import { useEffect, useRef, useState } from 'react';
import { Icon } from '@/components/ui.jsx';
import { checkSettlementOnChain } from '@/lib/api/facemarket.js';
import { chainCheckError, chainCheckedLabel, chainFieldValue, chainVerdict, shortHash } from '@/lib/settlementChain.js';

export function SettlementReceiptChain({ receipt }) {
  const [check, setCheck] = useState({ status: 'idle' });
  const [copied, setCopied] = useState(false);
  const alive = useRef(true);
  const busy = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  // 다른 영수증이 뜨면 이전 판정을 들고 가지 않는다.
  useEffect(() => { setCheck({ status: 'idle' }); }, [receipt?.paymentId]);

  const copyHash = async () => {
    try {
      await navigator.clipboard.writeText(receipt.txHash);
      setCopied(true);
      window.setTimeout(() => { if (alive.current) setCopied(false); }, 1600);
    } catch { /* 복사 권한이 없으면 title 의 원문을 보면 된다. */ }
  };

  const runCheck = async () => {
    if (busy.current || !receipt?.paymentId) return;
    busy.current = true;
    setCheck({ status: 'loading' });
    try {
      const result = await checkSettlementOnChain(receipt.paymentId);
      if (alive.current) setCheck({ status: 'done', result });
    } catch (error) {
      if (alive.current) setCheck({ status: 'error', message: chainCheckError(error) });
    } finally {
      busy.current = false;
    }
  };

  const verdict = check.status === 'done' ? chainVerdict(check.result) : null;
  const block = receipt?.recordedBlock;
  return (
    <div className="fm-chain">
      <div className="fm-chain-row">
        <span>트랜잭션</span>
        {receipt?.txHash ? (
          <button type="button" className="fm-chain-hash" title={receipt.txHash} onClick={copyHash}
            aria-label={`트랜잭션 해시 복사 ${receipt.txHash}`}>
            <code>{shortHash(receipt.txHash)}</code>
            <Icon name="copy" size={12} />
            {copied && <span className="fm-chain-copied">복사됨</span>}
          </button>
        ) : <code>-</code>}
      </div>
      <div className="fm-chain-row"><span>체인 ID</span><code>{receipt?.chainId || '-'}</code></div>
      <div className="fm-chain-row">
        <span>기록 블록</span>
        <code>{block === null || block === undefined ? '-' : `#${Number(block).toLocaleString('ko-KR')}`}</code>
      </div>
      <button type="button" className="fm-chain-check" onClick={runCheck}
        disabled={check.status === 'loading' || !receipt?.paymentId}>
        <Icon name="link" size={13} />
        {check.status === 'loading' ? '체인에 묻는 중…' : check.status === 'idle' ? '체인에서 확인' : '체인에서 다시 확인'}
      </button>
      {check.status === 'error' && <p className="fm-chain-error" role="alert">{check.message}</p>}
      {check.status === 'done' && (
        <div className="fm-chain-result" aria-live="polite">
          <div className="fm-chain-verdict">
            <span className={`fm-chain-badge ${verdict?.tone === 'ok' ? 'is-ok' : 'is-bad'}`}>{verdict?.label || '판정 불가'}</span>
            <span>{chainCheckedLabel(check.result.checkedAt)}</span>
          </div>
          <table className="fm-chain-table">
            <thead><tr><th scope="col">항목</th><th scope="col">우리 장부</th><th scope="col">체인</th><th scope="col"><span className="sr-only">대조</span></th></tr></thead>
            <tbody>
              {(check.result.fields || []).map((field) => (
                <tr key={field.key} className={field.match ? undefined : 'is-diff'}>
                  <td>{field.label}</td>
                  <td>{chainFieldValue(field, 'db')}</td>
                  <td>{chainFieldValue(field, 'chain')}</td>
                  <td>{field.match ? '같음' : '다름'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="fm-chain-note">{verdict?.sentence} 컨트랙트 getSettlement 를 eth_call 로 방금 읽은 값이에요.</p>
        </div>
      )}
    </div>
  );
}
