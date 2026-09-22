import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, Image as ImageIcon } from 'lucide-react';
import { getSettlementPreviewUrl, reportUsage } from '@/lib/api/facemarket.js';
import { formatKrw } from '../../facemarket-landing/facemarketTerms.js';
import { MyPageDialog } from './MyPageDialog.jsx';
import { EmptyPanel, MonthSelect } from './MyPageParts.jsx';
import { rowsForMonth, usageDate } from './usageMonths.js';
import { REPORT_REASONS, reportCanSubmit, reportPayload, usageReportReducer } from './usageReportState.js';
import s from './MyPage.module.css';

export function MyPageUsage({ data, month, onMonthChange }) {
  const [selectedId, setSelectedId] = useState(null);
  const [report, setReport] = useState(() => usageReportReducer(undefined, {}));
  const [previews, setPreviews] = useState({});
  const sending = useRef(false);
  const alive = useRef(false);
  const previewVersion = useRef({});
  const detailOpener = useRef(null);
  const dispatch = action => setReport(previous => usageReportReducer(previous, action));
  const rows = rowsForMonth(data.rows, month);
  const selected = data.rows.find(row => row.id === selectedId);
  // 미리보기는 정산 id 로 묻는다 — 발행본이 있든 없든 서버가 고른다(발행본 > 생성 컷).
  // 발행(셀러 다운로드 공증)만 보던 예전 방식은 운영에서 발행이 0건이라 전부 빈 그림이었다.
  const loadPreview = useCallback(async settlementId => {
    if (!settlementId) return;
    const version = (previewVersion.current[settlementId] || 0) + 1;
    previewVersion.current[settlementId] = version;
    try {
      const payload = await getSettlementPreviewUrl(settlementId);
      if (!payload?.url) throw new Error('preview unavailable');
      if (alive.current && previewVersion.current[settlementId] === version) {
        setPreviews(previous => ({ ...previous, [settlementId]: { url: payload.url, source: payload.source } }));
      }
    } catch {
      if (alive.current && previewVersion.current[settlementId] === version) {
        setPreviews(previous => {
          if (!previous[settlementId]) return previous;
          const next = { ...previous };
          delete next[settlementId];
          return next;
        });
      }
    }
  }, []);
  // 보이는 달의 행만 묻는다 — 정산은 최대 200행이라 전부 물으면 화면 하나가 요청 200개다.
  // 달을 바꾸면 그 달 것을 새로 받는다(서명 URL 은 10분짜리라 다시 받는 게 맞다).
  useEffect(() => {
    alive.current = true;
    for (const row of rowsForMonth(data.rows, month)) loadPreview(row.id);
    return () => {
      alive.current = false;
      for (const settlementId of Object.keys(previewVersion.current)) previewVersion.current[settlementId] += 1;
    };
  }, [data.rows, month, loadPreview]);
  const hidePreview = settlementId => setPreviews(previous => {
    if (!previous[settlementId]) return previous;
    const next = { ...previous };
    delete next[settlementId];
    return next;
  });
  const openDetail = async (row, opener) => {
    detailOpener.current = opener;
    setSelectedId(row.id);
    await loadPreview(row.id);
  };
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
          {previews[row.id] ? <img className={s.usageThumb} src={previews[row.id].url} alt="" loading="lazy" onError={() => hidePreview(row.id)} /> : <span className={s.usageThumb} aria-hidden="true"><ImageIcon className={s.icon} /></span>}
          <div className={s.usageCopy}><span className={s.usageTitle}>{row.productName || '상품명 미제공'}</span>
            <span className={s.usageMeta}>{row.sellerName || '셀러명 미제공'}{row.billingType === 'monthly' && ' · 월정액'}</span>
            {row.reported && <span className={s.usageMeta}>신고됨</span>}
          </div>
          <button type="button" className={s.usageLink} aria-haspopup="dialog" aria-label={`${row.productName || '상품명 미제공'} 상세페이지 열기`} onClick={event => openDetail(row, event.currentTarget)}>상세페이지 열기<ArrowUpRight className={s.icon} aria-hidden="true" /></button>
          <time className={s.usageDate} dateTime={row.createdAt}>{usageDate(row.createdAt)}</time>
        </li>)}</ul>
      </> : <EmptyPanel image title="아직 사용된 상세페이지가 없어요." description="첫 사용이 생기면 여기에서 확인할 수 있어요." />}
    {selected && report.phase === 'closed' && <MyPageDialog title="사용된 상세페이지" returnFocusRef={detailOpener} onClose={() => setSelectedId(null)}>
      <div className={s.usagePreview}>
        {previews[selected.id] ? <img className={s.previewImage} src={previews[selected.id].url} alt={`${selected.productName || '사용된 상세페이지'} 미리보기`} onError={() => hidePreview(selected.id)} />
          : <span className={`${s.previewImage} ${s.previewPlaceholder}`} aria-hidden="true"><ImageIcon className={s.icon} /></span>}
        {previews[selected.id]?.source === 'cut' && <p className={s.usageMeta}>셀러가 아직 상세페이지를 발행 전이라 내 얼굴이 쓰인 생성 컷을 보여드려요.</p>}
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
