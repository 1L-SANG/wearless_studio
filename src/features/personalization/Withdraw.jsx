/* =============================================================
   features/personalization — ⑧ 삭제·철회 (/personalization/withdraw)
   학습 동의만 철회(얕은 경로) · 필수 동의(서비스이용·국외이전) 개별 철회 ·
   전체 삭제(깊은 경로) — 백엔드 캐스케이드는 하나(FR-7)로 통일되지만 UI
   진입점은 분리해 오조작을 막는다(ux-flow §5). 전체 삭제·필수 동의 철회는
   모두 되돌릴 수 없는 전체 캐스케이드 파기로 이어진다(api-spec §3.1/§3.5).
   ============================================================= */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import { getConsents, getStatus, withdrawAll, withdrawConsent } from '@/lib/api/personalization.js';
import s from './Personalization.module.css';

const LABELS = {
  service_use: '서비스이용(얼굴·신체 수집·이용)',
  cross_border_transfer: '국외이전',
  training_use: 'AI 학습 활용',
};
const CASCADE_TYPES = new Set(['service_use', 'cross_border_transfer']);
const CASCADE_WARNING = '이 작업은 되돌릴 수 없어요. 얼굴 원본 사진, 얼굴 임베딩, 생성된 산출물이 전부 파기되고 백업도 보존기간 경과 후 소멸돼요. 정말 삭제할까요?';

export function Withdraw() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading'); // loading|ready|error
  const [status, setStatus] = useState(null);
  const [consents, setConsents] = useState([]);
  const [busyType, setBusyType] = useState(null);
  const [purgingAll, setPurgingAll] = useState(false);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const [st, c] = await Promise.all([getStatus(), getConsents()]);
      setStatus(st);
      setConsents(c.consents || []);
      setPhase('ready');
    } catch (e) {
      push?.(e.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const onWithdrawType = async (type) => {
    const cascades = CASCADE_TYPES.has(type);
    const msg = cascades ? CASCADE_WARNING
      : '학습 활용 동의를 철회하면 학습용 사본만 파기되고, 서비스는 계속 이용할 수 있어요. 철회할까요?';
    if (!window.confirm(msg)) return;
    setBusyType(type);
    try {
      const r = await withdrawConsent(type);
      push?.(r.purgeJobId ? '삭제가 시작됐어요.' : '철회했어요.', { icon: 'check' });
      await load();
    } catch (e) {
      push?.(e.message || '철회에 실패했어요.', { icon: 'alertCircle' });
    } finally {
      setBusyType(null);
    }
  };

  const onWithdrawAll = async () => {
    if (!window.confirm(CASCADE_WARNING)) return;
    setPurgingAll(true);
    try {
      await withdrawAll();
      push?.('삭제가 시작됐어요.', { icon: 'check' });
      await load();
    } catch (e) {
      push?.(e.message || '삭제에 실패했어요.', { icon: 'alertCircle' });
    } finally {
      setPurgingAll(false);
    }
  };

  if (phase === 'loading') return <div className="wizard narrow"><div className="surface">불러오는 중…</div></div>;
  if (phase === 'error') return <div className="wizard narrow"><div className="surface"><ErrorState desc="상태를 불러오지 못했어요." onRetry={load} /></div></div>;

  if (status.status === 'purging') {
    return (
      <div className="wizard narrow">
        <div className="page-head"><h1>얼굴·신체 데이터 삭제</h1></div>
        <div className={`${s.banner} ${s.bannerWarn}`}>
          <Icon name="alertTri" size={16} />
          <span>삭제가 진행 중이에요. 원본·임베딩·산출물을 순서대로 파기하고 있어요. 완료되면 새로 시작할 수 있어요.</span>
        </div>
        <Button variant="ghost" icon="refresh" onClick={load} style={{ marginTop: 14 }}>새로고침</Button>
      </div>
    );
  }

  if (status.status === 'none') {
    return (
      <div className="wizard narrow">
        <div className="page-head"><h1>얼굴·신체 데이터 삭제</h1><p>등록된 데이터가 없어요.</p></div>
      </div>
    );
  }

  const grantedConsents = consents.filter((c) => c.status === 'granted');

  return (
    <div className="wizard narrow">
      <div className="page-head">
        <h1>얼굴·신체 데이터 삭제</h1>
        <p>동의를 개별로 철회하거나, 전체 데이터를 한 번에 삭제할 수 있어요.</p>
      </div>

      <div className="surface">
        <div className={s.sectionLabel}>동의 개별 철회</div>
        {grantedConsents.length === 0 ? (
          <p className="hint">철회할 동의가 없어요.</p>
        ) : grantedConsents.map((c) => (
          <div key={c.type} className={s.consentItem} style={{ alignItems: 'center' }}>
            <div className={s.consentBody}>
              <div className={s.consentTitleRow}><span className={s.consentTitle}>{LABELS[c.type] || c.type}</span></div>
              {CASCADE_TYPES.has(c.type) && <div className={s.consentDesc}>철회하면 전체 데이터가 함께 삭제돼요.</div>}
            </div>
            <button type="button" className={s.mildBtn} onClick={() => onWithdrawType(c.type)} disabled={busyType === c.type}>
              {busyType === c.type ? '처리 중…' : '철회'}
            </button>
          </div>
        ))}
      </div>

      <div className="surface">
        <div className={s.dangerCard}>
          <div className={s.dangerTitle}>얼굴·신체 데이터 전체 삭제</div>
          <div className={s.dangerDesc}>원본 사진 3장, 얼굴 임베딩, 생성된 산출물이 전부 파기돼요. 백업은 보존기간 경과 후 소멸돼요. 되돌릴 수 없어요.</div>
          <button type="button" className={s.dangerBtn} onClick={onWithdrawAll} disabled={purgingAll}>
            {purgingAll ? '삭제 중…' : '전체 삭제'}
          </button>
        </div>
      </div>

      <button type="button" className={s.footerLink} onClick={() => navigate('/personalization')}
        style={{ background: 'none', border: 0, cursor: 'pointer' }}>
        <Icon name="chevLeft" size={13} />온보딩으로 돌아가기
      </button>
    </div>
  );
}

export default Withdraw;
