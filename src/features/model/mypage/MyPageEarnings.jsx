import { useCallback, useEffect, useState } from 'react';
import { getSettlementSummary, listSettlements, reportUsage } from '@/lib/api/facemarket.js';
import { formatKrw, MODEL_SHARE, STANDARD_UNIT_PRICE_KRW, MONTHLY_PASS_PRICE_KRW, MONTHLY_PASS_CUTS, MONTHLY_OVERAGE_KRW } from '../../facemarket-landing/facemarketTerms.js';
import { normalizeSettlementRow } from '../../facemarket-landing/payoutData.js';
import { MyPageDialog } from './MyPageDialog.jsx';
import { REPORT_REASONS, reportCanSubmit, reportPayload, usageReportReducer } from './usageReportState.js';
import s from './MyPage.module.css';

export function EarningsFigures({ summary }) {
  return <>
    <div className={s.earningsLine}><span>이번 달 내 몫</span><strong className={s.metric}>{formatKrw(summary.monthAmount)}</strong><span>{summary.monthCount}건</span></div>
    <p className={s.payoutNotice}>지급은 아직 시작 전이에요. 표시 금액은 지급 확정액이 아니에요.</p>
    <p className={s.earningsTotal}>누적 {formatKrw(summary.totalAmount)} · {summary.totalCount}건</p>
  </>;
}

export function MyPageEarnings({ modelId, licenses = [], revoked = false }) {
  const [phase, setPhase] = useState('loading');
  const [summary, setSummary] = useState(null);
  const [rows, setRows] = useState([]);
  const [attempt, setAttempt] = useState(0);
  const [all, setAll] = useState(false);
  const [report, setReport] = useState(() => usageReportReducer(undefined, {}));
  const dispatch = action => setReport(previous => usageReportReducer(previous, action));
  const retry = useCallback(() => setAttempt(previous => previous + 1), []);

  useEffect(() => {
    let alive = true;
    setPhase('loading');
    Promise.all([getSettlementSummary(), listSettlements()]).then(([totals, records]) => {
      if (!alive) return;
      setSummary(totals);
      setRows(records);
      setPhase('ready');
    }).catch(() => { if (alive) setPhase('error'); });
    return () => { alive = false; };
  }, [modelId, attempt]);

  useEffect(() => {
    if (phase === 'ready' && window.location.hash === '#earnings') {
      document.getElementById('earnings')?.scrollIntoView({ block: 'start' });
    }
  }, [phase]);

  const submitReport = async event => {
    event.preventDefault();
    if (!reportCanSubmit(report)) return;
    const payload = reportPayload(report);
    dispatch({ type: 'submit' });
    try {
      await reportUsage(payload.paymentId, payload.reason);
    } catch (error) {
      if (error.status !== 409 || error.code !== 'usage_already_reported') {
        dispatch({ type: 'error', message: error.message || '신고하지 못했어요. 다시 시도해 주세요.' });
        return;
      }
    }
    setRows(previous => previous.map(row => row.paymentId === payload.paymentId ? { ...row, reported: true } : row));
    dispatch({ type: 'success' });
  };

  return <section id="earnings" className={s.earnings} aria-label="수익과 사용 기록">
    {phase === 'loading' && <p role="status">수익을 불러오고 있어요.</p>}
    {phase === 'error' && <p role="status">지금은 수익을 불러오지 못했어요. <button className={s.textLink} type="button" onClick={retry}>다시 시도</button></p>}
    {phase === 'ready' && <>
      <EarningsFigures summary={summary} />
      <div className={s.dashboardSection}>
        <div className={s.sectionHeading}><h2>사용 기록</h2>
          {rows.length > 0 && <button type="button" className={s.textLink} onClick={() => setAll(value => !value)} aria-expanded={all}>{all ? '최근 5건 보기' : '전체 보기'}</button>}
        </div>
        {revoked && <p className={s.muted}>새 사용은 없어요.</p>}
        {all && <p className={s.muted}>표준가는 건당 {formatKrw(STANDARD_UNIT_PRICE_KRW)}, 월 이용권 {formatKrw(MONTHLY_PASS_PRICE_KRW)}이에요. 월 {MONTHLY_PASS_CUTS}건을 넘으면 건당 {formatKrw(MONTHLY_OVERAGE_KRW)}이고, 모델 몫은 {MODEL_SHARE * 100}%예요. 최근 최대 200건을 보여 드려요.</p>}
        {rows.length ? <ul className={s.usageList}>{rows.slice(0, all ? rows.length : 5).map(record => {
          const row = normalizeSettlementRow(record, licenses);
          return <li className={s.usageRow} key={row.id}>
            <div className={s.usageProduct}><strong>{row.item || '상품명 미제공'}</strong><p>{row.shop || '셀러명 미제공'} · {row.billingLabel} · {row.date}</p></div>
            <div className={s.usageValue}><strong>{formatKrw(row.share)}</strong><div className={s.usageTools}><span>{row.status}</span>
              {record.reported ? <span role="status">신고됨</span> : <button className={s.textLink} type="button" disabled={!record.paymentId}
                aria-label={`${row.item || '사용 기록'} 사용 신고`} onClick={() => dispatch({ type: 'open', paymentId: record.paymentId })}>신고</button>}
            </div></div>
          </li>;
        })}</ul> : <p className={s.emptyUsage}>아직 사용 기록이 없어요. 셀러가 처음 쓰면 메일로 알려요.</p>}
      </div>
    </>}
    {report.phase !== 'closed' && <MyPageDialog title="사용 신고" busy={report.phase === 'sending'} onClose={() => dispatch({ type: 'close' })}>
      <form onSubmit={submitReport}>
        <p>이 건의 사용을 신고하면 우리가 확인하고 필요하면 라이선스를 취소해요.</p>
        <fieldset className={s.reasons} disabled={report.phase === 'sending'}><legend>신고 사유를 골라 주세요</legend>
          {REPORT_REASONS.map(reason => <label key={reason}><input type="radio" name="report-reason" value={reason} checked={report.reason === reason}
            onChange={() => dispatch({ type: 'reason', value: reason })} />{reason}</label>)}
          <label htmlFor="report-detail">직접 적어도 좋아요</label>
          <textarea id="report-detail" maxLength={900} value={report.detail} onChange={event => dispatch({ type: 'detail', value: event.target.value })} />
        </fieldset>
        {report.error && <p className={s.error} role="alert">{report.error}</p>}
        <button className={s.primary} type="submit" disabled={!reportCanSubmit(report)}>{report.phase === 'sending' ? '보내고 있어요' : '신고 보내기'}</button>
      </form>
    </MyPageDialog>}
  </section>;
}
