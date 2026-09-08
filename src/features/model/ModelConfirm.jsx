import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  confirmMyModelTestCut,
  fetchMyModelTestCutUrl,
  getMyModelTestCuts,
  requestMyModelTestCutRedo,
} from '@/lib/api/facemarket.js';
import s from './ModelConfirm.module.css';

function PrivateCutImage({ cut, className, alt, eager = false }) {
  const [url, setUrl] = useState(null);

  useEffect(() => {
    let alive = true;
    let objectUrl = null;
    if (!cut?.imageUri) { setUrl(null); return undefined; }
    fetchMyModelTestCutUrl(cut.imageUri)
      .then((nextUrl) => {
        if (!alive) { URL.revokeObjectURL(nextUrl); return; }
        objectUrl = nextUrl;
        setUrl(nextUrl);
      })
      .catch(() => setUrl(null));
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [cut?.imageUri]);

  if (!url) return <div className={`${className} ${s.imageLoading}`} aria-label="테스트컷 불러오는 중">…</div>;
  return (
    <img
      className={className}
      src={url}
      alt={alt}
      width={eager ? 720 : 180}
      height={eager ? 900 : 225}
      loading={eager ? 'eager' : 'lazy'}
      fetchPriority={eager ? 'high' : undefined}
    />
  );
}

export function ModelConfirm() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading');
  const [data, setData] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const result = await getMyModelTestCuts();
      setData(result);
      setSelectedId((current) => (
        result.cuts?.some((cut) => cut.id === current) ? current : result.cuts?.[0]?.id || null
      ));
      setPhase('ready');
    } catch (error) {
      push?.(error.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const confirm = useCallback(async () => {
    if (!selectedId || !agreed) return;
    setBusy(true);
    try {
      await confirmMyModelTestCut(selectedId);
      push?.('프로필 컷을 확정했어요. 이제 모델 활동이 시작돼요.', { icon: 'check' });
      navigate('/status', { replace: true });
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusy(false); }
  }, [agreed, navigate, push, selectedId]);

  const redo = useCallback(async () => {
    if (data?.redoCount >= 1 || busy) return;
    if (!window.confirm('재생성은 1회만 요청할 수 있어요. 다시 만들어 달라고 요청할까요?')) return;
    setBusy(true);
    try {
      await requestMyModelTestCutRedo();
      push?.('다시 만들어 달라고 요청했어요.', { icon: 'check' });
      navigate('/status', { replace: true });
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusy(false); }
  }, [busy, data?.redoCount, navigate, push]);

  const selectedCut = data?.cuts?.find((cut) => cut.id === selectedId) || null;
  const canConfirm = data?.status === 'awaiting_confirm' && selectedId && agreed && !busy;
  const redoUsed = (data?.redoCount || 0) >= 1;

  if (phase === 'loading') return <div className={s.page}><p className={s.loading}>테스트컷을 불러오는 중이에요…</p></div>;
  if (phase === 'error') return <div className={s.page}><ErrorState desc="테스트컷을 불러오지 못했어요." onRetry={load} /></div>;
  if (!data?.cuts?.length) {
    return (
      <div className={s.page}>
        <ErrorState title="도착한 테스트컷이 없어요" desc="관리자가 컷을 보내면 이 화면에서 확인할 수 있어요." />
      </div>
    );
  }

  return (
    <main className={s.page}>
      <header className={s.head}>
        <span className={s.eyebrow}>공개 전 마지막 확인</span>
        <h1>내 프로필에 쓰일 이미지를 선택해주세요.</h1>
        <p>공개 전 마지막 확인이에요. 확정하기 전에는 아무것도 공개되지 않고 어떤 쇼핑몰도 내 얼굴을 쓸 수 없어요.</p>
      </header>

      <div className={s.layout}>
        <section className={s.preview} aria-label="선택한 테스트컷 큰 미리보기">
          <PrivateCutImage
            cut={selectedCut}
            className={s.previewImage}
            alt={`선택한 테스트컷 ${selectedCut?.sort + 1}`}
            eager
          />
          <span className={s.previewLabel}>프로필 후보 · 컷 {selectedCut?.sort + 1}</span>
        </section>

        <section className={s.controls} aria-labelledby="test-cut-options-title">
          <div>
            <p className={s.step}>1 · 컷 선택</p>
            <h2 id="test-cut-options-title">공개할 모습 한 장</h2>
          </div>
          <div className={s.options}>
            {data.cuts.map((cut) => (
              <label className={`${s.option}${selectedId === cut.id ? ` ${s.selected}` : ''}`} key={cut.id}>
                <input
                  type="radio"
                  name="approved-cut"
                  value={cut.id}
                  checked={selectedId === cut.id}
                  onChange={() => setSelectedId(cut.id)}
                />
                <PrivateCutImage cut={cut} className={s.thumb} alt={`테스트컷 ${cut.sort + 1}`} />
                <span>컷 {cut.sort + 1}</span>
              </label>
            ))}
          </div>

          <div className={s.agreement}>
            <p className={s.step}>2 · 공개 동의</p>
            <label className={s.checkLabel}>
              <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />
              <span>테스트 컷과 프로필을 확인했으며, 이 품질로 공개되는 것에 동의합니다.</span>
            </label>
          </div>

          <div className={s.actions}>
            <Button variant="primary" block disabled={!canConfirm} onClick={confirm}>
              {busy ? '확정 중…' : '프로필 확정 완료'}
            </Button>
            <span className={s.redoWrap} title={redoUsed ? '재생성은 1회까지예요' : undefined}>
              <Button variant="secondary" block disabled={busy || redoUsed} onClick={redo}>
                <Icon name="refresh" size={16} />
                다시 만들어 주세요
              </Button>
            </span>
            {redoUsed && <p className={s.limit}>재생성은 1회까지예요</p>}
          </div>
        </section>
      </div>
    </main>
  );
}

export default ModelConfirm;
