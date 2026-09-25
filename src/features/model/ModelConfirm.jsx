import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, ErrorState, useToast } from '@/components/ui.jsx';
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
import { pricingLine } from '@/lib/facemarketPricing.js';
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

/* 보정 이름표·선택은 **없어요**(2026-09-16 대표 결정). 관리자가 12장을 보고 보정 1종을 골라
   그 4장만 보내요 — 여기 오는 넷은 전부 같은 보정이라, 이름표를 붙이면 고를 수 없는 것을
   고르라는 화면이 돼요. 등록자가 하는 일은 확대샷 1장 + 전신샷 1장 고르기예요. */
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

  return (
    <aside className={s.previewPanel} aria-labelledby="public-profile-preview-title">
      <h2 className={s.previewTitle} id="public-profile-preview-title">모델 리스트에 이렇게 보여요</h2>

      <section className={s.detailCard} aria-label="모델 상세 미리보기">
        <div className={s.detailImages}>
          <figure className={s.detailFigure}>
            <PreviewImage
              cut={closeupCut}
              className={s.detailImage}
              alt={`${displayName} 확대샷 상세 미리보기`}
              placeholder="확대샷 준비 중"
              eager
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
              <div className={s.specRow}><dt>가격</dt><dd>{pricingLine()}</dd></div>
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
  // "이 사진으로는 만들고 싶지 않아요" 를 누르면 사유 입력 창이 열리고, 사유와 함께 재생성을 요청해요.
  const [redoOpen, setRedoOpen] = useState(false);
  const [redoReason, setRedoReason] = useState('');

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
    if (!data?.profile || !closeupCutId || !fullbodyCutId || !agreed) return;
    setBusy(true);
    try {
      await confirmMyModelTestCuts({ closeupCutId, fullbodyCutId });
      push?.('프로필을 확정했어요. 라이선스 증서도 발급됐어요.', { icon: 'check' });
      navigate('/status', { replace: true });
    } catch (error) {
      push?.(error.message, { icon: 'alertCircle' });
      if (error.code === 'license_inactive') await load();
    }
    finally { setBusy(false); }
  }, [agreed, closeupCutId, data?.profile, fullbodyCutId, load, navigate, push]);

  const redo = useCallback(async () => {
    const reason = redoReason.trim();
    if (data?.redoCount >= 1 || busy || !reason) return;
    setBusy(true);
    try {
      await requestMyModelTestCutRedo(reason);
      push?.('다시 만들어 달라고 요청했어요.', { icon: 'check' });
      navigate('/status', { replace: true });
    } catch (error) { push?.(error.message, { icon: 'alertCircle' }); }
    finally { setBusy(false); }
  }, [busy, data?.redoCount, navigate, push, redoReason]);

  const grouped = splitTestCutsByKind(data?.cuts || []);
  const closeupCut = grouped.closeup.find((cut) => cut.id === closeupCutId) || null;
  const fullbodyCut = grouped.fullbody.find((cut) => cut.id === fullbodyCutId) || null;
  const canConfirm = data?.status === 'awaiting_confirm'
    && data?.profile && closeupCutId && fullbodyCutId && agreed && !busy;
  const redoUsed = (data?.redoCount || 0) >= 1;

  if (phase === 'loading') return <div className={s.page}><p className={s.loading}>테스트컷을 불러오는 중이에요…</p></div>;
  if (phase === 'error') return <div className={s.page}><ErrorState desc="테스트컷을 불러오지 못했어요." onRetry={load} /></div>;
  if (!data?.profile) return (
    <main className={s.page}>
      <ErrorState
        title="라이선스 상태를 확인해 주세요"
        desc="활성 라이선스 정보를 찾을 수 없어 프로필을 공개할 수 없어요. 라이선스가 만료되었거나 해지되었다면 관리자에게 상태 확인을 요청해 주세요."
        onRetry={load}
      />
    </main>
  );

  return (
    <main className={s.page}>
      <header className={s.head}>
        <span className={s.eyebrow}>공개 전 마지막 확인</span>
        <h1>내 프로필에 쓰일 이미지를 선택해주세요.</h1>
        <p>확대샷 1장, 전신샷 1장을 골라 주세요. 확정 전에는 아무것도 공개되지 않고 어떤 쇼핑몰도 내 얼굴을 쓸 수 없어요.</p>
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
              {busy ? '확정 중…' : '프로필 확정 완료'}
            </Button>
            <span className={s.redoWrap} title={redoUsed ? '재생성은 1회까지예요' : undefined}>
              <Button variant="secondary" block disabled={busy || redoUsed} aria-expanded={redoOpen} aria-controls="redo-reason-panel" onClick={() => setRedoOpen((open) => !open)}>
                이 사진으로는 만들고 싶지 않아요.
              </Button>
            </span>
            {redoUsed && <p className={s.limit}>재생성은 1회까지예요</p>}
            {redoOpen && !redoUsed && (
              <form
                id="redo-reason-panel"
                className={s.redoPanel}
                aria-labelledby="redo-reason-title"
                onSubmit={(event) => { event.preventDefault(); redo(); }}
              >
                <label id="redo-reason-title" htmlFor="redo-reason">어떤 점이 마음에 안 드는지 적어 주세요</label>
                <textarea
                  id="redo-reason"
                  value={redoReason}
                  maxLength={1000}
                  rows={4}
                  placeholder="예: 확대샷 얼굴이 실제보다 각져 보여요. 전신샷 자세가 어색해요."
                  onChange={(event) => setRedoReason(event.target.value)}
                />
                <p className={s.redoHint}>재생성은 1회만 요청할 수 있어요. 요청하면 담당자가 사유를 보고 테스트컷을 다시 만들어요.</p>
                <div className={s.redoButtons}>
                  <Button variant="secondary" type="button" disabled={busy} onClick={() => setRedoOpen(false)}>취소</Button>
                  <Button variant="primary" type="submit" disabled={busy || !redoReason.trim()}>{busy ? '보내는 중…' : '요청 보내기'}</Button>
                </div>
              </form>
            )}
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
