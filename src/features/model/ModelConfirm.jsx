import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, Icon, useToast } from '@/components/ui.jsx';
import {
  confirmMyModelTestCuts,
  fetchMyModelTestCutUrl,
  getMyModelTestCuts,
  requestMyModelTestCutRedo,
} from '@/lib/api/facemarket.js';
import {
  defaultTestCutSelection,
  profilePhysiqueLine,
  splitTestCutsByKind,
  validityLabel,
} from './modelProfilePreview.js';
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
      width={480}
      height={640}
      loading={eager ? 'eager' : 'lazy'}
      fetchPriority={eager ? 'high' : undefined}
    />
  );
}

function CutChoiceGroup({ kind, label, cuts, selectedId, onSelect }) {
  const titleId = `${kind}-cut-title`;
  return (
    <section className={s.cutGroup} aria-labelledby={titleId}>
      <h2 id={titleId}>{label} · 1장 선택</h2>
      {cuts.length > 0 ? (
        <div className={s.options}>
          {cuts.map((cut, index) => (
            <label className={`${s.option}${selectedId === cut.id ? ` ${s.selected}` : ''}`} key={cut.id}>
              <input
                type="radio"
                name={`${kind}-cut`}
                value={cut.id}
                checked={selectedId === cut.id}
                onChange={() => onSelect(cut.id)}
              />
              <PrivateCutImage cut={cut} className={s.thumb} alt={`${label} 후보 ${index + 1}`} />
              <span>{label} {index + 1}</span>
            </label>
          ))}
        </div>
      ) : (
        <p className={s.missing} role="status">{label}이 아직 없어요. 관리자에게 알려 주세요.</p>
      )}
    </section>
  );
}

function PreviewImage({ cut, className, alt, placeholder, eager = false }) {
  if (!cut) return <div className={`${className} ${s.imagePlaceholder}`}>{placeholder}</div>;
  return <PrivateCutImage cut={cut} className={className} alt={alt} eager={eager} />;
}

function PublicProfilePreview({ profile, closeupCut, fullbodyCut }) {
  const displayName = profile?.displayName || '활동명';
  const physique = profilePhysiqueLine(profile);
  const allowedUse = profile?.license?.allowedUse?.length
    ? profile.license.allowedUse.join(' · ')
    : '미정';
  const unitPrice = Number.isFinite(profile?.license?.unitPrice)
    ? `${profile.license.unitPrice.toLocaleString('ko-KR')}원`
    : '미정';

  return (
    <aside className={s.previewPanel} aria-labelledby="public-profile-preview-title">
      <h2 className={s.previewTitle} id="public-profile-preview-title">모델 리스트에 이렇게 보여요</h2>

      <section className={s.listCard} aria-label="모델 리스트 카드 미리보기">
        <PreviewImage
          cut={closeupCut}
          className={s.listImage}
          alt={`${displayName} 확대샷`}
          placeholder="확대샷 준비 중"
          eager
        />
        <div className={s.listMeta}>
          <strong>{displayName}</strong>
          <span>{physique}</span>
        </div>
      </section>

      <section className={s.detailCard} aria-label="모델 상세 미리보기">
        <div className={s.detailImages}>
          <figure className={s.detailFigure}>
            <PreviewImage
              cut={closeupCut}
              className={s.detailImage}
              alt={`${displayName} 확대샷 상세 미리보기`}
              placeholder="확대샷 준비 중"
            />
            <figcaption>확대샷</figcaption>
          </figure>
          <figure className={s.detailFigure}>
            <PreviewImage
              cut={fullbodyCut}
              className={s.detailImage}
              alt={`${displayName} 전신샷 상세 미리보기`}
              placeholder="전신샷 준비 중"
            />
            <figcaption>전신샷</figcaption>
          </figure>
        </div>

        <h3 className={s.detailName}>{displayName}</h3>
        <div className={s.detailCols}>
          <section className={s.detailCol}>
            <h4>신체 사이즈</h4>
            <dl className={s.specList}>
              <div className={s.specRow}><dt>키·체형</dt><dd>{physique}</dd></div>
            </dl>
          </section>
          <section className={s.detailCol}>
            <h4>라이선스 조건</h4>
            <dl className={s.specList}>
              <div className={s.specRow}><dt>허용 품목</dt><dd>{allowedUse}</dd></div>
              <div className={s.specRow}><dt>건당 단가</dt><dd>{unitPrice}</dd></div>
              <div className={s.specRow}>
                <dt>유효기간</dt><dd>{validityLabel(profile?.license?.validDays)}</dd>
              </div>
            </dl>
          </section>
        </div>
      </section>
    </aside>
  );
}

export function ModelConfirm() {
  const navigate = useNavigate();
  const { push } = useToast();
  const [phase, setPhase] = useState('loading');
  const [data, setData] = useState(null);
  const [selection, setSelection] = useState({ closeupCutId: null, fullbodyCutId: null });
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setPhase('loading');
    try {
      const result = await getMyModelTestCuts();
      const cuts = result.cuts || [];
      const grouped = splitTestCutsByKind(cuts);
      const defaults = defaultTestCutSelection(cuts);
      setData(result);
      setSelection((current) => ({
        closeupCutId: grouped.closeup.some((cut) => cut.id === current.closeupCutId)
          ? current.closeupCutId
          : defaults.closeupCutId,
        fullbodyCutId: grouped.fullbody.some((cut) => cut.id === current.fullbodyCutId)
          ? current.fullbodyCutId
          : defaults.fullbodyCutId,
      }));
      setAgreed(false);
      setPhase('ready');
    } catch (error) {
      push?.(error.message, { icon: 'alertCircle' });
      setPhase('error');
    }
  }, [push]);

  useEffect(() => { load(); }, [load]);

  const { closeupCutId, fullbodyCutId } = selection;
  const confirm = useCallback(async () => {
    if (!closeupCutId || !fullbodyCutId || !agreed) return;
    setBusy(true);
    try {
      await confirmMyModelTestCuts({ closeupCutId, fullbodyCutId });
      push?.('프로필을 확정했어요. 모델 리스트에 올라갔어요.', { icon: 'check' });
      navigate('/status', { replace: true });
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusy(false); }
  }, [agreed, closeupCutId, fullbodyCutId, navigate, push]);

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

  const grouped = splitTestCutsByKind(data?.cuts || []);
  const closeupCut = grouped.closeup.find((cut) => cut.id === closeupCutId) || null;
  const fullbodyCut = grouped.fullbody.find((cut) => cut.id === fullbodyCutId) || null;
  const canConfirm = data?.status === 'awaiting_confirm'
    && closeupCutId && fullbodyCutId && agreed && !busy;
  const redoUsed = (data?.redoCount || 0) >= 1;

  if (phase === 'loading') return <div className={s.page}><p className={s.loading}>테스트컷을 불러오는 중이에요…</p></div>;
  if (phase === 'error') return <div className={s.page}><ErrorState desc="테스트컷을 불러오지 못했어요." onRetry={load} /></div>;

  return (
    <main className={s.page}>
      <header className={s.head}>
        <span className={s.eyebrow}>공개 전 마지막 확인</span>
        <h1>프로필에 걸 컷을 골라 주세요</h1>
        <p>확대샷 1장, 전신샷 1장을 고르면 오른쪽처럼 모델 리스트에 올라가요. 확정 전에는 아무것도 공개되지 않아요.</p>
      </header>

      <div className={s.layout}>
        <div className={s.selection}>
          <CutChoiceGroup
            kind="closeup"
            label="확대샷"
            cuts={grouped.closeup}
            selectedId={closeupCutId}
            onSelect={(cutId) => setSelection((current) => ({ ...current, closeupCutId: cutId }))}
          />
          <CutChoiceGroup
            kind="fullbody"
            label="전신샷"
            cuts={grouped.fullbody}
            selectedId={fullbodyCutId}
            onSelect={(cutId) => setSelection((current) => ({ ...current, fullbodyCutId: cutId }))}
          />

          <section className={s.agreement} aria-labelledby="profile-publication-consent-title">
            <h2 id="profile-publication-consent-title">공개 동의</h2>
            <label className={s.checkLabel}>
              <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />
              <span>고른 컷 2장과 아래 프로필(활동명·키·체형·라이선스 조건)이 FaceMarket 모델 리스트에 공개되는 것에 동의합니다.</span>
            </label>
          </section>

          <div className={s.actions}>
            <Button variant="primary" block disabled={!canConfirm} onClick={confirm}>
              {busy ? '확정 중…' : '이 모습으로 공개하기'}
            </Button>
            <span className={s.redoWrap} title={redoUsed ? '재생성은 1회까지예요' : undefined}>
              <Button variant="secondary" block disabled={busy || redoUsed} onClick={redo}>
                <Icon name="refresh" size={16} />
                다시 만들어 주세요
              </Button>
            </span>
            {redoUsed && <p className={s.limit}>재생성은 1회까지예요</p>}
          </div>
        </div>

        <PublicProfilePreview
          profile={data?.profile}
          closeupCut={closeupCut}
          fullbodyCut={fullbodyCut}
        />
      </div>
    </main>
  );
}

export default ModelConfirm;
