/* =============================================================
   features/library.jsx — 보관함 (PRD §4 nav)
   loading skeleton / empty / error / grid. New-creation tile.
   ============================================================= */
const { useState, useEffect, useCallback } = React;

function Library({ onNew, onOpen }) {
  const [phase, setPhase] = useState('loading');
  const [items, setItems] = useState([]);

  const load = useCallback(() => {
    setPhase('loading');
    api.getLibrary({})
      .then((list) => { setItems(list); setPhase('ready'); })
      .catch(() => setPhase('error'));
  }, []);
  useEffect(() => { load(); }, [load]);

  const statusPill = (s) => s === 'done' ? <span className="pill pill-soft st"><span className="dot dot-done" />완료</span>
    : s === 'generating' ? <span className="pill pill-soft st"><span className="dot dot-busy" />생성 중</span>
    : <span className="pill pill-soft st">초안</span>;

  return (
    <div className="wizard wide" style={{ paddingTop: 28 }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: 22 }}>
        <div><h1 style={{ fontFamily: 'var(--font-display)', fontSize: 28, color: 'var(--fg-1)' }}>보관함</h1>
          <p className="sec-sub" style={{ marginTop: 6 }}>지금까지 만든 상세페이지를 모아봤어요.</p></div>
        <Button variant="primary" icon="plus" onClick={onNew}>새 상세페이지</Button>
      </div>

      {phase === 'loading' && (
        <div className="lib-grid">{Array.from({ length: 8 }).map((_, i) => <div key={i}><Skeleton h={240} r={12} /></div>)}</div>
      )}
      {phase === 'error' && <div className="surface"><ErrorState desc="보관함을 불러오지 못했어요." onRetry={load} /></div>}
      {phase === 'ready' && items.length === 0 && (
        <div className="surface"><EmptyState icon="library" title="아직 만든 상세페이지가 없어요"
          desc="상품 사진 몇 장이면 첫 상세페이지를 만들 수 있어요." action={<Button variant="primary" icon="plus" onClick={onNew}>첫 상세페이지 만들기</Button>} /></div>
      )}
      {phase === 'ready' && items.length > 0 && (
        <div className="lib-grid">
          {items.map((it) => (
            <div className="lib-card" key={it.id} onClick={() => onOpen(it)}>
              <div className="lib-cover"><img src={it.cover} alt={it.title} /><div className="st">{statusPill(it.status)}</div></div>
              <div className="lib-info"><div className="t">{it.title}</div>
                <div className="m"><span>블록 {it.blocks}</span><span>·</span><span>{it.updatedAt}</span></div></div>
            </div>
          ))}
          <div className="lib-new" onClick={onNew}><Icon name="plus" size={22} /><span className="micro">새 상세페이지</span></div>
        </div>
      )}
    </div>
  );
}

window.Library = Library;
