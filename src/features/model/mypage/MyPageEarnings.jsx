import { useCallback, useEffect, useRef, useState } from 'react';
import { checkModelSettlementOnChain, getPayoutStatements, getSettlementSummary, listSettlements } from '@/lib/api/facemarket.js';
import { chainCheckError, chainCheckedLabel, chainVerdict, shortHash } from '../../../lib/settlementChain.js';
import { formatKrw } from '../../facemarket-landing/facemarketTerms.js';
import { rowsForMonth, usageDate, settlementLineParts } from './usageMonths.js';
import { EmptyPanel, MonthSelect } from './MyPageParts.jsx';
import { nextPayoutLabel, payoutMonthLabel, payoutStatementStatusLabel, payoutDisplayRows } from './payoutStatements.js';
import s from './MyPage.module.css';

export function useMyPageSettlements(modelId, enabled = true) {
  const [data, setData] = useState({ loading: true, summary: null, rows: [], statements: { items: [], nextPayout: null }, summaryError: false, rowsError: false, statementsError: false });
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    setData(previous => ({ ...previous, loading: true }));
    Promise.allSettled([getSettlementSummary(), listSettlements(), getPayoutStatements()]).then(([totals, records, statements]) => {
      if (!alive) return;
      setData({ loading: false,
        summary: totals.status === 'fulfilled' ? totals.value : null,
        rows: records.status === 'fulfilled' && Array.isArray(records.value) ? records.value : [],
        statements: statements.status === 'fulfilled' ? statements.value : { items: [], nextPayout: null },
        summaryError: totals.status === 'rejected', rowsError: records.status === 'rejected',
        statementsError: statements.status === 'rejected',
      });
    });
    return () => { alive = false; };
  }, [modelId, enabled, attempt]);
  const markReported = paymentId => setData(previous => ({ ...previous,
    rows: previous.rows.map(row => row.paymentId === paymentId ? { ...row, reported: true } : row),
  }));
  return { ...data, retry, markReported };
}

export function NextPayout({ nextPayout }) {
  const label = nextPayoutLabel(nextPayout);
  return label ? <small>{label}</small> : null;
}

export function EarningsFigures({ summary }) {
  return <p className={s.settlementLine}>{settlementLineParts(summary).map((part, index) => part.strong
    ? <strong key={index}>{part.text}</strong> : <span key={index}>{part.text}</span>)}</p>;
}

/* 정산 한 줄의 체인 칸(2026-09-26) — 짧은 트랜잭션 해시 + [체인 확인].
   [체인 확인]은 서버가 컨트랙트를 그 자리에서 eth_call 로 읽어 이 정산과 대조한 결과다(내 정산만).
   읽지 못하면 실패라고만 쓴다 — "일치"는 서버 판정일 때만. */
export function ChainCheckLine({ row }) {
  const [state, setState] = useState(null);
  const busy = useRef(false);
  const check = async () => {
    if (busy.current) return;
    busy.current = true;
    setState({ status: 'loading' });
    try {
      setState({ status: 'done', result: await checkModelSettlementOnChain(row.id) });
    } catch (error) {
      setState({ status: 'error', message: chainCheckError(error) });
    } finally {
      busy.current = false;
    }
  };
  const verdict = state?.status === 'done' ? chainVerdict(state.result) : null;
  const block = state?.result?.fields?.find(field => field.key === 'block')?.chain;
  const different = (state?.result?.fields || []).filter(field => !field.match).map(field => field.label);
  return <span className={s.chainLine}>
    <span className={s.chainHash} title={row.txHash || ''}>{row.txHash ? shortHash(row.txHash) : '체인 기록 대기'}</span>
    {row.txHash && <button type="button" className={s.chainLink} disabled={state?.status === 'loading'} onClick={check}>
      {state?.status === 'loading' ? '확인 중…' : '체인 확인'}</button>}
    {verdict && <span role="status" className={verdict.tone === 'ok' ? s.chainOk : s.chainBad}>
      {verdict.tone === 'ok'
        ? `체인 ${verdict.label}${block != null ? ` · 블록 #${Number(block).toLocaleString('ko-KR')}` : ''} · ${chainCheckedLabel(state.result.checkedAt)}`
        : `${verdict.label}${different.length && state.result.verdict === 'mismatch' ? ` — ${different.join(', ')}` : ''} · ${chainCheckedLabel(state.result.checkedAt)}`}
    </span>}
    {state?.status === 'error' && <span role="alert" className={s.chainError}>{state.message}</span>}
  </span>;
}

export function MyPageEarnings({ data, month, onMonthChange, children }) {
  const rows = rowsForMonth(data.rows, month);
  const failed = data.summaryError || data.rowsError;
  return <>
    {children}
    <div className={s.panelHeading}><div><h2>정산 내역</h2><p className={s.panelCaption}>사용 기록별 내 정산 금액을 확인해요.</p></div>
      <MonthSelect rows={data.rows} month={month} onChange={onMonthChange} label="정산 월" />
    </div>
    {data.loading ? <p role="status">정산 정보를 불러오고 있어요.</p>
      : failed ? <EmptyPanel title="정산 정보를 불러오지 못했어요." onRetry={data.retry} />
      : rows.length ? <table className={s.payoutTable}>
        <thead><tr><th scope="col">사용일</th><th scope="col">내역</th><th scope="col">금액</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.id}><td>{usageDate(row.createdAt).slice(5)}</td>
          <td>{row.productName || '상품명 미제공'}{row.billingType === 'monthly' && ' · 월정액'}<ChainCheckLine row={row} /></td><td>{formatKrw(row.modelAmount)}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={2}>합계 · {rows.length}건</td><td>{formatKrw(rows.reduce((total, row) => total + (Number(row.modelAmount) || 0), 0))}</td></tr></tfoot>
      </table> : <EmptyPanel title="아직 정산 내역이 없어요." description="이미지가 사용되면 정산 내역이 쌓여요." />}
    {data.summary && <p className={s.earningsTotal}>누적 {formatKrw(data.summary.totalAmount)} · {data.summary.totalCount}건</p>}
    {!data.statementsError && data.statements?.items?.length > 0 && <div className={s.statementTableWrap}><h3>월별 지급 상태</h3><table className={`${s.payoutTable} ${s.statementTable}`}>
      <thead><tr><th scope="col">정산 월</th><th scope="col">건수</th><th scope="col">금액</th><th scope="col">지급일</th><th scope="col">상태</th></tr></thead>
      <tbody>{data.statements.items.flatMap(payoutDisplayRows).map(statement => <tr key={statement.key}><td>{payoutMonthLabel(statement.periodMonth)}{statement.needsReconciliation ? ' · 추가 확인 필요' : statement.additional ? ' · 추가 미지급' : ''}</td><td>{statement.count}건</td>
        <td>{formatKrw(statement.amount)}</td><td>{statement.displayDate}</td><td>{payoutStatementStatusLabel(statement.status)}{statement.open ? ' · 집계 중' : ''}
          {statement.reference && <span className={s.statementRef}>이체 참조 {statement.reference}</span>}</td></tr>)}</tbody>
    </table></div>}
  </>;
}
