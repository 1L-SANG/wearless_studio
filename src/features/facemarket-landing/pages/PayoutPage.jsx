import { useCallback, useEffect, useState } from 'react';
import { useAuth } from '@/features/auth/AuthProvider.jsx';
import { ErrorState, Icon } from '@/components/ui.jsx';
import { listLicenses, listSettlements } from '@/lib/api/facemarket.js';
import { formatKrw } from '../facemarketTerms.js';
import { normalizeSettlementRow, settlementColumns, summarizeSettlements } from '../payoutData.js';
import { LandingShell } from '../LandingShell.jsx';
import landing from '../FacemarketLanding.module.css';
import s from './PayoutPage.module.css';

const TITLE = '정산 — FaceMarket';
const DESCRIPTION = '내 얼굴이 사용된 기록과 모델 몫 금액을 확인합니다.';

function SummaryCard({ children, label, value }) {
  return (
    <article className={s.summaryCard}>
      <span>{label}</span>
      <strong>{value}</strong>
      {children}
    </article>
  );
}

function TableCell({ column, row }) {
  if (column.key === 'thumbnail') {
    return (
      <td>
        {row.thumbnail
          ? <img alt={row.item ? `${row.item} 썸네일` : '정산 품목 썸네일'} className={s.thumbnail} src={row.thumbnail} />
          : <span className={s.cellEmpty}>—</span>}
      </td>
    );
  }
  if (column.key === 'item') {
    return (
      <td>
        <span className={s.itemName}>{row.item || row.billingLabel}</span>
        {row.item ? <small>{row.billingLabel}</small> : null}
      </td>
    );
  }
  if (column.key === 'share') return <td className={s.money}>{formatKrw(row.share)}</td>;
  if (column.key === 'status') {
    const tone = row.status === '만료' ? s.statusExpired : row.status === '활성' ? '' : s.statusUnknown;
    return <td><span className={`${s.status} ${tone}`}>{row.status}</span></td>;
  }
  return <td>{row[column.key] || <span className={s.cellEmpty}>—</span>}</td>;
}

export function PayoutView({ settlements = [], licenses = [], now = new Date() }) {
  const summary = summarizeSettlements(settlements, now);
  const columns = settlementColumns(settlements);
  const rows = settlements.map((row) => normalizeSettlementRow(row, licenses, now));
  const next = summary.nextSettlement;

  return (
    <div className={s.page}>
      <header className={s.head}>
        <div>
          <span className={s.eyebrow}>FaceMarket 모델</span>
          <h1>정산</h1>
          <p>내 얼굴이 쓰인 기록과 쌓인 내 몫을 확인해요.</p>
        </div>
        <span className={s.readyChip}>지급 준비 중 · 기록은 쌓이고 있어요</span>
      </header>

      <section aria-label="정산 요약" className={s.summaryGrid}>
        <SummaryCard label="이번 달" value={`${summary.monthCount}건`}>
          <small>{formatKrw(summary.monthAmount)}</small>
        </SummaryCard>
        <SummaryCard label="누적" value={formatKrw(summary.totalAmount)} />
        <SummaryCard label="다음 정산일" value={`${next.year}. ${next.month}. ${next.day}.`}>
          <small>매월 10일 · 전월분</small>
        </SummaryCard>
      </section>

      <p className={s.accountNote}><Icon name="info" size={15} /> 첫 지급 전에 계좌를 안내해 드려요.</p>

      {rows.length === 0 ? (
        <section className={s.empty}>
          <Icon name="coins" size={24} stroke={1.6} />
          <h2>아직 쌓인 정산 기록이 없어요</h2>
          <p>내 Digital DNA가 사용되면 날짜와 내 몫이 여기에 차곡차곡 기록돼요.</p>
        </section>
      ) : (
        <section aria-labelledby="payout-history-title" className={s.history}>
          <div className={s.historyHead}>
            <h2 id="payout-history-title">사용 기록</h2>
            <span>최근 {rows.length}건</span>
          </div>
          <div className={s.tableScroll}>
            <table>
              <thead><tr>{columns.map((column) => <th key={column.key} scope="col">{column.label}</th>)}</tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>{columns.map((column) => <TableCell column={column} key={column.key} row={row} />)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function PayoutContent() {
  const [phase, setPhase] = useState('loading');
  const [settlements, setSettlements] = useState([]);
  const [licenses, setLicenses] = useState([]);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const [settlementRows, licenseRows] = await Promise.all([
        listSettlements(),
        listLicenses().catch(() => []),
      ]);
      setSettlements(Array.isArray(settlementRows) ? settlementRows : []);
      setLicenses(Array.isArray(licenseRows) ? licenseRows : []);
      setPhase('ready');
    } catch {
      setPhase('error');
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (phase === 'loading') return <div className={s.page}><p className={s.loading}>정산 기록을 불러오는 중…</p></div>;
  if (phase === 'error') {
    return <div className={s.page}><div className={s.error}><ErrorState desc="정산 기록을 불러오지 못했어요." onRetry={load} /></div></div>;
  }
  return <PayoutView licenses={licenses} settlements={settlements} />;
}

export function PayoutPage() {
  const { session, loading, openLogin } = useAuth();
  return (
    <LandingShell description={DESCRIPTION} title={TITLE}>
      {() => {
        if (loading) return <section className={landing.section}><p className={landing.sectionLead}>불러오는 중이에요</p></section>;
        if (!session) {
          return (
            <section className={landing.section}>
              <span className={landing.eyebrow}>정산</span>
              <h1 className={landing.sectionTitle}>로그인하면 내 사용 기록과 정산을 볼 수 있어요</h1>
              <p className={landing.sectionLead}>내 얼굴이 사용된 날짜와 쌓인 내 몫을 한곳에서 확인해요.</p>
              <button className={landing.heroCta} onClick={() => openLogin('/payout')} type="button">
                로그인 <Icon name="arrowRight" size={16} stroke={2} />
              </button>
            </section>
          );
        }
        return <PayoutContent />;
      }}
    </LandingShell>
  );
}
