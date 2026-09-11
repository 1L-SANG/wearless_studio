import { useCallback, useEffect, useState } from 'react';
import { getPayoutStatements, getSettlementSummary, listSettlements } from '@/lib/api/facemarket.js';
import { formatKrw } from '../../facemarket-landing/facemarketTerms.js';
import { rowsForMonth, usageDate, settlementLineParts } from './usageMonths.js';
import { EmptyPanel, MonthSelect } from './MyPageParts.jsx';
import { nextPayoutLabel, payoutMonthLabel, payoutStatementStatusLabel } from './payoutStatements.js';
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
          <td>{row.productName || '상품명 미제공'}{row.billingType === 'monthly' && ' · 월정액'}</td><td>{formatKrw(row.modelAmount)}</td></tr>)}</tbody>
        <tfoot><tr><td colSpan={2}>합계 · {rows.length}건</td><td>{formatKrw(rows.reduce((total, row) => total + (Number(row.modelAmount) || 0), 0))}</td></tr></tfoot>
      </table> : <EmptyPanel title="아직 정산 내역이 없어요." description="이미지가 사용되면 정산 내역이 쌓여요." />}
    {data.summary && <p className={s.earningsTotal}>누적 {formatKrw(data.summary.totalAmount)} · {data.summary.totalCount}건</p>}
    {!data.statementsError && data.statements?.items?.length > 0 && <div className={s.statementTableWrap}><h3>월별 지급 상태</h3><table className={`${s.payoutTable} ${s.statementTable}`}>
      <thead><tr><th scope="col">정산 월</th><th scope="col">건수</th><th scope="col">금액</th><th scope="col">지급일</th><th scope="col">상태</th></tr></thead>
      <tbody>{data.statements.items.map(statement => <tr key={statement.periodMonth}><td>{payoutMonthLabel(statement.periodMonth)}</td><td>{statement.count}건</td>
        <td>{formatKrw(statement.amount)}</td><td>{statement.scheduledFor || '-'}</td><td>{payoutStatementStatusLabel(statement.status)}{statement.open ? ' · 집계 중' : ''}</td></tr>)}</tbody>
    </table></div>}
  </>;
}
