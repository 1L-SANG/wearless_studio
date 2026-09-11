import { useRef, useState } from 'react';
import { ArrowUpRight, Image as ImageIcon } from 'lucide-react';
import { reportUsage } from '@/lib/api/facemarket.js';
import { formatKrw } from '../../facemarket-landing/facemarketTerms.js';
import { MyPageDialog } from './MyPageDialog.jsx';
import { EmptyPanel, MonthSelect } from './MyPageParts.jsx';
import { rowsForMonth, usageDate } from './usageMonths.js';
import { REPORT_REASONS, reportCanSubmit, reportPayload, usageReportReducer } from './usageReportState.js';
import s from './MyPage.module.css';

export function MyPageUsage({ data, month, onMonthChange }) {
  const [selectedId, setSelectedId] = useState(null);
  const [report, setReport] = useState(() => usageReportReducer(undefined, {}));
  const sending = useRef(false);
  const detailOpener = useRef(null);
  const dispatch = action => setReport(previous => usageReportReducer(previous, action));
  const rows = rowsForMonth(data.rows, month);
  const selected = data.rows.find(row => row.id === selectedId);
  const submitReport = async event => {
    event.preventDefault();
    if (sending.current || !reportCanSubmit(report)) return;
    sending.current = true;
    const payload = reportPayload(report);
    dispatch({ type: 'submit' });
    try {
      try { await reportUsage(payload.paymentId, payload.reason); }
      catch (error) { if (error.status !== 409 || error.code !== 'usage_already_reported') throw error; }
      data.markReported(payload.paymentId);
      dispatch({ type: 'success' });
    } catch (error) { dispatch({ type: 'error', message: error.message || '신고하지 못했어요. 다시 시도해 주세요.' }); }
    finally { sending.current = false; }
  };
  return <>
    <div className={`${s.panelHeading} ${s.usageHeading}`}><div><h2>내 얼굴이 사용된 상세페이지 리스트</h2>
      <p className={s.panelCaption}>{data.loading || data.rowsError ? '사용된 상세페이지를 확인해요.' : rows.length ? `${rows.length}개의 상세페이지를 확인할 수 있어요.` : '첫 사용을 기다리고 있어요.'}</p></div>
      <MonthSelect rows={data.rows} month={month} onChange={onMonthChange} label="사용 내역 월" />
    </div>
    {data.loading ? <p role="status">사용된 페이지를 불러오고 있어요.</p>
      : data.rowsError ? <EmptyPanel title="사용된 페이지를 불러오지 못했어요." onRetry={data.retry} />
      : rows.length ? <>
        <div className={s.usageColumnLabels} aria-hidden="true"><span>상세페이지</span><span>링크</span><span>사용일</span></div>
        <ul className={s.usageList}>{rows.map(row => <li className={s.usageRow} key={row.id}>
          {row.thumbnailUrl ? <img className={s.usageThumb} src={row.thumbnailUrl} alt="" loading="lazy" /> : <span className={s.usageThumb} aria-hidden="true"><ImageIcon className={s.icon} /></span>}
          <div className={s.usageCopy}><span className={s.usageTitle}>{row.productName || '상품명 미제공'}</span>
            <span className={s.usageMeta}>{row.sellerName || '셀러명 미제공'}{row.billingType === 'monthly' && ' · 월정액'}</span>
            {row.reported && <span className={s.usageMeta}>신고됨</span>}
          </div>
          <button type="button" className={s.usageLink} aria-haspopup="dialog" aria-label={`${row.productName || '상품명 미제공'} 상세페이지 열기`} onClick={event => { detailOpener.current = event.currentTarget; setSelectedId(row.id); }}>상세페이지 열기<ArrowUpRight className={s.icon} aria-hidden="true" /></button>
          <time className={s.usageDate} dateTime={row.createdAt}>{usageDate(row.createdAt)}</time>
        </li>)}</ul>
      </> : <EmptyPanel image title="아직 사용된 상세페이지가 없어요." description="첫 사용이 생기면 여기에서 확인할 수 있어요." />}
    {selected && report.phase === 'closed' && <MyPageDialog title="사용된 상세페이지" returnFocusRef={detailOpener} onClose={() => setSelectedId(null)}>
      <div className={s.usagePreview}>
        {selected.previewUrl && <img className={s.previewImage} src={selected.previewUrl} alt={`${selected.productName || '사용된 상세페이지'} 미리보기`} />}
        <h3 className={s.previewTitle}>{selected.productName || '상품명 미제공'}</h3>
        <dl><div className={s.detailPair}><dt>셀러</dt><dd>{selected.sellerName || '셀러명 미제공'}</dd></div>
          <div className={s.detailPair}><dt>사용일</dt><dd>{usageDate(selected.createdAt)}</dd></div>
          <div className={s.detailPair}><dt>내 정산 금액</dt><dd>{formatKrw(selected.modelAmount)}</dd></div>
          <div className={s.detailPair}><dt>상태</dt><dd>{selected.reported ? '신고됨' : '활성'}</dd></div></dl>
        {selected.reported ? <p role="status">신고됨</p> : <button type="button" className={s.quietButton} disabled={!selected.paymentId}
          onClick={() => dispatch({ type: 'open', paymentId: selected.paymentId })}>사용 신고</button>}
      </div>
    </MyPageDialog>}
    {report.phase !== 'closed' && <MyPageDialog title="사용 신고" returnFocusRef={detailOpener} busy={report.phase === 'sending'} onClose={() => dispatch({ type: 'close' })}>
      <form onSubmit={submitReport}>
        <p>이 건의 사용을 신고하면 우리가 확인하고 필요하면 라이선스를 취소해요.</p>
        <fieldset className={s.reasons} disabled={report.phase === 'sending'}><legend>신고 사유를 골라 주세요</legend>
          {REPORT_REASONS.map(reason => <label key={reason}><input type="radio" name="report-reason" value={reason} checked={report.reason === reason}
            onChange={() => dispatch({ type: 'reason', value: reason })} />{reason}</label>)}
          <label htmlFor="report-detail">직접 적어도 좋아요</label>
          <textarea id="report-detail" maxLength={900} value={report.detail} onChange={event => dispatch({ type: 'detail', value: event.target.value })} />
        </fieldset>
        {report.error && <p className={s.error} role="alert">{report.error}</p>}
        <button className={s.primaryButton} type="submit" disabled={!reportCanSubmit(report)}>{report.phase === 'sending' ? '보내고 있어요' : '신고 보내기'}</button>
      </form>
    </MyPageDialog>}
  </>;
}
